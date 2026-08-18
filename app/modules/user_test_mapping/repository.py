"""All SQL for `question_bank` and `user_test_questions` lives here — this module is the
sole writer of both tables (see CLAUDE.md, "Architecture rules")."""

from typing import Any

from sqlalchemy import cast, delete, func, literal_column, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.user_test_mapping.models import QuestionBank, UserTestQuestions

#: Matches any `question_id` anywhere in a persisted `sections` blob, against the jsonpath variable
#: `$qid`. A source constant, never built from input — the id itself is bound separately via
#: `jsonb_build_object`. Cast explicitly because a plain bind parameter will not type-infer to
#: `jsonpath` through asyncpg.
_QUESTION_ID_JSONPATH = literal_column("'$.*.questions[*].question_id ? (@ == $qid)'::jsonpath")

#: Columns an admin edit is allowed to set. `id` is excluded on purpose — it is the natural key the
#: curation pipeline and every `user_test_questions.sections` blob reference, so renaming one in
#: place would orphan assembled papers silently (the column carries no FK).
EDITABLE_QUESTION_FIELDS: frozenset[str] = frozenset(
    {
        "section",
        "topic",
        "concept",
        "prerequisite_concept",
        "method_tag",
        "question_text",
        "option_a",
        "option_b",
        "option_c",
        "option_d",
        "answer",
        "explanation",
        "distractor_rationale_a",
        "distractor_rationale_b",
        "distractor_rationale_c",
        "distractor_rationale_d",
        "shortcut_available",
        "shortcut_name",
        "shortcut_how",
        "shortcut_saves_seconds",
        "difficulty",
        "expected_time_seconds",
        "source",
        "calibration",
        "batch_number",
        "set_id",
        "chart_type",
        "chart_image",
        "chart_image_svg",
        "chart_direction",
        "chart_data",
    }
)


class UserTestMappingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- question_bank reads (bulk loading stays in app/workers/seed_question_bank.py; the
    # admin panel's writes are further down under "question_bank admin") ----

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
        stmt = select(QuestionBank).where(QuestionBank.set_id == set_id).order_by(QuestionBank.id)
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

    # ---- question_bank admin ----
    #
    # The admin panel's read/write surface over the curated bank. It lives here rather than in a
    # module of its own because this module is `question_bank`'s declared sole writer (CLAUDE.md,
    # "Architecture rules"); ownership moves to `question_generation` in Phase 2 along with the
    # rest of the table's SQL.

    async def count_by_section_difficulty(self) -> dict[str, dict[int | None, int]]:
        """`{section: {difficulty: count}}` over the whole bank. `difficulty` is nullable, so a
        `None` key is a real bucket, not an absence."""
        stmt = (
            select(QuestionBank.section, QuestionBank.difficulty, func.count().label("n"))
            .group_by(QuestionBank.section, QuestionBank.difficulty)
            .order_by(QuestionBank.section, QuestionBank.difficulty)
        )
        result = await self.session.execute(stmt)
        counts: dict[str, dict[int | None, int]] = {}
        for section, difficulty, n in result.all():
            counts.setdefault(section, {})[difficulty] = n
        return counts

    async def count_topics_per_section(self) -> dict[str, int]:
        """Distinct topics held per section — the bank's side of the 54-pair topic coverage."""
        stmt = select(QuestionBank.section, func.count(func.distinct(QuestionBank.topic))).group_by(
            QuestionBank.section
        )
        result = await self.session.execute(stmt)
        return {section: n for section, n in result.all()}

    async def count_missing_answer(self) -> int:
        """Questions with no answer key. These can never score `mastered` (CLAUDE.md, "Evaluation
        model"), so the panel surfaces the count as an audit figure rather than hiding it."""
        stmt = select(func.count()).select_from(QuestionBank).where(QuestionBank.answer.is_(None))
        return int((await self.session.execute(stmt)).scalar_one())

    async def list_malformed_di_sets(self) -> list[tuple[str, int]]:
        """DI `set_id`s that do not hold exactly five questions, as `(set_id, size)`. A DI section
        is one whole set, so anything but five means a paper that cannot be assembled."""
        size = func.count().label("size")
        stmt = (
            select(QuestionBank.set_id, size)
            .where(QuestionBank.section == "di", QuestionBank.set_id.is_not(None))
            .group_by(QuestionBank.set_id)
            .having(size != 5)
            .order_by(QuestionBank.set_id)
        )
        result = await self.session.execute(stmt)
        return [(row[0], int(row[1])) for row in result.all()]

    async def count_di_set_size(self, set_id: str) -> int:
        stmt = select(func.count()).select_from(QuestionBank).where(QuestionBank.set_id == set_id)
        return int((await self.session.execute(stmt)).scalar_one())

    async def list_admin(
        self,
        *,
        section: str | None = None,
        topic: str | None = None,
        difficulty: int | None = None,
        set_id: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[QuestionBank], int]:
        """One page of the bank plus the unpaginated total, so the panel can render "n of N"."""
        filters = []
        if section is not None:
            filters.append(QuestionBank.section == section)
        if topic is not None:
            filters.append(QuestionBank.topic == topic)
        if difficulty is not None:
            filters.append(QuestionBank.difficulty == difficulty)
        if set_id is not None:
            filters.append(QuestionBank.set_id == set_id)
        if search:
            pattern = f"%{search}%"
            filters.append(
                or_(QuestionBank.question_text.ilike(pattern), QuestionBank.id.ilike(pattern))
            )

        total_stmt = select(func.count()).select_from(QuestionBank)
        rows_stmt = select(QuestionBank)
        for condition in filters:
            total_stmt = total_stmt.where(condition)
            rows_stmt = rows_stmt.where(condition)

        total = int((await self.session.execute(total_stmt)).scalar_one())
        rows_stmt = rows_stmt.order_by(QuestionBank.id).limit(limit).offset(offset)
        rows = list((await self.session.execute(rows_stmt)).scalars().all())
        return rows, total

    async def get_question(self, question_id: str) -> QuestionBank | None:
        stmt = select(QuestionBank).where(QuestionBank.id == question_id)
        return (await self.session.execute(stmt)).scalars().one_or_none()

    async def create_question(self, values: dict[str, Any]) -> QuestionBank:
        row = QuestionBank(**values)
        self.session.add(row)
        await self.session.flush()
        return row

    async def update_question(self, row: QuestionBank, values: dict[str, Any]) -> QuestionBank:
        """Applies only whitelisted columns — see `EDITABLE_QUESTION_FIELDS` for why `id` is not
        one of them."""
        for field, value in values.items():
            if field in EDITABLE_QUESTION_FIELDS:
                setattr(row, field, value)
        await self.session.flush()
        return row

    async def delete_question(self, question_id: str) -> None:
        await self.session.execute(delete(QuestionBank).where(QuestionBank.id == question_id))
        await self.session.flush()

    async def count_pending_paper_references(self, question_id: str) -> int:
        """How many candidates have this question in their *latest* assembled paper.

        `user_test_questions.sections` holds question ids in a JSON blob with no foreign key, so
        deleting a referenced question would break that candidate's `POST /v1/tests/start` with no
        database error at all. Only the latest cycle can still be unsat — earlier papers have been
        taken already — so that is the set this guards.

        The column is `JSON`, not `JSONB`, so it is cast before `jsonb_path_exists`. The id travels
        as a jsonpath variable rather than being interpolated into the path string.
        """
        latest = select(
            UserTestQuestions.sections.label("sections"),
            func.row_number()
            .over(
                partition_by=UserTestQuestions.user_id,
                order_by=UserTestQuestions.cycle_version.desc(),
            )
            .label("rn"),
        ).subquery()
        stmt = (
            select(func.count())
            .select_from(latest)
            .where(
                latest.c.rn == 1,
                func.jsonb_path_exists(
                    cast(latest.c.sections, JSONB),
                    _QUESTION_ID_JSONPATH,
                    func.jsonb_build_object("qid", question_id),
                ),
            )
        )
        return int((await self.session.execute(stmt)).scalar_one())
