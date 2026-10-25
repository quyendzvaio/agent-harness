"""LangGraph conversation checkpoints in their own schema (`langgraph`), separate from app tables."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from agent_harness.adapters.postgres.db import psycopg_conninfo

CHECKPOINT_SCHEMA = "langgraph"


@asynccontextmanager
async def open_checkpointer(database_url: str, *, max_size: int = 10) -> AsyncIterator[AsyncPostgresSaver]:
    pool = AsyncConnectionPool(
        psycopg_conninfo(database_url),
        min_size=1,
        max_size=max_size,
        open=False,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": f"-c search_path={CHECKPOINT_SCHEMA}",
        },
    )
    await pool.open()
    try:
        yield AsyncPostgresSaver(pool)  # type: ignore[arg-type]
    finally:
        await pool.close()


async def setup_checkpointer(database_url: str) -> None:
    async with await psycopg.AsyncConnection.connect(psycopg_conninfo(database_url), autocommit=True) as conn:
        await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{CHECKPOINT_SCHEMA}"')
    async with open_checkpointer(database_url, max_size=1) as saver:
        await saver.setup()
