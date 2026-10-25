"""Rule R1: only public http(s) URLs may be fetched (SSRF protection)."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from urllib.parse import SplitResult, urlsplit

from agent_harness.domain.errors import PolicyDenied

_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")


@dataclass(frozen=True, slots=True)
class SourcePolicy:
    allowed_schemes: frozenset[str] = frozenset({"http", "https"})
    #: When set, only these hosts (and their subdomains) may be fetched.
    allowed_hosts: frozenset[str] | None = None
    max_fetch_bytes: int = 2_000_000
    max_redirects: int = 5
    blocked_hosts: frozenset[str] = field(default_factory=lambda: frozenset({"localhost", "metadata.google.internal"}))

    def check_url(self, url: str) -> SplitResult:
        """Static checks that need no DNS. Resolved IPs are checked with `check_ip`."""
        try:
            parts = urlsplit(url.strip())
        except ValueError as exc:
            raise PolicyDenied(f"malformed URL: {url!r}") from exc
        scheme = parts.scheme.lower()
        if scheme not in self.allowed_schemes:
            raise PolicyDenied(f"scheme '{scheme or '?'}' is not allowed", url=url)
        host = (parts.hostname or "").lower().rstrip(".")
        if not host:
            raise PolicyDenied("URL has no host", url=url)
        if parts.username or parts.password:
            raise PolicyDenied("credentials in URL are not allowed", url=url)
        if host in self.blocked_hosts or host.endswith(_BLOCKED_HOST_SUFFIXES):
            raise PolicyDenied(f"host '{host}' is internal", url=url)
        literal_ip = _parse_ip(host)
        if literal_ip is not None:
            self.check_ip(str(literal_ip))
        if self.allowed_hosts is not None and not any(
            host == allowed or host.endswith("." + allowed) for allowed in self.allowed_hosts
        ):
            raise PolicyDenied(f"host '{host}' is not in the allowlist", url=url)
        return parts

    def check_ip(self, ip: str) -> None:
        address = _parse_ip(ip)
        if address is None:
            raise PolicyDenied(f"not an IP address: {ip}")
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        if not address.is_global or address.is_multicast:
            raise PolicyDenied(f"address {address} is not public", ip=str(address))


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value.strip("[]"))
    except ValueError:
        return None
