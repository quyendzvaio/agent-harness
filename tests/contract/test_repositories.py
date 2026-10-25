"""Scenario S8: the memory and Postgres repositories honour the same contract."""

from uuid import uuid4

import pytest

from agent_harness.domain.conversation import Artifact, Conversation, Message, Role, ToolInvocation, Turn, TurnStatus, Usage
from agent_harness.domain.evidence import Evidence
from agent_harness.domain.sources import SourceKind, SourceRef

SOURCE = SourceRef(SourceKind.GITHUB, "https://github.com/acme/tiny-api", "github:acme/tiny-api")


async def _seed(uow_factory):
    conversation = Conversation(title="first")
    turn = Turn(conversation_id=conversation.id, explicit_export=True)
    async with uow_factory() as uow:
        await uow.conversations.add(conversation)
        await uow.turns.add(turn)
        await uow.commit()
    return conversation, turn


async def test_conversation_turn_message_roundtrip(uow_factory):
    conversation, turn = await _seed(uow_factory)
    async with uow_factory() as uow:
        await uow.messages.add(Message(conversation.id, Role.USER, "hi", turn_id=turn.id))
        await uow.messages.add(Message(conversation.id, Role.ASSISTANT, "hello", turn_id=turn.id, metadata={"k": 1}))
        turn.status = TurnStatus.COMPLETED
        turn.usage = Usage(model_calls=2, tool_calls=3, tokens_in=10, tokens_out=20, model="m")
        turn.metadata = {"artifacts": [{"id": "x"}]}
        await uow.turns.update(turn)
        await uow.conversations.touch(conversation.id, title="renamed")
        await uow.commit()

    async with uow_factory() as uow:
        stored = await uow.turns.get(turn.id)
        messages = await uow.messages.list(conversation.id)
        listed = await uow.conversations.list()
        fetched = await uow.conversations.get(conversation.id)
    assert stored.status is TurnStatus.COMPLETED and stored.usage.tool_calls == 3 and stored.usage.model == "m"
    assert stored.explicit_export is True and stored.metadata == {"artifacts": [{"id": "x"}]}
    assert [(m.role, m.content) for m in messages] == [(Role.USER, "hi"), (Role.ASSISTANT, "hello")]
    assert messages[1].metadata == {"k": 1}
    assert fetched.title == "renamed" and [c.id for c in listed] == [conversation.id]


async def test_uncommitted_work_is_discarded(uow_factory):
    conversation = Conversation()
    async with uow_factory() as uow:
        await uow.conversations.add(conversation)
        assert await uow.conversations.get(conversation.id) is not None  # visible inside the unit of work
    async with uow_factory() as uow:
        assert await uow.conversations.get(conversation.id) is None


async def test_exception_rolls_back(uow_factory):
    conversation = Conversation()
    with pytest.raises(RuntimeError):
        async with uow_factory() as uow:
            await uow.conversations.add(conversation)
            raise RuntimeError("boom")
    async with uow_factory() as uow:
        assert await uow.conversations.get(conversation.id) is None


async def test_turn_requires_existing_conversation(uow_factory):
    with pytest.raises(Exception):  # noqa: B017 - FK violation type differs per backend
        async with uow_factory() as uow:
            await uow.turns.add(Turn(conversation_id=uuid4()))
            await uow.commit()


async def test_evidence_is_ordered_and_labels_unique_per_conversation(uow_factory):
    conversation, turn = await _seed(uow_factory)
    items = [Evidence(f"E{i}", SOURCE, f"src/a.py:L{i}-L{i + 1}", f"quote {i}") for i in (1, 2, 10)]
    async with uow_factory() as uow:
        await uow.evidence.add_many(conversation.id, turn.id, list(reversed(items)))
        await uow.commit()
    async with uow_factory() as uow:
        assert [e.label for e in await uow.evidence.list(conversation.id)] == ["E1", "E2", "E10"]
        assert (await uow.evidence.list(conversation.id))[0].source == SOURCE
    with pytest.raises(Exception):  # noqa: B017
        async with uow_factory() as uow:
            await uow.evidence.add_many(conversation.id, turn.id, [items[0]])
            await uow.commit()


async def test_tool_invocations_and_artifacts(uow_factory):
    conversation, turn = await _seed(uow_factory)
    artifact = Artifact(conversation.id, turn.id, "r.md", "text/markdown", "s3://b/k/r.md", 3, "abc")
    async with uow_factory() as uow:
        await uow.tool_invocations.add_many([ToolInvocation(turn.id, "load_skill", {"name": "x"}, "ok", 5)])
        await uow.artifacts.add(artifact)
        await uow.commit()
    async with uow_factory() as uow:
        invocations = await uow.tool_invocations.list(turn.id)
        assert invocations[0].input == {"name": "x"} and invocations[0].duration_ms == 5
        assert (await uow.artifacts.get(artifact.id)).storage_key == "k/r.md"
        assert [a.id for a in await uow.artifacts.list(conversation.id)] == [artifact.id]
        assert await uow.artifacts.get(uuid4()) is None


async def test_source_cache_ttl(source_cache):
    await source_cache.put("k", kind="github", url="u", payload={"a": 1}, ttl_s=60)
    assert await source_cache.get("k") == {"a": 1}
    await source_cache.put("k", kind="github", url="u", payload={"a": 2}, ttl_s=60)
    assert await source_cache.get("k") == {"a": 2}
    await source_cache.put("old", kind="blog", url="u", payload={"x": 1}, ttl_s=-1)
    assert await source_cache.get("old") is None
    assert await source_cache.get("missing") is None
