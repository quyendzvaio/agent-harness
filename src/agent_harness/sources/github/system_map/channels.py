"""Q4: both ends of every channel must be found before it becomes an edge.

HTTP    client call (base URL from the Q3 chain + path)  <->  route (mount prefix + router prefix + path)
gRPC    `New<S>Client` / `<S>Stub`                        <->  `Register<S>Server` / servicer, same .proto service
Queue   producer(topic)                                  <->  consumer(topic), topic resolved like an address
Data    process reads the address variable              <->  data node declared in a manifest

One end without the other is a gap, never an edge. Q6/Q8 attributes of each edge are read at the call site.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

from agent_harness.sources.github.system_map.catalog import Catalog, Pattern
from agent_harness.sources.github.system_map.envchain import EnvIndex, values_for
from agent_harness.sources.github.system_map.model import (
    EXTRACTED,
    INFERRED,
    Channel,
    Ev,
    Route,
    Store,
    SystemMap,
    weakest,
)
from agent_harness.sources.github.system_map.repo import Repo, inside

_DATA = {"db", "cache", "queue", "storage", "vector-store"}
_PARAM = re.compile(r"\$\{[^}]*\}|\{[^}]*\}|:[A-Za-z_]\w*|<[^>]*>|\[[^\]]*\]")
_NEXT_ROUTE = re.compile(r"(?:^|/)app/(?P<route>(?:[^/]+/)*)route\.(?:ts|js|mjs)$")
_NEXT_PAGES_API = re.compile(r"(?:^|/)pages/api/(?P<route>.+)\.(?:ts|js|mjs)$")
_NEXT_METHOD = re.compile(r"export\s+(?:async\s+)?(?:function|const)\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b")
_JS_IMPORT = re.compile(r"""(?:import\s+(?P<a>[A-Za-z_$][\w$]*)\s+from|(?:const|let|var)\s+(?P<b>[A-Za-z_$][\w$]*)\s*=\s*require\()\s*\(?\s*["'](?P<src>\.[^"']+)["']""")
_PY_FUNC = re.compile(r"^(?P<indent>\s*)(?:async\s+)?def\s+(?P<name>\w+)\s*\(", re.M)
_HEALTH_SERVICES = {"Health", "ServerReflection"}
_DEFINITION = ("func ", "def ", "class ", "public ", "export function ")


@dataclass(slots=True)
class Call:
    process: str
    var: str | None
    method: str  # GET/POST/... or ANY
    path: str
    ev: Ev
    realtime: bool = False


def norm_path(path: str) -> str:
    path = path.split("?", 1)[0].split("#", 1)[0]
    path = _PARAM.sub("{}", path)
    path = re.sub(r"/+", "/", "/" + path.strip("/"))
    return path


def paths_match(client: str, server: str) -> bool:
    a, b = norm_path(client).split("/"), norm_path(server).split("/")
    return len(a) == len(b) and all(x == y or x == "{}" or y == "{}" for x, y in zip(a, b, strict=True))


def _method(name: str) -> str:
    name = name.lower()
    for verb in ("post", "put", "patch", "delete"):
        if verb in name:
            return verb.upper()
    if name in ("get", "head", "options"):
        return name.upper()
    return "ANY"


class ChannelMatcher:
    def __init__(
        self, repo: Repo, catalog: Catalog, smap: SystemMap, owned: dict[str, list[str]], indexes: dict[str, EnvIndex],
        env_example: dict[str, tuple[str, Ev]] | None = None,
    ) -> None:  # fmt: skip
        self.repo = repo
        self.c = catalog.c
        self.catalog = catalog
        self.smap = smap
        self.owned = owned
        self.indexes = indexes
        self.env_example = env_example or {}
        self.calls: dict[str, list[Call]] = {}
        self.matched_routes: set[int] = set()

    # --- driver ---------------------------------------------------------------------------------------

    def run(self) -> None:
        for name, paths in self.owned.items():
            self.smap.routes += self._routes(name, paths)
            self.calls[name] = self._calls(name, paths)
        self._address_channels()
        self._grpc()
        self._queues()
        self._user_edges()
        self._unmatched_calls()

    def add(self, channel: Channel) -> None:
        for existing in self.smap.channels:
            if (existing.src, existing.dst, existing.kind, existing.detail) == (channel.src, channel.dst, channel.kind, channel.detail):
                return
        self.smap.channels.append(channel)

    # --- HTTP server side -----------------------------------------------------------------------------

    def _routes(self, process: str, paths: list[str]) -> list[Route]:
        prefixes: dict[tuple[str, str], str] = {}
        router_auth: set[tuple[str, str]] = set()
        mounts: dict[tuple[str, str], str] = {}
        auth = self.c["auth_markers"]
        for path in paths:
            text = self.repo.files[path]
            for pattern in self._applicable("router_prefix", path):
                for match in pattern.regex.finditer(text):
                    args = match.group("args") or ""
                    prefix = pattern.prefix_arg.search(args) if pattern.prefix_arg else None
                    prefixes[(path, match.group("owner"))] = prefix.group("prefix") if prefix else ""
                    if auth.search(args):
                        router_auth.add((path, match.group("owner")))
        for path in paths:
            text = self.repo.files[path]
            for pattern in self._applicable("router_mount", path):
                for match in pattern.regex.finditer(text):
                    groups = match.groupdict()
                    prefix = groups.get("prefix")
                    if prefix is None and pattern.prefix_arg is not None:
                        found = pattern.prefix_arg.search(groups.get("args") or "")
                        prefix = found.group("prefix") if found else ""
                    for target in self._mounted_files(path, match.group("owner"), paths):
                        mounts.setdefault(target, prefix or "")
        routes: list[Route] = []
        for path in paths:
            text = self.repo.files[path]
            lines = self.repo.lines(path)
            for pattern in self._applicable("server_routes", path):
                for match in pattern.regex.finditer(text):
                    groups = match.groupdict()
                    owner = groups.get("owner") or ""
                    line = self.repo.line_of(path, match.start())
                    method = groups.get("method") or "ANY"
                    method = "WS" if method == "websocket" else "ANY" if method.lower() in ("route", "api_route", "all", "handle", "handlefunc") else method.upper()
                    prefix = mounts.get((path, owner), mounts.get((path, "*"), "")) + prefixes.get((path, owner), "")
                    body = "\n".join(lines[line - 1 : line + 40])
                    end = re.search(r"\n\s*@\w+\.\w+\(|\n(?:async\s+)?def\s+\w+.*\n(?:.*\n)*?\s*@", body)
                    window = body[: end.start()] if end else body
                    routes.append(Route(
                        process=process, method=method, path=norm_path(prefix + groups["path"]), ev=self.repo.ev(path, line),
                        realtime=method == "WS" or bool(self.c["realtime_server"].search(window)),
                        auth=(path, owner) in router_auth or bool(auth.search("\n".join(lines[line - 1 : line + 8]))),
                    ))  # fmt: skip
            for regex, prefix in ((_NEXT_ROUTE, "/"), (_NEXT_PAGES_API, "/api/")):
                found = regex.search(path)
                if found:
                    segments = [s for s in found.group("route").strip("/").split("/") if s and not (s.startswith("(") and s.endswith(")"))]
                    methods = _NEXT_METHOD.findall(text) or ["ANY"]
                    for method in methods:
                        routes.append(Route(process, method, norm_path(prefix + "/".join(segments)), self.repo.ev(path, 1), framework="next"))
        if self.smap.processes[process].entrypoint and "langgraph" in (self.smap.processes[process].entrypoint or ""):
            for route in ("/threads", "/threads/{}/runs", "/runs", "/assistants"):
                routes.append(Route(process, "ANY", route, self.smap.processes[process].ev, framework="langgraph"))
        return routes

    def _mounted_files(self, path: str, owner: str, paths: list[str]) -> list[tuple[str, str]]:
        """`include_router(auth.router)` in main.py -> [("app/routers/auth.py", "router")]."""
        module, _, attr = owner.rpartition(".")
        if module:
            stem = module.rsplit(".", 1)[-1]
            return [(p, attr) for p in paths if posixpath.splitext(posixpath.basename(p))[0] == stem]
        text = self.repo.files[path]
        for match in _JS_IMPORT.finditer(text):
            if (match.group("a") or match.group("b")) == owner:
                base = posixpath.normpath(posixpath.join(posixpath.dirname(path), match.group("src")))
                for ext in ("", ".js", ".ts", ".mjs", ".cjs", "/index.js", "/index.ts"):
                    if base + ext in self.repo.files:
                        return [(base + ext, "*")]
        return [(path, owner)]

    # --- HTTP client side -----------------------------------------------------------------------------

    def _calls(self, process: str, paths: list[str]) -> list[Call]:
        index = self.indexes[process]
        calls: list[Call] = []
        wrappers: dict[str, tuple[str | None, str]] = {}  # function -> (var, defining file)
        for path in paths:
            text = self.repo.files[path]
            realtime = bool(self.c["realtime_client"].search(text))
            bases: list[tuple[int, str | None]] = []
            for pattern in self._applicable("client_base", path):
                for match in pattern.regex.finditer(text):
                    bases.append((match.start(), index.var_of(match.group("ident"))))
            if bases:
                for pattern in self._applicable("client_relative_call", path):
                    for match in pattern.regex.finditer(text):
                        line = self.repo.line_of(path, match.start())
                        if self.repo.lines(path)[line - 1].lstrip().startswith("@"):
                            continue  # `@app.post("/x")` declares a route
                        before = [var for pos, var in bases if pos <= match.start()] or [bases[0][1]]
                        calls.append(Call(process, before[-1], _method(match.group("method")), match.group("path"),
                                          self.repo.ev(path, line), realtime))  # fmt: skip
                if path.endswith(".py"):
                    calls += self._python_wrapper_calls(process, path, bases[0][1], realtime)
            for pattern in self._applicable("client_composed_url", path):
                for match in pattern.regex.finditer(text):
                    var = index.var_of(match.group("ident"))
                    if var:
                        calls.append(Call(process, var, "ANY", match.group("path"),
                                          self.repo.ev(path, self.repo.line_of(path, match.start())), realtime))  # fmt: skip
            if path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")):
                for match in self.c["js_fetch_wrapper"].finditer(text):
                    function = self._enclosing_js_function(path, match.start())
                    if function:
                        wrappers[function] = (index.var_of(match.group("ident")), path)
        if wrappers:
            self._wrapper_chain(paths, wrappers)
            for path in paths:
                if not path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")):
                    continue
                text = self.repo.files[path]
                visible = {fn: info for fn, info in wrappers.items() if info[1] == path or posixpath.splitext(posixpath.basename(info[1]))[0] in text}
                if not visible:
                    continue
                realtime = bool(self.c["realtime_client"].search(text))
                for match in self.c["js_wrapper_call"].finditer(text):
                    if match.group("fn") in visible:
                        var = visible[match.group("fn")][0]
                        calls.append(Call(process, var, _method(match.group("fn")), match.group("path"),
                                          self.repo.ev(path, self.repo.line_of(path, match.start())), realtime))  # fmt: skip
        return calls

    def _python_wrapper_calls(self, process: str, path: str, var: str | None, realtime: bool) -> list[Call]:
        """`def api_post(client, path, ...): return await client.post(path, ...)` then `api_post(c, "/x", ...)`."""
        text = self.repo.files[path]
        wrappers: dict[str, str] = {}
        for match in _PY_FUNC.finditer(text):
            header = text[match.end() : text.find(")", match.end())]
            params = re.findall(r"\b(path|url|endpoint|route)\b", header)
            if not params:
                continue
            body = text[match.end() : match.end() + 1500]
            next_def = _PY_FUNC.search(text, match.end())
            if next_def:
                body = text[match.end() : next_def.start()]
            call = re.search(rf"\.(get|post|put|patch|delete|request|stream)\(\s*(?:\w+\s*,\s*)?({'|'.join(params)})\b", body)
            if call:
                wrappers[match.group("name")] = _method(call.group(1))
        calls: list[Call] = []
        if not wrappers:
            return calls
        for match in re.finditer(r"\b(?P<fn>" + "|".join(map(re.escape, wrappers)) + r")\((?P<args>[^)\n]*?)[\"'](?P<path>/[^\"']*)[\"']", text):
            line = self.repo.line_of(path, match.start())
            if self.repo.lines(path)[line - 1].lstrip().startswith(("def ", "async def ")):
                continue
            calls.append(Call(process, var, wrappers[match.group("fn")], match.group("path"), self.repo.ev(path, line), realtime))
        return calls

    def _wrapper_chain(self, paths: list[str], wrappers: dict[str, tuple[str | None, str]]) -> None:
        """`authed(path) { return request(path, ...) }` makes `authed` a wrapper too."""
        for _ in range(3):
            for path in {info[1] for info in wrappers.values()}:
                text = self.repo.files[path]
                for match in re.finditer(r"\b(?P<fn>[A-Za-z_$][\w$]*)\s*(?:<[^()]*?>)?\(\s*(?P<arg>[A-Za-z_$][\w$]*)\s*[,)]", text):
                    if match.group("fn") in wrappers:
                        outer = self._enclosing_js_function(path, match.start())
                        if outer and outer not in wrappers:
                            wrappers[outer] = (wrappers[match.group("fn")][0], path)

    def _enclosing_js_function(self, path: str, pos: int) -> str | None:
        best = None
        for match in self.c["js_function_def"].finditer(self.repo.files[path]):
            if match.start() > pos:
                break
            best = match.group("a") or match.group("b")
        return best

    # --- channels from the Q3 chain ---------------------------------------------------------------------

    def _address_channels(self) -> None:
        routes_by_process: dict[str, list[Route]] = {}
        for route in self.smap.routes:
            routes_by_process.setdefault(route.process, []).append(route)
        for binding in self.smap.bindings:
            if binding.target is None:
                if binding.value and binding.kind and binding.note and "không khớp" in binding.note:
                    self.smap.gap("Q3", f"{binding.process}.{binding.var}={binding.value}: {binding.note}", binding.value_ev)
                continue
            target = self.smap.processes[binding.target]
            client_ev = binding.read_ev or binding.value_ev
            if target.name == binding.process:
                continue
            if target.kind in _DATA or target.kind == "external" or binding.kind in ("db", "cache", "queue", "storage", "mail", "vector-store"):
                self.add(Channel(
                    src=binding.process, dst=target.name, kind=binding.kind if binding.kind != "http" or target.kind != "external" else "http",
                    address=f"{binding.var}={binding.value}", detail=binding.var, client_ev=client_ev,
                    server_ev=target.ev if target.where != "boundary" else None,
                    match=EXTRACTED if binding.read_ev else INFERRED, addr=binding.confidence,
                    sync="async" if binding.kind == "queue" else "sync", variant=binding.variant,
                    reason=binding.note if binding.confidence == INFERRED or not binding.read_ev else "", call_site=False,
                ))  # fmt: skip
                continue
            if binding.kind == "grpc" and not binding.via_command:
                continue  # matched through .proto services in _grpc()
            calls = [c for c in self.calls.get(binding.process, []) if c.var == binding.var]
            if not calls and binding.via_command:
                self.add(Channel(
                    src=binding.process, dst=target.name, kind=binding.kind, address=f"{binding.var}={binding.value}",
                    detail=binding.var, client_ev=binding.read_ev, server_ev=target.ev, match=INFERRED, addr=binding.confidence,
                    variant=binding.variant, reason="địa chỉ truyền qua command của manifest; route chưa khớp ở code", call_site=False,
                ))  # fmt: skip
                continue
            if not calls:
                self.smap.gap("Q4", f"{binding.process} có địa chỉ {binding.var}={binding.value} trỏ tới {target.name} "
                                    f"nhưng không thấy lời gọi nào dùng nó (một đầu)", client_ev)  # fmt: skip
                continue
            for call in calls:
                route = self._match(call, routes_by_process.get(target.name, []))
                if route is None:
                    continue
                self.matched_routes.add(id(route))
                kind = "realtime" if route.realtime or (call.realtime and route.method in ("GET", "WS", "ANY") and "stream" in route.path) else "http"
                self.add(Channel(
                    src=binding.process, dst=target.name, kind=kind, address=f"{binding.var}={binding.value}",
                    detail=f"{route.method} {route.path}", client_ev=call.ev, server_ev=route.ev, match=EXTRACTED,
                    addr=binding.confidence, sync="push" if kind == "realtime" else "sync", variant=binding.variant,
                    reason=binding.note if binding.confidence == INFERRED else "",
                ))  # fmt: skip

    def _match(self, call: Call, routes: list[Route]) -> Route | None:
        candidates = [r for r in routes if paths_match(call.path, r.path)]
        exact = [r for r in candidates if call.method in ("ANY", r.method) or r.method in ("ANY", "WS")]
        return (exact or [None])[0]

    def _unmatched_calls(self) -> None:
        targets = {(b.process, b.var): b.target for b in self.smap.bindings if b.target}
        routes_by_process: dict[str, list[Route]] = {}
        for route in self.smap.routes:
            routes_by_process.setdefault(route.process, []).append(route)
        seen: set[tuple[str, str]] = set()
        for process, calls in self.calls.items():
            for call in calls:
                target = targets.get((process, call.var or ""))
                if not target or target == process or self.smap.processes[target].kind in _DATA | {"external"}:
                    continue
                routes = routes_by_process.get(target, [])
                if routes and self._match(call, routes) is None and (call.path, target) not in seen:
                    seen.add((call.path, target))
                    self.smap.gap("Q9-I3", f"{process} gọi {call.method} {norm_path(call.path)} trên {target} nhưng {target} "
                                           f"không có route khớp (đường chết hoặc route đã đổi tên)", call.ev)  # fmt: skip
                elif not routes and (process, target) not in seen:
                    seen.add((process, target))
                    self.smap.gap("Q4", f"không tìm thấy route nào của {target} để khớp lời gọi từ {process} "
                                        f"(framework chưa có trong catalog?)", call.ev)  # fmt: skip

    # --- gRPC ---------------------------------------------------------------------------------------------

    def _grpc(self) -> None:
        services: dict[str, Ev] = {}
        for path in self.repo.paths:
            if path.endswith(".proto"):
                for match in self.c["proto_service"].finditer(self.repo.files[path]):
                    services.setdefault(match.group("service"), self.repo.ev(path, self.repo.line_of(path, match.start())))
        if not services:
            return
        servers: dict[str, list[tuple[str, Ev]]] = {}
        clients: list[tuple[str, str, Ev]] = []
        for process, paths in self.owned.items():
            for path in paths:
                text = self.repo.files[path]
                for key in ("grpc_servers", "grpc_clients"):
                    for pattern in self._applicable(key, path):
                        for match in pattern.regex.finditer(text):
                            service = _proto_name(match.group("service"), services)
                            if service is None or service in _HEALTH_SERVICES:
                                continue
                            ev = self.repo.ev(path, self.repo.line_of(path, match.start()))
                            if ev.text.startswith(_DEFINITION):
                                continue  # the stub's own definition, not a use
                            if key == "grpc_servers":
                                servers.setdefault(service, []).append((process, ev))
                            else:
                                clients.append((process, service, ev))
        bindings = {(b.process, b.target): b for b in self.smap.bindings if b.target}
        for process, service, ev in clients:
            for server, server_ev in servers.get(service, []):
                if server == process:
                    continue
                binding = bindings.get((process, server))
                self.add(Channel(
                    src=process, dst=server, kind="grpc", address=f"{binding.var}={binding.value}" if binding else "(chưa theo được)",
                    detail=f"rpc {service}", client_ev=ev, server_ev=server_ev, match=EXTRACTED,
                    addr=binding.confidence if binding else INFERRED, variant=binding.variant if binding else "base",
                    reason="" if binding else f"khớp qua .proto service {service}; chưa thấy biến địa chỉ trỏ tới {server}",
                ))  # fmt: skip
            if not servers.get(service):
                self.smap.gap("Q4", f"{process} gọi gRPC {service} nhưng không process nào đăng ký server cho nó", ev)

    # --- queues -----------------------------------------------------------------------------------------

    def _queues(self) -> None:
        producers: list[tuple[str, str, Ev, str]] = []  # process, topic, ev, confidence
        consumers: list[tuple[str, str, Ev, str]] = []
        for process, paths in self.owned.items():
            for path in paths:
                text = self.repo.files[path]
                for key, sink in (("queue_producers", producers), ("queue_consumers", consumers)):
                    for pattern in self._applicable(key, path):
                        if pattern.requires is not None and not pattern.requires.search(text):
                            continue
                        for match in pattern.regex.finditer(text):
                            topic, confidence = self._topic(process, match.group("topic"))
                            if topic:
                                sink.append((process, topic, self.repo.ev(path, self.repo.line_of(path, match.start())), confidence))
        if not producers and not consumers:
            return
        brokers = {b.process: b for b in self.smap.bindings if b.kind == "queue" and b.target}
        consumed = {t for _, t, _, _ in consumers}
        produced = {t for _, t, _, _ in producers}
        for role, entries, others in (("publish", producers, consumed), ("consume", consumers, produced)):
            for process, topic, ev, confidence in entries:
                store = self.smap.stores.setdefault(f"topic:{topic}", Store(name=f"topic:{topic}", kind="queue", ev=ev))
                (store.writers if role == "publish" else store.readers).setdefault(process, ev)
                broker = brokers.get(process)
                if broker is not None:
                    src, dst = (process, broker.target) if role == "publish" else (broker.target, process)
                    self.smap.channels[:] = [c for c in self.smap.channels if not (c.src == process and c.dst == broker.target and c.detail == broker.var)]
                    self.add(Channel(
                        src=src, dst=dst, kind="queue", address=f"{broker.var}={broker.value}", detail=f"{role} {topic}",
                        client_ev=ev, server_ev=self.smap.processes[broker.target].ev,
                        match=EXTRACTED if topic in others else INFERRED, addr=weakest(broker.confidence, confidence),  # type: ignore[arg-type]
                        sync="async", variant=broker.variant,
                        reason="" if topic in others else f"không thấy đầu {'consumer' if role == 'publish' else 'producer'} cho topic {topic}",
                    ))  # fmt: skip
        if not brokers:
            for process, topic, ev, confidence in producers:
                for consumer, other, consumer_ev, other_confidence in consumers:
                    if other == topic and consumer != process:
                        self.add(Channel(process, consumer, "queue", f"topic {topic}", f"topic {topic}", ev, consumer_ev,
                                         EXTRACTED, weakest(confidence, other_confidence), sync="async",  # type: ignore[arg-type]
                                         reason="chưa theo được địa chỉ broker"))  # fmt: skip

    def _topic(self, process: str, expression: str) -> tuple[str | None, str]:
        expression = expression.strip().rstrip(",")
        literal = re.fullmatch(r"""[rbf]?["'`]([^"'`$]+)["'`]""", expression)
        if literal:
            return literal.group(1), EXTRACTED
        index = self.indexes[process]
        var = index.var_of(expression)
        if var:
            values = values_for(self.smap.processes[process], var, index, self.env_example)
            if values and values[0].value:
                return values[0].value, values[0].confidence
            return f"${var}", INFERRED
        return None, INFERRED

    # --- the user boundary ------------------------------------------------------------------------------

    def _user_edges(self) -> None:
        for process in self.smap.processes.values():
            if not process.published or process.is_boundary:
                continue
            kind = process.kind if process.kind in _DATA else "http"
            self.add(Channel(
                src="user", dst=process.name, kind=kind, address=", ".join(process.published), detail="ports/ingress",
                client_ev=None, server_ev=process.ev, match=EXTRACTED, addr=EXTRACTED, variant=process.variant,
                reason="cổng publish ra ngoài" if kind not in _DATA else "kho dữ liệu publish cổng ra ngoài",
            ))  # fmt: skip

    # --- Q6 / Q8 at the call sites -------------------------------------------------------------------------

    def reliability(self) -> None:
        startup = self.c["startup_hooks"]
        for channel in self.smap.channels:
            ev = channel.client_ev
            if not channel.call_site or ev is None or ev.path not in self.repo.files or ev.path.endswith((".yml", ".yaml")) or "Dockerfile" in ev.path:
                continue
            lines = self.repo.lines(ev.path)
            index = ev.line - 1
            notes: list[str] = []
            failure = _failure_mode(lines, index)
            if failure:
                notes.append(failure)
            window = "\n".join(lines[max(0, index - 30) : index + 3])
            timeout = None
            for found in self.c["timeout_arg"].finditer(window):
                timeout = found.group("value")
            if timeout:
                notes.append(f"timeout={timeout}")
            if self.c["retry_markers"].search(self.repo.files[ev.path]):
                notes.append("có retry/backoff trong file")
            channel.on_failure = "; ".join(notes) or "không try/except, không timeout tại điểm gọi"
            function = _python_function(lines, index)
            if function and (function in ("startup", "lifespan", "on_startup") or startup.search("\n".join(lines[max(0, index - 60) : index]))
                             and function in ("lifespan", "startup")):  # fmt: skip
                channel.when = "boot-time"
            if channel.kind in ("http", "grpc") and re.search(r"create_task\(|BackgroundTasks|add_task\(|\.delay\(", "\n".join(lines[max(0, index - 3) : index + 2])):
                channel.sync = "async"
            if self.smap.processes.get(channel.src) and self.smap.processes[channel.src].kind == "batch-job":
                channel.when = "build-time"

    # --- helpers -------------------------------------------------------------------------------------------

    def _applicable(self, key: str, path: str) -> list[Pattern]:
        return [p for p in self.c.get(key, ()) if p.applies(path)]


def _proto_name(raw: str, services: dict[str, Ev]) -> str | None:
    for candidate in (raw, raw.removesuffix("Servicer"), raw.removesuffix("Impl"), raw + "Service", raw.removesuffix("Grpc")):
        if candidate in services:
            return candidate
    return None


def _python_function(lines: list[str], index: int) -> str | None:
    for i in range(index, -1, -1):
        match = re.match(r"\s*(?:async\s+)?def\s+(\w+)\s*\(", lines[i])
        if match:
            return match.group(1)
    return None


def _failure_mode(lines: list[str], index: int) -> str | None:
    """Q8: a `try:` above the call at a lower indent, and what its `except` does."""
    if index >= len(lines):
        return None
    call_indent = len(lines[index]) - len(lines[index].lstrip())
    for i in range(index - 1, max(-1, index - 25), -1):
        stripped = lines[i].strip()
        indent = len(lines[i]) - len(lines[i].lstrip())
        if stripped.startswith(("try:", "try {")) and indent < call_indent:
            for j in range(index + 1, min(len(lines), index + 40)):
                handler = lines[j].strip()
                if (handler.startswith("except") or handler.startswith("} catch") or handler.startswith("catch")) and (
                    len(lines[j]) - len(lines[j].lstrip()) == indent or handler.startswith("}")
                ):
                    body = []
                    for k in range(j + 1, min(len(lines), j + 12)):
                        if lines[k].strip() and len(lines[k]) - len(lines[k].lstrip()) <= indent and not lines[k].strip().startswith(("}", ")")):
                            break
                        body.append(lines[k].strip())
                    text = " ".join(body)
                    if re.search(r"\braise\b|\bthrow\b|HTTPException\(\s*(status_code\s*=\s*)?5", text):
                        return f"fail-closed (except → raise) ← L{j + 1}"
                    return f"fail-open (except nuốt lỗi: `{text[:60]}`) ← L{j + 1}"
            return None
        if stripped.startswith(("def ", "async def ", "function ")) and indent < call_indent:
            return None
    return None


def files_under(paths: list[str], directory: str) -> list[str]:
    return [p for p in paths if inside(p, directory)]
