"""The five-part text of a system map and the diagram plan drawn from it."""

import pytest

from agent_harness.domain.errors import InvalidInput
from agent_harness.sources.github.system_map import build_system_map
from agent_harness.sources.github.system_map.render import SECTIONS, diagram_plan, header, render
from fixtures.system_map import compose_repo, docs_repo


class Cites:
    """Stands in for the evidence store: one label per locator, in order of first use."""

    def __init__(self) -> None:
        self.locators: dict[str, str] = {}

    def __call__(self, locator: str, quote: str) -> str:
        assert quote, locator
        return f"[{self.locators.setdefault(locator, f'E{len(self.locators) + 1}')}]"


@pytest.fixture(scope="module")
def compose():
    return build_system_map(compose_repo.FILES)


def test_overview_has_the_five_parts_in_order(compose):
    cites = Cites()
    text = render(compose, cites)
    assert text.startswith("Kiến trúc ĐƯỢC KHAI BÁO từ docker-compose.yml, docker-compose.full.yml. Chưa đối chiếu với runtime.")
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert [h[:5] for h in headings] == ["## 1.", "## 2.", "## 3.", "## 4."]
    assert "variant: base (docker-compose.yml), full (docker-compose.full.yml)" in text
    assert "| tên | loại | nơi chạy | entrypoint | bằng chứng | tin cậy |" in text
    assert "| tin cậy khớp | tin cậy địa chỉ |" in text  # the two confidence columns are separate
    assert "- Q9-I6: node redis (cache) không có cạnh nào" in text
    assert "frontend ↔ kafka" in text  # the README claim lands in the gaps


def test_every_row_cites_a_locator_that_is_not_a_file_read(compose):
    cites = Cites()
    render(compose, cites)
    assert cites.locators
    for locator in cites.locators:
        assert locator.endswith(")"), locator  # "(node)", "(channel: http)", "(store)", "(gap)"
        assert "-L" not in locator.split(" (")[0]  # never the `path:Lx-Ly` shape that counts as having read the file


def test_edge_rows_carry_both_ends_and_reasons(compose):
    cites = Cites()
    text = render(compose, cites, section="channels")
    row = next(line for line in text.splitlines() if line.startswith("| worker → api | http | POST /internal/enrichments"))
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert cells[3].endswith("services/worker/worker.py:L14") and cells[4].startswith("backend/app/routers/internal.py:L")
    assert cells[8] == "EXTRACTED" and cells[9] == "EXTRACTED" and cells[7].startswith("fail-open")
    browser = next(line for line in text.splitlines() if line.startswith("| frontend → api | http |"))
    assert "| EXTRACTED | INFERRED (`localhost:8000` → cổng do api publish" in browser
    label = cites.locators["services/worker/worker.py:L14 (channel: http)"]
    assert f"[{label}]" in row


def test_variant_and_process_filters(compose):
    base = render(compose, Cites(), section="channels", variant="base")
    assert "worker → api" not in base and "frontend → api" in base
    only_worker = render(compose, Cites(), section="channels", process="worker")
    assert "frontend → api" not in only_worker and "kafka → worker" in only_worker
    with pytest.raises(InvalidInput, match="unknown variant"):
        render(compose, Cites(), variant="prod")
    with pytest.raises(InvalidInput, match="no node named"):
        render(compose, Cites(), process="nothing")


def test_other_sections(compose):
    env = render(compose, Cites(), section="env", process="worker")
    assert "| worker | API_BASE_URL | http://api:8000 | compose |" in env
    stores = render(compose, Cites(), section="stores")
    assert "| topic:security_events | queue | api backend/app/services/kafka.py" in stores
    hypotheses = render(compose, Cites(), section="hypotheses")
    assert "[CHƯA KIỂM ĐƯỢC] “The frontend calls kafka directly to stream alerts.”" in hypotheses
    gaps_text = render(compose, Cites(), section="gaps", limit=5)
    assert "vi phạm / 10 bất biến đã kiểm" in gaps_text
    assert set(SECTIONS) == {"overview", "processes", "channels", "stores", "env", "gaps", "hypotheses"}


def test_repo_without_runtime_says_so():
    smap = build_system_map(docs_repo.FILES)
    assert header(smap).startswith("Không có topology runtime trong repo")
    assert "dev-host, external-hosted" in header(smap)
    with pytest.raises(InvalidInput, match="no runtime topology"):
        diagram_plan(smap, Cites())


def test_zero_gaps_is_stated_explicitly():
    files = {
        "docker-compose.yml": "services:\n  api:\n    build: .\n    ports: ['8000:8000']\n",
        "Dockerfile": 'CMD ["uvicorn", "app:app"]\n',
        "app.py": "from fastapi import FastAPI\napp = FastAPI()\n",
    }
    text = render(build_system_map(files), Cites())
    assert "Q9: 0 vi phạm / 10 bất biến đã kiểm." in text


def test_diagram_plan_numbers_edges_from_the_boundary_and_marks_inferred(compose):
    cites = Cites()
    nodes, edges = diagram_plan(compose, cites, variant="full")
    labels = {n.id: n.label for n in nodes}
    assert {labels[e.source] for e in edges if e.step in ("1", "2")} == {"user"}
    kinds = {n.label: n.kind for n in nodes}
    assert kinds["kafka"] == "queue" and kinds["postgres"] == "database" and kinds["user"] == "client"
    by_pair = {(labels[e.source], labels[e.target]): e for e in edges}
    assert by_pair[("api", "kafka")].is_async and not by_pair[("api", "kafka")].inferred
    browser = by_pair[("frontend", "api")]
    assert browser.inferred and browser.label.endswith("(?)") and len(browser.label) <= 60
    assert all(e.evidence and all(x.startswith("[E") for x in e.evidence) for e in edges)
    assert ("worker", "api") in by_pair
    base_nodes, base_edges = diagram_plan(compose, Cites(), variant="base")
    base_labels = {n.id: n.label for n in base_nodes}
    assert ("worker", "api") not in {(base_labels[e.source], base_labels[e.target]) for e in base_edges}
