"""Scenario S10: memory and MinIO artifact stores honour the same contract."""

import hashlib

import pytest

from agent_harness.domain.errors import NotFound


async def test_put_open_exists_delete(artifact_store):
    data = ("# Báo cáo\n" * 20_000).encode()  # > one read chunk
    stored = await artifact_store.put("conversations/c1/a1/report.md", data, "text/markdown")
    assert stored.size_bytes == len(data) and stored.sha256 == hashlib.sha256(data).hexdigest()
    assert stored.uri.endswith("/conversations/c1/a1/report.md")
    assert await artifact_store.exists("conversations/c1/a1/report.md")
    assert b"".join([chunk async for chunk in artifact_store.open("conversations/c1/a1/report.md")]) == data
    await artifact_store.delete("conversations/c1/a1/report.md")
    assert not await artifact_store.exists("conversations/c1/a1/report.md")


async def test_missing_object(artifact_store):
    assert not await artifact_store.exists("nope")
    with pytest.raises(NotFound):
        [chunk async for chunk in artifact_store.open("nope")]


async def test_health(artifact_store):
    await artifact_store.health()
