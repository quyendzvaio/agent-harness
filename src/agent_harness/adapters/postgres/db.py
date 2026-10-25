from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def create_engine(database_url: str, *, pool_size: int = 10) -> AsyncEngine:
    return create_async_engine(database_url, pool_size=pool_size, pool_pre_ping=True)


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def psycopg_conninfo(database_url: str) -> str:
    """SQLAlchemy URL -> libpq URL for psycopg/LangGraph (`postgresql+psycopg://` -> `postgresql://`)."""
    return sa.make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)


async def check_schema(engine: AsyncEngine, database_url: str) -> None:
    """Raise unless the database is reachable and migrated to head (used by /readyz)."""
    head = ScriptDirectory.from_config(alembic_config(database_url)).get_current_head()
    async with engine.connect() as conn:
        current = await conn.run_sync(lambda sync: MigrationContext.configure(sync).get_current_revision())
    if current != head:
        raise RuntimeError(f"database schema at revision {current!r}, expected {head!r}; run `agent-harness init`")
