from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class FetchResponse:
    url: str
    status: int
    content: bytes
    headers: Mapping[str, str] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class HttpFetcher(Protocol):
    """Outbound HTTP used by source adapters. The production implementation enforces rule R1."""

    async def get(
        self, url: str, *, headers: Mapping[str, str] | None = None, max_bytes: int | None = None
    ) -> FetchResponse: ...
