from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from agent_harness.api.auth import require_admin, require_user
from agent_harness.api.deps import skill_service
from agent_harness.api.schemas import SkillOut
from agent_harness.application.skill_service import SkillService

router = APIRouter(prefix="/v1/skills", tags=["skills"])


@router.get("", dependencies=[Depends(require_user)])
async def list_skills(service: SkillService = Depends(skill_service)) -> list[SkillOut]:
    return [SkillOut.of(s) for s in service.list()]


@router.get("/{name}", dependencies=[Depends(require_user)])
async def get_skill(name: str, service: SkillService = Depends(skill_service)) -> SkillOut:
    return SkillOut.of(service.get(name), detail=True)


@router.post("/reload", dependencies=[Depends(require_admin)])
async def reload_skills(service: SkillService = Depends(skill_service)) -> dict[str, Any]:
    return service.reload()
