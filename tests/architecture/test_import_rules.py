"""Dependency rules from PLAN §3, enforced on every import statement (including function-local ones)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "agent_harness"
PKG = "agent_harness"

FRAMEWORKS = {"langchain", "langchain_core", "langchain_openai", "openai", "langgraph"}
WEB = {"fastapi", "starlette", "uvicorn"}
DB = {"sqlalchemy", "psycopg", "psycopg_pool", "alembic"}
GRAPH = {"graphify", "networkx"}
INFRA = FRAMEWORKS | WEB | DB | GRAPH | {"httpx", "minio"}

# group -> (allowed internal prefixes, forbidden third-party top-level packages)
RULES: dict[str, tuple[set[str], set[str]]] = {
    "domain": ({"domain"}, INFRA),
    "ports": ({"domain", "ports"}, INFRA),
    "config": ({"domain", "config"}, INFRA),
    "harness": ({"domain", "ports", "harness"}, INFRA),
    "skills": ({"domain", "ports", "skills", "harness.observability"}, INFRA),
    "research": ({"domain", "ports", "research"}, INFRA),
    "sources": ({"domain", "ports", "sources", "harness.observability"}, FRAMEWORKS | WEB | DB | GRAPH | {"minio"}),
    "capabilities": ({"domain", "ports", "harness", "skills", "sources", "research", "capabilities"}, INFRA),
    "application": ({"domain", "ports", "harness", "application"}, INFRA),
    "adapters.langchain": ({"domain", "ports", "harness", "adapters.langchain"}, WEB | DB | GRAPH | {"minio"}),
    "adapters.models": ({"adapters.models"}, WEB | DB | GRAPH | {"minio"}),
    "adapters.graphify": ({"domain", "ports", "adapters.graphify"}, FRAMEWORKS | WEB | DB | {"httpx", "minio"}),
    "adapters.memory": ({"domain", "ports", "adapters.memory"}, INFRA),
    "adapters.storage": ({"domain", "ports", "adapters.storage"}, FRAMEWORKS | WEB | DB | GRAPH),
    "adapters.postgres": ({"domain", "ports", "config", "adapters.postgres"}, WEB | GRAPH | {"minio", "langchain", "langchain_core"}),
    "api": ({"domain", "application", "api", "config"}, FRAMEWORKS | DB | GRAPH | {"httpx", "minio"}),
    "interfaces": ({"config", "bootstrap", "harness.observability", "interfaces"}, FRAMEWORKS | DB | GRAPH | {"minio"}),
    "adapters": (set(), INFRA),  # the bare package __init__
    "bootstrap": (set(), set()),  # composition root: may import anything
}
#: Only these modules may talk to LangChain/LangGraph at all.
LANGGRAPH_ALLOWED = ("adapters.langchain", "adapters.models", "adapters.postgres.checkpointer", "bootstrap")


def _modules() -> list[tuple[str, Path]]:
    out = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).with_suffix("")
        parts = [p for p in rel.parts if p != "__init__"]
        if "builtin" in parts:  # skill hook files are data, loaded by path
            continue
        out.append((".".join(parts), path))
    return out


def _group(module: str) -> str | None:
    matches = [g for g in RULES if module == g or module.startswith(g + ".")]
    return max(matches, key=len) if matches else None


def _imports(path: Path) -> list[str]:
    names = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
    return names


def _violations() -> list[str]:
    problems = []
    for module, path in _modules():
        group = _group(module)
        if group is None or group == "bootstrap":
            continue
        allowed, forbidden = RULES[group]
        for name in _imports(path):
            top = name.split(".")[0]
            if top == PKG:
                target = name.removeprefix(PKG + ".")
                if not any(target == a or target.startswith(a + ".") for a in allowed):
                    problems.append(f"{module} imports {name} (allowed: {sorted(allowed)})")
            elif top in forbidden:
                problems.append(f"{module} imports third-party '{top}'")
            if top in FRAMEWORKS and not any(module == a or module.startswith(a + ".") for a in LANGGRAPH_ALLOWED):
                problems.append(f"{module} imports '{top}'; only {LANGGRAPH_ALLOWED} may")
    return sorted(set(problems))


def test_every_module_belongs_to_a_layer():
    orphans = [m for m, _ in _modules() if m and _group(m) is None]
    assert orphans == []


def test_dependency_rules():
    assert _violations() == []


def test_github_and_blog_sources_are_independent():
    for module, path in _modules():
        for name in _imports(path):
            if module.startswith("sources.github"):
                assert "sources.blog" not in name, f"{module} imports {name}"
            if module.startswith("sources.blog"):
                assert "sources.github" not in name, f"{module} imports {name}"


def test_rules_catch_a_violation(tmp_path, monkeypatch):
    """The checker itself works: a planted bad import is reported."""
    fake_src = tmp_path / "agent_harness"
    (fake_src / "application").mkdir(parents=True)
    (fake_src / "application" / "bad.py").write_text("import sqlalchemy\nfrom agent_harness.adapters.postgres import db\n")
    monkeypatch.setitem(globals(), "SRC", fake_src)
    found = _violations()
    assert any("third-party 'sqlalchemy'" in p for p in found)
    assert any("imports agent_harness.adapters.postgres" in p for p in found)


@pytest.mark.parametrize("module", ["domain", "ports", "harness", "application", "api"])
def test_core_layers_import_without_infrastructure(module):
    """Importing a core layer must not drag in LangChain or SQLAlchemy at runtime."""
    import subprocess
    import sys

    code = (
        f"import sys, importlib, pkgutil; import agent_harness.{module} as p\n"
        f"[importlib.import_module(m.name) for m in pkgutil.walk_packages(p.__path__, p.__name__ + '.')]\n"
        "bad = sorted(k for k in sys.modules if k.split('.')[0] in {'langchain','langgraph','sqlalchemy','minio','psycopg','graphify','networkx'})\n"
        "print(bad); raise SystemExit(1 if bad else 0)"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
