"""Deterministic internal dependency graph: who imports whom.

Python is parsed with `ast`; JavaScript/TypeScript and Go imports are read with regular
expressions and resolved against the repository's own files. Dynamic imports, path aliases
(tsconfig `paths`) and plugin registries are not resolved; they are counted as unresolved.
"""

from __future__ import annotations

import ast
import posixpath
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from agent_harness.sources.github.snapshot import is_ignored

PY = (".py",)
JS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
GO = (".go",)
_JS_PATTERNS = [
    re.compile(r"""(?:^|[\s;])(?:import|export)\s[^'"`;]*?\sfrom\s*['"]([^'"]+)['"]""", re.M),
    re.compile(r"""(?:^|[\s;])import\s*['"]([^'"]+)['"]""", re.M),
    re.compile(r"""\brequire\(\s*['"]([^'"]+)['"]\s*\)"""),
    re.compile(r"""\bimport\(\s*['"]([^'"]+)['"]\s*\)"""),
]
_GO_BLOCK = re.compile(r"^import\s*\(\s*(.*?)\)", re.S | re.M)
_GO_SINGLE = re.compile(r'^import\s+(?:[\w.]+\s+)?"([^"]+)"', re.M)
_GO_SPEC = re.compile(r'(?:[\w.]+\s+)?"([^"]+)"')
_GO_MODULE = re.compile(r"^module\s+(\S+)", re.M)
_STDLIB = set(sys.stdlib_module_names)


def package_of(path: str) -> str:
    """Directory of a file (or a directory node such as Go packages, which end with '/')."""
    if path.endswith("/"):
        return path.rstrip("/") or "."
    parent = str(PurePosixPath(path).parent)
    return "." if parent in ("", ".") else parent


@dataclass(slots=True)
class DependencyGraph:
    files_by_language: Counter[str] = field(default_factory=Counter)
    #: internal file -> internal files (or Go package directories ending with '/')
    edges: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    #: external package -> number of files importing it
    external: Counter[str] = field(default_factory=Counter)
    unresolved: Counter[str] = field(default_factory=Counter)
    parse_errors: list[str] = field(default_factory=list)

    @property
    def nodes(self) -> set[str]:
        return set(self.edges) | {t for targets in self.edges.values() for t in targets}

    def fan_in(self) -> Counter[str]:
        counts: Counter[str] = Counter()
        for source, targets in self.edges.items():
            for target in targets:
                if target != source:
                    counts[target] += 1
        return counts

    def package_edges(self) -> dict[str, Counter[str]]:
        result: dict[str, Counter[str]] = defaultdict(Counter)
        for source, targets in self.edges.items():
            src = package_of(source)
            result.setdefault(src, Counter())
            for target in targets:
                dst = package_of(target)
                if dst != src:
                    result[src][dst] += 1
        return result

    def package_cycles(self) -> list[list[str]]:
        graph = {k: set(v) for k, v in self.package_edges().items()}
        return [sorted(c) for c in _strongly_connected(graph) if len(c) > 1]

    def render(self, *, scope: str = "", level: str = "package", limit: int = 150) -> str:
        scope = scope.strip("/")
        inside = (lambda p: p == scope or p.startswith(scope + "/")) if scope else (lambda p: True)
        languages = ", ".join(f"{k} {v}" for k, v in self.files_by_language.most_common()) or "none"
        lines = [
            f"Static import analysis — files: {languages}; internal edges: {sum(len(t) for t in self.edges.values())}; "
            f"unresolved imports: {sum(self.unresolved.values())}",
        ]
        if level == "file":
            lines.append("File → imports (internal):")
            for source in sorted(s for s in self.edges if inside(s)):
                targets = sorted(self.edges[source])
                lines.append(f"  {source} → {', '.join(targets) if targets else '(none)'}")
        else:
            lines.append("Package (directory) → depends on (×number of file-level imports):")
            for source, targets in sorted(self.package_edges().items()):
                if not inside(source):
                    continue
                rendered = ", ".join(f"{t} ×{n}" for t, n in targets.most_common()) or "(no internal dependencies)"
                lines.append(f"  {source} → {rendered}")
        fan_in = [(p, n) for p, n in self.fan_in().most_common(15) if inside(p)]
        if fan_in:
            lines.append("Most depended-on (fan-in): " + ", ".join(f"{p} ({n})" for p, n in fan_in))
        cycles = self.package_cycles()
        lines.append("Package cycles: " + ("; ".join(" ↔ ".join(c) for c in cycles) if cycles else "none"))
        if self.external:
            lines.append("External packages (files importing): " + ", ".join(f"{k} ({v})" for k, v in self.external.most_common(30)))
        if self.unresolved:
            lines.append("Unresolved (aliases/dynamic): " + ", ".join(f"{k} ({v})" for k, v in self.unresolved.most_common(10)))
        if self.parse_errors:
            lines.append(f"Could not parse: {', '.join(self.parse_errors[:10])}")
        if len(lines) > limit:
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines; narrow with scope=)"]
        return "\n".join(lines)


def build_dependency_graph(files: Mapping[str, str]) -> DependencyGraph:
    graph = DependencyGraph()
    paths = {p for p in files if not is_ignored(p)}
    py_index = python_module_index(paths)
    py_roots = {name.split(".")[0] for name in py_index}
    go_module = _go_module(files)
    for path in sorted(paths):
        suffix = PurePosixPath(path).suffix
        source = files[path]
        if suffix in PY:
            graph.files_by_language["Python"] += 1
            graph.edges.setdefault(path, set())
            _python_imports(graph, path, source, py_index, py_roots)
        elif suffix in JS:
            graph.files_by_language["JS/TS"] += 1
            graph.edges.setdefault(path, set())
            _js_imports(graph, path, source, paths)
        elif suffix in GO and not path.endswith("_test.go"):
            graph.files_by_language["Go"] += 1
            graph.edges.setdefault(path, set())
            _go_imports(graph, path, source, go_module, paths)
    return graph


# --- Python ------------------------------------------------------------------------------------


SOURCE_ROOTS = ("src", "lib", "python", "py")


def python_module_index(paths: set[str]) -> dict[str, str]:
    """Dotted module name -> file.

    A file can be importable under several names; all plausible ones are indexed, strongest first:
    1. the path from the repository root (namespace packages without `__init__.py`, PEP 420);
    2. the path from a conventional source root (`src/`, `lib/`, ...);
    3. the path from the nearest ancestor that is not a regular package (`__init__.py` chain).
    Names from rule 3 never shadow the standard library (a nested `types.py` is not `types`).
    """
    packages = {str(PurePosixPath(p).parent) for p in paths if p.endswith("__init__.py")}
    strong: dict[str, str] = {}
    weak: dict[str, str] = {}
    for path in sorted(paths):
        if not path.endswith(".py"):
            continue
        pure = PurePosixPath(path)
        parts = list(pure.parent.parts) if pure.name == "__init__.py" else list(pure.with_suffix("").parts)
        if not parts:
            continue
        strong.setdefault(".".join(parts), path)
        if parts[0] in SOURCE_ROOTS and len(parts) > 1:
            strong.setdefault(".".join(parts[1:]), path)
        root = len(pure.parent.parts)
        while root > 0 and "/".join(pure.parent.parts[:root]) in packages:
            root -= 1
        name = ".".join(parts[root:])
        if name and (root == 0 or name.split(".")[0] not in _STDLIB):
            weak.setdefault(name, path)
    return {**weak, **strong}


def _python_imports(graph: DependencyGraph, path: str, source: str, index: dict[str, str], roots: set[str]) -> None:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        graph.parse_errors.append(path)
        return
    own = _own_module(path, index)
    package = own if path.endswith("__init__.py") else own.rpartition(".")[0]
    externals: set[str] = set()
    for node in _import_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = _resolve_python(alias.name, index)
                if target:
                    _add(graph, path, target)
                else:
                    externals.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = package.split(".") if package else []
                if node.level > 1:
                    anchor = anchor[: max(0, len(anchor) - (node.level - 1))]
                base = ".".join([*anchor, base] if base else anchor)
            # `from pkg import mod` imports the submodule; `from pkg import func` imports pkg itself.
            targets: set[str] = set()
            needs_base = False
            for alias in node.names:
                submodule = index.get(f"{base}.{alias.name}" if base else alias.name)
                if submodule:
                    targets.add(submodule)
                else:
                    needs_base = True
            if needs_base and base:
                resolved = index.get(base)
                if resolved:
                    targets.add(resolved)
                elif node.level or base.split(".")[0] in roots:
                    graph.unresolved["." * node.level + (node.module or "")] += 1  # internal but not found
                else:
                    externals.add(base.split(".")[0])
            for target in targets:
                _add(graph, path, target)
    for name in externals - _STDLIB - {"__future__"}:
        graph.external[name] += 1


def _own_module(path: str, index: dict[str, str]) -> str:
    """Name used to resolve relative imports: the longest (most qualified) name of the file."""
    names = [m for m, p in index.items() if p == path] if len(index) < 2_000 else _reverse(index).get(path, [])
    return max(names, key=lambda m: m.count("."), default="")


_REVERSE_CACHE: dict[int, dict[str, list[str]]] = {}


def _reverse(index: dict[str, str]) -> dict[str, list[str]]:
    key = id(index)
    if key not in _REVERSE_CACHE:
        _REVERSE_CACHE.clear()
        reverse: dict[str, list[str]] = defaultdict(list)
        for module, file in index.items():
            reverse[file].append(module)
        _REVERSE_CACHE[key] = reverse
    return _REVERSE_CACHE[key]


_BLOCK_FIELDS = ("body", "orelse", "finalbody", "handlers", "cases")


def _import_nodes(tree: ast.Module):
    """Import statements at any nesting depth (functions, if/try blocks), without visiting expressions."""
    stack = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node
            continue
        for name in _BLOCK_FIELDS:
            children = getattr(node, name, None)
            if children:
                stack.extend(children)


def _add(graph: DependencyGraph, source: str, target: str) -> None:
    if target != source:
        graph.edges[source].add(target)


def _resolve_python(name: str, index: dict[str, str]) -> str | None:
    parts = name.split(".")
    for end in range(len(parts), 0, -1):
        found = index.get(".".join(parts[:end]))
        if found:
            return found
    return None


# --- JavaScript / TypeScript ---------------------------------------------------------------------


def _js_imports(graph: DependencyGraph, path: str, source: str, paths: set[str]) -> None:
    seen_external: set[str] = set()
    for pattern in _JS_PATTERNS:
        for spec in pattern.findall(source):
            if spec.startswith("."):
                target = _resolve_js(posixpath.normpath(posixpath.join(posixpath.dirname(path), spec)), paths)
                if target:
                    if target != path:
                        graph.edges[path].add(target)
                else:
                    graph.unresolved[spec] += 1
            elif spec.startswith(("@/", "~/", "#")):
                graph.unresolved[spec.split("/")[0] + "/…"] += 1
            else:
                name = "/".join(spec.split("/")[:2]) if spec.startswith("@") else spec.split("/")[0]
                if name.startswith("node:"):
                    continue
                if name not in seen_external:
                    seen_external.add(name)
                    graph.external[name] += 1


def _resolve_js(base: str, paths: set[str]) -> str | None:
    if base in paths:
        return base
    for ext in JS:
        if base + ext in paths:
            return base + ext
    stem = base.rsplit(".", 1)[0]  # "./x.js" written in TS sources that resolve to x.ts
    for ext in JS:
        if stem + ext in paths:
            return stem + ext
    for ext in JS:
        if f"{base}/index{ext}" in paths:
            return f"{base}/index{ext}"
    return None


# --- Go ---------------------------------------------------------------------------------------------


def _go_module(files: Mapping[str, str]) -> str | None:
    match = _GO_MODULE.search(files.get("go.mod", ""))
    return match.group(1) if match else None


def _go_imports(graph: DependencyGraph, path: str, source: str, module: str | None, paths: set[str]) -> None:
    specs = _GO_SINGLE.findall(source)
    for block in _GO_BLOCK.findall(source):
        specs += _GO_SPEC.findall(block)
    own_dir = package_of(path)
    for spec in specs:
        if module and (spec == module or spec.startswith(module + "/")):
            directory = spec[len(module) :].strip("/") or "."
            if directory != own_dir and any(package_of(p) == directory for p in paths if p.endswith(".go")):
                graph.edges[path].add(directory + "/")
            elif directory != own_dir:
                graph.unresolved[spec] += 1
        elif "." in spec.split("/")[0]:
            graph.external["/".join(spec.split("/")[:3])] += 1


def _strongly_connected(graph: dict[str, set[str]]) -> list[list[str]]:
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    result: list[list[str]] = []
    counter = 0

    def visit(node: str) -> None:
        nonlocal counter
        index[node] = low[node] = counter
        counter += 1
        stack.append(node)
        on_stack.add(node)
        for nxt in graph.get(node, ()):
            if nxt not in index:
                visit(nxt)
                low[node] = min(low[node], low[nxt])
            elif nxt in on_stack:
                low[node] = min(low[node], index[nxt])
        if low[node] == index[node]:
            component = []
            while True:
                item = stack.pop()
                on_stack.discard(item)
                component.append(item)
                if item == node:
                    break
            result.append(component)

    sys.setrecursionlimit(max(sys.getrecursionlimit(), 10_000))
    for node in list(graph):
        if node not in index:
            visit(node)
    return result
