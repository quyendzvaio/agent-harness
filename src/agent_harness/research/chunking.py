from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Chunk:
    locator: str
    text: str


def chunk_text_by_chars(text: str, *, locator_prefix: str, max_chars: int = 1_500) -> list[Chunk]:
    """Split on paragraph boundaries, keeping each chunk under `max_chars`."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[Chunk] = []
    buffer: list[str] = []
    size = 0
    for paragraph in paragraphs:
        if buffer and size + len(paragraph) > max_chars:
            chunks.append(Chunk(f"{locator_prefix}#{len(chunks) + 1}", "\n\n".join(buffer)))
            buffer, size = [], 0
        buffer.append(paragraph[:max_chars])
        size += len(paragraph)
    if buffer:
        chunks.append(Chunk(f"{locator_prefix}#{len(chunks) + 1}", "\n\n".join(buffer)))
    return chunks


def chunk_lines(lines: list[str], *, path: str, first_line: int = 1, max_lines: int = 80) -> list[Chunk]:
    """Fixed-size line windows with `path:Lstart-Lend` locators."""
    chunks = []
    for offset in range(0, len(lines), max_lines):
        window = lines[offset : offset + max_lines]
        start = first_line + offset
        chunks.append(Chunk(f"{path}:L{start}-L{start + len(window) - 1}", "\n".join(window)))
    return chunks
