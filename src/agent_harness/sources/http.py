"""Outbound HTTP that enforces rule R1 on every hop, including redirects."""

from __future__ import annotations

import asyncio
import base64
import socket
from collections.abc import Awaitable, Callable, Mapping
from urllib.parse import urljoin, urlsplit

import httpx

from agent_harness.domain.errors import PolicyDenied, ToolFailed, TransientError
from agent_harness.domain.policies import SourcePolicy
from agent_harness.ports.repositories import SourceCache
from agent_harness.ports.source_fetcher import FetchResponse, HttpFetcher

Resolver = Callable[[str], Awaitable[list[str]]]
_REDIRECT_CODES = {301, 302, 303, 307, 308}
_USER_AGENT = "agent-harness-research/0.1 (+https://github.com)"
#: Larger bodies (repository tarballs) are not worth a database round-trip.
MAX_CACHED_BYTES = 1_000_000


async def system_resolver(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


class SafeHttpClient:
    """`HttpFetcher` implementation.

    Known limitation: the IP check happens before httpx connects, so a DNS answer that changes
    between the check and the connection (DNS rebinding) is not covered. Put the service behind
    an egress proxy/firewall in production for defence in depth.
    """

    def __init__(
        self,
        policy: SourcePolicy,
        *,
        client: httpx.AsyncClient | None = None,
        resolver: Resolver = system_resolver,
        timeout_s: float = 20.0,
    ) -> None:
        self._policy = policy
        self._client = client or httpx.AsyncClient(timeout=timeout_s, follow_redirects=False)
        self._resolver = resolver

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(
        self, url: str, *, headers: Mapping[str, str] | None = None, max_bytes: int | None = None
    ) -> FetchResponse:
        limit = max_bytes or self._policy.max_fetch_bytes
        request_headers = {"User-Agent": _USER_AGENT, **(headers or {})}
        current = url
        for _ in range(self._policy.max_redirects + 1):
            await self._check(current)
            try:
                async with self._client.stream("GET", current, headers=request_headers) as response:
                    if response.status_code in _REDIRECT_CODES and "location" in response.headers:
                        target = urljoin(current, response.headers["location"])
                        if urlsplit(target).hostname != urlsplit(current).hostname:
                            # Never forward credentials to another host.
                            request_headers = {k: v for k, v in request_headers.items() if k.lower() != "authorization"}
                        current = target
                        continue
                    if response.status_code == 429 or response.status_code >= 500:
                        raise TransientError(f"upstream returned {response.status_code}", url=current)
                    body = await _read_limited(response, limit)
                    return FetchResponse(current, response.status_code, body, dict(response.headers))
            except httpx.TimeoutException as exc:
                raise TransientError(f"timeout fetching {current}") from exc
            except httpx.TransportError as exc:
                raise TransientError(f"network error fetching {current}: {exc}") from exc
        raise PolicyDenied(f"too many redirects (>{self._policy.max_redirects})", url=url)

    async def _check(self, url: str) -> None:
        parts = self._policy.check_url(url)
        host = parts.hostname or ""
        try:
            addresses = await self._resolver(host)
        except OSError as exc:
            raise ToolFailed(f"cannot resolve host '{host}'") from exc
        if not addresses:
            raise ToolFailed(f"cannot resolve host '{host}'")
        for address in addresses:
            self._policy.check_ip(address)


async def _read_limited(response: httpx.Response, limit: int) -> bytes:
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise ToolFailed(f"response is {declared} bytes, limit is {limit}")
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > limit:
            raise ToolFailed(f"response exceeds {limit} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


class CachingFetcher:
    """`HttpFetcher` decorator: caches successful GETs in a `SourceCache` (fetch once, cite many times)."""

    def __init__(self, inner: HttpFetcher, cache: SourceCache, *, kind: str, ttl_s: int) -> None:
        self._inner = inner
        self._cache = cache
        self._kind = kind
        self._ttl_s = ttl_s

    async def get(
        self, url: str, *, headers: Mapping[str, str] | None = None, max_bytes: int | None = None
    ) -> FetchResponse:
        accept = (headers or {}).get("Accept", "")
        key = f"{self._kind}:{url}|{accept}"
        cached = await self._cache.get(key)
        if cached is not None:
            return FetchResponse(cached["url"], cached["status"], base64.b64decode(cached["body"]), cached["headers"])
        response = await self._inner.get(url, headers=headers, max_bytes=max_bytes)
        if response.status == 200 and len(response.content) <= MAX_CACHED_BYTES:
            payload = {
                "url": response.url,
                "status": response.status,
                "headers": {k: v for k, v in response.headers.items() if k.lower() in ("content-type", "etag")},
                "body": base64.b64encode(response.content).decode(),
            }
            await self._cache.put(key, kind=self._kind, url=url, payload=payload, ttl_s=self._ttl_s)
        return response
