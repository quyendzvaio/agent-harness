from __future__ import annotations

from collections.abc import Iterable

from agent_harness.domain.evidence import Evidence
from agent_harness.domain.sources import SourceRef

MAX_QUOTE_CHARS = 2_000


class EvidenceStore:
    """Conversation-scoped evidence. Labels continue across turns so follow-up answers can cite earlier reads."""

    def __init__(self, existing: Iterable[Evidence] = ()) -> None:
        self._by_label: dict[str, Evidence] = {}
        self._by_key: dict[tuple[str, str], Evidence] = {}
        self._new: list[Evidence] = []
        for item in existing:
            self._index(item)

    def add(self, source: SourceRef, locator: str, quote: str) -> Evidence:
        key = (source.canonical_id, locator)
        if key in self._by_key:
            return self._by_key[key]
        item = Evidence(f"E{len(self._by_label) + 1}", source, locator, quote[:MAX_QUOTE_CHARS])
        self._index(item)
        self._new.append(item)
        return item

    def get(self, label: str) -> Evidence | None:
        return self._by_label.get(label)

    def items(self) -> list[Evidence]:
        return list(self._by_label.values())

    def labels(self) -> set[str]:
        return set(self._by_label)

    @property
    def new_items(self) -> list[Evidence]:
        return list(self._new)

    def _index(self, item: Evidence) -> None:
        self._by_label[item.label] = item
        self._by_key[(item.source.canonical_id, item.locator)] = item
