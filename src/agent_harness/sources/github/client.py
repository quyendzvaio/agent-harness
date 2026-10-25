from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from agent_harness.domain.errors import InvalidInput, ToolFailed
from agent_harness.ports.source_fetcher import HttpFetcher

API_BASE = "https://api.github.com"


class GithubClient:
    """Thin GitHub REST client. Everything above it works with plain dicts and strings."""

    def __init__(self, fetcher: HttpFetcher, *, token: str | None = None, api_base: str = API_BASE) -> None:
        self._fetcher = fetcher
        self._token = token
        self._api_base = api_base.rstrip("/")

    async def get_repo(self, owner: str, repo: str) -> dict[str, Any]:
        return await self._json(f"/repos/{owner}/{repo}")

    async def get_tree(self, owner: str, repo: str, ref: str) -> list[dict[str, Any]]:
        data = await self._json(f"/repos/{owner}/{repo}/git/trees/{quote(ref, safe='')}?recursive=1")
        return [
            {"path": item["path"], "type": item["type"], "size": item.get("size")}
            for item in data.get("tree", [])
            if item.get("type") in ("blob", "tree")
        ]

    async def get_file(self, owner: str, repo: str, path: str, ref: str | None = None) -> str:
        url = f"{self._api_base}/repos/{owner}/{repo}/contents/{quote(path)}"
        if ref:
            url += f"?ref={quote(ref, safe='')}"
        response = await self._fetcher.get(url, headers=self._headers("application/vnd.github.raw+json"))
        self._raise_for_status(response.status, f"{owner}/{repo}/{path}")
        return response.text

    async def get_tarball(self, owner: str, repo: str, ref: str, *, max_bytes: int) -> bytes:
        """Whole repository at `ref` as .tar.gz (one request; GitHub redirects to codeload)."""
        url = f"{self._api_base}/repos/{owner}/{repo}/tarball/{quote(ref, safe='')}"
        response = await self._fetcher.get(url, headers=self._headers("application/vnd.github+json"), max_bytes=max_bytes)
        self._raise_for_status(response.status, f"{owner}/{repo} tarball")
        return response.content

    async def _json(self, path: str) -> dict[str, Any]:
        response = await self._fetcher.get(self._api_base + path, headers=self._headers("application/vnd.github+json"))
        self._raise_for_status(response.status, path)
        return json.loads(response.content)

    def _headers(self, accept: str) -> dict[str, str]:
        headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    @staticmethod
    def _raise_for_status(status: int, what: str) -> None:
        if status == 404:
            raise InvalidInput(f"GitHub resource not found (or private): {what}")
        if status in (401, 403):
            raise ToolFailed(f"GitHub refused the request ({status}); rate limited or GITHUB_TOKEN missing")
        if status >= 400:
            raise ToolFailed(f"GitHub returned {status} for {what}")
