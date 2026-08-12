"""All SQL for `user_topic_map` lives here — this module is the table's sole writer
(see CLAUDE.md, "Architecture rules")."""

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.user_topic_mapping.models import UserTopicMastery


class UserTopicMappingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_all(self, user_id: str) -> list[UserTopicMastery]:
        """Every topic row for a candidate, ordered so rotation and the API response are stable."""
        stmt = (
            select(UserTopicMastery)
            .where(UserTopicMastery.user_id == user_id)
            .order_by(UserTopicMastery.section, UserTopicMastery.topic)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def current_cycles(self, user_id: str | None = None) -> dict[str, int]:
        """Every candidate's current cycle. All of a candidate's rows carry the same value, so
        `max` is just a way to collapse them.

        Exists for `evaluation_report`'s reconcile sweep, which needs to know which candidates
        are still sitting on a cycle they have already been scored for. It reads this through
        the service rather than joining to `user_topic_map` itself.
        """
        stmt = select(UserTopicMastery.user_id, func.max(UserTopicMastery.cycle_version)).group_by(
            UserTopicMastery.user_id
        )
        if user_id is not None:
            stmt = stmt.where(UserTopicMastery.user_id == user_id)
        result = await self.session.execute(stmt)
        return {row[0]: int(row[1]) for row in result.all()}

    async def seed_missing(
        self,
        user_id: str,
        cycle_version: int,
        initial_level: int,
        topics: list[tuple[str, str]],
    ) -> None:
        """Insert a baseline row for each `(section, topic)` the candidate does not have yet.

        `ON CONFLICT DO NOTHING` on the (user_id, topic) primary key, so this is safe to call on
        a duplicate signup *and* on every evaluation — the second use is what picks up topics
        added to `question_bank` after a candidate signed up.
        """
        if not topics:
            return
        stmt = (
            pg_insert(UserTopicMastery)
            .values(
                [
                    {
                        "user_id": user_id,
                        "topic": topic,
                        "section": section,
                        "current_level": initial_level,
                        "pending_dir": 0,
                        "last_cycle": 0,
                        "times_tested": 0,
                        "cycle_version": cycle_version,
                        "mastery_score": 0.0,
                        "streak": 0,
                    }
                    for section, topic in topics
                ]
            )
            .on_conflict_do_nothing(index_elements=["user_id", "topic"])
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def apply_outcome(
        self,
        user_id: str,
        topic: str,
        *,
        current_level: int,
        pending_dir: int,
        mastery_score: float,
        streak: int,
    ) -> None:
        """Write one topic's post-evaluation state. The values are computed by `ladder.py`;
        this only persists them."""
        stmt = (
            update(UserTopicMastery)
            .where(UserTopicMastery.user_id == user_id, UserTopicMastery.topic == topic)
            .values(
                current_level=current_level,
                pending_dir=pending_dir,
                mastery_score=mastery_score,
                streak=streak,
            )
        )
        await self.session.execute(stmt)

    async def mark_scheduled(self, user_id: str, topics: list[str], cycle_version: int) -> None:
        """Record that these topics were put into the test for `cycle_version`.

        `last_cycle` is written at *scheduling* time, not at answering time, so a topic the
        candidate never reached still rotates out and does not monopolise the next test.
        """
        if not topics:
            return
        stmt = (
            update(UserTopicMastery)
            .where(UserTopicMastery.user_id == user_id, UserTopicMastery.topic.in_(topics))
            .values(
                last_cycle=cycle_version,
                times_tested=UserTopicMastery.times_tested + 1,
            )
        )
        await self.session.execute(stmt)

    async def bump_cycle(self, user_id: str, cycle_version: int) -> None:
        """Move every one of the candidate's rows onto the new cycle. Applied to all rows, not
        just the tested ones, so `cycle_version` stays a single per-candidate counter."""
        stmt = (
            update(UserTopicMastery)
            .where(UserTopicMastery.user_id == user_id)
            .values(cycle_version=cycle_version)
        )
        await self.session.execute(stmt)
        await self.session.flush()
