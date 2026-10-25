from __future__ import annotations

import sqlalchemy as sa
from alembic import context

from agent_harness.adapters.postgres.tables import metadata

config = context.config
if not config.get_main_option("sqlalchemy.url"):
    from agent_harness.config.settings import Settings

    config.set_main_option("sqlalchemy.url", Settings().database_url.replace("%", "%%"))

# psycopg 3 supports sync and async with the same `postgresql+psycopg://` URL, so migrations run
# synchronously and work whether or not an event loop is already running.
url = config.get_main_option("sqlalchemy.url")

if context.is_offline_mode():
    context.configure(url=url, target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = sa.create_engine(url, poolclass=sa.pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
