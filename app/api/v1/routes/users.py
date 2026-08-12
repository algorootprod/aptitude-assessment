from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.modules.user_topic_mapping.schemas import SignupIn, UserTopicMapOut
from app.modules.user_topic_mapping.service import UserTopicMappingService

router = APIRouter()


@router.post("/signup")
async def signup(
    body: SignupIn, session: AsyncSession = Depends(get_db_session)
) -> UserTopicMapOut:
    """The diagram's "User signup" arrow from Node into `user_topic_mapping`."""
    service = UserTopicMappingService(session)
    return await service.handle_user_signup(body.user_id)
