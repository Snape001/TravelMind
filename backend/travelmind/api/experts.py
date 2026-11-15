from fastapi import APIRouter

from travelmind.api.dependencies import SignedIn
from travelmind.runtime.skills import SkillRegistry
from travelmind.schemas.api import ErrorResponse
from travelmind.schemas.phase4_experts import ExpertProfilesRead

router = APIRouter(prefix="/api/v1", tags=["experts"], responses={401: {"model": ErrorResponse}})


@router.get("/experts")
async def read_experts(_: SignedIn) -> ExpertProfilesRead:
    return SkillRegistry().profiles()
