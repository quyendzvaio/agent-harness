"""build_system_map on three small repos (compose, k8s, docs) plus the deterministic helpers behind it."""

import time

import pytest

from agent_harness.sources.github.system_map import build_system_map
from agent_harness.sources.github.system_map.catalog import load_catalog
from agent_harness.sources.github.system_map.channels import norm_path, paths_match
from agent_harness.sources.github.system_map.envchain import parse_addresses, resolve_host
from agent_harness.sources.github.system_map.manifests import interpolate, parse_dockerfile
from agent_harness.sources.github.system_map.model import EXTRACTED, INFERRED, Ev, Process, SystemMap
from fixtures.system_map import compose_repo, docs_repo, k8s_repo


@pytest.fixture(scope="module")
def compose():
    return build_system_map(compose_repo.FILES)


@pytest.fixture(scope="module")
def k8s():
    return build_system_map(k8s_repo.FILES)


def channel(smap, src, dst, kind=None, detail=None):
    found = [c for c in smap.channels if c.src == src and c.dst == dst and (kind is None or c.kind == kind)
             and (detail is None or c.detail == detail)]  # fmt: skip
    assert found, f"no channel {src} -> {dst} {kind or ''} {detail or ''}"
    return found[0]


def gaps(smap, rule):
    return [g.detail for g in smap.gaps if g.rule == rule]


# --- Q0-Q2: manifests -----------------------------------------------------------------------------------------


def test_compose_variant_is_merged_onto_the_base(compose):
    assert compose.manifests[:2] == ["docker-compose.yml", "docker-compose.full.yml"]
    assert compose.variants == {"base": ["docker-compose.yml"], "full": ["docker-compose.full.yml"]}
    api = compose.processes["api"]
    assert api.variant == "base" and "KAFKA_TOPIC" in api.env  # added by the full variant
    assert compose.processes["worker"].variant == "full" and compose.processes["kafka"].variant == "full"


def test_roles_come_from_entrypoints_and_images(compose):
    kinds = {name: (p.kind, p.where) for name, p in compose.processes.items()}
    assert kinds["api"] == ("http-server", "manifest")  # Dockerfile CMD: sh -c "uvicorn ... ${PORT}"
    assert kinds["frontend"] == ("web", "manifest")  # npm run start -> package.json "next start"
    assert kinds["worker"] == ("worker", "manifest")  # python worker.py + AIOKafkaConsumer in the entry file
    assert kinds["postgres"] == ("db", "manifest") and kinds["redis"] == ("cache", "manifest")
    assert kinds["kafka"] == ("queue", "manifest")
    assert compose.processes["api"].published == ["8000"] and compose.processes["api"].code_dir == "backend"


def test_scripts_that_write_a_store_are_batch_jobs_and_the_rest_is_tooling(compose):
    seed = compose.processes["script:scripts/seed.py"]
    assert (seed.kind, seed.where, seed.confidence) == ("batch-job", "manual", INFERRED)
    assert not any("smoke" in name for name in compose.processes)


def test_code_nobody_starts_is_undeployed(compose):
    undeployed = compose.processes["undeployed"]
    assert undeployed.where == "undeployed" and undeployed.code_dir == "tools/undeployed"
    assert any("tools/undeployed" in g for g in gaps(compose, "Q9-I7"))


def test_dockerfile_exec_and_shell_forms():
    exec_form = parse_dockerfile("Dockerfile", 'ENV PORT=8000\nEXPOSE 8000\nCMD ["sh", "-c", "uvicorn app:app --port ${PORT}"]\n')
    assert exec_form.cmd == "uvicorn app:app --port ${PORT}" and exec_form.env["PORT"][0] == "8000"
    assert exec_form.expose == ["8000"] and exec_form.cmd_line == 3
    shell_form = parse_dockerfile("Dockerfile", "ENTRYPOINT python\nCMD worker.py\n")
    assert shell_form.cmd is not None and "worker.py" in shell_form.cmd


def test_compose_interpolation():
    assert interpolate("${X:-fallback}", {}) == ("fallback", False, "X không đặt → mặc định trong manifest")
    value, known, note = interpolate("${SMTP_HOST:-}", {"SMTP_HOST": "smtp.example.com"})
    assert (value, known) == ("smtp.example.com", False) and ".env mẫu" in note


def test_k8s_workloads_get_their_code_dirs_from_skaffold(k8s):
    names = {n for n, p in k8s.processes.items() if p.where == "manifest"}
    assert names == {"frontend", "checkoutservice", "shippingservice", "cartservice", "loadgenerator", "redis-cart"}
    assert k8s.processes["checkoutservice"].code_dir == "src/checkoutservice"
    assert k8s.processes["redis-cart"].kind == "cache"
    assert k8s.processes["loadgenerator"].kind == "agent"  # locust
    assert channel(k8s, "user", "frontend").detail == "ports/ingress"  # LoadBalancer Service


def test_docs_repo_has_no_runtime_topology():
    smap = build_system_map(docs_repo.FILES)
    assert not smap.has_runtime
    wheres = {p.where for p in smap.processes.values()}
    assert wheres == {"dev-host", "external-hosted"}
    assert smap.processes["github-pages"].confidence == INFERRED


# --- Q3: environment chains -----------------------------------------------------------------------------------


def test_env_chain_from_code_to_manifest_to_host(compose):
    binding = next(b for b in compose.bindings if b.process == "worker" and b.var == "API_BASE_URL")
    assert (binding.target, binding.kind, binding.confidence, binding.source) == ("api", "http", EXTRACTED, "compose")
    assert binding.read_ev.path == "services/worker/worker.py"
    # BaseSettings field database_url -> DATABASE_URL, set by compose to the postgres service
    db = next(b for b in compose.bindings if b.process == "api" and b.var == "DATABASE_URL")
    assert (db.target, db.kind, db.confidence) == ("postgres", "db", EXTRACTED)
    assert db.read_ev.path == "backend/app/core/config.py"


def test_localhost_and_env_example_values_are_inferred(compose):
    browser = next(b for b in compose.bindings if b.process == "frontend" and b.var == "NEXT_PUBLIC_API_BASE_URL")
    assert (browser.target, browser.confidence) == ("api", INFERRED) and "8000" in browser.note
    smtp = next(b for b in compose.bindings if b.process == "api" and b.var == "SMTP_HOST")
    assert smtp.target == "smtp.example.com" and smtp.confidence == INFERRED  # from .env.example only


def test_address_parsing_and_host_resolution():
    assert [(a.scheme, a.host, a.port) for a in parse_addresses("DATABASE_URL", "postgresql://u:p@db:5432/x")] == [("postgresql", "db", "5432")]
    assert [(a.host, a.port) for a in parse_addresses("CART_SERVICE_ADDR", "cartservice:7070")] == [("cartservice", "7070")]
    assert parse_addresses("LISTEN_ADDR", "0.0.0.0:8080") == []  # a listen address, not a connection
    smap = SystemMap(processes={"api": Process("api", "http-server", "manifest", Ev("c.yml", 2), published=["8000"])})
    ev = Ev("c.yml", 9)
    assert resolve_host(parse_addresses("X", "http://api:8000")[0], smap, ev).confidence == EXTRACTED
    local = resolve_host(parse_addresses("X", "http://localhost:8000")[0], smap, ev)
    assert local.target.name == "api" and local.confidence == INFERRED
    assert resolve_host(parse_addresses("X", "http://localhost:9999")[0], smap, ev).target is None
    assert resolve_host(parse_addresses("X", "https://api.openai.com/v1")[0], smap, ev).target.kind == "external"
    assert resolve_host(parse_addresses("X", "http://nowhere:1")[0], smap, ev).target is None


def test_unset_variables_without_default_are_gaps(compose):
    assert not gaps(compose, "Q9-I1")  # every variable the fixture reads is set or defaulted
    smap = build_system_map({**compose_repo.FILES, "services/worker/extra.py": 'import os\nTOKEN = os.environ["WORKER_TOKEN"]\n'})
    assert any("WORKER_TOKEN" in g for g in gaps(smap, "Q9-I1"))


# --- Q4: both ends of a channel -------------------------------------------------------------------------------


def test_http_client_matches_router_prefix_and_path(compose):
    edge = channel(compose, "worker", "api", "http", "POST /internal/enrichments")
    assert (edge.match, edge.addr) == (EXTRACTED, EXTRACTED)
    assert edge.client_ev.path == "services/worker/worker.py" and edge.server_ev.path == "backend/app/routers/internal.py"
    assert edge.on_failure.startswith("fail-open") and "timeout=10" in edge.on_failure


def test_dead_client_call_is_a_gap_not_an_edge(compose):
    assert not [c for c in compose.channels if c.detail == "GET /internal/nope"]
    assert any("/internal/nope" in g for g in gaps(compose, "Q9-I3"))


def test_browser_calls_and_sse_have_inferred_addresses(compose):
    http = channel(compose, "frontend", "api", "http", "GET /public")
    sse = channel(compose, "frontend", "api", "realtime", "GET /stream/events")
    assert (http.match, http.addr) == (EXTRACTED, INFERRED) and sse.sync == "push"


def test_queue_topic_matched_through_settings_and_env(compose):
    produce = channel(compose, "api", "kafka", "queue")
    consume = channel(compose, "kafka", "worker", "queue")
    assert "security_events" in produce.detail and "security_events" in consume.detail
    assert produce.sync == consume.sync == "async" and produce.variant == "full"
    topic = compose.stores["topic:security_events"]
    assert set(topic.writers) == {"api"} and set(topic.readers) == {"worker"}


def test_grpc_needs_proto_client_server_and_address(k8s):
    edge = channel(k8s, "checkoutservice", "shippingservice", "grpc")
    assert edge.detail == "rpc ShippingService" and (edge.match, edge.addr) == (EXTRACTED, EXTRACTED)
    assert edge.client_ev.path == "src/checkoutservice/main.go" and edge.server_ev.path == "src/shippingservice/main.go"
    # generated stubs (genproto/*.pb.go) in every service declare every client and server: never edges
    pairs = {(c.src, c.dst) for c in k8s.channels if c.kind == "grpc"}
    assert pairs == {("checkoutservice", "shippingservice"), ("frontend", "checkoutservice"), ("frontend", "cartservice")}


def test_address_in_a_manifest_command_is_an_inferred_edge(k8s):
    edge = channel(k8s, "loadgenerator", "frontend", "http")
    assert (edge.match, edge.addr) == (INFERRED, EXTRACTED) and "command" in edge.reason


def test_route_paths_normalise_parameters():
    assert norm_path("/alerts/{alert_id}/triage?x=1") == "/alerts/{}/triage"
    assert paths_match("/alerts/42", "/alerts/:id") and paths_match("/a/${id}", "/a/[id]")
    assert not paths_match("/a/b", "/a")


# --- Q5, Q7, Q8 -----------------------------------------------------------------------------------------------


def test_store_writers_and_readers(compose, k8s):
    postgres = compose.stores["postgres"]
    assert set(postgres.writers) == {"api", "script:scripts/seed.py"} and set(postgres.readers) == {"api"}
    assert any("shared mutable state" in g for g in gaps(compose, "Q5"))
    redis = k8s.stores["redis-cart"]
    assert set(redis.writers) == {"cartservice"} and set(redis.readers) == {"cartservice"}  # C# SetAsync/GetAsync


def test_process_signals(compose):
    notes = " ".join(compose.processes["api"].notes)
    assert "stateful" in notes and "_producer" in notes
    assert "GET /public" in notes and "POST /internal/enrichments" not in notes.split("auth dependency:")[-1]
    assert any("không chờ healthy" in n for n in compose.processes["frontend"].notes)
    assert not any("postgres" in n and "healthy" in n for n in compose.processes["api"].notes)  # condition: service_healthy


# --- Q9 and documentation claims ------------------------------------------------------------------------------


def test_closure_invariants(compose):
    assert compose.invariants_checked == 10
    assert any("redis" in g for g in gaps(compose, "Q9-I6"))  # declared, nobody connects
    assert gaps(compose, "Q9-I8")  # HTTP between our own processes with no shared schema


def test_depends_on_cycle_is_a_gap():
    files = {"docker-compose.yml": "services:\n  a:\n    image: busybox\n    depends_on: [b]\n  b:\n    image: busybox\n    depends_on: [a]\n"}
    assert any("a → b → a" in g or "b → a → b" in g for g in gaps(build_system_map(files), "Q9-I9"))


def test_readme_claims_are_hypotheses(compose):
    claims = {claim: verified for claim, _, verified in compose.hypotheses}
    assert claims == {"The frontend calls kafka directly to stream alerts.": False}
    assert any("frontend ↔ kafka" in g for g in gaps(compose, "claim"))


def test_catalog_parses():
    catalog = load_catalog()
    assert catalog.image_kind("postgres:16-alpine") == "db" and catalog.image_kind("bitnami/redis:7") == "cache"
    assert catalog.role("celery -A app worker") == "worker" and catalog.role("uvicorn app:app") == "http-server"


def test_large_repo_builds_in_seconds():
    files = dict(compose_repo.FILES)
    for i in range(3_000):
        files[f"backend/app/mods/mod_{i}.py"] = f"import os\n\nVALUE_{i} = os.getenv('V_{i}', 'x')\n\n\ndef f_{i}():\n    return {i}\n"
    started = time.perf_counter()
    smap = build_system_map(files)
    assert time.perf_counter() - started < 3.0
    assert "api" in smap.processes
