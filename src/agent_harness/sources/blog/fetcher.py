from __future__ import annotations

from agent_harness.domain.errors import InvalidInput, ToolFailed
from agent_harness.ports.source_fetcher import HttpFetcher
from agent_harness.sources.blog.article_parser import Article, parse_article

_HTML_TYPES = ("text/html", "application/xhtml+xml")


class BlogFetcher:
    def __init__(self, fetcher: HttpFetcher) -> None:
        self._fetcher = fetcher

    async def fetch(self, url: str) -> Article:
        response = await self._fetcher.get(url, headers={"Accept": "text/html,application/xhtml+xml"})
        if response.status == 404:
            raise InvalidInput(f"page not found: {url}")
        if response.status >= 400:
            raise ToolFailed(f"page returned {response.status}: {url}")
        content_type = {k.lower(): v for k, v in response.headers.items()}.get("content-type", "text/html")
        if not content_type.lower().startswith(_HTML_TYPES):
            raise InvalidInput(f"not an HTML page (content-type: {content_type})")
        article = parse_article(response.text)
        if article.paragraph_count == 0:
            raise ToolFailed("no readable article text found (page may need JavaScript)")
        return article
