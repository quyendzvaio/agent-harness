"""Snapshot, dependency graph, declared entrypoints, search and coverage."""

import io
import tarfile

import pytest

from agent_harness.domain.errors import CapabilityUnavailable, InvalidInput, ToolFailed
from agent_harness.sources.github.architecture_analyzer import find_entrypoints
from agent_harness.sources.github.dependency_graph import build_dependency_graph, python_module_index
from agent_harness.sources.github.detector import parse_github_url
from agent_harness.sources.github.repository_reader import RepositoryReader
from agent_harness.sources.github.snapshot import RepositorySnapshot
from fakes import REPO_URL, FakeGithubClient, make_tarball

TARGET = parse_github_url(REPO_URL)


# --- snapshot -------------------------------------------------------------------------------------


def test_snapshot_strips_prefix_skips_binary_large_and_vendored():
    data = make_tarball(
        {"a.py": "x = 1\n", "img.png": b"\x89PNG\x00\x00data", "big.txt": "y" * 600_000, "node_modules/x/i.js": "1"}
    )
    snap = RepositorySnapshot.from_tarball(data, "main")
    assert snap.files == {"a.py": "x = 1\n"}
    assert snap.skipped == {"img.png": "binary", "big.txt": "larger than 512000 bytes"}
    assert snap.ignored_count == 1
    assert {i["path"] for i in snap.tree() if i["type"] == "blob"} == {"a.py", "img.png", "big.txt"}


def test_snapshot_ignores_symlinks_and_rejects_garbage():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        link = tarfile.TarInfo("p/evil")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        archive.addfile(link)
    assert RepositorySnapshot.from_tarball(buffer.getvalue(), "main").files == {}
    with pytest.raises(ToolFailed):
        RepositorySnapshot.from_tarball(b"not a tarball", "main")


# --- dependency graph -----------------------------------------------------------------------------------


def test_python_module_index_handles_src_layout_namespaces_and_scripts():
    paths = {"src/pkg/__init__.py", "src/pkg/core/model.py", "scripts/run.py", "setup.py",
             "nspkg/types.py", "nspkg/sub/__init__.py", "backend/app/__init__.py", "backend/app/main.py"}  # fmt: skip
    index = python_module_index(paths)
    assert index["pkg"] == "src/pkg/__init__.py" and index["pkg.core.model"] == "src/pkg/core/model.py"
    assert index["run"] == "scripts/run.py" and index["setup"] == "setup.py"
    assert index["nspkg.types"] == "nspkg/types.py"  # PEP 420 namespace package
    assert "types" not in index  # a nested types.py must not shadow the standard library
    assert index["app.main"] == "backend/app/main.py"  # import root found via the __init__ chain


def test_python_graph_absolute_relative_external_and_cycles():
    files = {
        "src/app/__init__.py": "",
        "src/app/api/__init__.py": "",
        "src/app/api/routes.py": "from ..core import service\nfrom fastapi import APIRouter\nimport os\n",
        "src/app/core/__init__.py": "",
        "src/app/core/service.py": "from app.core.model import Note\nfrom . import model\nimport app.api.routes\n",
        "src/app/core/model.py": "import pydantic\nfrom .missing import x\n",
        "src/app/broken.py": "def (:\n",
    }
    graph = build_dependency_graph(files)
    assert graph.edges["src/app/api/routes.py"] == {"src/app/core/service.py"}
    assert graph.edges["src/app/core/service.py"] == {"src/app/core/model.py", "src/app/api/routes.py"}
    assert graph.external == {"fastapi": 1, "pydantic": 1}  # os is stdlib
    assert graph.unresolved == {".missing": 1}
    assert graph.parse_errors == ["src/app/broken.py"]
    assert ["src/app/api", "src/app/core"] in graph.package_cycles()
    assert graph.fan_in()["src/app/core/model.py"] == 1


def test_js_ts_graph_resolves_relative_index_and_extensionless():
    files = {
        "web/src/index.ts": "import { App } from './app'\nimport React from 'react'\nimport x from '@/alias'\n",
        "web/src/app.tsx": "import { api } from './lib'\nconst z = require('./util.js')\nimport '@scope/pkg/sub'\n",
        "web/src/lib/index.ts": "export const api = 1\n",
        "web/src/util.ts": "export default 1\n",
    }
    graph = build_dependency_graph(files)
    assert graph.edges["web/src/index.ts"] == {"web/src/app.tsx"}
    assert graph.edges["web/src/app.tsx"] == {"web/src/lib/index.ts", "web/src/util.ts"}
    assert graph.external == {"react": 1, "@scope/pkg": 1}
    assert graph.unresolved == {"@/…": 1}


def test_go_graph_uses_module_path():
    files = {
        "go.mod": "module github.com/acme/svc\n\ngo 1.22\n",
        "cmd/svc/main.go": 'package main\n\nimport (\n  "fmt"\n  "github.com/acme/svc/internal/store"\n  chi "github.com/go-chi/chi/v5"\n)\n\nfunc main() {}\n',
        "internal/store/store.go": "package store\n",
    }
    graph = build_dependency_graph(files)
    assert graph.edges["cmd/svc/main.go"] == {"internal/store/"}
    assert graph.external == {"github.com/go-chi/chi": 1}
    assert "cmd/svc → internal/store ×1" in graph.render()


# --- entrypoints --------------------------------------------------------------------------------------


def test_declared_entrypoints_beat_filename_guesses():
    files = {
        "pyproject.toml": '[project.scripts]\nmycli = "pkg.cli:main"\n',
        "package.json": '{"name": "w", "main": "./dist/index.js", "bin": {"w": "bin/w.js"}, "scripts": {"start": "node server.js"}}',
        "Dockerfile": 'FROM python\nCMD ["uvicorn", "pkg.web:app"]\n',
        "Procfile": "web: gunicorn pkg.web:app\n",
        "pkg/__init__.py": "", "pkg/cli.py": "def main(): ...\n",
        "tools/gen.py": 'if __name__ == "__main__":\n    run()\n',
        "app.py": "print('not an entrypoint, just named like one')\n",
    }  # fmt: skip
    found = dict(find_entrypoints(set(files), files))
    assert found["pkg/cli.py"].startswith("declared in pyproject.toml [project.scripts]")
    assert found["dist/index.js"] == "declared in package.json `main`"
    assert found["bin/w.js"] == "declared in package.json `bin.w`"
    assert found["node server.js"] == "package.json `scripts.start`"
    assert found['["uvicorn", "pkg.web:app"]'] == "Dockerfile CMD"
    assert found["gunicorn pkg.web:app"] == "Procfile `web`"
    assert found["tools/gen.py"].startswith("has `if __name__")
    assert "app.py" not in found  # filename guesses are only a fallback


def test_filename_guess_is_labelled_when_nothing_is_declared():
    assert find_entrypoints({"src/main.py", "lib/x.py"}, {}) == [("src/main.py", "guess from filename — verify by reading it")]


# --- reader: search, coverage, fallback ---------------------------------------------------------------------


async def test_search_literal_regex_glob_and_context():
    reader = RepositoryReader(FakeGithubClient())
    result = await reader.search(TARGET, "notestore")
    assert [(h.path, h.line) for h in result.hits] == [
        ("src/tiny_api/routes.py", 3), ("src/tiny_api/routes.py", 6), ("src/tiny_api/store.py", 1), ("tests/test_store.py", 1),
        ("tests/test_store.py", 5),
    ]  # fmt: skip
    assert ">" in result.hits[0].snippet and result.hits[0].start == 2
    only_tests = await reader.search(TARGET, r"def \w+\(", regex=True, path_glob="tests/*")
    assert [(h.path, h.line) for h in only_tests.hits] == [("tests/test_store.py", 4)]
    with pytest.raises(InvalidInput):
        await reader.search(TARGET, "(", regex=True)


async def test_snapshot_is_downloaded_once_and_cached():
    client = FakeGithubClient()
    reader = RepositoryReader(client)
    await reader.overview(TARGET)
    await reader.read_file(TARGET, "src/tiny_api/main.py")
    await reader.search(TARGET, "x")
    await reader.dependency_graph(TARGET)
    assert client.calls.count("tarball main") == 1
    assert not any(c.startswith("file ") for c in client.calls)  # no per-file API calls


async def test_fallback_without_snapshot_reads_files_but_search_is_unavailable():
    class TooLarge(FakeGithubClient):
        async def get_tarball(self, *args, **kwargs):
            raise ToolFailed("response exceeds 60000000 bytes")

    reader = RepositoryReader(TooLarge())
    overview = await reader.overview(TARGET)
    assert overview.complete is False and overview.readme.startswith("# tiny-api")
    assert (await reader.read_file(TARGET, "src/tiny_api/store.py")).total_lines == 7
    with pytest.raises(CapabilityUnavailable):
        await reader.search(TARGET, "x")


async def test_coverage_counts_read_lines():
    reader = RepositoryReader(FakeGithubClient())
    coverage = await reader.coverage(TARGET, {"src/tiny_api/store.py": set(range(1, 8)), "README.md": {1}})
    assert len(coverage.source_files) == 5 and coverage.read == {"src/tiny_api/store.py": 7}
    assert coverage.read_ratio == pytest.approx(0.2)
