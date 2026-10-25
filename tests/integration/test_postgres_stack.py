"""End-to-end on the production adapters: Postgres repositories + Postgres checkpointer + MinIO."""

from uuid import UUID

import pytest
from langchain_core.messages import AIMessage

from agent_harness.adapters.models.scripted import ScriptedChatModel, tool_call
from agent_harness.adapters.postgres.checkpointer import open_checkpointer, setup_checkpointer
from agent_harness.adapters.postgres.db import session_factory
from agent_harness.adapters.postgres.repositories import PostgresSourceCache, PostgresUnitOfWork
from builders import chat, client, data_of, make_container, make_settings
from conftest import needs_minio, needs_postgres
from fakes import REPO_URL

pytestmark = [needs_postgres, needs_minio, pytest.mark.postgres, pytest.mark.minio]


async def test_full_turn_with_export_on_real_infrastructure(pg_engine, migrated_database_url, minio_store):
    await setup_checkpointer(migrated_database_url)
    sessions = session_factory(pg_engine)
    report = "# tiny-api\n\nDùng FastAPI [E1]."
    model = ScriptedChatModel(
        responses=[
            tool_call("github_list_tree", {"url": REPO_URL}, "a"),
            tool_call("export_markdown", {"title": "tiny api", "content": report}, "b"),
            AIMessage("Đã phân tích và xuất file [E1]."),
            AIMessage("Lượt hai nhớ được lượt một."),
        ]
    )
    async with open_checkpointer(migrated_database_url, max_size=2) as checkpointer:
        container = await make_container(
            make_settings(database_url=migrated_database_url),
            chat_model=model,
            checkpointer=checkpointer,
            artifact_store=minio_store,
            uow_factory=lambda: PostgresUnitOfWork(sessions),
            source_cache=PostgresSourceCache(sessions),
        )
        async with client(container) as http:
            _, events = await chat(http, f"phân tích {REPO_URL} rồi xuất file md")
            conversation_id = data_of(events, "conversation")[0]["conversation_id"]
            artifact = data_of(events, "artifact")[0]
            assert (await http.get(f"/v1/artifacts/{artifact['id']}")).text == report
            _, events2 = await chat(http, "còn gì nữa?", conversation_id)
            messages = (await http.get(f"/v1/conversations/{conversation_id}/messages")).json()

    assert data_of(events2, "done")
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assert "phân tích" in model.seen[-1][1].content  # first user turn came back from Postgres checkpoints
    async with PostgresUnitOfWork(sessions) as uow:
        turn = await uow.turns.get(UUID(data_of(events, "conversation")[0]["turn_id"]))
        evidence = await uow.evidence.list(UUID(conversation_id))
        artifacts = await uow.artifacts.list(UUID(conversation_id))
    assert turn.status.value == "completed" and turn.explicit_export and turn.usage.tool_calls == 2
    assert [e.label for e in evidence] == ["E1"]
    assert artifacts[0].storage_uri.startswith(f"s3://{minio_store.bucket}/conversations/{conversation_id}/")
    await container.aclose()
