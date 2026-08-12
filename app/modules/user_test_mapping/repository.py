"""All SQL for `question_bank` and `user_test_questions` lives here — this module is the
sole writer of both tables (see CLAUDE.md, "Architecture rules")."""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.user_test_mapping.models import QuestionBank, UserTestQuestions


class UserTestMappingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- question_bank (read-only; the bank is loaded by app/workers/seed_question_bank.py) ----

    async def list_topics(self) -> list[tuple[str, str]]:
        """Every distinct `(section, topic)` in the bank — 54 pairs today."""
        stmt = (
            select(QuestionBank.section, QuestionBank.topic)
            .distinct()
            .order_by(QuestionBank.section, QuestionBank.topic)
        )
        result = await self.session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    async def get_questions_by_ids(self, question_ids: list[str]) -> list[QuestionBank]:
        if not question_ids:
            return []
        stmt = select(QuestionBank).where(QuestionBank.id.in_(question_ids))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def find_candidates(
        self,
        *,
        section: str,
        topic: str,
        levels: list[int] | None = None,
        exclude_ids: set[str] | None = None,
        limit: int = 32,
    ) -> list[QuestionBank]:
        """Non-DI candidate pool for one slot.

        `levels=None` means any difficulty. Ordered by `id` so selection is deterministic and a
        replayed cycle rebuilds the identical test.
        """
        stmt = select(QuestionBank).where(
            QuestionBank.section == section, QuestionBank.topic == topic
        )
        if levels:
            stmt = stmt.where(QuestionBank.difficulty.in_(levels))
        if exclude_ids:
            stmt = stmt.where(QuestionBank.id.not_in(exclude_ids))
        stmt = stmt.order_by(QuestionBank.id).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_di_sets(
        self,
        *,
        topic: str | None = None,
        exclude_set_ids: set[str] | None = None,
    ) -> list[tuple[str, int]]:
        """DI sets as `(set_id, rounded mean difficulty)`, ordered by `set_id`.

        A DI set is five questions sharing one chart, one topic and one `set_id`, so DI is
        selected a set at a time. Note that no set in the current bank has a *uniform*
        difficulty — every one is a 1->4 ramp — so its "level" is the rounded mean, and asking
        for an exact level will usually miss. See CLAUDE.md, "Question bank".
        """
        mean_difficulty = func.round(func.avg(QuestionBank.difficulty)).label("level")
        stmt = select(QuestionBank.set_id, mean_difficulty).where(
            QuestionBank.section == "di", QuestionBank.set_id.is_not(None)
        )
        if topic is not None:
            stmt = stmt.where(QuestionBank.topic == topic)
        if exclude_set_ids:
            stmt = stmt.where(QuestionBank.set_id.not_in(exclude_set_ids))
        stmt = stmt.group_by(QuestionBank.set_id).order_by(QuestionBank.set_id)
        result = await self.session.execute(stmt)
        return [(row[0], int(row[1])) for row in result.all()]

    async def get_set_questions(self, set_id: str) -> list[QuestionBank]:
        """The five questions of one DI set, in their authored order (the `.q1`-`.q5` id suffix,
        which is also the set's difficulty ramp)."""
        stmt = (
            select(QuestionBank).where(QuestionBank.set_id == set_id).order_by(QuestionBank.id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ---- user_test_questions ----

    async def get_for_user(self, user_id: str, cycle_version: int) -> UserTestQuestions | None:
        stmt = select(UserTestQuestions).where(
            UserTestQuestions.user_id == user_id,
            UserTestQuestions.cycle_version == cycle_version,
        )
        result = await self.session.execute(stmt)
        return result.scalars().one_or_none()

    async def get_latest_for_user(self, user_id: str) -> UserTestQuestions | None:
        stmt = (
            select(UserTestQuestions)
            .where(UserTestQuestions.user_id == user_id)
            .order_by(UserTestQuestions.cycle_version.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalars().one_or_none()

    async def list_all_for_user(self, user_id: str) -> list[UserTestQuestions]:
        """Every test ever assembled for a candidate — the source of "already seen" question and
        set ids. At one row per cycle holding 20 ids, this stays cheap enough not to warrant a
        separate history table."""
        stmt = (
            select(UserTestQuestions)
            .where(UserTestQuestions.user_id == user_id)
            .order_by(UserTestQuestions.cycle_version)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def upsert(
        self, user_id: str, cycle_version: int, sections: dict[str, Any]
    ) -> UserTestQuestions:
        """`ON CONFLICT DO NOTHING` on (user_id, cycle_version): a replayed cycle must return the
        test that was already assembled, never silently swap the candidate onto a different one.
        """
        stmt = (
            pg_insert(UserTestQuestions)
            .values(user_id=user_id, cycle_version=cycle_version, sections=sections)
            .on_conflict_do_nothing(constraint="uq_user_test_questions_cycle")
        )
        await self.session.execute(stmt)
        await self.session.flush()

        existing = await self.get_for_user(user_id, cycle_version)
        if existing is None:  # pragma: no cover - the insert above guarantees a row
            raise RuntimeError(f"user_test_questions row missing after upsert: {user_id}")
        return existing
