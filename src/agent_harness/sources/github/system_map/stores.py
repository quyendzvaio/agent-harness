"""Q5 (who writes and who reads each store) plus the per-process Q7/Q8 facts read from code.

A store's writer/reader comes from statements in the process's own code (`session.add`, `INSERT`,
`put_object`, `open(..., "w")` / `SELECT`, `get_object`, ...), never from the connection alone. Scripts
outside every process that write to a store are batch-jobs (run manually), not tooling.
"""

from __future__ import annotations

import posixpath
import re

from agent_harness.sources.github.system_map.catalog import Catalog
from agent_harness.sources.github.system_map.envchain import EnvIndex, build_bindings, scan_env
from agent_harness.sources.github.system_map.model import (
    EXTRACTED,
    INFERRED,
    Channel,
    Ev,
    Process,
    Store,
    SystemMap,
)
from agent_harness.sources.github.system_map.repo import TEST_DIRS, Repo, inside

_DATA = ("db", "cache", "storage", "vector-store")
_CACHE_WRITE = re.compile(r"\.(?:String|Hash|List|Key)?(set|setex|hset|hmset|lpush|rpush|sadd|zadd|xadd|incr|expire|publish|delete)(?:Async)?\(", re.I)
_EXEC_INTO = re.compile(r"\b(psql|mysql|mongosh|mongo|redis-cli|sqlite3|docker(?:[- ]compose)?\s+(?:compose\s+)?exec)\b")
_CACHE_READ = re.compile(r"\.(?:String|Hash|List|Key)?(get|hget|hgetall|getall|lrange|range|smembers|zrange|xread|mget|exists|subscribe)(?:Async)?\(", re.I)


def assign_stores(
    repo: Repo, catalog: Catalog, smap: SystemMap, owned: dict[str, list[str]], orphans: list[str],
    indexes: dict[str, EnvIndex], env_example: dict[str, tuple[str, Ev]],
) -> None:  # fmt: skip
    c = catalog.c
    for process in list(smap.processes.values()):
        if process.kind in _DATA:  # queue brokers are represented by their topics (channels.py)
            smap.stores.setdefault(process.name, Store(process.name, process.kind, process.ev))
    _batch_jobs(repo, catalog, smap, orphans, owned, indexes, env_example)
    for channel in smap.channels:
        target = smap.processes.get(channel.dst)
        if target is None or target.kind not in _DATA or channel.src == "user":
            continue
        store = smap.stores[target.name]
        paths = owned.get(channel.src, [])
        if not paths and channel.client_ev:
            paths = [channel.client_ev.path]
        write_re, read_re = (_CACHE_WRITE, _CACHE_READ) if target.kind == "cache" else (c["db_writes"], c["db_reads"])
        writer = _first_hit(repo, paths, write_re, cache=target.kind == "cache")
        reader = _first_hit(repo, paths, read_re, cache=target.kind == "cache")
        if writer:
            store.writers.setdefault(channel.src, writer)
        if reader:
            store.readers.setdefault(channel.src, reader)
        if not writer and not reader:
            store.notes.append(f"{channel.src} kết nối nhưng không thấy câu lệnh ghi/đọc trong code")
        if any(any(marker in p for p in paths) for marker in c["schema_markers"]) and store.schema_owner is None:
            store.schema_owner = channel.src
    _volumes(repo, catalog, smap, owned)
    for store in smap.stores.values():
        if len(store.writers) > 1:
            smap.gap("Q5", f"{store.name} có {len(store.writers)} writer ({', '.join(sorted(store.writers))}) → shared mutable state")
    _process_signals(repo, catalog, smap, owned)


def _first_hit(repo: Repo, paths: list[str], regex: re.Pattern[str], *, cache: bool = False) -> Ev | None:
    for path in paths:
        text = repo.files.get(path, "")
        if cache and "redis" not in text.lower():
            continue
        match = regex.search(text)
        if match:
            return repo.ev(path, repo.line_of(path, match.start()))
    return None


def _batch_jobs(
    repo: Repo, catalog: Catalog, smap: SystemMap, orphans: list[str], owned: dict[str, list[str]],
    indexes: dict[str, EnvIndex], env_example: dict[str, tuple[str, Ev]],
) -> None:  # fmt: skip
    writes = catalog.c["db_writes"]
    for path in orphans:
        if TEST_DIRS.search(path):
            continue
        text = repo.files[path]
        match = next((m for m in writes.finditer(text) if not m.group(0).startswith("open(")), None)
        if not match:
            continue  # tooling: not started by a manifest and touches no store (writing a local file is not a store)
        name = f"script:{path}"
        smap.processes[name] = Process(
            name=name, kind="batch-job", where="manual", ev=repo.ev(path, repo.line_of(path, match.start())), confidence=INFERRED,
            code_dir=None, entrypoint=f"python {path}" if path.endswith(".py") else path,
            reason="script không manifest nào gọi nhưng có câu lệnh ghi dữ liệu",
        )  # fmt: skip
        index = scan_env(repo, catalog, [path])
        indexes[name] = index
        owned[name] = [path]
        before = len(smap.bindings)
        build_bindings(smap, repo, {name: index}, env_example)
        targets = [b for b in smap.bindings[before:] if b.target and smap.processes[b.target].kind in _DATA]
        for binding in targets:
            smap.channels.append(Channel(
                src=name, dst=binding.target or "", kind=binding.kind, address=f"{binding.var}={binding.value}", detail=binding.var,
                client_ev=binding.read_ev or binding.value_ev, server_ev=smap.processes[binding.target or ""].ev,
                match=EXTRACTED if binding.read_ev else INFERRED, addr=binding.confidence, when="build-time", reason=binding.note,
            ))  # fmt: skip
        if not targets:
            exec_hit = _EXEC_INTO.search(text)
            data = {alias: p for p in smap.processes.values() if p.kind in _DATA for alias in {p.name, *p.aliases}}
            hit = next(((alias, m) for m in [exec_hit] if m for alias in data if re.search(rf"\b{re.escape(alias)}\b", text[max(0, m.start() - 200) : m.end() + 200])), None)
            if hit:
                alias, found = hit
                target = data[alias]
                smap.channels.append(Channel(
                    src=name, dst=target.name, kind=target.kind, address=f"`{found.group(0).strip()}`", detail=found.group(1),
                    client_ev=repo.ev(path, repo.line_of(path, found.start())), server_ev=target.ev, match=INFERRED, addr=INFERRED,
                    when="build-time", call_site=False, reason=f"script chạy `{found.group(1)}` vào {target.name} (suy từ lệnh, không qua biến địa chỉ)",
                ))  # fmt: skip
            else:
                smap.gap("Q5", f"{path} ghi dữ liệu nhưng không xác định được store nào (một đầu)", smap.processes[name].ev)


def _volumes(repo: Repo, catalog: Catalog, smap: SystemMap, owned: dict[str, list[str]]) -> None:
    for process in list(smap.processes.values()):
        for source, target, read_only, ev in process.volumes:
            if process.kind in _DATA or process.kind == "queue":
                store = smap.stores.get(process.name)
                if store is not None:
                    store.notes.append(f"dữ liệu lưu trên volume `{source or target}` ← {ev.at}")
                continue
            if not source or target.endswith(".sock") or source.endswith(".sock"):
                continue  # anonymous volume or the Docker socket
            if source.startswith(".") and posixpath.normpath(source).lstrip("./") in repo.dirs | {""}:
                continue  # the repository's own code mounted for hot reload: not data
            name = f"volume:{source}"
            node = smap.processes.setdefault(name, Process(name=name, kind="storage", where="manifest", ev=ev, variant=process.variant,
                                                           reason=f"mount vào {process.name}:{target}"))  # fmt: skip
            store = smap.stores.setdefault(name, Store(name, "storage", ev))
            if read_only:
                store.readers.setdefault(process.name, ev)
                smap.channels.append(Channel(node.name, process.name, "file", f"{source}:{target}:ro", f"mount {target} (ro)",
                                             ev, ev, EXTRACTED, EXTRACTED, sync="async", variant=process.variant))  # fmt: skip
                continue
            writer = None
            for path in owned.get(process.name, []):
                text = repo.files[path]
                if target in text and catalog.c["db_writes"].search(text):
                    writer = repo.ev(path, repo.find_line(path, target))
                    break
            if writer:
                store.writers.setdefault(process.name, writer)
            else:
                store.notes.append(f"{process.name} mount rw {target} nhưng chưa thấy code ghi vào đó (writer tiềm năng)")
                store.readers.setdefault(process.name, ev)
            smap.channels.append(Channel(process.name, node.name, "file", f"{source}:{target}", f"mount {target} (rw)",
                                         writer or ev, ev, EXTRACTED if writer else INFERRED, EXTRACTED, sync="async",
                                         variant=process.variant, reason="" if writer else "mount rw, chưa xác nhận ghi bằng code"))  # fmt: skip


def _process_signals(repo: Repo, catalog: Catalog, smap: SystemMap, owned: dict[str, list[str]]) -> None:
    """Q7 (stateful, startup) and Q8 (public routes, secrets) facts that belong to a process, not an edge."""
    c = catalog.c
    routes_by_process: dict[str, list] = {}
    for route in smap.routes:
        routes_by_process.setdefault(route.process, []).append(route)
    for process in smap.processes.values():
        paths = owned.get(process.name, [])
        for path in paths:
            if not path.endswith(".py"):
                continue
            text = repo.files[path]
            globals_ = re.findall(r"^\s*global\s+(\w+)", text, re.M)
            if globals_:
                line = repo.find_line(path, f"global {globals_[0]}")
                process.notes.append(f"stateful: biến module-level `{globals_[0]}` giữ trạng thái (global) ← {path}:L{line} → không scale ngang tự do")
                break
        if process.published:
            open_routes = [r for r in routes_by_process.get(process.name, []) if not r.auth and not c["ops_paths"].match(r.path)]
            if open_routes:
                sample = ", ".join(f"{r.method} {r.path}" for r in open_routes[:6])
                process.notes.append(f"{len(open_routes)} route publish ra ngoài không thấy auth dependency: {sample}"
                                     f"{' ...' if len(open_routes) > 6 else ''}")  # fmt: skip
        secrets = sorted(var for var, values in process.env.items()
                         if c["secret_names"].search(var) and any(v.source.startswith(("compose", "k8s", "build-arg")) for v in values))  # fmt: skip
        if secrets:
            process.notes.append(f"secret nhận qua environment: {', '.join(secrets[:8])}{' ...' if len(secrets) > 8 else ''}")
        hooks = [p for p in paths if c["startup_hooks"].search(repo.files[p])]
        if hooks:
            process.notes.append(f"có hook khởi động ← {hooks[0]}:L{repo.find_line(hooks[0], 'startup') if 'startup' in repo.files[hooks[0]] else repo.find_line(hooks[0], 'lifespan')}")
        for dep, condition, ev in process.depends_on:
            if condition != "service_healthy":
                process.notes.append(f"depends_on {dep} không chờ healthy (chỉ thứ tự start) ← {ev.at}")


def code_dir_files(repo: Repo, directory: str) -> list[str]:
    return [p for p in repo.paths if inside(p, directory)]
