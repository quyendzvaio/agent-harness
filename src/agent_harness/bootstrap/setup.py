from __future__ import annotations

import asyncio
from collections.abc import Callable

from agent_harness.config.settings import Settings


async def initialize(settings: Settings, report: Callable[[str], None] = print) -> None:
    """Idempotent provisioning: app schema (Alembic), checkpoint schema, artifact bucket."""
    from alembic import command

    from agent_harness.adapters.postgres.checkpointer import CHECKPOINT_SCHEMA, setup_checkpointer
    from agent_harness.adapters.postgres.db import alembic_config
    from agent_harness.adapters.storage.minio_artifact_store import MinioArtifactStore

    await asyncio.to_thread(command.upgrade, alembic_config(settings.database_url), "head")
    report("database: migrated to head")
    await setup_checkpointer(settings.database_url)
    report(f"checkpoints: schema '{CHECKPOINT_SCHEMA}' ready")
    store = MinioArtifactStore.connect(
        settings.minio_endpoint,
        settings.minio_access_key.get_secret_value(),
        settings.minio_secret_key.get_secret_value(),
        settings.minio_bucket,
        secure=settings.minio_secure,
    )
    await store.ensure_bucket()
    report(f"artifacts: bucket '{settings.minio_bucket}' ready")
