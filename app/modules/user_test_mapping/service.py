"""Only cross-module entry point into `user_test_mapping` (see CLAUDE.md, "Architecture rules")."""

import math
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.constants import (
    DEFAULT_EXPECTED_TIME_SECONDS,
    DI_SECTION,
    OPTION_LETTERS,
    SECTION_ORDER,
    SelectionFallback,
)
from app.core.exceptions import NotFoundError, TestMappingError
from app.modules.user_test_mapping import __version__
from app.modules.user_test_mapping.models import QuestionBank, UserTestQuestions
from app.modules.user_test_mapping.repository import UserTestMappingRepository
from app.modules.user_test_mapping.schemas import (
    QuestionOut,
    QuestionScoringOut,
    SectionQuestions,
    SelectedQuestion,
    SelectedSection,
    TopicRef,
    TopicSlot,
    UserTestMapOut,
)
from app.modules.user_test_mapping.selection import pick_di_set, pick_question

_MODULE = "user_test_mapping"


class UserTestMappingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = UserTestMappingRepository(session)

    async def list_topics(self) -> list[TopicRef]:
        """Every `(section, topic)` the bank holds. Called by `user_topic_mapping` to seed a new
        candidate's rows and to reconcile existing ones against topics added since signup —
        `question_bank` is this module's table, so that read goes through here."""
        pairs = await self.repo.list_topics()
        return [TopicRef(section=section, topic=topic) for section, topic in pairs]

    async def has_test_for_cycle(self, user_id: str, cycle_version: int) -> bool:
        """Whether this cycle's test is already assembled.

        `user_topic_mapping` checks this before rotating topics: a signup that arrives over both
        REST *and* the SQS queue — the expected case, see CLAUDE.md ("Integration surface") —
        must not advance the rotation the second time, or the candidate's first test would show
        topics 1-5 while topics 1-10 were recorded as already served.
        """
        return await self.repo.get_for_user(user_id, cycle_version) is not None

    async def get_assembled_test(
        self, user_id: str, cycle_version: int
    ) -> list[SelectedSection] | None:
        """The persisted slot list for one cycle — ids, order, levels and section budgets.

        `get_for_user` hydrates the same row into question *content* for the candidate; this
        returns the bookkeeping instead, which is what `evaluation_report` needs to know which
        section a question belonged to, what the section's clock was, and — for DI — which topic
        the whole set counted towards. `None` if that cycle was never assembled.
        """
        row = await self.repo.get_for_user(user_id, cycle_version)
        if row is None:
            return None
        return [
            SelectedSection.model_validate(payload)
            for payload in (row.sections or {}).values()
            if isinstance(payload, dict)
        ]

    async def get_scoring_metadata(self, question_ids: list[str]) -> list[QuestionScoringOut]:
        """Answer keys and coaching material for a set of questions.

        The only route to an answer key in the service, and the reason `evaluation_report` never
        touches `question_bank` itself (see CLAUDE.md, "Architecture rules"). Called *after* a
        candidate submits, never before — `get_for_user` is the pre-test read and strips all of
        this.
        """
        questions = await self.repo.get_questions_by_ids(question_ids)
        return [self._as_scoring_out(question) for question in questions]

    async def on_topic_change(
        self, user_id: str, cycle_version: int, slots: list[TopicSlot]
    ) -> UserTestMapOut:
        """Assemble and persist the candidate's test for `cycle_version`, then return it.

        Called synchronously on the caller's own session from both
        `user_topic_mapping.handle_user_signup` (new candidate) and
        `user_topic_mapping.update_from_evaluation` (after every evaluation) — the diagram's
        "on new user signup" / "after every evaluation" arrows into this module. Sharing the
        session is what lets `update_from_evaluation` return only once the *next* test exists.

        `slots` carries the (section, topic, level) triples chosen by `user_topic_mapping`, which
        owns the rotation state in `user_topic_map`. This module resolves each slot to a concrete
        question, which is the part that depends on what the bank actually holds.
        """
        existing = await self.repo.get_for_user(user_id, cycle_version)
        if existing is not None:
            # Cycle already assembled — a retried call must hand back the same test, never a
            # freshly-rolled one. (`repo.upsert` is DO NOTHING for the same reason.)
            return await self._hydrate(existing)

        seen_ids, seen_order, seen_sets, seen_set_order = await self._seen(user_id)
        sections: dict[str, Any] = {}

        for section in SECTION_ORDER:
            section_slots = [s for s in slots if s.section == section]
            if not section_slots:
                continue
            if section == DI_SECTION:
                selected = await self._build_di_section(
                    section_slots[0], seen_sets, seen_set_order
                )
            else:
                selected = await self._build_section(
                    section, section_slots, seen_ids, seen_order
                )
            if selected is None:
                continue
            # A slot filled now must not be filled again by a later slot in the same test.
            seen_ids.update(q.question_id for q in selected.questions)
            if selected.set_id:
                seen_sets.add(selected.set_id)
            sections[section] = selected.model_dump(exclude_none=True)

        if not sections:
            raise TestMappingError(
                _MODULE, __version__, f"no questions could be selected for user {user_id}"
            )

        row = await self.repo.upsert(user_id, cycle_version, sections)
        return await self._hydrate(row)

    async def get_for_user(self, user_id: str) -> UserTestMapOut:
        """Serves `POST /v1/tests/start`. A pure read of the pre-assembled current cycle, so it
        is idempotent: calling it twice returns the same test. Full question content is inlined —
        Node never queries this service's DB (see CLAUDE.md, "Integration surface")."""
        row = await self.repo.get_latest_for_user(user_id)
        if row is None:
            raise NotFoundError(
                _MODULE,
                __version__,
                f"no assembled test for user {user_id} — no signup has been processed",
            )
        return await self._hydrate(row)

    # ---- assembly ----

    async def _build_section(
        self,
        section: str,
        slots: list[TopicSlot],
        seen_ids: set[str],
        seen_order: dict[str, int],
    ) -> SelectedSection | None:
        """One question per slot, each from its own topic."""
        picked: list[SelectedQuestion] = []
        for order, slot in enumerate(slots, start=1):
            candidates = await self.repo.find_candidates(section=section, topic=slot.topic)
            choice = pick_question(
                level=slot.level,
                candidates=list(candidates),
                seen_ids=seen_ids,
                seen_order=seen_order,
            )
            if choice is None:
                continue
            question, fallback = choice
            picked.append(self._as_selected(question, slot.level, order, fallback))
            seen_ids.add(question.id)

        if not picked:
            return None
        return SelectedSection(
            section=section,
            budget_seconds=self._budget(picked),
            questions=picked,
        )

    async def _build_di_section(
        self, slot: TopicSlot, seen_sets: set[str], seen_set_order: dict[str, int]
    ) -> SelectedSection | None:
        """One whole `set_id` fills DI: five questions sharing a chart, a topic and a set."""
        topic_sets = await self.repo.list_di_sets(topic=slot.topic)
        in_topic = {sid for sid, _ in topic_sets}
        all_sets = await self.repo.list_di_sets()
        other_sets = [(sid, lvl) for sid, lvl in all_sets if sid not in in_topic]

        choice = pick_di_set(
            level=slot.level,
            topic_sets=topic_sets,
            other_topic_sets=other_sets,
            seen_set_ids=seen_sets,
            seen_set_order=seen_set_order,
        )
        if choice is None:
            return None
        set_id, fallback = choice

        questions = await self.repo.get_set_questions(set_id)
        if not questions:
            return None

        picked = [
            self._as_selected(q, q.difficulty or slot.level, order, fallback)
            for order, q in enumerate(questions, start=1)
        ]
        return SelectedSection(
            section=DI_SECTION,
            budget_seconds=self._budget(picked),
            questions=picked,
            set_id=set_id,
            topic=questions[0].topic,
            level=slot.level,
            selection_fallback=fallback,
        )

    @staticmethod
    def _as_selected(
        question: QuestionBank, level: int, order: int, fallback: SelectionFallback
    ) -> SelectedQuestion:
        return SelectedQuestion(
            question_id=question.id,
            topic=question.topic,
            level=question.difficulty or level,
            expected_time_seconds=question.expected_time_seconds
            or DEFAULT_EXPECTED_TIME_SECONDS.get(question.section, 60),
            order=order,
            selection_fallback=fallback,
        )

    @staticmethod
    def _budget(questions: list[SelectedQuestion]) -> int:
        """The section clock Node will run, in seconds. Derived from the questions actually
        chosen rather than fixed per section, because a section's total time now moves with the
        candidate's levels."""
        total = sum(q.expected_time_seconds for q in questions)
        return int(math.ceil(total * get_settings().time_budget_slack))

    # ---- reads ----

    async def _seen(
        self, user_id: str
    ) -> tuple[set[str], dict[str, int], set[str], dict[str, int]]:
        """Question and set ids the candidate has already been served, with the cycle each was
        first served in, so the `repeat` fallback can prefer the least recently seen.

        Derived from every prior `user_test_questions` row rather than from a dedicated history
        table: one row per cycle holding 20 ids is small enough to parse in Python, and the
        column is `JSON` rather than `JSONB` anyway.
        """
        rows = await self.repo.list_all_for_user(user_id)
        seen_ids: set[str] = set()
        seen_order: dict[str, int] = {}
        seen_sets: set[str] = set()
        seen_set_order: dict[str, int] = {}

        for row in rows:
            for payload in (row.sections or {}).values():
                if not isinstance(payload, dict):
                    continue
                set_id = payload.get("set_id")
                if isinstance(set_id, str):
                    seen_sets.add(set_id)
                    seen_set_order.setdefault(set_id, row.cycle_version)
                for question in payload.get("questions", []):
                    question_id = question.get("question_id")
                    if isinstance(question_id, str):
                        seen_ids.add(question_id)
                        seen_order.setdefault(question_id, row.cycle_version)

        return seen_ids, seen_order, seen_sets, seen_set_order

    async def _hydrate(self, row: UserTestQuestions) -> UserTestMapOut:
        """Persisted id list -> full question content for Node, with answer keys and coaching
        material (explanation, distractor rationales, shortcuts) left behind."""
        stored = [
            SelectedSection.model_validate(payload)
            for payload in (row.sections or {}).values()
            if isinstance(payload, dict)
        ]
        question_ids = [q.question_id for section in stored for q in section.questions]
        by_id = {q.id: q for q in await self.repo.get_questions_by_ids(question_ids)}

        sections: list[SectionQuestions] = []
        for stored_section in sorted(stored, key=lambda s: SECTION_ORDER.index(s.section)):
            ordered = sorted(stored_section.questions, key=lambda q: q.order)
            questions = [
                self._as_question_out(by_id[q.question_id], q.expected_time_seconds)
                for q in ordered
                if q.question_id in by_id
            ]
            first = by_id.get(ordered[0].question_id) if ordered else None
            sections.append(
                SectionQuestions(
                    section=stored_section.section,
                    budget_seconds=stored_section.budget_seconds,
                    # DI's chart and its prose are shared by all five questions of the set, so
                    # they ride on the section — repeating a ~37KB SVG five times would put
                    # ~190KB of duplicate payload on every /v1/tests/start response.
                    direction=first.chart_direction if first else None,
                    chart_svg=first.chart_image_svg if first else None,
                    questions=questions,
                )
            )

        return UserTestMapOut(
            user_id=row.user_id, cycle_version=row.cycle_version, sections=sections
        )

    @staticmethod
    def _options(question: QuestionBank) -> list[str | None]:
        """Options in letter order, holes preserved — index i is `OPTION_LETTERS[i]`."""
        return [question.option_a, question.option_b, question.option_c, question.option_d]

    @classmethod
    def _as_question_out(cls, question: QuestionBank, expected_time_seconds: int) -> QuestionOut:
        return QuestionOut(
            id=question.id,
            section=question.section,
            topic=question.topic,
            question_text=question.question_text,
            options=[option for option in cls._options(question) if option is not None],
            direction=question.chart_direction,
            expected_time_seconds=expected_time_seconds,
        )

    @classmethod
    def _as_scoring_out(cls, question: QuestionBank) -> QuestionScoringOut:
        rationales = (
            question.distractor_rationale_a,
            question.distractor_rationale_b,
            question.distractor_rationale_c,
            question.distractor_rationale_d,
        )
        return QuestionScoringOut(
            id=question.id,
            section=question.section,
            topic=question.topic,
            concept=question.concept,
            prerequisite_concept=question.prerequisite_concept,
            question_text=question.question_text,
            options=[option for option in cls._options(question) if option is not None],
            answer=question.answer,
            expected_time_seconds=question.expected_time_seconds
            or DEFAULT_EXPECTED_TIME_SECONDS.get(question.section, 60),
            explanation=question.explanation,
            distractor_rationale={
                letter: rationale
                for letter, rationale in zip(OPTION_LETTERS, rationales, strict=False)
                if rationale is not None
            },
            shortcut_name=question.shortcut_name,
            shortcut_how=question.shortcut_how,
            shortcut_saves_seconds=question.shortcut_saves_seconds,
        )
