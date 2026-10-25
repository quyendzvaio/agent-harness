from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = sa.MetaData(
    naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)

_ts = sa.DateTime(timezone=True)


def _fk(table: str) -> sa.ForeignKey:
    return sa.ForeignKey(f"{table}.id", ondelete="CASCADE")


conversations = sa.Table(
    "conversations",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("title", sa.Text, nullable=False, server_default=""),
    sa.Column("created_at", _ts, nullable=False),
    sa.Column("updated_at", _ts, nullable=False, index=True),
    sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
)

turns = sa.Table(
    "turns",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("conversation_id", UUID(as_uuid=True), _fk("conversations"), nullable=False, index=True),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("explicit_export", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("active_skill", sa.Text),
    sa.Column("model_calls", sa.Integer, nullable=False, server_default="0"),
    sa.Column("tool_calls", sa.Integer, nullable=False, server_default="0"),
    sa.Column("tokens_in", sa.Integer, nullable=False, server_default="0"),
    sa.Column("tokens_out", sa.Integer, nullable=False, server_default="0"),
    sa.Column("model", sa.Text),
    sa.Column("started_at", _ts, nullable=False),
    sa.Column("finished_at", _ts),
    sa.Column("error", JSONB),
    sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
)

messages = sa.Table(
    "messages",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("conversation_id", UUID(as_uuid=True), _fk("conversations"), nullable=False),
    sa.Column("turn_id", UUID(as_uuid=True), _fk("turns")),
    sa.Column("role", sa.Text, nullable=False),
    sa.Column("content", sa.Text, nullable=False),
    sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
    sa.Column("created_at", _ts, nullable=False),
    sa.Index("ix_messages_conversation_created", "conversation_id", "created_at"),
)

evidence = sa.Table(
    "evidence",
    metadata,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("conversation_id", UUID(as_uuid=True), _fk("conversations"), nullable=False),
    sa.Column("turn_id", UUID(as_uuid=True), _fk("turns"), nullable=False),
    sa.Column("label", sa.Text, nullable=False),
    sa.Column("ordinal", sa.Integer, nullable=False),
    sa.Column("source_kind", sa.Text, nullable=False),
    sa.Column("source_url", sa.Text, nullable=False),
    sa.Column("source_canonical_id", sa.Text, nullable=False),
    sa.Column("locator", sa.Text, nullable=False),
    sa.Column("quote", sa.Text, nullable=False),
    sa.UniqueConstraint("conversation_id", "label", name="uq_evidence_conversation_label"),
)

tool_invocations = sa.Table(
    "tool_invocations",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("turn_id", UUID(as_uuid=True), _fk("turns"), nullable=False, index=True),
    sa.Column("tool_name", sa.Text, nullable=False),
    sa.Column("input", JSONB, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("duration_ms", sa.Integer, nullable=False),
    sa.Column("error", JSONB),
    sa.Column("created_at", _ts, nullable=False),
)

artifacts = sa.Table(
    "artifacts",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("conversation_id", UUID(as_uuid=True), _fk("conversations"), nullable=False, index=True),
    sa.Column("turn_id", UUID(as_uuid=True), _fk("turns"), nullable=False),
    sa.Column("filename", sa.Text, nullable=False),
    sa.Column("media_type", sa.Text, nullable=False),
    sa.Column("storage_uri", sa.Text, nullable=False),
    sa.Column("size_bytes", sa.BigInteger, nullable=False),
    sa.Column("sha256", sa.Text, nullable=False),
    sa.Column("created_at", _ts, nullable=False),
)

sources = sa.Table(
    "sources",
    metadata,
    sa.Column("cache_key", sa.Text, primary_key=True),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("url", sa.Text, nullable=False),
    sa.Column("payload", JSONB, nullable=False),
    sa.Column("fetched_at", _ts, nullable=False),
    sa.Column("expires_at", _ts, nullable=False, index=True),
)
