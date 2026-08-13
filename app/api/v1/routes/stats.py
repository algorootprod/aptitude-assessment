from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.modules.user_stats.schemas import UserStatsOut
from app.modules.user_stats.service import UserStatsService

router = APIRouter()


@router.get("/{user_id}")
async def get_user_stats(
    user_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> UserStatsOut:
    """The profile stats card's Daily-20-native numbers: per-section win rate, tests taken,
    questions solved, and average time per question. 404 for an unknown candidate.

    XP, tier and rank are deliberately not here — see `UserStatsOut`.
    """
    service = UserStatsService(session)
    return await service.get_stats(user_id)
