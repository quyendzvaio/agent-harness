"""Service accessors. The API sees application services only, never adapters."""

from __future__ import annotations

from fastapi import Request

from agent_harness.application.artifact_service import ArtifactService
from agent_harness.application.chat_service import ChatService
from agent_harness.application.conversation_service import ConversationService
from agent_harness.application.skill_service import SkillService


def chat_service(request: Request) -> ChatService:
    return request.app.state.container.chat


def conversation_service(request: Request) -> ConversationService:
    return request.app.state.container.conversations


def skill_service(request: Request) -> SkillService:
    return request.app.state.container.skills


def artifact_service(request: Request) -> ArtifactService:
    return request.app.state.container.artifacts
