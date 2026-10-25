"""Composition root: the only place that knows every implementation (PLAN §2).

Swapping an implementation means passing a different object in `Overrides`, or changing settings;
no other module changes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver

from agent_harness.application.artifact_service import ArtifactService
from agent_harness.application.chat_service import ChatService
from agent_harness.application.conversation_service import ConversationService
from agent_harness.application.skill_service import SkillService
from agent_harness.capabilities.diagram_tools import build_diagram_tools
from agent_harness.capabilities.export_tools import build_export_tools
from agent_harness.capabilities.finalizers import (
    AnswerPrefixFinalizer,
    CitationFinalizer,
    DiagramFinalizer,
    SkillFinalHook,
    SkillPostToolHook,
    system_prompt_builder,
)
from agent_harness.capabilities.skill_tools import build_skill_tools
from agent_harness.capabilities.source_tools import build_source_tools
from agent_harness.capabilities.system_map_tools import SystemMaps, build_system_map_tools
from agent_harness.config.settings import Settings
from agent_harness.domain.policies import ExportPolicy, SourcePolicy
from agent_harness.domain.skills import SkillTrust
from agent_harness.harness.engine import AgentEngine
from agent_harness.harness.observability.logger import get_logger
from agent_harness.harness.runtime import AgentRuntime
from agent_harness.harness.tools.executor import ToolExecutor, default_middleware
from agent_harness.harness.tools.registry import ToolRegistry
from agent_harness.ports.artifact_store import ArtifactStore
from agent_harness.ports.claim_verifier import ClaimVerifier
from agent_harness.ports.code_graph import CodeGraphBuilder
from agent_harness.ports.repositories import SourceCache, UnitOfWorkFactory
from agent_harness.ports.source_fetcher import HttpFetcher
from agent_harness.skills.hooks import HookRunner
from agent_harness.skills.registry import BUILTIN_DIR, SkillDirectory, SkillRegistry
from agent_harness.sources.blog.fetcher import BlogFetcher
from agent_harness.sources.github.client import GithubClient
from agent_harness.sources.github.repository_reader import RepositoryReader
from agent_harness.sources.http import CachingFetcher, SafeHttpClient
from agent_harness.sources.registry import SourceRegistry

log = get_logger(__name__)

HealthCheck = Callable[[], Awaitable[None]]
EngineFactory = Callable[[ToolExecutor], AgentEngine]


@dataclass
class Overrides:
    """Replace any infrastructure piece (tests, alternative deployments)."""

    uow_factory: UnitOfWorkFactory | None = None
    source_cache: SourceCache | None = None
    artifact_store: ArtifactStore | None = None
    http_fetcher: HttpFetcher | None = None
    github_client: Any | None = None
    engine_factory: EngineFactory | None = None
    chat_model: BaseChatModel | None = None
    fallback_models: Sequence[BaseChatModel] | None = None
    checkpointer: BaseCheckpointSaver | None = None
    builtin_skills_dir: Path | None = None
    claim_verifier: ClaimVerifier | None = None
    #: None = graphify when the `graph` extra is installed; pass `False` to disable the code graph.
    code_graph_builder: CodeGraphBuilder | Literal[False] | None = None


@dataclass
class Container:
    settings: Settings
    chat: ChatService
    conversations: ConversationService
    skills: SkillService
    artifacts: ArtifactService
    tool_registry: ToolRegistry
    engine: AgentEngine
    health_checks: dict[str, HealthCheck] = field(default_factory=dict)
    _stack: AsyncExitStack = field(default_factory=AsyncExitStack)

    async def aclose(self) -> None:
        await self.chat.drain()
        await self._stack.aclose()


async def build_container(settings: Settings, overrides: Overrides | None = None) -> Container:
    o = overrides or Overrides()
    stack = AsyncExitStack()
    health: dict[str, HealthCheck] = {}
    try:
        uow_factory, source_cache = await _persistence(settings, o, stack, health)
        artifact_store = o.artifact_store or await _minio(settings, stack)
        health["artifact_store"] = artifact_store.health

        source_policy = SourcePolicy(allowed_hosts=settings.allowed_host_set, max_fetch_bytes=settings.limits.max_fetch_bytes)
        fetcher = o.http_fetcher
        if fetcher is None:
            safe = SafeHttpClient(source_policy)
            stack.push_async_callback(safe.aclose)
            fetcher = safe
        github_client = o.github_client or GithubClient(
            CachingFetcher(fetcher, source_cache, kind="github", ttl_s=settings.source_cache_ttl_s),
            token=settings.github_token.get_secret_value() if settings.github_token else None,
        )
        blog = BlogFetcher(CachingFetcher(fetcher, source_cache, kind="blog", ttl_s=settings.source_cache_ttl_s))

        directories = [SkillDirectory(o.builtin_skills_dir or BUILTIN_DIR, SkillTrust.TRUSTED)]
        if settings.skills_user_dir:
            directories.append(SkillDirectory(settings.skills_user_dir, SkillTrust.DECLARATIVE))
        skill_registry = SkillRegistry(directories)
        health["skills"] = _skills_check(skill_registry)
        hooks = HookRunner(timeout_s=settings.limits.hook_timeout_s)

        artifacts = ArtifactService(uow_factory, artifact_store)
        sources = SourceRegistry(settings.enabled_source_set)
        github = RepositoryReader(
            github_client,
            code_graph_builder=_code_graph_builder(settings, o),
            code_graph_timeout_s=settings.code_graph_timeout_s,
            system_map_max_files=settings.system_map_max_files,
        )
        system_maps = SystemMaps(sources, github)
        registry = ToolRegistry(
            [
                *build_skill_tools(skill_registry, hooks),
                *build_source_tools(sources=sources, github=github, blog=blog),
                *build_system_map_tools(system_maps),
                *build_export_tools(artifacts),
                *build_diagram_tools(system_maps),
            ]
        )
        executor = ToolExecutor(registry, default_middleware(registry, extra=[SkillPostToolHook(hooks)]))
        engine = o.engine_factory(executor) if o.engine_factory else await _langchain_engine(settings, o, executor, stack)

        chat = ChatService(
            uow_factory=uow_factory,
            runtime=AgentRuntime(
                engine,
                [SkillFinalHook(hooks), AnswerPrefixFinalizer(), DiagramFinalizer(), CitationFinalizer(verifier=o.claim_verifier)],
            ),
            limits=settings.limits,
            limit_ceiling=settings.limits_ceiling,
            export_policy=ExportPolicy(settings.export_mode),
            source_policy=source_policy,
            system_prompt_builder=system_prompt_builder(skill_registry),
        )
        return Container(
            settings=settings,
            chat=chat,
            conversations=ConversationService(uow_factory),
            skills=SkillService(skill_registry),
            artifacts=artifacts,
            tool_registry=registry,
            engine=engine,
            health_checks=health,
            _stack=stack,
        )
    except BaseException:
        await stack.aclose()
        raise


async def _persistence(
    settings: Settings, o: Overrides, stack: AsyncExitStack, health: dict[str, HealthCheck]
) -> tuple[UnitOfWorkFactory, SourceCache]:
    if o.uow_factory is not None and o.source_cache is not None:
        return o.uow_factory, o.source_cache
    from agent_harness.adapters.postgres.db import check_schema, create_engine, session_factory
    from agent_harness.adapters.postgres.repositories import PostgresSourceCache, PostgresUnitOfWork

    engine = create_engine(settings.database_url, pool_size=settings.db_pool_size)
    stack.push_async_callback(engine.dispose)
    sessions = session_factory(engine)
    health["database"] = lambda: check_schema(engine, settings.database_url)
    return (
        o.uow_factory or (lambda: PostgresUnitOfWork(sessions)),
        o.source_cache or PostgresSourceCache(sessions),
    )


def _code_graph_builder(settings: Settings, o: Overrides) -> CodeGraphBuilder | None:
    if o.code_graph_builder is False:
        return None
    if o.code_graph_builder is not None:
        return o.code_graph_builder
    try:
        from agent_harness.adapters.graphify.code_graph import GraphifyCodeGraphBuilder
    except ImportError:
        log.info("code_graph_disabled", extra={"reason": "graphify not installed (uv sync --extra graph)"})
        return None
    return GraphifyCodeGraphBuilder(max_files=settings.code_graph_max_files)


async def _minio(settings: Settings, stack: AsyncExitStack) -> ArtifactStore:
    from agent_harness.adapters.storage.minio_artifact_store import MinioArtifactStore

    return MinioArtifactStore.connect(
        settings.minio_endpoint,
        settings.minio_access_key.get_secret_value(),
        settings.minio_secret_key.get_secret_value(),
        settings.minio_bucket,
        secure=settings.minio_secure,
    )


async def _langchain_engine(settings: Settings, o: Overrides, executor: ToolExecutor, stack: AsyncExitStack) -> AgentEngine:
    from agent_harness.adapters.langchain.engine import LangChainAgentEngine
    from agent_harness.adapters.models.openai_compatible import openai_compatible_model

    checkpointer = o.checkpointer
    if checkpointer is None:
        if o.uow_factory is not None:  # in-memory deployment (tests/dev without Postgres)
            from langgraph.checkpoint.memory import InMemorySaver

            checkpointer = InMemorySaver()
        else:
            from agent_harness.adapters.postgres.checkpointer import open_checkpointer

            checkpointer = await stack.enter_async_context(open_checkpointer(settings.database_url))
    def make(name: str) -> BaseChatModel:
        return openai_compatible_model(
            name,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            temperature=settings.model_temperature,
            reasoning_effort=settings.model_reasoning_effort,
            max_output_tokens=settings.model_max_output_tokens,
            guard_output=settings.model_output_guard,
            idle_timeout_s=settings.model_idle_timeout_s,
        )

    model = o.chat_model or make(settings.llm_model)
    fallbacks = (
        list(o.fallback_models)
        if o.fallback_models is not None
        else [make(name) for name in settings.fallback_models]
    )
    return LangChainAgentEngine(
        model=model,
        executor=executor,
        checkpointer=checkpointer,
        fallback_models=fallbacks,
        context_clear_trigger_tokens=settings.context_clear_trigger_tokens,
        model_retries=settings.model_max_retries,
        guard_answers=settings.model_output_guard,
    )


def _skills_check(registry: SkillRegistry) -> HealthCheck:
    async def check() -> None:
        if not registry.list():
            raise RuntimeError("no skills loaded")

    return check
