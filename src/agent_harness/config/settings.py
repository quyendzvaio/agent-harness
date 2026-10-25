from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from agent_harness.domain.policies import ExecutionLimits

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_nested_delimiter="__", extra="ignore", case_sensitive=False
    )

    # --- server -----------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"

    # --- auth (PLAN §4.6) ---------------------------------------------------
    auth_enabled: bool = False
    api_key: SecretStr | None = None
    admin_api_key: SecretStr | None = None

    # --- persistence ----------------------------------------------------------
    database_url: str = "postgresql+psycopg://agent:agent@localhost:5433/agent"
    db_pool_size: int = 10

    # --- artifacts (MinIO) ------------------------------------------------------
    minio_endpoint: str = "localhost:9000"
    minio_access_key: SecretStr = SecretStr("minioadmin")
    minio_secret_key: SecretStr = SecretStr("minioadmin")
    minio_bucket: str = "artifacts"
    minio_secure: bool = False

    # --- model (any OpenAI-compatible endpoint) ---------------------------------------------
    llm_api_key: SecretStr | None = None
    llm_base_url: str = Field(
        "https://api.groq.com/openai/v1", validation_alias=AliasChoices("llm_base_url", "base_url")
    )
    llm_model: str = "qwen/qwen3.8-27b"
    llm_fallback_models: str = ""
    model_temperature: float = 0.2
    #: Retries of a model call that failed transiently (connection dropped mid-stream, timeout, 429, 5xx).
    model_max_retries: int = Field(2, ge=0, le=5)
    #: Cap on one model call's output; ends a repetition loop the guard misses. None = provider default.
    model_max_output_tokens: int | None = Field(8192, ge=256)
    #: Abort and retry a call whose output leaks the model's reasoning or loops (adapters/models/output_guard.py).
    model_output_guard: bool = True
    #: `reasoning_effort` for models that support thinking (values are provider-specific); None = default.
    model_reasoning_effort: str | None = None
    #: Clear old tool outputs from the model context above this many tokens (0 = never). Evidence and
    #: `save_note` notes survive, so citations stay valid during long repository reads.
    context_clear_trigger_tokens: int = 100_000

    # --- sources & skills -----------------------------------------------------------
    github_token: SecretStr | None = None
    enabled_sources: str = "github,blog"
    allowed_hosts: str = ""
    source_cache_ttl_s: int = 3600
    skills_user_dir: Path | None = None
    #: Symbol-level code graph (extra `graph`, graphify). Repos with more source files are refused.
    code_graph_max_files: int = 5_000
    #: How long one tool call waits for a graph build; the build itself keeps running and is reused.
    code_graph_timeout_s: float = 180
    #: Code files scanned per process by `github_system_map` (manifests are always read in full).
    system_map_max_files: int = 3_000

    # --- policies -----------------------------------------------------------------------
    export_mode: Literal["explicit", "deny", "allow"] = "explicit"
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)
    #: The most a skill may raise the per-turn budget to (frontmatter `limits:`).
    limits_ceiling: ExecutionLimits = Field(
        default_factory=lambda: ExecutionLimits(max_model_calls=60, max_tool_calls=200, turn_timeout_s=1_200)
    )

    @property
    def enabled_source_set(self) -> frozenset[str]:
        return frozenset(_split(self.enabled_sources))

    @property
    def allowed_host_set(self) -> frozenset[str] | None:
        hosts = _split(self.allowed_hosts)
        return frozenset(hosts) if hosts else None

    @property
    def fallback_models(self) -> list[str]:
        return _split(self.llm_fallback_models)

    @model_validator(mode="after")
    def _auth_keys_present(self) -> Settings:
        if self.auth_enabled and (self.api_key is None or self.admin_api_key is None):
            raise ValueError("AUTH_ENABLED=true requires both API_KEY and ADMIN_API_KEY")
        return self

    @model_validator(mode="after")
    def _ceiling_at_least_limits(self) -> Settings:
        for name in ("max_model_calls", "max_tool_calls", "turn_timeout_s"):
            if getattr(self.limits_ceiling, name) < getattr(self.limits, name):
                raise ValueError(f"LIMITS_CEILING__{name.upper()} must be >= LIMITS__{name.upper()}")
        return self

    def check_bind(self, host: str) -> None:
        """Refuse to expose an unauthenticated API beyond loopback (PLAN §4.6)."""
        if host not in _LOOPBACK_HOSTS and not self.auth_enabled:
            raise ValueError(
                f"Refusing to bind {host!r} with AUTH_ENABLED=false. "
                "Set AUTH_ENABLED=true with API_KEY/ADMIN_API_KEY, or bind to 127.0.0.1."
            )


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]
