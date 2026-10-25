"""Deterministic runtime topology of a repository (the `reading-system-architecture` method, Q0-Q9).

`build_system_map(files)` reads manifests first (what runs), then follows each process's environment
chain (where it connects), matches both ends of every channel, assigns data ownership, and finally runs
the closure invariants. Everything carries `file:line`; nothing is concluded from names or docs alone.
"""

from __future__ import annotations

from collections.abc import Mapping

from agent_harness.sources.github.system_map.catalog import load_catalog
from agent_harness.sources.github.system_map.channels import ChannelMatcher
from agent_harness.sources.github.system_map.closure import check_closure, check_hypotheses
from agent_harness.sources.github.system_map.envchain import build_bindings, scan_env
from agent_harness.sources.github.system_map.manifests import ManifestReader
from agent_harness.sources.github.system_map.model import SystemMap
from agent_harness.sources.github.system_map.repo import GENERATED, TEST_DIRS, Repo, owner_of
from agent_harness.sources.github.system_map.stores import assign_stores

__all__ = ["SystemMap", "build_system_map"]

#: Code files scanned per process at most (keeps one build within a few seconds on large monorepos).
MAX_FILES_PER_PROCESS = 3_000
CODE_SUFFIXES = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".cs", ".java", ".kt", ".rs", ".rb", ".php", ".vue", ".svelte")


def build_system_map(files: Mapping[str, str], *, max_files: int = MAX_FILES_PER_PROCESS) -> SystemMap:
    catalog = load_catalog()
    repo = Repo(files)
    smap = SystemMap()
    reader = ManifestReader(repo, catalog, smap)
    reader.read()

    code_dirs = [(p.name, p.code_dir) for p in smap.processes.values() if p.code_dir is not None]
    owned: dict[str, list[str]] = {name: [] for name, _ in code_dirs}
    orphans: list[str] = []
    for path in repo.paths:
        if not path.endswith(CODE_SUFFIXES) or GENERATED.search(path):
            continue
        owner = owner_of(path, code_dirs)
        if owner is None:
            orphans.append(path)
        elif not TEST_DIRS.search(path[len(smap.processes[owner].code_dir or "") :]):
            owned[owner].append(path)
    for name, paths in owned.items():
        if len(paths) > max_files:
            smap.truncated = True
            smap.gap("Q0", f"{name}: {len(paths)} file code, chỉ quét {max_files} file đầu")
            owned[name] = paths[:max_files]

    indexes = {name: scan_env(repo, catalog, paths) for name, paths in owned.items()}
    build_bindings(smap, repo, indexes, reader.env_example)
    matcher = ChannelMatcher(repo, catalog, smap, owned, indexes, reader.env_example)
    matcher.run()
    assign_stores(repo, catalog, smap, owned, orphans, indexes, reader.env_example)
    matcher.reliability()
    check_closure(smap, catalog)
    check_hypotheses(repo, smap)
    smap.manifests = list(dict.fromkeys(smap.manifests))
    return smap
