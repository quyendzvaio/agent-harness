"""Readable-text extraction with the standard library only (no readability dependency)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

_SKIP = {"script", "style", "noscript", "nav", "footer", "aside", "form", "svg", "iframe", "button", "template"}
_HEADINGS = {"h1", "h2", "h3", "h4"}
_BLOCKS = {"p", "li", "pre", "blockquote", "figcaption", "td", "dd", "dt"}
_VOID = {"br", "img", "hr", "meta", "link", "input", "source", "wbr", "area", "base", "col", "embed", "param", "track"}


@dataclass(slots=True)
class Section:
    heading: str
    paragraphs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Article:
    title: str
    author: str | None
    published: str | None
    sections: list[Section]

    @property
    def paragraph_count(self) -> int:
        return sum(len(s.paragraphs) for s in self.sections)


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta: dict[str, str] = {}
        self.stack: list[str] = []
        self.skip_depth = 0
        self.in_title = False
        self.buffer: list[str] = []
        self.current_tag: str | None = None
        # (container_depth, kind, text) where container is article/main if present
        self.blocks: list[tuple[bool, str, str]] = []
        self.container_depth = 0
        self.saw_container = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {k: v or "" for k, v in attrs}
        if tag == "meta":
            key = attributes.get("property") or attributes.get("name")
            if key and attributes.get("content"):
                self.meta[key.lower()] = attributes["content"]
            return
        if tag in _VOID:
            return
        self.stack.append(tag)
        if tag in _SKIP or (tag == "header" and not self.container_depth):
            self.skip_depth += 1
        if tag in ("article", "main"):
            self.container_depth += 1
            self.saw_container = True
        if tag == "title":
            self.in_title = True
        if tag in _HEADINGS | _BLOCKS and not self.skip_depth:
            self._flush()
            self.current_tag = tag

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID or tag not in self.stack:
            return
        while self.stack:
            open_tag = self.stack.pop()
            if tag == "title":
                self.in_title = False
            if open_tag in _HEADINGS | _BLOCKS and open_tag == self.current_tag:
                self._flush()
            if open_tag in _SKIP or (open_tag == "header" and not self.container_depth):
                self.skip_depth = max(0, self.skip_depth - 1)
            if open_tag in ("article", "main"):
                self.container_depth = max(0, self.container_depth - 1)
            if open_tag == tag:
                break

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title += data
        elif not self.skip_depth and self.current_tag:
            self.buffer.append(data)

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self.buffer)).strip()
        if text and self.current_tag:
            kind = "heading" if self.current_tag in _HEADINGS else "para"
            self.blocks.append((self.container_depth > 0, kind, text))
        self.buffer = []
        self.current_tag = None


def parse_article(html: str) -> Article:
    extractor = _Extractor()
    extractor.feed(html)
    extractor.close()
    extractor._flush()
    blocks = [b for b in extractor.blocks if b[0]] if extractor.saw_container else extractor.blocks
    meta = extractor.meta
    title = meta.get("og:title") or extractor.title.strip()
    sections: list[Section] = []
    current = Section(heading="")
    for _, kind, text in blocks:
        if kind == "heading":
            if not title:
                title = text
            if current.paragraphs or current.heading:
                sections.append(current)
            current = Section(heading=text)
        elif len(text) >= 20 or text.endswith((".", ":", "?", "!")):
            current.paragraphs.append(text)
    if current.paragraphs or current.heading:
        sections.append(current)
    # Drop a leading heading-only section that just repeats the title.
    sections = [s for s in sections if s.paragraphs or s.heading != title]
    return Article(
        title=title or "(untitled)",
        author=meta.get("author") or meta.get("article:author"),
        published=meta.get("article:published_time") or meta.get("date"),
        sections=sections,
    )
