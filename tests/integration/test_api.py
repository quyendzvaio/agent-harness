from uuid import UUID, uuid4

from agent_harness.harness.scripted import TextStep, ToolStep
from builders import chat, client, data_of, kinds, make_container, make_settings
from fakes import BLOG_URL, REPO_URL

GITHUB_SCRIPT = [
    ToolStep("load_skill", {"name": "github-architecture"}),
    ToolStep("github_repo_overview", {"url": REPO_URL}),
    ToolStep("github_read_file", {"url": REPO_URL, "path": "src/tiny_api/main.py"}),
    TextStep("Service tạo ứng dụng FastAPI và gắn router ghi chú vào app [E4]."),
]


async def test_github_turn_streams_events_and_persists():
    container = await make_container(script=GITHUB_SCRIPT)
    async with client(container) as http:
        response, events = await chat(http, f"phân tích kiến trúc {REPO_URL}")
        assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
        assert kinds(events)[0] == "conversation" and kinds(events)[-1] == "done"
        assert [s["name"] for s in data_of(events, "skill_loaded")] == ["github-architecture"]
        finished = data_of(events, "tool_finished")
        assert [f["status"] for f in finished] == ["ok", "ok", "ok"]
        assert data_of(events, "citation_report")[0]["valid"] == ["E4"]
        assert "artifact" not in kinds(events)

        conversation_id = data_of(events, "conversation")[0]["conversation_id"]
        messages = (await http.get(f"/v1/conversations/{conversation_id}/messages")).json()
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert "[E4]" in messages[1]["content"]

    turn = container.db.turns[UUID(data_of(events, "conversation")[0]["turn_id"])]
    assert turn.status.value == "completed" and turn.active_skill == "github-architecture"
    assert turn.usage.tool_calls == 3
    assert [i.tool_name for i in container.db.tool_invocations] == ["load_skill", "github_repo_overview", "github_read_file"]


async def test_follow_up_turn_continues_evidence_labels():
    first = [ToolStep("github_list_tree", {"url": REPO_URL}), TextStep("Cây thư mục có src và tests [E1].")]
    second = [ToolStep("github_read_file", {"url": REPO_URL, "path": "src/tiny_api/store.py"}), TextStep("NoteStore lưu ghi chú trong list [E2], còn cây thư mục ở [E1].")]  # fmt: skip
    scripts = iter([first, second])
    container = await make_container(script=lambda ctx: next(scripts))
    async with client(container) as http:
        _, events = await chat(http, f"xem {REPO_URL}")
        conversation_id = data_of(events, "conversation")[0]["conversation_id"]
        _, events2 = await chat(http, "store lưu dữ liệu ở đâu?", conversation_id)
    assert data_of(events2, "tool_finished")[0]["evidence_labels"] == ["E2"]
    assert data_of(events2, "citation_report")[0] == {"valid": ["E2", "E1"], "invalid": [], "uncited_sentences": 0, "total_sentences": 1}
    async with client(container) as http:
        evidence = (await http.get(f"/v1/conversations/{conversation_id}/evidence")).json()
        missing = await http.get(f"/v1/conversations/{uuid4()}/evidence")
    assert [e["label"] for e in evidence] == ["E1", "E2"]
    assert evidence[1]["kind"] == "github" and evidence[1]["locator"].startswith("src/tiny_api/store.py:L")
    assert "NoteStore" in evidence[1]["quote"] and missing.status_code == 404


async def test_blog_turn_marks_untrusted_content():
    container = await make_container(
        script=[ToolStep("load_skill", {"name": "blog-explainer"}), ToolStep("web_fetch_article", {"url": BLOG_URL}), TextStep("ok")]
    )
    async with client(container) as http:
        _, events = await chat(http, f"giải thích {BLOG_URL}")
    assert [f["status"] for f in data_of(events, "tool_finished")] == ["ok", "ok"]
    assert len(data_of(events, "tool_finished")[1]["evidence_labels"]) >= 4


async def test_unknown_conversation_is_404():
    container = await make_container(script=[TextStep("x")])
    async with client(container) as http:
        response, _ = await chat(http, "hi", str(uuid4()))
    assert response.status_code == 404 and response.json()["error"]["code"] == "not_found"


async def test_export_flow_and_download():
    report = "# Báo cáo tiny-api\n\nDùng FastAPI [E1]."
    container = await make_container(
        script=[ToolStep("github_list_tree", {"url": REPO_URL}), ToolStep("export_markdown", {"title": "Báo cáo tiny-api", "content": report}), TextStep("Đã xuất file [E1].")]  # fmt: skip
    )
    async with client(container) as http:
        _, events = await chat(http, f"phân tích {REPO_URL} và xuất file md")
        artifact = data_of(events, "artifact")[0]
        assert artifact["filename"] == "bao-cao-tiny-api.md"
        download = await http.get(f"/v1/artifacts/{artifact['id']}")
        assert download.status_code == 200 and download.text == report
        assert "bao-cao-tiny-api.md" in download.headers["content-disposition"]
        conversation_id = data_of(events, "conversation")[0]["conversation_id"]
        listed = (await http.get(f"/v1/conversations/{conversation_id}/artifacts")).json()
        assert [a["id"] for a in listed] == [artifact["id"]]
        assert (await http.get(f"/v1/artifacts/{uuid4()}")).status_code == 404


async def test_auth_enabled_requires_keys():
    settings = make_settings(auth_enabled=True, api_key="user-key", admin_api_key="admin-key")
    container = await make_container(settings, script=[TextStep("x")])
    async with client(container) as http:
        assert (await http.get("/v1/skills")).status_code == 401
        assert (await http.get("/v1/skills", headers={"X-API-Key": "wrong"})).status_code == 401
        assert (await http.get("/v1/skills", headers={"X-API-Key": "user-key"})).status_code == 200
        response, _ = await chat(http, "hi")
        assert response.status_code == 401
        assert (await http.post("/v1/skills/reload", headers={"X-API-Key": "user-key"})).status_code == 401
        assert (await http.post("/v1/skills/reload", headers={"X-API-Key": "admin-key"})).status_code == 200
        assert (await http.get("/healthz")).status_code == 200


def test_unauthenticated_bind_outside_loopback_is_refused():
    import pytest

    with pytest.raises(ValueError):
        make_settings().check_bind("0.0.0.0")
    make_settings().check_bind("127.0.0.1")
    make_settings(auth_enabled=True, api_key="a", admin_api_key="b").check_bind("0.0.0.0")
    with pytest.raises(ValueError):
        make_settings(auth_enabled=True)  # keys missing


async def test_readyz_reports_dependencies():
    container = await make_container(script=[TextStep("x")])
    async with client(container) as http:
        ok = await http.get("/readyz")
        assert ok.status_code == 200 and ok.json()["checks"] == {"artifact_store": "ok", "skills": "ok"}
        container.artifacts._store.healthy = False
        down = await http.get("/readyz")
        assert down.status_code == 503 and down.json()["checks"]["artifact_store"].startswith("error")


async def test_skills_endpoints():
    container = await make_container(script=[TextStep("x")])
    async with client(container) as http:
        names = [s["name"] for s in (await http.get("/v1/skills")).json()]
        assert names == ["blog-explainer", "export-report", "github-architecture", "github-deep-dive", "github-system-architecture"]
        detail = (await http.get("/v1/skills/github-architecture")).json()
        assert detail["hooks_enabled"] and "github_repo_overview" in detail["instructions"]
        assert (await http.get("/v1/skills/nope")).status_code == 404
