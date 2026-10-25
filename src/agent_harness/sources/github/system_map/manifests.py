"""Q0 (boundary), Q1 (processes), Q2 (roles) and the manifest half of Q7/Q8: what runs, and how it is started.

Only manifests decide what runs. Code is opened here for one reason: to tell the role of a process whose
entrypoint is a bare interpreter call (`python worker.py`).
"""

from __future__ import annotations

import json
import posixpath
import re
import shlex
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from agent_harness.sources.github.system_map import yamllines
from agent_harness.sources.github.system_map.catalog import Catalog, image_name
from agent_harness.sources.github.system_map.model import (
    EXTRACTED,
    INFERRED,
    EnvValue,
    Ev,
    Process,
    SystemMap,
)
from agent_harness.sources.github.system_map.repo import TEST_DIRS, Repo, inside, norm_dir

_COMPOSE = re.compile(r"(^|/)(docker-)?compose(\.(?P<variant>[\w.-]+?))?\.ya?ml$")
_INTERP = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:(?P<op>:?[-?])(?P<default>[^}]*))?\}|\$(?P<bare>[A-Za-z_][A-Za-z0-9_]*)")
_K8S_WORKLOADS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "Pod", "ReplicaSet"}
_DEPLOY_CMD = re.compile(
    r"\b(gcloud\s+(run|functions|app)\s+deploy|aws\s+lambda\s+(create|update)|serverless\s+deploy|sls\s+deploy|"
    r"fly(ctl)?\s+deploy|vercel\b[^\n]*--prod|netlify\s+deploy|railway\s+up|heroku\s+container|runpod\w*|"
    r"az\s+(webapp|containerapp|functionapp)\s+(up|create|update|deploy))",
    re.I,
)
_COMPOSE_RUN = re.compile(r"\b(?:docker[- ]compose|docker\s+compose)\b[^\n]*?\b(run|exec)\s+(?:--?[\w-]+(?:[= ]\S+)?\s+)*(?P<svc>[\w.-]+)\s+(?P<cmd>[^\n;&|]+)")
#: Variant name used for the files that Compose loads by default.
BASE = "base"


@dataclass(slots=True)
class Dockerfile:
    path: str
    cmd: str | None = None
    cmd_line: int = 0
    env: dict[str, tuple[str, int]] = field(default_factory=dict)
    args: dict[str, tuple[str, int]] = field(default_factory=dict)
    expose: list[str] = field(default_factory=list)


def interpolate(value: str, lookup: dict[str, str]) -> tuple[str, bool, str]:
    """Compose-style `${X:-default}`. Returns (value, fully_known, note). `lookup` holds known values (.env)."""
    notes: list[str] = []
    known = True

    def sub(match: re.Match[str]) -> str:
        nonlocal known
        name = match.group("name") or match.group("bare")
        if name in lookup:
            notes.append(f"{name} từ .env mẫu")
            known = False  # .env.example is a sample, not the deployed value
            return lookup[name]
        if match.group("op") and match.group("op").endswith("-"):
            notes.append(f"{name} không đặt → mặc định trong manifest")
            known = False
            return match.group("default") or ""
        notes.append(f"{name} lấy từ môi trường máy chạy")
        known = False
        return ""

    out = _INTERP.sub(sub, value)
    return out, known, "; ".join(notes)


def parse_dockerfile(path: str, text: str) -> Dockerfile:
    out = Dockerfile(path)
    entry: tuple[str, int] | None = None
    cmd: tuple[str, int] | None = None
    logical: list[tuple[int, str]] = []
    buffer, start = "", 0
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if not buffer:
            start = number
        if line.endswith("\\"):
            buffer += line[:-1] + " "
            continue
        buffer += line
        if buffer.strip() and not buffer.lstrip().startswith("#"):
            logical.append((start, buffer.strip()))
        buffer = ""
    for number, line in logical:
        word, _, rest = line.partition(" ")
        word = word.upper()
        rest = rest.strip()
        if word == "FROM":  # a new stage resets CMD/ENTRYPOINT; ENV of earlier stages is not inherited
            entry = cmd = None
            out.env = {}
            out.expose = []
        elif word == "ARG":
            name, _, default = rest.partition("=")
            out.args[name.strip()] = (default.strip().strip("\"'"), number)
        elif word == "ENV":
            for name, value in _env_pairs(rest):
                out.env[name] = (_dockerfile_vars(value, out), number)
        elif word == "EXPOSE":
            out.expose += [p.split("/")[0] for p in rest.split()]
        elif word in ("CMD", "ENTRYPOINT"):
            command = _exec_form(rest)
            if word == "CMD":
                cmd = (command, number)
            else:
                entry = (command, number)
    parts = [p for p in (entry, cmd) if p]
    if parts:
        out.cmd = " ".join(p[0] for p in parts)
        out.cmd_line = parts[-1][1]
    return out


def _env_pairs(rest: str) -> Iterator[tuple[str, str]]:
    if "=" not in rest.split(" ", 1)[0]:  # legacy `ENV KEY value`
        name, _, value = rest.partition(" ")
        yield name, value.strip().strip("\"'")
        return
    try:
        tokens = shlex.split(rest)
    except ValueError:
        tokens = rest.split()
    for token in tokens:
        name, _, value = token.partition("=")
        yield name, value


def _dockerfile_vars(value: str, dockerfile: Dockerfile) -> str:
    known = {k: v for k, (v, _) in {**dockerfile.args, **dockerfile.env}.items()}
    return _INTERP.sub(lambda m: known.get(m.group("name") or m.group("bare"), m.group(0)), value)


def _exec_form(rest: str) -> str:
    if rest.startswith("["):
        try:
            items = json.loads(rest)
        except json.JSONDecodeError:
            return rest
        if len(items) >= 3 and items[0] in ("sh", "bash", "/bin/sh", "/bin/bash") and items[1] == "-c":
            return items[2]
        return " ".join(str(i) for i in items)
    return rest


def _command(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        items = [str(v) for v in value]
        if len(items) >= 3 and items[0] in ("sh", "bash", "/bin/sh", "/bin/bash") and items[1] == "-c":
            return items[2]
        return " ".join(items)
    return str(value)


class ManifestReader:
    def __init__(self, repo: Repo, catalog: Catalog, smap: SystemMap) -> None:
        self.repo = repo
        self.catalog = catalog
        self.smap = smap
        self.dockerfiles = {p: parse_dockerfile(p, repo.files[p]) for p in repo.paths if _is_dockerfile(p)}
        self.env_example = self._env_example()
        self.referenced_dirs: set[str] = set()
        self.pod_labels: dict[str, dict[str, str]] = {}

    # --- entry point ---------------------------------------------------------------------------------

    def read(self) -> None:
        self._compose()
        self._k8s()
        self._procfiles()
        self._other_platforms()
        self._ci_and_scripts()
        self._dev_hosts_and_undeployed()
        for process in self.smap.processes.values():
            self._assign_role(process)
        self._user_boundary()

    # --- .env samples ---------------------------------------------------------------------------------

    def _env_example(self) -> dict[str, tuple[str, Ev]]:
        out: dict[str, tuple[str, Ev]] = {}
        for path in self.repo.paths:
            base = posixpath.basename(path)
            if base in (".env.example", ".env.sample", "env.example", ".env.template", ".env.dist", "example.env"):
                for number, line in enumerate(self.repo.lines(path), 1):
                    name, sep, value = line.strip().partition("=")
                    if sep and name and not name.startswith("#"):
                        out.setdefault(name.strip().removeprefix("export "), (value.strip().strip("\"'"), self.repo.ev(path, number)))
        return out

    # --- docker compose -------------------------------------------------------------------------------

    def _compose(self) -> None:
        stacks: dict[str, list[tuple[str, str]]] = {}
        for path in self.repo.paths:
            match = _COMPOSE.search(path)
            if not match or TEST_DIRS.search(posixpath.dirname(path)):
                continue
            variant = match.group("variant") or BASE
            if variant == "override" or variant.endswith(".override") or variant.endswith("override"):
                variant = BASE
            stacks.setdefault(posixpath.dirname(path), []).append((path, variant))
        for directory, entries in stacks.items():
            entries.sort(key=lambda e: (e[1] != BASE, "override" in e[0], e[0]))
            has_base = any(v == BASE for _, v in entries)
            prefix = f"{directory}:" if len(stacks) > 1 and directory else ""
            for path, variant in entries:
                name = f"{prefix}{variant}" if has_base or variant != BASE else f"{prefix}{BASE}"
                self.smap.manifests.append(path)
                self.smap.variants.setdefault(name, []).append(path)
                doc = yamllines.load(self.repo.files[path])
                if not isinstance(doc, dict):
                    self.smap.gap("Q0", f"{path} không parse được YAML", self.repo.ev(path, 1))
                    continue
                if not doc.get("networks"):
                    note = f"{path}: không khai báo networks → mọi service trong stack thấy nhau (Q8)"
                    if note not in self.smap.notes and variant == BASE:
                        self.smap.notes.append(note)
                services = doc.get("services") or {}
                for svc_name, svc in services.items():
                    if isinstance(svc, dict):
                        self._compose_service(str(svc_name), svc, services, path, directory, name)

    def _compose_service(self, name: str, svc: Any, services: Any, path: str, directory: str, variant: str) -> None:
        key = name if name not in self.smap.processes or self.smap.processes[name].ev.path.startswith(directory) else f"{name}@{directory}"
        ev = self.repo.ev(path, yamllines.line_of(services, name))
        process = self.smap.processes.get(key)
        if process is None:
            process = Process(name=key, kind="", where="manifest", ev=ev, variant=variant)
            process.aliases.add(name)
            self.smap.processes[key] = process
        elif variant != BASE and process.variant == BASE:
            process.notes.append(f"được {path} ({variant}) bổ sung")

        def ev_of(field_name: str) -> Ev:
            return self.repo.ev(path, yamllines.line_of(svc, field_name))

        build = svc.get("build")
        if build is not None:
            context = build if isinstance(build, str) else (build.get("context") or ".")
            dockerfile = "Dockerfile" if isinstance(build, str) else (build.get("dockerfile") or "Dockerfile")
            process.code_dir = norm_dir(directory, str(context))
            self.referenced_dirs.add(process.code_dir)
            df_path = norm_dir(process.code_dir, str(dockerfile))
            if df_path in self.dockerfiles:
                self._apply_dockerfile(process, self.dockerfiles[df_path], override=False)
            if isinstance(build, dict) and isinstance(build.get("args"), dict):
                for arg, value in build["args"].items():
                    self._set_env(process, str(arg), value, self.repo.ev(path, yamllines.line_of(build["args"], arg)), "build-arg", variant)
        if svc.get("image"):
            process.image = str(svc["image"])
        command = _command(svc.get("command"))
        entrypoint = _command(svc.get("entrypoint"))
        if command or entrypoint:
            process.entrypoint = " ".join(c for c in (entrypoint, command) if c)
            process.notes.append(f"entrypoint từ {ev_of('command' if command else 'entrypoint').at}")
        for alias_key in ("container_name", "hostname"):
            if svc.get(alias_key):
                process.aliases.add(str(svc[alias_key]))
        networks = svc.get("networks")
        if isinstance(networks, dict):
            for cfg in networks.values():
                if isinstance(cfg, dict):
                    process.aliases.update(str(a) for a in cfg.get("aliases") or [])
        env = svc.get("environment")
        if isinstance(env, dict):
            for var, value in env.items():
                self._set_env(process, str(var), value, self.repo.ev(path, yamllines.line_of(env, var)), "compose", variant)
        elif isinstance(env, list):
            for i, item in enumerate(env):
                var, sep, value = str(item).partition("=")
                self._set_env(process, var, value if sep else None, self.repo.ev(path, yamllines.line_of(env, i)), "compose", variant)
        env_files = svc.get("env_file")
        for i, env_file in enumerate([env_files] if isinstance(env_files, str) else env_files or []):
            file_path = norm_dir(directory, str(env_file if isinstance(env_file, str) else env_file.get("path", "")))
            if file_path in self.repo.files:
                for number, line in enumerate(self.repo.lines(file_path), 1):
                    var, sep, value = line.strip().partition("=")
                    if sep and var and not var.startswith("#"):
                        self._set_env(process, var.strip(), value.strip(), self.repo.ev(file_path, number), "env-file", variant)
            else:
                process.notes.append(f"env_file {file_path} không có trong repo (giá trị chỉ có lúc chạy) ← {ev_of('env_file').at}")
        ports = svc.get("ports") or []
        for i, port in enumerate(ports):
            published, target = _port(port)
            if published:
                process.published.append(published)
            if target:
                process.container_ports.append(target)
        process.container_ports += [str(p).split("/")[0] for p in svc.get("expose") or []]
        depends = svc.get("depends_on")
        if isinstance(depends, dict):
            for dep, cfg in depends.items():
                condition = (cfg or {}).get("condition", "") if isinstance(cfg, dict) else ""
                process.depends_on.append((str(dep), condition, self.repo.ev(path, yamllines.line_of(depends, dep))))
        elif isinstance(depends, list):
            for i, dep in enumerate(depends):
                process.depends_on.append((str(dep), "", self.repo.ev(path, yamllines.line_of(depends, i))))
        health = svc.get("healthcheck")
        if isinstance(health, dict) and health.get("test"):
            process.healthcheck = _command(health["test"])
        for i, volume in enumerate(svc.get("volumes") or []):
            source, target, read_only = _volume(volume)
            if target:
                process.volumes.append((source, target, read_only, self.repo.ev(path, yamllines.line_of(svc["volumes"], i))))
        if svc.get("profiles"):
            process.profiles = tuple(str(p) for p in svc["profiles"])
        if "secrets" in svc:
            process.notes.append(f"nhận secret qua `secrets:` ← {ev_of('secrets').at}")

    def _set_env(self, process: Process, var: str, value: Any, ev: Ev, source: str, variant: str) -> None:
        if not var:
            return
        lookup = {k: v for k, (v, _) in self.env_example.items()}
        if value is None:
            entry = EnvValue(None, ev, source, INFERRED, variant)
        else:
            text, known, note = interpolate(str(value), lookup)
            entry = EnvValue(text, ev, source if not note else f"{source} ({note})", EXTRACTED if known else INFERRED, variant)
        values = process.env.setdefault(var, [])
        values[:] = [v for v in values if v.variant != variant or v.source.startswith("dockerfile")]
        values.insert(0, entry)

    def _apply_dockerfile(self, process: Process, dockerfile: Dockerfile, *, override: bool) -> None:
        if dockerfile.cmd and (override or not process.entrypoint):
            process.entrypoint = dockerfile.cmd
            process.notes.append(f"entrypoint từ {dockerfile.path}:L{dockerfile.cmd_line}")
        for var, (value, line) in dockerfile.env.items():
            if var not in process.env:
                process.env[var] = [EnvValue(value, self.repo.ev(dockerfile.path, line), "dockerfile", EXTRACTED)]
        process.container_ports += [p for p in dockerfile.expose if p not in process.container_ports]

    # --- kubernetes + skaffold -------------------------------------------------------------------------

    def _k8s(self) -> None:
        artifacts = self._skaffold()
        workloads: list[tuple[str, Any, Any]] = []
        services: list[tuple[str, Any]] = []
        config_maps: dict[str, Any] = {}
        ingresses: list[tuple[str, Any]] = []
        for path in self.repo.paths:
            if not path.endswith((".yaml", ".yml")) or _COMPOSE.search(path) or "/.github/" in f"/{path}":
                continue
            text = self.repo.files[path]
            if "kind:" not in text or "apiVersion:" not in text or "{{" in text:
                continue
            found = False
            for doc in yamllines.load_all(text):
                if not isinstance(doc, dict) or "kind" not in doc:
                    continue
                kind = doc.get("kind")
                if kind in _K8S_WORKLOADS:
                    workloads.append((path, doc, kind))
                    found = True
                elif kind == "Service":
                    services.append((path, doc))
                    found = True
                elif kind == "ConfigMap":
                    config_maps[str((doc.get("metadata") or {}).get("name"))] = (path, doc)
                elif kind in ("Ingress", "HTTPRoute", "VirtualService"):
                    ingresses.append((path, doc))
            if found:
                self.smap.manifests.append(path)
        seen: set[str] = set()
        for path, doc, kind in workloads:
            meta = doc.get("metadata") or {}
            name = str(meta.get("name") or "")
            if not name or name in seen:
                continue
            seen.add(name)
            spec = doc.get("spec") or {}
            if kind == "CronJob":
                spec = ((spec.get("jobTemplate") or {}).get("spec")) or {}
            template = spec.get("template") or ({"spec": spec} if kind == "Pod" else {})
            pod = template.get("spec") or {}
            containers = pod.get("containers") or []
            if not containers:
                continue
            main = containers[0]
            ev = self.repo.ev(path, yamllines.line_of(meta, "name"))
            process = self.smap.processes.get(name) or Process(name=name, kind="", where="manifest", ev=ev, variant="k8s")
            process.aliases.add(name)
            if kind in ("CronJob",):
                process.kind = "scheduler"
            elif kind == "Job":
                process.kind = "batch-job"
            process.image = str(main.get("image") or "")
            command = _command(main.get("command"))
            args = _command(main.get("args"))
            if command or args:
                process.entrypoint = " ".join(c for c in (command, args) if c)
            process.container_ports += [str(p.get("containerPort")) for p in main.get("ports") or [] if isinstance(p, dict)]
            labels = (template.get("metadata") or {}).get("labels") or {}
            self.pod_labels[name] = {str(k): str(v) for k, v in labels.items()}
            process.notes.append(f"k8s {kind} ← {ev.at}")
            env = main.get("env") or []
            for i, item in enumerate(env):
                if not isinstance(item, dict):
                    continue
                var, value_ev = str(item.get("name")), self.repo.ev(path, yamllines.line_of(env, i))
                if "value" in item:
                    self._set_env(process, var, item.get("value"), value_ev, "k8s", "k8s")
                elif isinstance(item.get("valueFrom"), dict):
                    ref = item["valueFrom"]
                    cm = ref.get("configMapKeyRef") or {}
                    if cm and str(cm.get("name")) in config_maps:
                        cm_path, cm_doc = config_maps[str(cm["name"])]
                        data = cm_doc.get("data") or {}
                        key = cm.get("key")
                        if key in data:
                            self._set_env(process, var, data[key], self.repo.ev(cm_path, yamllines.line_of(data, key)), "k8s configmap", "k8s")
                            continue
                    self._set_env(process, var, None, value_ev, "k8s secret/ref", "k8s")
            for item in main.get("envFrom") or []:
                ref = (item or {}).get("configMapRef") or {}
                if str(ref.get("name")) in config_maps:
                    cm_path, cm_doc = config_maps[str(ref["name"])]
                    data = cm_doc.get("data") or {}
                    for var, value in data.items():
                        self._set_env(process, str(var), value, self.repo.ev(cm_path, yamllines.line_of(data, var)), "k8s configmap", "k8s")
            code_dir, why = self._code_dir_for_image(process.image, artifacts)
            if code_dir is not None:
                process.code_dir = code_dir
                self.referenced_dirs.add(code_dir)
                process.notes.append(why)
                df = norm_dir(code_dir, "Dockerfile")
                if df in self.dockerfiles:
                    self._apply_dockerfile(process, self.dockerfiles[df], override=not process.entrypoint)
            self.smap.processes[name] = process
            self.smap.variants.setdefault("k8s", [])
            if path not in self.smap.variants["k8s"]:
                self.smap.variants["k8s"].append(path)
        by_labels = {p.name: p for p in self.smap.processes.values() if p.variant == "k8s"}
        for path, doc in services:
            meta = doc.get("metadata") or {}
            spec = doc.get("spec") or {}
            name = str(meta.get("name") or "")
            selector = spec.get("selector") or {}
            target = next(
                (p for p in by_labels.values()
                 if selector and all(self.pod_labels.get(p.name, {}).get(str(k)) == str(v) for k, v in selector.items())),
                by_labels.get(name),
            )  # fmt: skip
            if target is None:
                continue
            target.aliases.add(name)
            for port in spec.get("ports") or []:
                if isinstance(port, dict) and port.get("port"):
                    target.container_ports.append(str(port["port"]))
            if spec.get("type") in ("LoadBalancer", "NodePort"):
                target.published.append(f"{spec.get('type')} {name}")
                target.notes.append(f"Service {name} type {spec.get('type')} ← {self.repo.ev(path, yamllines.line_of(spec, 'type')).at}")
        for path, doc in ingresses:
            for backend in _ingress_backends(doc):
                for process in by_labels.values():
                    if backend in process.aliases:
                        process.published.append(f"ingress {backend}")
                        process.notes.append(f"ingress → {backend} ← {path}")

    def _skaffold(self) -> dict[str, tuple[str, Ev]]:
        out: dict[str, tuple[str, Ev]] = {}
        for path in self.repo.named("skaffold.yaml", "skaffold.yml"):
            self.smap.manifests.append(path)
            for doc in yamllines.load_all(self.repo.files[path]):
                artifacts = ((doc or {}).get("build") or {}).get("artifacts") if isinstance(doc, dict) else None
                for i, artifact in enumerate(artifacts or []):
                    if isinstance(artifact, dict) and artifact.get("image"):
                        context = norm_dir(posixpath.dirname(path), str(artifact.get("context") or "."))
                        out[image_name(str(artifact["image"]))] = (context, self.repo.ev(path, yamllines.line_of(artifacts, i)))
        return out

    def _code_dir_for_image(self, image: str | None, artifacts: dict[str, tuple[str, Ev]]) -> tuple[str | None, str]:
        if not image:
            return None, ""
        name = image_name(image)
        if name in artifacts:
            context, ev = artifacts[name]
            return context, f"thư mục code {context or '/'} từ skaffold ← {ev.at}"
        candidates = [posixpath.dirname(p) for p in self.dockerfiles if posixpath.basename(posixpath.dirname(p)) == name]
        if len(candidates) == 1:
            return candidates[0], f"thư mục code {candidates[0]} đoán theo tên image (INFERRED)"
        return None, ""

    # --- Procfile, platforms ----------------------------------------------------------------------------

    def _procfiles(self) -> None:
        for path in self.repo.named("Procfile"):
            self.smap.manifests.append(path)
            directory = posixpath.dirname(path)
            for number, line in enumerate(self.repo.lines(path), 1):
                name, sep, command = line.partition(":")
                if sep and name.strip() and not name.startswith("#"):
                    key = name.strip() if name.strip() not in self.smap.processes else f"{name.strip()}@{directory}"
                    self.smap.processes[key] = Process(
                        name=key, kind="", where="manifest", ev=self.repo.ev(path, number), code_dir=directory, entrypoint=command.strip(), variant="procfile"
                    )
                    self.referenced_dirs.add(directory)

    def _other_platforms(self) -> None:
        for path in self.repo.named("langgraph.json"):
            self.smap.manifests.append(path)
            directory = posixpath.dirname(path)
            self.smap.processes.setdefault(
                "langgraph",
                Process(name="langgraph", kind="agent", where="manifest", ev=self.repo.ev(path, self.repo.find_line(path, "graphs")),
                        code_dir=directory, entrypoint="langgraph up", variant="langgraph",
                        notes=["LangGraph Server: route /threads, /runs, /assistants là framework route"]),
            )  # fmt: skip
            self.referenced_dirs.add(directory)
        for path in self.repo.named("supervisord.conf"):
            self.smap.manifests.append(path)
            program = None
            for number, line in enumerate(self.repo.lines(path), 1):
                head = re.match(r"\[program:([\w.-]+)\]", line.strip())
                if head:
                    program = head.group(1)
                elif program and line.strip().startswith("command="):
                    self.smap.processes.setdefault(program, Process(name=program, kind="", where="manifest", ev=self.repo.ev(path, number),
                                                                    code_dir=posixpath.dirname(path), entrypoint=line.split("=", 1)[1].strip()))  # fmt: skip
        for path in self.repo.paths:
            if path.endswith(".service") and "ExecStart=" in self.repo.files[path]:
                self.smap.manifests.append(path)
                number = self.repo.find_line(path, "ExecStart=")
                name = posixpath.basename(path).removesuffix(".service")
                self.smap.processes.setdefault(name, Process(name=name, kind="", where="manifest", ev=self.repo.ev(path, number),
                                                             entrypoint=self.repo.lines(path)[number - 1].split("=", 1)[1].strip()))  # fmt: skip
        for marker in ("fly.toml", "render.yaml", "vercel.json", "serverless.yml", "serverless.yaml", "app.yaml", "netlify.toml"):
            for path in self.repo.named(marker):
                self.smap.manifests.append(path)
                directory = posixpath.dirname(path)
                name = f"{marker.split('.')[0]}:{directory or 'root'}"
                self.smap.processes.setdefault(
                    name,
                    Process(name=name, kind="web" if marker in ("vercel.json", "netlify.toml") else "", where="external-hosted",
                            ev=self.repo.ev(path, 1), confidence=INFERRED, code_dir=directory, reason=f"triển khai qua {marker}"),
                )  # fmt: skip
                self.referenced_dirs.add(directory)

    # --- CI workflows, Makefile, deploy scripts ----------------------------------------------------------

    def _ci_and_scripts(self) -> None:
        for path in self.repo.paths:
            in_ci = path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml"))
            if not (in_ci or posixpath.basename(path) == "Makefile" or path.endswith((".sh", ".bash", ".ps1"))):
                continue
            text = self.repo.files[path]
            if in_ci:
                self.smap.manifests.append(path)
            if posixpath.basename(path) == "Makefile":
                self.smap.manifests.append(path)
            if in_ci and "actions/deploy-pages" in text:
                number = self.repo.find_line(path, "actions/deploy-pages")
                self.smap.processes.setdefault(
                    "github-pages",
                    Process(name="github-pages", kind="web", where="external-hosted", ev=self.repo.ev(path, number), confidence=INFERRED,
                            reason="site tĩnh được workflow deploy lên GitHub Pages"),
                )  # fmt: skip
            for match in _DEPLOY_CMD.finditer(text):
                number = self.repo.line_of(path, match.start())
                directory = self._dir_mentioned(text) or ""
                name = f"deploy:{posixpath.basename(path)}:{match.group(1).split()[0].lower()}"
                self.smap.processes.setdefault(
                    name,
                    Process(name=name, kind="", where="external-hosted", ev=self.repo.ev(path, number), confidence=INFERRED,
                            code_dir=directory or None, reason=f"script/CI chạy `{match.group(1)}`"),
                )  # fmt: skip
                if directory:
                    self.referenced_dirs.add(directory)
            for match in _COMPOSE_RUN.finditer(text):
                svc, command = match.group("svc"), match.group("cmd").strip()
                number = self.repo.line_of(path, match.start())
                name = f"job:{svc}:{command.split()[0] if command else 'run'}"
                target = self.smap.processes.get(svc)
                self.smap.processes.setdefault(
                    name,
                    Process(name=name, kind="batch-job", where="manifest", ev=self.repo.ev(path, number),
                            code_dir=target.code_dir if target else None, entrypoint=command, variant=target.variant if target else BASE,
                            reason=f"`compose {match.group(1)} {svc}` trong {path}"),
                )  # fmt: skip

    def _dir_mentioned(self, text: str) -> str | None:
        for path in self.dockerfiles:
            directory = posixpath.dirname(path)
            if directory and (directory in text or path in text):
                return directory
        return None

    # --- dev hosts and undeployed code --------------------------------------------------------------------

    def _dev_hosts_and_undeployed(self) -> None:
        claimed = {p.code_dir for p in self.smap.processes.values() if p.code_dir is not None}

        def owned(directory: str) -> bool:
            return any(c is not None and (c == directory or (c and inside(directory, c))) for c in claimed)

        for path in self.repo.named("package.json"):
            directory = posixpath.dirname(path)
            if owned(directory) or TEST_DIRS.search(directory):
                continue
            try:
                scripts = json.loads(self.repo.files[path]).get("scripts") or {}
            except (json.JSONDecodeError, AttributeError):
                continue
            for script in ("dev", "start", "serve"):
                command = scripts.get(script)
                if isinstance(command, str):
                    name = f"dev:{directory or 'root'}"
                    self.smap.processes[name] = Process(
                        name=name, kind="", where="dev-host", ev=self.repo.ev(path, self.repo.find_line(path, f'"{script}"')),
                        confidence=INFERRED, code_dir=directory, entrypoint=command,
                        reason=f"chỉ có script `{script}` trong package.json, không manifest nào khởi động",
                    )  # fmt: skip
                    claimed.add(directory)
                    break
        for path in sorted(self.dockerfiles):
            directory = posixpath.dirname(path)
            if owned(directory) or TEST_DIRS.search(directory):
                continue
            name = posixpath.basename(directory) or "root"
            key = name if name not in self.smap.processes else f"{name}@{directory}"
            dockerfile = self.dockerfiles[path]
            self.smap.processes[key] = Process(
                name=key, kind="", where="undeployed", ev=self.repo.ev(path, dockerfile.cmd_line or 1), confidence=INFERRED,
                code_dir=directory, entrypoint=dockerfile.cmd, container_ports=list(dockerfile.expose),
                reason="có Dockerfile nhưng không manifest nào khởi động",
            )  # fmt: skip
            claimed.add(directory)

    # --- Q2 roles -------------------------------------------------------------------------------------------

    def _assign_role(self, process: Process) -> None:
        if process.kind:
            return
        if process.image and process.code_dir is None:
            kind = self.catalog.image_kind(process.image)
            process.kind = kind or "unknown"
            if kind is None:
                process.notes.append(f"image {process.image} không có trong catalog → loại chưa rõ")
            return
        command = process.entrypoint or ""
        code_dir = process.code_dir
        script = _package_script(command, code_dir, self.repo) if code_dir is not None else None
        if script:
            process.notes.append(f"`{command}` chạy script `{script}` trong {norm_dir(code_dir or '', 'package.json')}")
        role = self.catalog.role(script or command) if command else None
        is_web = code_dir is not None and any(
            norm_dir(code_dir, marker) in self.repo.files for marker in self.catalog.web_markers
        )
        if is_web and role in (None, "http-server", "web"):
            process.kind = "web"
            return
        if role:
            process.kind = role
            return
        if code_dir is not None:
            entry_file = _entry_file(command, code_dir, self.repo)
            candidates = [entry_file] if entry_file else [p for p in self.repo.under(code_dir) if not TEST_DIRS.search(p)][:400]
            everything = [p for p in self.repo.under(code_dir) if not TEST_DIRS.search(p)][:400]
            for regex, kind in self.catalog.code_markers:
                hit = next((p for p in candidates if p and regex.search(self.repo.files.get(p, ""))), None)
                if hit is None and entry_file:  # the entry file may only wire up modules that hold the server
                    hit = next((p for p in everything if regex.search(self.repo.files.get(p, ""))), None)
                if hit:
                    process.kind = kind
                    process.confidence = INFERRED if process.where == "manifest" and not entry_file else process.confidence
                    process.notes.append(f"vai trò {kind} suy từ code {hit} (entrypoint `{command or '?'}`)")
                    return
        if process.image:
            kind = self.catalog.image_kind(process.image)
            if kind:
                process.kind = kind
                return
        process.kind = "unknown"
        process.notes.append("không xác định được vai trò từ entrypoint")

    def _user_boundary(self) -> None:
        for process in self.smap.processes.values():
            process.published = list(dict.fromkeys(process.published))
            process.container_ports = list(dict.fromkeys(p for p in process.container_ports if p and p != "None"))
        if any(p.published for p in self.smap.processes.values()):
            self.smap.processes.setdefault(
                "user", Process(name="user", kind="user", where="boundary", ev=Ev("", 0, "ports/ingress publish ra ngoài"))
            )


_NPM_RUN = re.compile(r"^(?:npm|pnpm|yarn|bun)\s+(?:run(?:-script)?\s+)?(?P<script>[\w:.-]+)")


def _package_script(command: str, code_dir: str, repo: Repo) -> str | None:
    """`npm run start` -> the `start` script of the code dir's package.json (`next start`)."""
    match = _NPM_RUN.match(command.strip())
    path = norm_dir(code_dir, "package.json")
    if match is None or path not in repo.files:
        return None
    try:
        scripts = json.loads(repo.files[path]).get("scripts") or {}
    except (ValueError, AttributeError):
        return None
    value = scripts.get(match["script"])
    return value if isinstance(value, str) else None


def _entry_file(command: str, code_dir: str, repo: Repo) -> str | None:
    for token in re.split(r"[\s\"']+", command):
        if re.search(r"\.(py|js|mjs|cjs|ts)$", token):
            for candidate in (norm_dir(code_dir, token), norm_dir(code_dir, posixpath.join("src", token))):
                if candidate in repo.files:
                    return candidate
        module = re.match(r"^([A-Za-z_][\w.]*):[A-Za-z_]\w*$", token)  # uvicorn app.main:app
        if module:
            candidate = norm_dir(code_dir, module.group(1).replace(".", "/") + ".py")
            if candidate in repo.files:
                return candidate
    return None


def _is_dockerfile(path: str) -> bool:
    base = posixpath.basename(path)
    return base == "Dockerfile" or base.startswith("Dockerfile.") or base.endswith(".Dockerfile")


def _port(port: Any) -> tuple[str | None, str | None]:
    if isinstance(port, dict):
        return (str(port["published"]) if port.get("published") else None, str(port.get("target") or "") or None)
    text = str(port).split("/")[0]
    parts = text.split(":")
    if len(parts) == 1:
        return None, parts[0]
    return parts[-2], parts[-1]


def _volume(volume: Any) -> tuple[str, str, bool]:
    if isinstance(volume, dict):
        return str(volume.get("source") or ""), str(volume.get("target") or ""), bool(volume.get("read_only"))
    parts = str(volume).split(":")
    if len(parts) == 1:
        return "", parts[0], False
    read_only = len(parts) > 2 and "ro" in parts[2].split(",")
    return parts[0], parts[1], read_only


def _ingress_backends(doc: Any) -> Iterator[str]:
    def walk(node: Any) -> Iterator[str]:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("serviceName",) and isinstance(value, str):
                    yield value
                elif key == "service" and isinstance(value, dict) and value.get("name"):
                    yield str(value["name"])
                elif key == "backendRefs" and isinstance(value, list):
                    yield from (str(v.get("name")) for v in value if isinstance(v, dict) and v.get("name"))
                elif key == "destination" and isinstance(value, dict) and value.get("host"):
                    yield str(value["host"]).split(".")[0]
                else:
                    yield from walk(value)
        elif isinstance(node, list):
            for item in node:
                yield from walk(item)

    yield from walk(doc.get("spec"))
