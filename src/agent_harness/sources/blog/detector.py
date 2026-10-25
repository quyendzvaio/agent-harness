from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from agent_harness.domain.sources import SourceKind, SourceRef

_TRACKING_PREFIXES = ("utm_", "fbclid", "gclid", "ref", "mc_")


def blog_source(url: str) -> SourceRef | None:
    """Any http(s) page is a candidate article; the canonical id drops fragments and tracking params."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith(_TRACKING_PREFIXES)])
    canonical = urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/") or "/", query, ""))
    return SourceRef(SourceKind.BLOG, url.strip(), f"blog:{canonical}")
