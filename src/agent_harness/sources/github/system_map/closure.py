"""Q9: the graph is closed only when every invariant holds; each violation is one gap line.

Also turns architecture claims in README/docs into hypotheses: a claim naming two components with no
edge between them is reported as unverified, never adopted.
"""

from __future__ import annotations

import posixpath
import re

from agent_harness.sources.github.system_map.catalog import Catalog
from agent_harness.sources.github.system_map.model import SystemMap
from agent_harness.sources.github.system_map.repo import Repo

INVARIANTS = (
    "I1 mọi biến env code đọc có nơi đặt hoặc mặc định",
    "I2 mọi route server có caller hoặc là public/vận hành",
    "I3 mọi lời gọi tới route trong repo có đầu server khớp",
    "I4 route framework ngoài repo không tính vi phạm",
    "I5 mọi store có cả writer lẫn reader",
    "I6 mọi node không phải biên có cạnh",
    "I7 mọi node undeployed được liệt kê",
    "I8 hai đầu mỗi hợp đồng dùng cùng schema",
    "I9 không có chu trình trong thứ tự khởi động",
    "I10 mỗi dòng có bằng chứng file:dòng",
)
_DATA = {"db", "cache", "queue", "storage", "vector-store"}
_DOC_FILES = re.compile(r"^(README|CLAUDE|ARCHITECTURE|AGENTS)[^/]*\.md$|^docs?/[^/]+\.md$", re.I)
_ARROW = re.compile(r"→|->|=>|gọi|calls?|sends?|publish|consume|writes?|reads?|ghi|đọc|đẩy|nhận", re.I)


def check_closure(smap: SystemMap, catalog: Catalog) -> None:
    smap.invariants_checked = len(INVARIANTS)
    ops = catalog.c["ops_paths"]
    # I1
    for binding in smap.bindings:
        if binding.value is None and binding.source == "không đặt":
            smap.gap("Q9-I1", f"{binding.process} đọc {binding.var} nhưng không manifest nào đặt và không có mặc định", binding.read_ev)
    # I2 + I4: routes of processes nobody outside can reach must have a caller
    called = {(c.dst, c.detail) for c in smap.channels}
    for route in smap.routes:
        process = smap.processes[route.process]
        if route.framework or process.published or ops.match(route.path):
            continue
        if (route.process, f"{route.method} {route.path}") not in called:
            smap.gap("Q9-I2", f"route {route.method} {route.path} của {route.process} không có caller trong repo "
                              f"và {route.process} không publish cổng", route.ev)  # fmt: skip
    # I5 (an isolated store is reported once, by I6; a host directory mounted read-only is written outside the system)
    touched = {c.src for c in smap.channels} | {c.dst for c in smap.channels}
    for store in smap.stores.values():
        if store.name in smap.processes and store.name not in touched:
            continue
        missing = [role for role, who in (("writer", store.writers), ("reader", store.readers)) if not who]
        if store.name.startswith("volume:/") and missing == ["writer"]:
            store.notes.append("thư mục của máy host: writer nằm ngoài hệ thống")
            continue
        if missing:
            smap.gap("Q9-I5", f"store {store.name} không có {' và '.join(missing)} trong code đã quét", store.ev)
    # I6
    for process in smap.processes.values():
        if process.is_boundary or process.where == "undeployed":
            continue
        if process.name not in touched:
            smap.gap("Q9-I6", f"node {process.name} ({process.kind}) không có cạnh nào: code chết, chưa khởi động, hoặc thiếu cạnh", process.ev)
        elif process.kind in ("http-server", "web") and not process.published and not any(c.dst == process.name for c in smap.channels):
            smap.gap("Q9-I6", f"{process.name} là {process.kind} nhưng không ai gọi tới và không publish cổng", process.ev)
    # I7
    for process in smap.processes.values():
        if process.where == "undeployed":
            smap.gap("Q9-I7", f"{process.name}: có code ({process.code_dir}) nhưng không manifest nào khởi động", process.ev)
    # I8: HTTP between two processes with no shared schema is an implicit contract
    has_spec = any(p.endswith(("openapi.json", "openapi.yaml", "openapi.yml", "swagger.json", "swagger.yaml")) for p in smap.manifests) or False
    pairs = sorted({(c.src, c.dst) for c in smap.channels if c.kind in ("http", "realtime") and c.src != "user"
                    and smap.processes[c.dst].kind not in _DATA | {"external"}})  # fmt: skip
    if pairs and not has_spec:
        smap.gap("Q9-I8", "hợp đồng HTTP ngầm (không thấy OpenAPI/schema dùng chung hai đầu): "
                          + ", ".join(f"{a}→{b}" for a, b in pairs[:10]))  # fmt: skip
    # I9
    graph = {p.name: [d for d, _, _ in p.depends_on] for p in smap.processes.values()}
    for cycle in _cycles(graph):
        smap.gap("Q9-I9", f"chu trình depends_on: {' → '.join(cycle)} (deadlock lúc khởi động)")
    # I10
    for process in smap.processes.values():
        if not process.is_boundary and not process.ev.path:
            smap.gap("Q9-I10", f"node {process.name} không có bằng chứng file:dòng")
    for channel in smap.channels:
        if channel.client_ev is None and channel.server_ev is None:
            smap.gap("Q9-I10", f"cạnh {channel.src}→{channel.dst} không có bằng chứng file:dòng")


def _cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    found: list[list[str]] = []
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> None:
        state[node] = 1
        stack.append(node)
        for nxt in graph.get(node, []):
            if state.get(nxt) == 1:
                found.append(stack[stack.index(nxt) :] + [nxt])
            elif nxt in graph and not state.get(nxt):
                visit(nxt)
        stack.pop()
        state[node] = 2

    for node in graph:
        if not state.get(node):
            visit(node)
    return found


def check_hypotheses(repo: Repo, smap: SystemMap, limit: int = 30) -> None:
    names: dict[str, str] = {}
    for process in smap.processes.values():
        if process.kind == "user":
            continue
        for alias in {process.name, *process.aliases}:
            if len(alias) >= 4 and not alias.startswith(("dev:", "script:", "volume:", "job:", "deploy:")):
                names[alias.lower()] = process.name
    if len(names) < 2:
        return
    pattern = re.compile(r"(?<![\w-])(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r")(?![\w-])", re.I)
    linked = {frozenset((c.src, c.dst)) for c in smap.channels}
    via_broker: dict[str, set[str]] = {}
    for channel in smap.channels:
        if channel.kind == "queue":
            for end, other in ((channel.src, channel.dst), (channel.dst, channel.src)):
                via_broker.setdefault(end, set()).add(other)
    for path in repo.paths:
        if not _DOC_FILES.search(path) or posixpath.dirname(path).count("/") > 1:
            continue
        for number, line in enumerate(repo.lines(path), 1):
            if not _ARROW.search(line):
                continue
            found = list(dict.fromkeys(names[m.group(1).lower()] for m in pattern.finditer(line)))
            if len(found) < 2:
                continue
            a, b = found[0], found[1]
            verified = frozenset((a, b)) in linked or bool(via_broker.get(a, set()) & via_broker.get(b, set()))
            claim = line.strip()[:160]
            smap.hypotheses.append((claim, repo.ev(path, number), verified))
            if not verified:
                smap.gap("claim", f"tài liệu nói {a} ↔ {b} nhưng không tìm thấy cạnh tương ứng: “{claim}”", repo.ev(path, number))
            if len(smap.hypotheses) >= limit:
                return
