"""Text views of a `SystemMap` in the five-part shape of the `reading-system-architecture` method,
and the node/edge plan a diagram is drawn from.

Rendering never decides evidence labels itself: every row with a `file:line` goes through `cite`, which
the caller (the tool) backs with its evidence store. Locators end in a `(kind)` suffix so a map row never
counts as having read the file.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace

from agent_harness.domain.errors import InvalidInput
from agent_harness.sources.github.system_map.model import (
    EXTRACTED,
    INFERRED,
    Channel,
    Ev,
    Process,
    SystemMap,
    weakest,
)

#: (locator, quote) -> "[E#]"
Cite = Callable[[str, str], str]

SECTIONS = ("overview", "processes", "channels", "stores", "env", "gaps", "hypotheses")
BASE = "base"
MAX_CELL = 70


def header(smap: SystemMap) -> str:
    if smap.has_runtime:
        manifests = [m for m in smap.manifests if not m.startswith(".github/")] or smap.manifests
        shown = ", ".join(manifests[:12]) + (f" (+{len(manifests) - 12})" if len(manifests) > 12 else "")
        return f"Kiến trúc ĐƯỢC KHAI BÁO từ {shown}. Chưa đối chiếu với runtime."
    hosted = sorted({p.where for p in smap.processes.values() if p.where in ("dev-host", "external-hosted", "manual", "undeployed")})
    return (
        "Không có topology runtime trong repo (không compose/k8s/Procfile nào khởi động process)"
        + (f"; chỉ có {', '.join(hosted)}." if hosted else ".")
    )


def variants_line(smap: SystemMap) -> str:
    names = list(smap.variants)
    profiles = sorted({p for proc in smap.processes.values() for p in proc.profiles})
    parts = []
    if names:
        parts.append("variant: " + ", ".join(f"{n} ({', '.join(files)})" for n, files in smap.variants.items()))
    if profiles:
        parts.append("profile: " + ", ".join(profiles))
    return "; ".join(parts)


def in_variant(process: Process, variant: str | None) -> bool:
    if variant is None or process.is_boundary:
        return True
    if process.profiles and variant not in process.profiles:
        return False
    return process.variant in (BASE, variant) or variant in process.profiles


def select(smap: SystemMap, variant: str | None, process: str | None) -> tuple[list[Process], list[Channel]]:
    """Nodes and edges of one variant, optionally only those touching `process` (and its neighbours)."""
    if variant is not None and variant not in smap.variants and not any(variant in p.profiles for p in smap.processes.values()):
        known = list(smap.variants) + sorted({x for p in smap.processes.values() for x in p.profiles})
        raise InvalidInput(f"unknown variant {variant!r}; known: {', '.join(known) or 'none'}")
    nodes = {name: p for name, p in smap.processes.items() if in_variant(p, variant)}
    edges = [c for c in smap.channels if c.src in nodes and c.dst in nodes and (variant is None or c.variant in (BASE, variant))]
    if process is not None:
        name = find_process(smap, process)
        edges = [c for c in edges if name in (c.src, c.dst)]
        keep = {name} | {c.src for c in edges} | {c.dst for c in edges}
        nodes = {n: p for n, p in nodes.items() if n in keep}
    return list(nodes.values()), edges


def find_process(smap: SystemMap, name: str) -> str:
    if name in smap.processes:
        return name
    lowered = name.lower()
    for process in smap.processes.values():
        if lowered == process.name.lower() or lowered in {a.lower() for a in process.aliases}:
            return process.name
    matches = [p.name for p in smap.processes.values() if lowered in p.name.lower()]
    if len(matches) == 1:
        return matches[0]
    raise InvalidInput(f"no node named {name!r}; nodes: {', '.join(list(smap.processes)[:40])}")


# --- evidence ---------------------------------------------------------------------------------------------


def _cite_ev(cite: Cite, ev: Ev | None, kind: str, quote: str) -> str:
    if ev is None or not ev.path:
        return "-"
    return f"{cite(f'{ev.path}:L{ev.line} ({kind})', quote)} {ev.at}"


def _quote(ev: Ev | None) -> str:
    return f"{ev.at}: {ev.text}" if ev and ev.path else ""


def channel_quote(channel: Channel) -> str:
    parts = [f"{channel.src} → {channel.dst} [{channel.kind}] {channel.detail} ({channel.address})"]
    if channel.client_ev:
        parts.append(f"client {_quote(channel.client_ev)}")
    if channel.server_ev:
        parts.append(f"server {_quote(channel.server_ev)}")
    if channel.reason:
        parts.append(f"lý do: {channel.reason}")
    return " | ".join(parts)


def cite_channel(cite: Cite, channel: Channel) -> str:
    ev = channel.client_ev or channel.server_ev
    if ev is None or not ev.path:
        return ""
    return cite(f"{ev.path}:L{ev.line} (channel: {channel.kind})", channel_quote(channel))


# --- tables -----------------------------------------------------------------------------------------------


def _cell(text: object, width: int = MAX_CELL) -> str:
    value = " ".join(str(text if text not in (None, "") else "-").split()).replace("|", "\\|")
    return value if len(value) <= width else value[: width - 1] + "…"


def _row(*cells: object) -> str:
    return "| " + " | ".join(_cell(c) for c in cells) + " |"


def _table(columns: Iterable[str], rows: list[str], limit: int, more: str) -> list[str]:
    columns = list(columns)
    out = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns), *rows[:limit]]
    if len(rows) > limit:
        out.append(f"... còn {len(rows) - limit} dòng; {more}")
    return out


def _where(process: Process) -> str:
    where = process.where
    if process.variant != BASE:
        where += f" ({process.variant})"
    if process.profiles:
        where += f" [profile {', '.join(process.profiles)}]"
    return where


def _process_rows(processes: list[Process], cite: Cite) -> list[str]:
    rows = []
    order = {"manifest": 0, "external-hosted": 1, "dev-host": 2, "manual": 3, "undeployed": 4, "boundary": 5}
    for p in sorted(processes, key=lambda p: (order.get(p.where, 9), p.is_data, p.name)):
        if p.kind == "user":
            continue
        quote = f"{p.name} ({p.kind or 'unknown'}, {p.where}) — {_quote(p.ev)}" + (f" | lý do: {p.reason}" if p.reason else "")
        confidence = p.confidence + (f" ({p.reason})" if p.confidence == INFERRED and p.reason else "")
        rows.append(_row(p.name, p.kind or "unknown", _where(p), p.entrypoint or p.image or "-",
                         _cite_ev(cite, p.ev, "node", quote), confidence))  # fmt: skip
    return rows


def _channel_row(c: Channel, cite: Cite, detail: str | None = None) -> str:
    label = cite_channel(cite, c)
    client = f"{label} {c.client_ev.at}" if c.client_ev and c.client_ev.path else "-"
    server = (f"{label} {c.server_ev.at}" if not c.client_ev or not c.client_ev.path else c.server_ev.at) if c.server_ev and c.server_ev.path else "-"
    match = c.match + (f" ({c.reason})" if c.match == INFERRED and c.reason else "")
    addr = c.addr + (f" ({c.reason})" if c.addr == INFERRED and c.reason and c.match != INFERRED else "")
    where = detail or (f"{c.detail} ({c.address})" if c.address and c.address != c.detail else c.detail)
    return _row(f"{c.src} → {c.dst}", c.kind, where, client, server, c.sync, c.when, c.on_failure, match, addr)


_EDGE_COLUMNS = ("từ → đến", "kênh", "địa chỉ / topic / volume", "đầu client", "đầu server", "sync/async", "thời điểm",
                 "khi hỏng", "tin cậy khớp", "tin cậy địa chỉ")  # fmt: skip


def grouped(channels: list[Channel]) -> list[list[Channel]]:
    """Channels of the same (src, dst, kind) together, in first-seen order."""
    groups: dict[tuple[str, str, str], list[Channel]] = {}
    for channel in channels:
        groups.setdefault(channel.key, []).append(channel)
    return list(groups.values())


def _group_detail(group: list[Channel]) -> str:
    details = list(dict.fromkeys(c.detail for c in group))
    text = ", ".join(details[:3]) + (f" +{len(details) - 3}" if len(details) > 3 else "")
    addresses = list(dict.fromkeys(c.address for c in group if c.address and c.address != c.detail))
    return f"{text} ({addresses[0]})" if addresses else text


def _edge_rows(channels: list[Channel], cite: Cite, *, group: bool) -> list[str]:
    if not group:
        return [_channel_row(c, cite) for c in channels]
    rows = []
    for members in grouped(channels):
        first = members[0]
        merged = replace(
            first, match=weakest(*(c.match for c in members)), addr=weakest(*(c.addr for c in members)),
            on_failure="; ".join(dict.fromkeys(c.on_failure for c in members if c.on_failure)),
        )
        rows.append(_channel_row(merged, cite, _group_detail(members)))
    return rows


def _store_rows(smap: SystemMap, names: set[str], cite: Cite) -> list[str]:
    rows = []
    for store in smap.stores.values():
        if store.name not in names and not any(n in names for n in (*store.writers, *store.readers)):
            continue
        writers = ", ".join(f"{n} {ev.at}" for n, ev in store.writers.items()) or "-"
        readers = ", ".join(f"{n} {ev.at}" for n, ev in store.readers.items()) or "-"
        quote = f"store {store.name} ({store.kind}) — {_quote(store.ev)} | writer: {writers} | reader: {readers}"
        owner = store.schema_owner or (next(iter(store.writers)) + " (INFERRED: writer duy nhất)" if len(store.writers) == 1 else "-")
        rows.append(_row(store.name, store.kind, writers, readers, owner, _cite_ev(cite, store.ev, "store", quote),
                         "; ".join(store.notes)))  # fmt: skip
    return rows


def _gap_rows(smap: SystemMap, names: set[str] | None, cite: Cite) -> list[str]:
    rows = []
    for gap in smap.gaps:
        if names is not None and not any(n in gap.detail for n in names):
            continue
        where = _cite_ev(cite, gap.ev, "gap", f"{gap.rule}: {gap.detail} — {_quote(gap.ev)}") if gap.ev else ""
        rows.append(f"- {gap.rule}: {gap.detail}" + (f" — {where}" if where else ""))
    return rows


def _env_rows(smap: SystemMap, names: set[str], cite: Cite) -> list[str]:
    rows = []
    for b in smap.bindings:
        if b.process not in names:
            continue
        quote = f"{b.process}.{b.var} = {b.value!r} ({b.source}) — đọc {_quote(b.read_ev)} | đặt {_quote(b.value_ev)}"
        rows.append(_row(b.process, b.var, b.value if b.value is not None else "(không đặt)", b.source,
                         _cite_ev(cite, b.read_ev, "env", quote), _cite_ev(cite, b.value_ev, "env", quote),
                         f"{b.target} [{b.kind}]" if b.target else "-", b.confidence, b.note))  # fmt: skip
    return rows


# --- sections ---------------------------------------------------------------------------------------------


def render(
    smap: SystemMap, cite: Cite, *, section: str = "overview", variant: str | None = None, process: str | None = None,
    limit: int = 60,
) -> str:  # fmt: skip
    if section not in SECTIONS:
        raise InvalidInput(f"section must be one of {', '.join(SECTIONS)}")
    nodes, edges = select(smap, variant, process)
    names = {p.name for p in nodes}
    focus = f" (variant={variant})" if variant else ""
    focus += f" (process={find_process(smap, process)})" if process else ""
    more = "lọc bằng process=<tên> hoặc variant=<tên>, hoặc tăng limit"
    out = [header(smap)]

    if section == "overview":
        if line := variants_line(smap):
            out.append(line)
        out += [f"Ghi chú: {n}" for n in smap.notes]
        if smap.truncated:
            out.append("CẢNH BÁO: repo lớn, chỉ quét một phần code (xem Khoảng trống Q0).")
        out += ["", f"## 1. Node{focus}", *_table(("tên", "loại", "nơi chạy", "entrypoint", "bằng chứng", "tin cậy"),
                                                  _process_rows(nodes, cite), limit, more)]  # fmt: skip
        notes = [f"- {p.name}: {n}" for p in nodes for n in p.notes]
        if notes:
            out += ["", "Thuộc tính node (Q7/Q8):", *notes[:limit]]
        out += ["", f"## 2. Cạnh{focus} (gộp theo cặp; chi tiết từng route: section=channels)",
                *_table(_EDGE_COLUMNS, _edge_rows(edges, cite, group=True), limit, more)]  # fmt: skip
        out += ["", "## 3. Sở hữu dữ liệu",
                *_table(("store", "loại", "writer", "reader", "sở hữu", "bằng chứng", "ghi chú"), _store_rows(smap, names, cite), limit, more)]  # fmt: skip
        gaps = _gap_rows(smap, names if process else None, cite)
        out += ["", "## 4. Khoảng trống (Q9)"]
        out += gaps[:limit] or [f"Q9: 0 vi phạm / {smap.invariants_checked} bất biến đã kiểm."]
        if len(gaps) > limit:
            out.append(f"... còn {len(gaps) - limit} khoảng trống; section=gaps")
        unverified = sum(1 for _, _, ok in smap.hypotheses if not ok)
        if smap.hypotheses:
            out += ["", f"Giả thuyết từ README/docs: {len(smap.hypotheses)} claim, {unverified} chưa kiểm được (section=hypotheses)."]
        out += ["", "Tiếp theo: cạnh/node INFERRED và mỗi khoảng trống → đọc đúng file:dòng (github_read_file start_line/end_line) "
                    "trước khi khẳng định; section=channels|env|gaps|stores với process=<tên> để xem chi tiết."]  # fmt: skip
    elif section == "processes":
        out += ["", f"## Node{focus}", *_table(("tên", "loại", "nơi chạy", "entrypoint", "bằng chứng", "tin cậy"),
                                               _process_rows(nodes, cite), limit, more)]  # fmt: skip
        for p in nodes:
            details = [f"code: {p.code_dir}" if p.code_dir is not None else "", f"image: {p.image}" if p.image else "",
                       f"publish: {', '.join(p.published)}" if p.published else "",
                       f"depends_on: {', '.join(f'{d} ({c})' for d, c, _ in p.depends_on)}" if p.depends_on else "",
                       f"healthcheck: {p.healthcheck}" if p.healthcheck else "", *p.notes]  # fmt: skip
            details = [d for d in details if d]
            if details:
                out.append(f"- {p.name}: " + "; ".join(details))
    elif section == "channels":
        out += ["", f"## Cạnh{focus} ({len(edges)})", *_table(_EDGE_COLUMNS, _edge_rows(edges, cite, group=False), limit, more)]
    elif section == "stores":
        out += ["", "## Sở hữu dữ liệu", *_table(("store", "loại", "writer", "reader", "sở hữu", "bằng chứng", "ghi chú"),
                                                 _store_rows(smap, names, cite), limit, more)]  # fmt: skip
    elif section == "env":
        out += ["", f"## Chuỗi biến môi trường (Q3){focus}",
                *_table(("process", "biến", "giá trị", "nguồn", "code đọc", "nơi đặt", "→ đích", "tin cậy", "ghi chú"),
                        _env_rows(smap, names, cite), limit, more)]  # fmt: skip
    elif section == "gaps":
        gaps = _gap_rows(smap, names if process else None, cite)
        out += ["", f"## Khoảng trống (Q9){focus}: {len(gaps)} vi phạm / {smap.invariants_checked} bất biến đã kiểm"]
        out += gaps[:limit] or ["Q9: 0 vi phạm."]
        if len(gaps) > limit:
            out.append(f"... còn {len(gaps) - limit}; tăng limit hoặc lọc process=<tên>")
    else:
        out += ["", "## Giả thuyết từ README/docs (không phải nguồn sự thật)"]
        for claim, ev, verified in smap.hypotheses[:limit]:
            status = "có cạnh khớp" if verified else "CHƯA KIỂM ĐƯỢC"
            out.append(f"- [{status}] “{claim}” — {_cite_ev(cite, ev, 'doc claim', claim)}")
        if not smap.hypotheses:
            out.append("Không có claim nào trong README/docs nêu hai node của map.")
    return "\n".join(out)


# --- diagram plan -----------------------------------------------------------------------------------------

#: Map node kind -> diagram shape kind (research.diagram.KINDS).
SHAPE = {
    "user": "client", "web": "service", "http-server": "service", "gateway": "service", "unknown": "service",
    "worker": "worker", "agent": "worker", "batch-job": "worker", "scheduler": "scheduler",
    "db": "database", "vector-store": "database", "cache": "cache", "queue": "queue", "storage": "storage",
    "external": "external",
}  # fmt: skip


@dataclass(frozen=True, slots=True)
class PlanNode:
    id: str
    label: str
    kind: str
    group: str | None


@dataclass(frozen=True, slots=True)
class PlanEdge:
    step: str
    source: str
    target: str
    label: str
    evidence: tuple[str, ...]
    is_async: bool
    inferred: bool


def diagram_plan(
    smap: SystemMap, cite: Cite, *, variant: str | None = None, process: str | None = None, max_nodes: int = 30,
    max_edges: int = 50, max_label: int = 60,
) -> tuple[list[PlanNode], list[PlanEdge]]:  # fmt: skip
    """Nodes and numbered edges straight from the map: one edge per (src, dst, kind), steps in breadth-first
    order from the boundary, INFERRED edges suffixed "(?)"."""
    nodes, edges = select(smap, variant, process)
    groups = grouped(edges)
    used = {c.src for g in groups for c in g} | {c.dst for g in groups for c in g}
    kept = [p for p in nodes if p.name in used]
    if len(kept) > max_nodes:  # drop the least structural nodes first
        for drop in (lambda p: p.name.startswith("volume:"), lambda p: p.where == "manual", lambda p: p.kind == "external"):
            if len(kept) <= max_nodes:
                break
            kept = [p for p in kept if not drop(p)]
    if len(kept) > max_nodes:
        raise InvalidInput(f"the map has {len(kept)} connected nodes (max {max_nodes}); pass variant=<name> or process=<name>")
    ids = {p.name: f"p{i}" for i, p in enumerate(kept, 1)}
    groups = [g for g in groups if g[0].src in ids and g[0].dst in ids]
    if not groups:
        raise InvalidInput("the map has no edge to draw for this selection" + ("" if smap.has_runtime else " (no runtime topology in this repository)"))

    order = _bfs_order(kept, groups)
    groups.sort(key=lambda g: (order.get(g[0].src, len(order)), order.get(g[0].dst, len(order)), g[0].kind))
    groups = groups[:max_edges]
    plan_nodes = [PlanNode(ids[p.name], p.name[:max_label], SHAPE.get(p.kind, "service"), _group_of(p)) for p in kept
                  if any(p.name in (g[0].src, g[0].dst) for g in groups)]  # fmt: skip
    plan_edges = []
    for step, group in enumerate(groups, 1):
        first = group[0]
        inferred = weakest(*(c.match for c in group), *(c.addr for c in group)) == INFERRED
        suffix = " (?)" if inferred else ""
        label = f"{first.kind}: {_group_detail(group)}"
        label = label[: max_label - len(suffix) - 1] + "…" if len(label) + len(suffix) > max_label else label
        evidence = tuple(dict.fromkeys(e for e in (cite_channel(cite, c) for c in group[:3]) if e))
        plan_edges.append(PlanEdge(str(step), ids[first.src], ids[first.dst], label + suffix, evidence,
                                   first.sync in ("async", "push") or first.kind in ("queue", "file"), inferred))  # fmt: skip
    return plan_nodes, plan_edges


def _bfs_order(nodes: list[Process], groups: list[list[Channel]]) -> dict[str, int]:
    out: dict[str, list[str]] = {}
    incoming: set[str] = set()
    for group in groups:
        out.setdefault(group[0].src, []).append(group[0].dst)
        incoming.add(group[0].dst)
    roots = [p.name for p in nodes if p.kind == "user"] + [p.name for p in nodes if p.name not in incoming and p.kind != "user"]
    order: dict[str, int] = {}
    queue = list(roots)
    while queue:
        name = queue.pop(0)
        if name in order:
            continue
        order[name] = len(order)
        queue += [n for n in out.get(name, []) if n not in order]
    return order


def _group_of(process: Process) -> str | None:
    if process.is_boundary:
        return None
    if process.is_data:
        return "Dữ liệu"
    return {"manifest": "Hệ thống", "external-hosted": "Triển khai ngoài", "dev-host": "Máy dev", "manual": "Chạy tay"}.get(process.where)


__all__ = ["EXTRACTED", "SECTIONS", "Cite", "diagram_plan", "header", "render", "select"]
