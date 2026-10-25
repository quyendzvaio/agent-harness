"""The system map: an evidence graph of what runs, how it connects and who owns which data.

Vocabulary and the two confidence levels follow the `reading-system-architecture` method:
EXTRACTED = backed by `file:line` on both ends (edge) or in a manifest (node); INFERRED = from a
default, a doc, a name, or only one end, always with the reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Confidence = Literal["EXTRACTED", "INFERRED"]
EXTRACTED: Confidence = "EXTRACTED"
INFERRED: Confidence = "INFERRED"

#: Node kinds. Data nodes (db..vector-store) own state; boundary nodes (user, external) are outside the system.
PROCESS_KINDS = ("http-server", "worker", "scheduler", "agent", "web", "batch-job", "gateway", "unknown")
DATA_KINDS = ("db", "cache", "queue", "storage", "vector-store")
BOUNDARY_KINDS = ("user", "external")
#: Where a node runs.
WHERE = ("manifest", "dev-host", "external-hosted", "undeployed", "manual", "boundary")


@dataclass(frozen=True, slots=True)
class Ev:
    path: str
    line: int
    text: str = ""

    @property
    def at(self) -> str:
        return f"{self.path}:L{self.line}"


def weakest(*levels: Confidence) -> Confidence:
    return INFERRED if INFERRED in levels else EXTRACTED


@dataclass(slots=True)
class EnvValue:
    """A value given to an environment variable of one process, and where it was given."""

    value: str | None
    ev: Ev
    source: str  # "compose", "k8s", "dockerfile", "env-file", "env-example", "code-default", "build-arg"
    confidence: Confidence
    variant: str = "base"


@dataclass(slots=True)
class Process:
    """A node: something that runs (process), holds data (data node) or sits at the boundary."""

    name: str
    kind: str
    where: str
    ev: Ev
    confidence: Confidence = EXTRACTED
    code_dir: str | None = None
    entrypoint: str | None = None
    image: str | None = None
    #: Host ports published to the outside ("8000" from "8000:8000").
    published: list[str] = field(default_factory=list)
    container_ports: list[str] = field(default_factory=list)
    profiles: tuple[str, ...] = ()
    variant: str = "base"
    aliases: set[str] = field(default_factory=set)
    env: dict[str, list[EnvValue]] = field(default_factory=dict)
    depends_on: list[tuple[str, str, Ev]] = field(default_factory=list)
    healthcheck: str | None = None
    volumes: list[tuple[str, str, bool, Ev]] = field(default_factory=list)  # source, target, read_only, ev
    notes: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def is_data(self) -> bool:
        return self.kind in DATA_KINDS

    @property
    def is_boundary(self) -> bool:
        return self.kind in BOUNDARY_KINDS


@dataclass(slots=True)
class Channel:
    """An edge: both ends found (or the far end is infrastructure/external declared in a manifest)."""

    src: str
    dst: str
    kind: str  # http, grpc, queue, db, cache, storage, realtime, file, mail, external
    address: str
    detail: str  # route, topic, rpc service, mount path...
    client_ev: Ev | None
    server_ev: Ev | None
    match: Confidence
    addr: Confidence
    sync: str = "sync"  # sync | async | push
    when: str = "run-time"  # run-time | boot-time | build-time
    on_failure: str = ""
    variant: str = "base"
    reason: str = ""
    #: False when `client_ev` is where the address is read (settings field, getenv), not a call.
    call_site: bool = True

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.src, self.dst, self.kind)


@dataclass(slots=True)
class Store:
    """A data node and who touches it (Q5)."""

    name: str
    kind: str
    ev: Ev | None
    writers: dict[str, Ev] = field(default_factory=dict)
    readers: dict[str, Ev] = field(default_factory=dict)
    schema_owner: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Gap:
    """One Q9 violation or unverified claim. `rule` names the invariant."""

    rule: str
    detail: str
    ev: Ev | None = None


@dataclass(slots=True)
class Route:
    process: str
    method: str  # GET/POST/... or ANY; WS for websockets
    path: str
    ev: Ev
    realtime: bool = False
    auth: bool = False
    framework: str = ""


@dataclass(slots=True)
class Binding:
    """Q3 chain for one variable of one process: code reads it, something sets it, its host resolves."""

    process: str
    var: str
    value: str | None
    source: str
    value_ev: Ev | None
    read_ev: Ev | None
    confidence: Confidence
    target: str | None = None  # node name the address resolves to
    kind: str = ""  # channel kind implied by the address
    note: str = ""
    variant: str = "base"
    #: The variable is used in the manifest's command (`--host=http://${FRONTEND_ADDR}`), not read by code.
    via_command: bool = False


@dataclass(slots=True)
class SystemMap:
    manifests: list[str] = field(default_factory=list)
    variants: dict[str, list[str]] = field(default_factory=dict)
    processes: dict[str, Process] = field(default_factory=dict)
    channels: list[Channel] = field(default_factory=list)
    stores: dict[str, Store] = field(default_factory=dict)
    routes: list[Route] = field(default_factory=list)
    bindings: list[Binding] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    hypotheses: list[tuple[str, Ev, bool]] = field(default_factory=list)  # claim, where, verified
    invariants_checked: int = 0
    truncated: bool = False

    @property
    def has_runtime(self) -> bool:
        return any(p.where == "manifest" and not p.is_boundary for p in self.processes.values())

    def gap(self, rule: str, detail: str, ev: Ev | None = None) -> None:
        self.gaps.append(Gap(rule, detail, ev))
