"""Static, LLM-free summary of a repository's shape. Gives the model a map before it reads code."""

from __future__ import annotations

import json
import re
import tomllib
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from agent_harness.sources.github.dependency_graph import python_module_index

_LANGUAGES = {
    ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript", ".jsx": "JavaScript",
    ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin", ".rb": "Ruby", ".php": "PHP",
    ".cs": "C#", ".cpp": "C++", ".cc": "C++", ".c": "C", ".swift": "Swift", ".scala": "Scala",
}  # fmt: skip
#: Filename guesses, used only when nothing in the repo *declares* an entrypoint.
_ENTRYPOINT_NAMES = {"main.py", "__main__.py", "app.py", "manage.py", "cli.py", "server.py", "main.go", "main.rs", "index.ts", "index.js", "server.ts", "server.js"}
_TEST_MARKERS = ("tests/", "test/", "__tests__/", "spec/")
_IGNORED = ("node_modules/", "vendor/", ".git/", "dist/", "build/", ".venv/", "__pycache__/")
_MAIN_GUARD = re.compile(r"""^if\s+__name__\s*==\s*['"]__main__['"]\s*:""", re.M)
_GO_MAIN = re.compile(r"^package\s+main\b.*?^func\s+main\(\)", re.S | re.M)
_DOCKER_CMD = re.compile(r"^\s*(CMD|ENTRYPOINT)\s+(.+)$", re.M | re.I)


@dataclass(slots=True)
class ArchitectureSummary:
    languages: list[tuple[str, int]] = field(default_factory=list)
    top_level: list[str] = field(default_factory=list)
    packages: list[str] = field(default_factory=list)
    #: (location, how we know). Declared sources come first; filename guesses are marked as such.
    entrypoints: list[tuple[str, str]] = field(default_factory=list)
    test_dirs: list[str] = field(default_factory=list)
    dependencies: dict[str, list[str]] = field(default_factory=dict)
    file_count: int = 0

    def render(self) -> str:
        lines = [f"Files: {self.file_count}"]
        if self.languages:
            lines.append("Languages: " + ", ".join(f"{name} ({count})" for name, count in self.languages))
        lines.append("Top level: " + ", ".join(self.top_level))
        if self.packages:
            lines.append("Packages/modules: " + ", ".join(self.packages))
        if self.entrypoints:
            lines.append("Entrypoints:")
            lines += [f"  - {where} — {why}" for where, why in self.entrypoints]
        if self.test_dirs:
            lines.append("Tests: " + ", ".join(self.test_dirs))
        for source, deps in self.dependencies.items():
            lines.append(f"Dependencies ({source}): " + ", ".join(deps[:40]))
        return "\n".join(lines)


def analyze(tree: list[dict], files: Mapping[str, str]) -> ArchitectureSummary:
    """`files` holds whatever contents are available: the whole repo (snapshot) or just key files."""
    paths = [item["path"] for item in tree if item["type"] == "blob" and not item["path"].startswith(_IGNORED)]
    dirs = {item["path"] for item in tree if item["type"] == "tree"}
    summary = ArchitectureSummary(file_count=len(paths))
    summary.languages = Counter(
        _LANGUAGES[suffix] for f in paths if (suffix := PurePosixPath(f).suffix) in _LANGUAGES
    ).most_common(6)
    summary.top_level = sorted({f.split("/", 1)[0] + ("/" if "/" in f else "") for f in paths})[:30]
    summary.packages = sorted(
        str(PurePosixPath(f).parent) for f in paths if f.endswith("__init__.py") and f.count("/") <= 3
    )[:25] or sorted(d for d in dirs if d.count("/") == 1 and d.split("/")[0] in ("src", "lib", "pkg", "internal", "app"))[:25]
    summary.entrypoints = find_entrypoints(set(paths), files)
    summary.test_dirs = sorted({f.split("/")[0] + "/" for f in paths if any(m in f + "/" for m in _TEST_MARKERS)})[:10]
    summary.dependencies = _dependencies({p: c for p, c in files.items() if "/" not in p})
    return summary


def find_entrypoints(paths: set[str], files: Mapping[str, str]) -> list[tuple[str, str]]:
    """Entrypoints the repository declares, strongest evidence first; filename guesses only as fallback."""
    found: list[tuple[str, str]] = []
    py_index = python_module_index(paths)

    def py_target(spec: str) -> str:
        module = spec.split(":", 1)[0].strip()
        return py_index.get(module) or f"{module} (module not found in repo)"

    pyproject = files.get("pyproject.toml")
    if pyproject:
        try:
            data = tomllib.loads(pyproject)
        except tomllib.TOMLDecodeError:
            data = {}
        project = data.get("project", {})
        for table, label in ((project.get("scripts", {}), "[project.scripts]"), (project.get("gui-scripts", {}), "[project.gui-scripts]"),
                             (data.get("tool", {}).get("poetry", {}).get("scripts", {}), "[tool.poetry.scripts]")):  # fmt: skip
            for name, spec in table.items():
                if isinstance(spec, str):
                    found.append((py_target(spec), f"declared in pyproject.toml {label}: `{name} = {spec}`"))
    package_json = files.get("package.json")
    if package_json:
        try:
            data = json.loads(package_json)
        except ValueError:
            data = {}
        if isinstance(data.get("main"), str):
            found.append((data["main"].lstrip("./"), "declared in package.json `main`"))
        bins = data.get("bin")
        for name, target in ({data.get("name", "bin"): bins} if isinstance(bins, str) else bins or {}).items():
            found.append((str(target).lstrip("./"), f"declared in package.json `bin.{name}`"))
        for script in ("start", "dev", "serve"):
            command = data.get("scripts", {}).get(script)
            if isinstance(command, str):
                found.append((command, f"package.json `scripts.{script}`"))
    for path in sorted(paths):
        name = PurePosixPath(path).name
        content = files.get(path)
        if content is None:
            continue
        if name == "Dockerfile" or name.endswith(".Dockerfile"):
            for kind, command in _DOCKER_CMD.findall(content):
                found.append((command.strip(), f"{path} {kind.upper()}"))
        elif name == "Procfile":
            for line in content.splitlines():
                if ":" in line:
                    found.append((line.split(":", 1)[1].strip(), f"{path} `{line.split(':', 1)[0].strip()}`"))
        elif name == "__main__.py":
            found.append((path, "`python -m` package entrypoint (__main__.py)"))
        elif path.endswith(".py") and _MAIN_GUARD.search(content):
            found.append((path, "has `if __name__ == \"__main__\":`"))
        elif path.endswith(".go") and _GO_MAIN.search(content):
            found.append((path, "Go `package main` with `func main()`"))
    if not found:
        found = [
            (f, "guess from filename — verify by reading it")
            for f in sorted(paths)
            if PurePosixPath(f).name in _ENTRYPOINT_NAMES or f.startswith("cmd/")
        ]
    unique: dict[str, str] = {}
    for where, why in found:
        unique.setdefault(where, why)
    return list(unique.items())[:20]


def _dependencies(key_files: dict[str, str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path, content in key_files.items():
        name = PurePosixPath(path).name
        try:
            if name == "pyproject.toml":
                data = tomllib.loads(content)
                deps = data.get("project", {}).get("dependencies", [])
                found[path] = [re.split(r"[ <>=!~;\[]", d, maxsplit=1)[0] for d in deps]
            elif name == "package.json":
                data = json.loads(content)
                found[path] = sorted({**data.get("dependencies", {}), **data.get("devDependencies", {})})
            elif name == "requirements.txt":
                found[path] = [
                    re.split(r"[ <>=!~;\[]", line.strip(), maxsplit=1)[0]
                    for line in content.splitlines()
                    if line.strip() and not line.startswith(("#", "-"))
                ]
            elif name == "go.mod":
                found[path] = re.findall(r"^\s*([\w./-]+\.[\w./-]+)\s+v", content, flags=re.M)
            elif name == "Cargo.toml":
                found[path] = sorted(tomllib.loads(content).get("dependencies", {}))
        except (ValueError, tomllib.TOMLDecodeError):
            continue
    return {k: v for k, v in found.items() if v}
