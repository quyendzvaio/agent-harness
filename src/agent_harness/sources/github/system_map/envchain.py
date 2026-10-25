"""Q3: where each process connects, through the three-link chain

    code reads variable X  ->  a manifest sets X for that process  ->  the host in the value resolves

Every link carries its own `file:line`; the weakest link decides the confidence of the address.
"""

from __future__ import annotations

import ast
import ipaddress
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from agent_harness.sources.github.system_map.catalog import Catalog
from agent_harness.sources.github.system_map.model import (
    EXTRACTED,
    INFERRED,
    Binding,
    Confidence,
    EnvValue,
    Ev,
    Process,
    SystemMap,
    weakest,
)
from agent_harness.sources.github.system_map.repo import Repo

LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}
_HOST_PORT = re.compile(r"^[A-Za-z0-9_.-]+:\d{1,5}(,[A-Za-z0-9_.-]+:\d{1,5})*$")
_HOSTNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_ADDRESS_VAR = re.compile(r"(_HOST|_HOSTNAME|_ADDR|_ADDRESS|_URL|_URI|_DSN|_ENDPOINT|_SERVER|_SERVERS|_BROKERS?|_BASE)$", re.I)
_SCHEME_KIND = [
    (re.compile(r"^(postgres|postgresql|mysql|mariadb|mongodb|sqlite|mssql|sqlserver|cockroachdb|clickhouse)"), "db"),
    (re.compile(r"^rediss?"), "cache"),
    (re.compile(r"^(amqps?|kafka|nats|pulsar)"), "queue"),
    (re.compile(r"^(s3|gs|minio)"), "storage"),
    (re.compile(r"^(smtps?|imaps?|pop3s?)"), "mail"),
    (re.compile(r"^wss?"), "realtime"),
    (re.compile(r"^grpcs?"), "grpc"),
    (re.compile(r"^https?"), "http"),
]
_VAR_KIND = [
    (re.compile(r"SMTP|IMAP|MAIL", re.I), "mail"),
    (re.compile(r"REDIS|MEMCACHE", re.I), "cache"),
    (re.compile(r"KAFKA|BOOTSTRAP|RABBIT|AMQP|NATS|BROKER", re.I), "queue"),
    (re.compile(r"DATABASE|POSTGRES|MYSQL|MONGO|^DB_|_DB_", re.I), "db"),
    (re.compile(r"S3|MINIO|BUCKET", re.I), "storage"),
    (re.compile(r"QDRANT|ELASTIC|OPENSEARCH|WEAVIATE|MILVUS|CHROMA", re.I), "vector-store"),
]
_DATA_KINDS = {"db", "cache", "queue", "storage", "vector-store"}


@dataclass(slots=True)
class Read:
    var: str
    default: str | None
    ev: Ev
    name: str | None = None  # identifier the value is assigned to


@dataclass(slots=True)
class EnvIndex:
    """What one process's code reads from the environment, and the identifiers holding those values."""

    reads: dict[str, list[Read]] = field(default_factory=dict)
    idents: dict[str, str] = field(default_factory=dict)  # identifier -> variable
    settings: dict[str, str] = field(default_factory=dict)  # settings attribute -> variable

    def var_of(self, expression: str) -> str | None:
        """`API_BASE_URL`, `settings.kafka_topic`, `get_settings().x`, `self.cfg.x` -> variable name."""
        expression = expression.strip().strip("()")
        if expression in self.idents:
            return self.idents[expression]
        last = expression.rsplit(".", 1)[-1]
        if "." in expression and last in self.settings:
            return self.settings[last]
        return self.idents.get(last)

    def add(self, read: Read) -> None:
        self.reads.setdefault(read.var, []).append(read)
        if read.name:
            self.idents.setdefault(read.name, read.var)


def scan_env(repo: Repo, catalog: Catalog, paths: list[str]) -> EnvIndex:
    index = EnvIndex()
    for path in paths:
        text = repo.files[path]
        for pattern in catalog.env_readers:
            if not pattern.applies(path):
                continue
            for match in pattern.regex.finditer(text):
                groups = match.groupdict()
                var = groups.get("var") or groups.get("var2")
                if not var:
                    continue
                default = _literal(groups.get("default"))
                name = groups.get("name")
                index.add(Read(var, default, repo.ev(path, repo.line_of(path, match.start())), name))
        if path.endswith(".py") and "BaseSettings" in text:
            _settings_fields(repo, path, index)
    _propagate(repo, paths, index)
    return index


def _literal(text: str | None) -> str | None:
    if not text:
        return None
    text = text.strip()
    if text[:1] in "\"'`" and text[-1:] == text[:1]:
        return text[1:-1]
    return text


def _settings_fields(repo: Repo, path: str, index: EnvIndex) -> None:
    try:
        tree = ast.parse(repo.files[path])
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or not any(_name(b).endswith("BaseSettings") for b in node.bases):
            continue
        prefix = _env_prefix(node)
        for stmt in node.body:
            if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)):
                continue
            attr = stmt.target.id
            if attr == "model_config":
                continue
            default, alias = _field_default(stmt.value)
            var = alias or (prefix + attr).upper()
            index.settings[attr] = var
            index.reads.setdefault(var, []).append(Read(var, default, repo.ev(path, stmt.lineno), f"settings.{attr}"))


def _name(node: ast.expr) -> str:
    return node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ""


def _env_prefix(cls: ast.ClassDef) -> str:
    for stmt in cls.body:
        call = stmt.value if isinstance(stmt, ast.Assign | ast.AnnAssign) else None
        if isinstance(call, ast.Call):
            for kw in call.keywords:
                if kw.arg == "env_prefix" and isinstance(kw.value, ast.Constant):
                    return str(kw.value.value)
        if isinstance(stmt, ast.ClassDef) and stmt.name == "Config":
            for inner in stmt.body:
                if isinstance(inner, ast.Assign) and any(_name(t) == "env_prefix" for t in inner.targets) and isinstance(inner.value, ast.Constant):
                    return str(inner.value.value)
    return ""


def _field_default(value: ast.expr | None) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    if isinstance(value, ast.Constant):
        return (None if value.value is None else str(value.value)), None
    if isinstance(value, ast.Call) and _name(value.func) == "Field":
        default = alias = None
        if value.args and isinstance(value.args[0], ast.Constant):
            default = value.args[0].value
        for kw in value.keywords:
            if kw.arg == "default" and isinstance(kw.value, ast.Constant):
                default = kw.value.value
            if kw.arg in ("alias", "validation_alias", "env") and isinstance(kw.value, ast.Constant):
                alias = str(kw.value.value)
        return (None if default is None or default is ... else str(default)), alias
    return None, None


_ASSIGN = re.compile(r"^\s*(?:export\s+)?(?:const\s+|let\s+|var\s+)?(?P<name>[A-Za-z_$][\w$]*)\s*(?::[^=\n]+)?=\s*(?P<expr>[^=\n].*)$", re.M)
_FUNC = re.compile(r"(?:function\s+(?P<a>[A-Za-z_$][\w$]*)\s*\(|(?:const|let)\s+(?P<b>[A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*(?::[^=]+)?=>|def\s+(?P<c>[A-Za-z_]\w*)\s*\()")


_IDENT = re.compile(r"(?<![\w.$])([A-Za-z_$][\w$]*)")


def _propagate(repo: Repo, paths: list[str], index: EnvIndex) -> None:
    """One assignment hop: `base = cfg.get("x") or LLM_BASE_URL` binds `base` to LLM_BASE_URL; and a const
    initialised by a function that reads the environment (`API_BASE_URL = resolveApiBaseUrl()`)."""
    by_function: dict[str, str] = {}
    wanted = set(paths)
    for reads in index.reads.values():
        for read in reads:
            if read.ev.path not in wanted or (read.name or "").startswith("settings."):
                continue
            function = _enclosing_function(repo.files[read.ev.path], repo, read.ev.path, read.ev.line)
            if function:
                by_function.setdefault(function, read.var)
    for _ in range(2):
        for path in paths:
            for match in _ASSIGN.finditer(repo.files[path]):
                name, expression = match.group("name"), match.group("expr")
                if name in index.idents or name.isupper() is False and len(name) < 3:
                    continue
                call = re.match(r"\s*(?:await\s+)?([A-Za-z_$][\w$]*)\(", expression)
                if call and call.group(1) in by_function:
                    index.idents[name] = by_function[call.group(1)]
                    continue
                for token in _IDENT.findall(expression):  # identifiers not reached through an attribute
                    if token in index.idents:
                        index.idents[name] = index.idents[token]
                        break


def _enclosing_function(text: str, repo: Repo, path: str, line: int) -> str | None:
    best = None
    for match in _FUNC.finditer(text):
        if repo.line_of(path, match.start()) > line:
            break
        best = match.group("a") or match.group("b") or match.group("c")
    return best


# --- values and addresses ------------------------------------------------------------------------------


def values_for(process: Process, var: str, index: EnvIndex, env_example: dict[str, tuple[str, Ev]]) -> list[EnvValue]:
    """Values the variable can take for this process, strongest source first (one per variant)."""
    if process.env.get(var):
        return list(process.env[var])
    if var in env_example:
        value, ev = env_example[var]
        return [EnvValue(value, ev, "env-example (mẫu, chưa thấy nơi đặt)", INFERRED)]
    for read in index.reads.get(var, []):
        if read.default:
            return [EnvValue(read.default, read.ev, "mặc định trong code, chưa thấy nơi đặt", INFERRED)]
    return []


@dataclass(slots=True)
class Address:
    scheme: str
    host: str
    port: str


def parse_addresses(var: str, value: str | None) -> list[Address]:
    if not value:
        return []
    value = value.strip()
    out: list[Address] = []
    if "://" in value:
        for piece in value.split(","):
            if "://" not in piece:
                continue
            try:
                parts = urlsplit(piece.strip())
                host, port = parts.hostname or "", str(parts.port or "")
            except ValueError:
                continue
            if host:
                out.append(Address(parts.scheme.lower(), host.lower(), port))
        return out
    if value.split(":")[0] in ("0.0.0.0", "::", "*", "[::]") or re.search(r"(BIND|LISTEN)", var, re.I):
        return []  # a listen address, not an outbound connection
    if _HOST_PORT.match(value):
        for piece in value.split(","):
            host, _, port = piece.rpartition(":")
            out.append(Address("", host.lower(), port))
        return out
    if _ADDRESS_VAR.search(var) and _HOSTNAME.match(value) and not value.isdigit() and value.lower() not in ("true", "false", "none"):
        out.append(Address("", value.lower(), ""))
    return out


def channel_kind(var: str, address: Address, target: Process | None) -> str:
    for regex, kind in _SCHEME_KIND:
        if address.scheme and regex.match(address.scheme):
            return kind
    if target is not None and target.kind in _DATA_KINDS:
        return target.kind
    if target is not None and target.kind not in ("external", "user"):
        # A host that is one of our own processes: the variable's name (EMAIL_SERVICE_ADDR) is not the protocol.
        return "grpc" if var.upper().endswith(("_ADDR", "_ADDRESS")) else "http"
    for regex, kind in _VAR_KIND:
        if regex.search(var):
            return kind
    if var.upper().endswith(("_ADDR", "_ADDRESS")) and target is not None:
        return "grpc"
    return "http"


@dataclass(slots=True)
class Resolution:
    target: Process | None
    confidence: Confidence
    note: str


def resolve_host(address: Address, smap: SystemMap, value_ev: Ev) -> Resolution:
    host = address.host
    for suffix in (".svc.cluster.local", ".svc"):
        if host.endswith(suffix):
            host = host[: -len(suffix)].split(".")[0]
    for process in smap.processes.values():
        if process.where != "boundary" and (host == process.name or host in process.aliases):
            return Resolution(process, EXTRACTED, f"host `{host}` là tên service")
    if host in LOCAL_HOSTS:
        for process in smap.processes.values():
            if address.port and address.port in process.published:
                return Resolution(process, INFERRED, f"`{host}:{address.port}` → cổng do {process.name} publish; gọi từ máy host/trình duyệt")
        return Resolution(None, INFERRED, f"`{host}:{address.port}` không khớp cổng publish nào")
    try:
        ip = ipaddress.ip_address(host)
        return Resolution(None, INFERRED, f"địa chỉ IP {ip} không gắn với service nào")
    except ValueError:
        pass
    if "." in host and re.search(r"\.[a-z]{2,}$", host) and not host.endswith((".local", ".internal", ".lan")):
        external = smap.processes.get(host)
        if external is None:
            external = Process(name=host, kind="external", where="boundary", ev=value_ev, reason="domain công khai")
            smap.processes[host] = external
        return Resolution(external, EXTRACTED, "domain công khai → hệ thống ngoài")
    return Resolution(None, INFERRED, f"host `{host}` không khớp service/alias nào")


def build_bindings(
    smap: SystemMap, repo: Repo, indexes: dict[str, EnvIndex], env_example: dict[str, tuple[str, Ev]]
) -> None:
    """Q3 for every process with code: one `Binding` per (variable, distinct value)."""
    for name, index in indexes.items():
        process = smap.processes[name]
        variables = set(index.reads) | set(process.env)
        for var in sorted(variables):
            reads = index.reads.get(var, [])
            read_ev = reads[0].ev if reads else None
            via_command = read_ev is None and _uses_var(process.entrypoint, var)
            if via_command:
                read_ev = process.ev
            values = values_for(process, var, index, env_example)
            if not values:
                if reads and not any(r.default for r in reads):
                    smap.bindings.append(Binding(name, var, None, "không đặt", None, read_ev, INFERRED,
                                                 note="code đọc nhưng không manifest nào đặt và không có mặc định"))  # fmt: skip
                continue
            seen: set[str | None] = set()
            for value in values:
                if value.value in seen:
                    continue
                seen.add(value.value)
                addresses = parse_addresses(var, value.value)
                if not addresses:
                    if value.value in (None, ""):
                        smap.bindings.append(Binding(name, var, value.value, value.source, value.ev, read_ev, value.confidence,
                                                     note="giá trị rỗng → cạnh không tồn tại ở cấu hình này", variant=value.variant))  # fmt: skip
                    continue
                for address in addresses:
                    resolution = resolve_host(address, smap, value.ev)
                    target = resolution.target
                    confidence = weakest(value.confidence, resolution.confidence)
                    note = resolution.note
                    if read_ev is None:
                        confidence = INFERRED
                        note += "; manifest đặt nhưng không thấy code đọc (có thể thư viện tự đọc)"
                    elif via_command:
                        note += f"; biến dùng trong command `{process.entrypoint}` của manifest"
                    kind = channel_kind(var, address, target)
                    if via_command and re.search(rf"https?://\$\{{?{re.escape(var)}\b", process.entrypoint or ""):
                        kind = "http"
                    smap.bindings.append(Binding(
                        name, var, value.value, value.source, value.ev, read_ev, confidence,
                        target=target.name if target else None, kind=kind, note=note,
                        variant=value.variant, via_command=via_command,
                    ))  # fmt: skip


def _uses_var(command: str | None, var: str) -> bool:
    return bool(command) and re.search(rf"\$\{{?{re.escape(var)}\b", command or "") is not None
