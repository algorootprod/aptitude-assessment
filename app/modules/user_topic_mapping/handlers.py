"""SQS entrypoint for this module. Routes to the same `UserTopicMappingService.handle_user_signup`
that `POST /v1/users/signup` calls — see CLAUDE.md ("Integration surface") for why both exist."""

from typing import Any

from app.infrastructure.db.session import session_scope
from app.modules.user_topic_mapping.service import UserTopicMappingService


async def handle_user_signup(envelope: dict[str, Any]) -> None:
    """Handler passed to `infrastructure.messaging.subscriber.consume` for
    `algoaptitude-user-signup.fifo`. `envelope` is the standard
    `{event, version, occurred_at, payload}` shape; `payload["user_id"]` is required.
    """
    async with session_scope() as session:
        service = UserTopicMappingService(session)
        await service.handle_user_signup(envelope["payload"]["user_id"])
