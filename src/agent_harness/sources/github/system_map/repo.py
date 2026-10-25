"""Read-only view of the snapshot used by every reading step: lines, evidence, directory ownership."""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable, Mapping
from functools import cached_property

from agent_harness.sources.github.snapshot import is_ignored
from agent_harness.sources.github.system_map.model import Ev

#: Directories whose files never define the running system (they still count as Q5 batch-job candidates
#: when they write to a store, see stores.py).
TEST_DIRS = re.compile(r"(^|/)(tests?|__tests__|spec|e2e|fixtures|examples?|docs?|testdata)(/|$)")
#: Generated stubs (protobuf/gRPC) declare every service's client and server; they say nothing about who
#: calls whom.
GENERATED = re.compile(r"(^|/)(genproto|generated|gen)/|\.pb\.go$|_pb2(_grpc)?\.pyi?$|_grpc_pb\.[jt]s$|_pb\.[jt]s$|\.g\.cs$|Grpc\.cs$")


def norm_dir(base: str, rel: str) -> str:
    """Join and normalise a repo-relative directory; the repository root is ""."""
    joined = posixpath.normpath(posixpath.join(base or ".", rel or "."))
    return "" if joined == "." else joined


def inside(path: str, directory: str) -> bool:
    return directory == "" or path == directory or path.startswith(directory + "/")


class Repo:
    def __init__(self, files: Mapping[str, str]) -> None:
        self.files = {p: t for p, t in files.items() if not is_ignored(p) and ".." not in p.split("/")}
        self._lines: dict[str, list[str]] = {}

    @cached_property
    def paths(self) -> list[str]:
        return sorted(self.files)

    @cached_property
    def dirs(self) -> set[str]:
        out = {""}
        for path in self.files:
            parts = path.split("/")[:-1]
            out.update("/".join(parts[: i + 1]) for i in range(len(parts)))
        return out

    def lines(self, path: str) -> list[str]:
        if path not in self._lines:
            self._lines[path] = self.files.get(path, "").splitlines()
        return self._lines[path]

    def ev(self, path: str, line: int) -> Ev:
        lines = self.lines(path)
        text = lines[line - 1].strip() if 0 < line <= len(lines) else ""
        return Ev(path, max(line, 1), text[:200])

    def line_of(self, path: str, pos: int) -> int:
        """1-based line of character offset `pos`."""
        return self.files[path].count("\n", 0, pos) + 1

    def find_line(self, path: str, needle: str, default: int = 1) -> int:
        for number, line in enumerate(self.lines(path), 1):
            if needle in line:
                return number
        return default

    def under(self, directory: str) -> list[str]:
        return [p for p in self.paths if inside(p, directory)]

    def named(self, *names: str) -> list[str]:
        return [p for p in self.paths if posixpath.basename(p) in names]


def owner_of(path: str, code_dirs: Iterable[tuple[str, str]]) -> str | None:
    """Process owning `path`: the one whose code dir is the deepest prefix of it."""
    best: tuple[int, str] | None = None
    for name, directory in code_dirs:
        if inside(path, directory) and (best is None or len(directory) > best[0]):
            best = (len(directory), name)
    return best[1] if best else None
