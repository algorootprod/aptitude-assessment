"""Only cross-module entry point into `user_stats` (see CLAUDE.md, "Architecture rules").

Owns no table of its own — pure composition over `user_topic_mapping`'s and
`evaluation_report`'s public services, never their repositories or models directly.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.evaluation_report.service import EvaluationReportService
from app.modules.user_stats.mapping import win_rates_from_section_progress
from app.modules.user_stats.schemas import UserStatsOut
from app.modules.user_topic_mapping.service import UserTopicMappingService


class UserStatsService:
    def __init__(self, session: AsyncSession) -> None:
        self.topic_mapping = UserTopicMappingService(session)
        self.evaluation_report = EvaluationReportService(session)

    async def get_stats(self, user_id: str) -> UserStatsOut:
        """404s via `UserTopicMappingService.get_for_user` for an unknown candidate — the same
        check every other per-candidate read in this service relies on."""
        topic_map = await self.topic_mapping.get_for_user(user_id)
        answers = await self.evaluation_report.get_answer_summary(user_id)
        return UserStatsOut(
            user_id=user_id,
            win_rates=win_rates_from_section_progress(topic_map.section_progress),
            tests_taken=answers.tests_taken,
            questions_solved=answers.questions_solved,
            avg_time_per_question_seconds=answers.avg_time_per_question_seconds,
        )
