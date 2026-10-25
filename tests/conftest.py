from __future__ import annotations

import os
import uuid

import pytest
import sqlalchemy as sa

from agent_harness.adapters.memory.artifact_store import MemoryArtifactStore
from agent_harness.adapters.memory.repositories import MemoryDatabase, MemoryUnitOfWork

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
TEST_MINIO_ENDPOINT = os.environ.get("TEST_MINIO_ENDPOINT")

needs_postgres = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set (docker compose up postgres-test)")
needs_minio = pytest.mark.skipif(not TEST_MINIO_ENDPOINT, reason="TEST_MINIO_ENDPOINT not set (docker compose up minio)")

_APP_TABLES = ("tool_invocations", "evidence", "artifacts", "messages", "turns", "conversations", "sources")


@pytest.fixture(scope="session")
def migrated_database_url() -> str:
    """Migrate the test database to head once per session (fresh schema)."""
    assert TEST_DATABASE_URL
    from alembic import command

    from agent_harness.adapters.postgres.db import alembic_config

    sync_url = sa.make_url(TEST_DATABASE_URL)
    engine = sa.create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;"))
        conn.execute(sa.text("DROP SCHEMA IF EXISTS langgraph CASCADE;"))
    engine.dispose()
    command.upgrade(alembic_config(TEST_DATABASE_URL), "head")
    return TEST_DATABASE_URL


@pytest.fixture
async def pg_engine(migrated_database_url):
    from agent_harness.adapters.postgres.db import create_engine

    engine = create_engine(migrated_database_url, pool_size=2)
    async with engine.begin() as conn:
        await conn.execute(sa.text(f"TRUNCATE {', '.join(_APP_TABLES)} CASCADE"))
    yield engine
    await engine.dispose()


# The backend-selecting fixtures are sync so they can resolve async fixtures via getfixturevalue.
@pytest.fixture(params=["memory", pytest.param("postgres", marks=[needs_postgres, pytest.mark.postgres])])
def uow_factory(request):
    if request.param == "memory":
        db = MemoryDatabase()
        return lambda: MemoryUnitOfWork(db)
    from agent_harness.adapters.postgres.db import session_factory
    from agent_harness.adapters.postgres.repositories import PostgresUnitOfWork

    sessions = session_factory(request.getfixturevalue("pg_engine"))
    return lambda: PostgresUnitOfWork(sessions)


@pytest.fixture(params=["memory", pytest.param("postgres", marks=[needs_postgres, pytest.mark.postgres])])
def source_cache(request):
    if request.param == "memory":
        from agent_harness.adapters.memory.repositories import MemorySourceCache

        return MemorySourceCache()
    from agent_harness.adapters.postgres.db import session_factory
    from agent_harness.adapters.postgres.repositories import PostgresSourceCache

    return PostgresSourceCache(session_factory(request.getfixturevalue("pg_engine")))


@pytest.fixture(params=["memory", pytest.param("minio", marks=[needs_minio, pytest.mark.minio])])
def artifact_store(request):
    if request.param == "memory":
        return MemoryArtifactStore()
    return request.getfixturevalue("minio_store")


@pytest.fixture
async def minio_store():
    from agent_harness.adapters.storage.minio_artifact_store import MinioArtifactStore

    store = MinioArtifactStore.connect(
        TEST_MINIO_ENDPOINT,
        os.environ.get("TEST_MINIO_ACCESS_KEY", "minioadmin"),
        os.environ.get("TEST_MINIO_SECRET_KEY", "minioadmin"),
        f"artifacts-test-{uuid.uuid4().hex[:8]}",
    )
    await store.ensure_bucket()
    yield store
    await store.remove_bucket()
