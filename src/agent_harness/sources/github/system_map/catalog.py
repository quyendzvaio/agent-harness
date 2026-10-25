"""Loads the YAML catalogs that hold the reading rules (image kinds, entrypoint roles, env readers, channels).

The catalogs are data: adding a framework or a language is a YAML change, not a code change.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import yaml

CATALOG_DIR = Path(__file__).parent / "catalog"


@dataclass(frozen=True, slots=True)
class Pattern:
    exts: tuple[str, ...]
    regex: re.Pattern[str]
    requires: re.Pattern[str] | None = None
    prefix_arg: re.Pattern[str] | None = None
    #: env_readers: the read fails when the variable is unset (`os.environ["X"]`, Go `mustMapEnv`).
    required: bool = False

    def applies(self, path: str) -> bool:
        return posixpath.splitext(path)[1] in self.exts


@dataclass(frozen=True, slots=True)
class Catalog:
    images: tuple[tuple[re.Pattern[str], str], ...]
    roles: tuple[tuple[re.Pattern[str], str], ...]
    code_markers: tuple[tuple[re.Pattern[str], str], ...]
    web_markers: frozenset[str]
    env_readers: tuple[Pattern, ...]
    c: dict[str, Any]  # connectors: lists of Pattern or compiled single regexes

    def image_kind(self, image: str) -> str | None:
        name = image_name(image)
        return next((kind for regex, kind in self.images if regex.search(name)), None)

    def role(self, command: str) -> str | None:
        return next((kind for regex, kind in self.roles if regex.search(command)), None)


def image_name(image: str) -> str:
    """`us-docker.pkg.dev/p/repo/adservice:v1@sha256:..` -> `adservice`."""
    last = image.split("@", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    return last.split(":", 1)[0].lower()


def _compile(text: str, flags: int = re.M) -> re.Pattern[str]:
    try:
        return re.compile(text, flags)
    except re.error as exc:  # a broken catalog must fail loudly in tests, not silently at runtime
        raise ValueError(f"invalid catalog regex {text!r}: {exc}") from exc


def _patterns(items: list[dict[str, Any]], flags: int = re.M) -> tuple[Pattern, ...]:
    return tuple(
        Pattern(
            tuple(item["exts"]),
            _compile(item["pattern"], flags),
            _compile(item["requires"]) if item.get("requires") else None,
            _compile(item["prefix_arg"]) if item.get("prefix_arg") else None,
            bool(item.get("required")),
        )
        for item in items
    )


def _read(name: str) -> Any:
    return yaml.safe_load((CATALOG_DIR / name).read_text(encoding="utf-8"))


@cache
def load_catalog() -> Catalog:
    entry = _read("entrypoints.yaml")
    raw = _read("connectors.yaml")
    connectors: dict[str, Any] = {}
    for key, value in raw.items():
        if key.isupper():  # YAML anchors for extension lists
            continue
        if isinstance(value, list) and value and isinstance(value[0], dict):
            connectors[key] = _patterns(value)
        elif isinstance(value, list):
            connectors[key] = tuple(value)
        else:
            connectors[key] = _compile(value, re.M | (re.I if key in {"db_writes", "db_reads"} else 0))
    return Catalog(
        images=tuple((_compile(i["match"], re.I), i["kind"]) for i in _read("images.yaml")),
        roles=tuple((_compile(r["match"], re.I), r["kind"]) for r in entry["roles"]),
        code_markers=tuple((_compile(r["match"]), r["kind"]) for r in entry["code_markers"]),
        web_markers=frozenset(entry["web_markers"]),
        env_readers=_patterns(_read("env_readers.yaml")),
        c=connectors,
    )
