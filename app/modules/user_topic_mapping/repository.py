"""All SQL for `user_topic_map` and `user_section_progress` lives here — this module is the sole
writer of both (see CLAUDE.md, "Architecture rules")."""

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.user_topic_mapping.models import UserSectionProgress, UserTopicMastery


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

    # ---- user_section_progress ----

    async def save_section_progress(
        self, user_id: str, cycle_version: int, rows: list[tuple[str, int, float, float]]
    ) -> None:
        """Append one progress point per section as `(section, level, raw_score, progress_score)`.

        `DO NOTHING` rather than `DO UPDATE`: `update_from_evaluation` runs under
        `retry_sync_call()` with a fresh session per attempt, so a retry that gets past the replay
        guard must not append a duplicate point or rewrite one the candidate has already seen.
        """
        if not rows:
            return
        stmt = (
            pg_insert(UserSectionProgress)
            .values(
                [
                    {
                        "user_id": user_id,
                        "cycle_version": cycle_version,
                        "section": section,
                        "current_level": level,
                        "raw_score": raw_score,
                        "progress_score": progress_score,
                    }
                    for section, level, raw_score, progress_score in rows
                ]
            )
            .on_conflict_do_nothing(constraint="uq_user_section_progress_cycle")
        )
        await self.session.execute(stmt)

    async def latest_section_progress(self, user_id: str) -> list[UserSectionProgress]:
        """The newest point per section — what `get_for_user` reports as current standing.

        `DISTINCT ON` keeps this one round trip; the ordering inside the parentheses is what picks
        which row survives per section, so it must stay `(section, cycle_version DESC)`.
        """
        stmt = (
            select(UserSectionProgress)
            .where(UserSectionProgress.user_id == user_id)
            .distinct(UserSectionProgress.section)
            .order_by(UserSectionProgress.section, UserSectionProgress.cycle_version.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def section_progress_history(
        self, user_id: str, tests: int
    ) -> list[UserSectionProgress]:
        """Every stored point from the candidate's most recent `tests` evaluated cycles, oldest
        first so the caller can plot without re-sorting.

        Two steps rather than a `LIMIT` on the rows themselves: a limit would cut mid-cycle and
        return a partial set of sections for the oldest test in the window.
        """
        cycles_stmt = (
            select(UserSectionProgress.cycle_version)
            .where(UserSectionProgress.user_id == user_id)
            .distinct()
            .order_by(UserSectionProgress.cycle_version.desc())
            .limit(tests)
        )
        cycles = list((await self.session.execute(cycles_stmt)).scalars().all())
        if not cycles:
            return []

        stmt = (
            select(UserSectionProgress)
            .where(
                UserSectionProgress.user_id == user_id,
                UserSectionProgress.cycle_version.in_(cycles),
            )
            .order_by(UserSectionProgress.cycle_version, UserSectionProgress.section)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
