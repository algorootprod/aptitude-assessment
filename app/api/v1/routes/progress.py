from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.modules.user_topic_mapping.schemas import ProgressHistoryOut
from app.modules.user_topic_mapping.service import UserTopicMappingService

router = APIRouter()


@router.get("/{user_id}")
async def get_progress_history(
    user_id: str,
    tests: int = Query(
        default=10,
        ge=1,
        le=50,
        description="How many of the most recent tests to return, newest-last.",
    ),
    session: AsyncSession = Depends(get_db_session),
) -> ProgressHistoryOut:
    """The candidate's per-section progress across their last `tests` evaluated tests.

    Progress only — no topic breakdown, no questions. Each point is the 0-100 figure that section
    stood at after one test, stored at evaluation time rather than recomputed, so a point always
    matches what the candidate was shown then.

    404 if the candidate does not exist. A candidate who exists but has sat no tests gets every
    section with an empty `points` list, which is a valid answer rather than an error.
    """
    service = UserTopicMappingService(session)
    return await service.get_progress_history(user_id, tests)
