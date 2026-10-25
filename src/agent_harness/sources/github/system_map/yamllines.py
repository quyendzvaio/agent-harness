"""YAML that remembers where each key and list item came from, so every conclusion can cite `file:line`."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import yaml


class LineDict(dict):
    """A mapping whose `lines[key]` is the 1-based line of that key; `line` is where the mapping starts."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: dict[Any, int] = {}
        self.line = 0


class LineList(list):
    """A sequence whose `lines[i]` is the 1-based line of item `i`."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[int] = []
        self.line = 0


class _Loader(yaml.SafeLoader):
    pass


def _mapping(loader: _Loader, node: yaml.MappingNode) -> LineDict:
    loader.flatten_mapping(node)  # resolves `<<: *anchor` merge keys
    out = LineDict()
    out.line = node.start_mark.line + 1
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        try:
            hash(key)
        except TypeError:
            continue
        out[key] = loader.construct_object(value_node, deep=True)
        out.lines[key] = key_node.start_mark.line + 1
    return out


def _sequence(loader: _Loader, node: yaml.SequenceNode) -> LineList:
    out = LineList()
    out.line = node.start_mark.line + 1
    for child in node.value:
        out.append(loader.construct_object(child, deep=True))
        out.lines.append(child.start_mark.line + 1)
    return out


_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)
_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_SEQUENCE_TAG, _sequence)
# Tags like `!Ref`/`!reference` (CloudFormation, GitLab) must not abort the whole file.
_Loader.add_multi_constructor("!", lambda loader, suffix, node: None)


def load_all(text: str) -> Iterator[Any]:
    """Every document in `text`; a document that does not parse ends the iteration instead of raising."""
    try:
        yield from yaml.load_all(text, Loader=_Loader)  # noqa: S506 - SafeLoader subclass
    except yaml.YAMLError:
        return


def load(text: str) -> Any:
    return next(load_all(text), None)


def line_of(container: Any, key: Any, default: int = 1) -> int:
    if isinstance(container, LineDict):
        return container.lines.get(key, container.line or default)
    if isinstance(container, LineList) and isinstance(key, int) and 0 <= key < len(container.lines):
        return container.lines[key]
    return getattr(container, "line", 0) or default
