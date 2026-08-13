"""Only cross-module entry point into `evaluation_report` (see CLAUDE.md, "Architecture rules").

`evaluate()` is shaped around one constraint: **the candidate should see their report the moment
they finish.** So the request path does only what the report needs —

    1 query    the assembled test          section budgets, order, DI set membership
    1 query    question scoring metadata   answer keys and coaching material
    pure       classify x20 -> build the report
    3 inserts  answers, results, report    all bulk, all ON CONFLICT DO NOTHING
    return     the report

— and everything the *next* test needs is spawned as a background task: the level ladder, the
cycle bump, assembly of tomorrow's paper, and the `evaluation-completed` SQS event. None of that
is needed until the candidate next sits down, so making them wait for ~20 more Neon round-trips
buys nothing.

This inverts the ordering the scaffold described (ladder synchronously, report in the
background), and the trade is explicit: a background task that dies leaves the candidate scored
but not advanced. `reconcile()` is the answer to that, and `user_topic_mapping`'s replay guard is
what makes re-running safe.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.constants import DI_SECTION, EVENT_EVALUATION_COMPLETED, SECTION_ORDER, Quadrant
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.core.tasks import spawn
from app.infrastructure.db.session import session_scope
from app.infrastructure.messaging.publisher import publish
from app.infrastructure.messaging.retry import retry_sync_call
from app.modules.evaluation_report import __version__
from app.modules.evaluation_report.models import UserReport
from app.modules.evaluation_report.report import (
    ClassifiedQuestion,
    SectionOutcome,
    build_actions,
    build_findings,
    build_headline,
    build_question_reviews,
    build_section_table,
    build_tiles,
)
from app.modules.evaluation_report.repository import EvaluationReportRepository
from app.modules.evaluation_report.schemas import (
    AnswerSummaryOut,
    ReconciledCandidate,
    ReconcileOut,
    ReportOut,
    SubmittedAnswer,
    TestCompletedIn,
)
from app.modules.evaluation_report.scoring import classify, di_section_score, is_correct
from app.modules.user_test_mapping.schemas import QuestionScoringOut, SelectedSection
from app.modules.user_test_mapping.service import UserTestMappingService
from app.modules.user_topic_mapping.schemas import EvaluationResultIn, TopicOutcomeIn
from app.modules.user_topic_mapping.service import UserTopicMappingService

_MODULE = "evaluation_report"

log = get_logger(_MODULE)


class EvaluationReportService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = EvaluationReportRepository(session)
        self.test_mapping = UserTestMappingService(session)
        self.topic_mapping = UserTopicMappingService(session)

    async def evaluate(self, payload: TestCompletedIn) -> ReportOut:
        """Serves `POST /v1/tests/complete`. Returns the finished report, not an acknowledgement.

        Idempotent: a resubmitted paper returns the report the candidate already got rather than
        re-scoring, so a flaky network or a double-tapped button cannot produce two verdicts on
        one sitting.
        """
        existing = await self.repo.get_report(payload.user_id, payload.cycle_version)
        if existing is not None:
            log.info(
                "evaluation_replayed",
                user_id=payload.user_id,
                cycle=payload.cycle_version,
            )
            return self._as_report_out(existing)

        sections = await self._resolve(payload)

        await self.repo.save_answers(payload.user_id, payload.cycle_version, payload.answers)
        await self.repo.save_results(
            payload.user_id,
            payload.cycle_version,
            [(q.question_id, q.section, q.quadrant) for s in sections for q in s.questions],
        )
        stored = self.repo.to_model(
            payload.user_id, payload.cycle_version, self._render(sections)
        )
        await self.repo.save_report(stored)

        spawn(
            _run_post_evaluation(
                payload.user_id, payload.cycle_version, topic_outcomes(sections)
            ),
            name=f"post-evaluation:{payload.user_id}:{payload.cycle_version}",
        )

        # Returned from the row that was just written, so this response and every later re-read
        # of the same report are byte-identical.
        return self._as_report_out(stored)

    async def get_report(self, user_id: str, cycle_version: int | None = None) -> ReportOut:
        """Serves `GET /v1/reports/{user_id}`. Latest cycle unless one is named."""
        report = (
            await self.repo.get_latest_report(user_id)
            if cycle_version is None
            else await self.repo.get_report(user_id, cycle_version)
        )
        if report is None:
            where = "" if cycle_version is None else f" for cycle {cycle_version}"
            raise NotFoundError(_MODULE, __version__, f"no report for user {user_id}{where}")
        return self._as_report_out(report)

    async def get_answer_summary(self, user_id: str) -> AnswerSummaryOut:
        """Read by `user_stats` for the profile stats card. Not gated on candidate existence —
        the caller already confirmed that via `user_topic_mapping`; a candidate who exists but
        has sat nothing yet gets zeros, which is a valid answer, not an error."""
        tests_taken, questions_solved, avg_time = await self.repo.answer_summary(user_id)
        return AnswerSummaryOut(
            tests_taken=tests_taken,
            questions_solved=questions_solved,
            avg_time_per_question_seconds=avg_time,
        )

    async def reconcile(self, user_id: str | None = None) -> ReconcileOut:
        """Serves `POST /v1/admin/reconcile`. Re-runs post-evaluation work that never completed.

        A candidate is *stuck* when they have been scored for cycle N but their topic map is
        still on cycle N — the signature of a background task that died between the report being
        written and the ladder being applied. Pass a `user_id` to fix one, or omit it to sweep.

        Safe to run at any time: a candidate who is not stuck is skipped, and
        `update_from_evaluation`'s own replay guard would reject a duplicate anyway.
        """
        scored = await self.repo.latest_evaluated_cycles(user_id)
        current = await self.topic_mapping.list_current_cycles(user_id)

        reconciled: list[ReconciledCandidate] = []
        for candidate, cycle in sorted(scored.items()):
            if current.get(candidate) != cycle:
                continue
            outcomes = await self._outcomes_from_stored(candidate, cycle)
            if not outcomes:
                log.warning("reconcile_no_outcomes", user_id=candidate, cycle=cycle)
                continue
            await _apply_post_evaluation(candidate, cycle, outcomes)
            reconciled.append(
                ReconciledCandidate(
                    user_id=candidate, cycle_version=cycle, action="ladder_applied"
                )
            )
            log.info("reconciled", user_id=candidate, cycle=cycle)

        return ReconcileOut(
            scanned=len(scored),
            reconciled=reconciled,
            skipped=len(scored) - len(reconciled),
        )

    # ---- scoring ----

    async def _resolve(self, payload: TestCompletedIn) -> list[SectionOutcome]:
        """Join the submission to the paper it answers, and classify every question."""
        assembled = await self.test_mapping.get_assembled_test(
            payload.user_id, payload.cycle_version
        )
        if not assembled:
            raise NotFoundError(
                _MODULE,
                __version__,
                f"no assembled test for user {payload.user_id} at cycle "
                f"{payload.cycle_version}",
            )

        question_ids = [q.question_id for s in assembled for q in s.questions]
        metadata = {
            q.id: q for q in await self.test_mapping.get_scoring_metadata(question_ids)
        }
        submitted = {answer.question_id: answer for answer in payload.answers}
        reported_time = {row.section: row.time_used_seconds for row in payload.sections}

        outcomes: list[SectionOutcome] = []
        for section in sorted(assembled, key=lambda s: _section_index(s.section)):
            questions = [
                self._classify_one(slot, metadata.get(slot.question_id), submitted)
                for slot in sorted(section.questions, key=lambda q: q.order)
            ]
            outcomes.append(
                SectionOutcome(
                    section=section.section,
                    budget_seconds=section.budget_seconds,
                    # Node's wall clock if it sent one; otherwise the sum of per-question times,
                    # which slightly under-counts (it misses reading time between questions).
                    time_used_seconds=reported_time.get(
                        section.section, sum(q.elapsed_seconds for q in questions)
                    ),
                    questions=questions,
                )
            )
        return outcomes

    @staticmethod
    def _classify_one(
        slot: Any, question: QuestionScoringOut | None, submitted: dict[str, SubmittedAnswer]
    ) -> ClassifiedQuestion:
        # A question missing from the submission was never answered and never reached.
        answer = submitted.get(
            slot.question_id,
            SubmittedAnswer(question_id=slot.question_id, elapsed_seconds=0, unreached=True),
        )
        expected = (
            question.expected_time_seconds if question else slot.expected_time_seconds
        )
        correct_option = question.answer if question else None
        quadrant: Quadrant = classify(answer, correct_option, expected)
        picked = answer.picked.strip().upper() if answer.picked else None

        return ClassifiedQuestion(
            question_id=slot.question_id,
            section=question.section if question else "",
            topic=question.topic if question else slot.topic,
            concept=question.concept if question else None,
            prerequisite_concept=question.prerequisite_concept if question else None,
            quadrant=quadrant,
            picked=picked,
            correct_option=correct_option,
            is_correct=is_correct(picked, correct_option),
            elapsed_seconds=answer.elapsed_seconds,
            expected_time_seconds=expected,
            order=slot.order,
            question_text=question.question_text if question else "",
            options=question.options if question else [],
            explanation=question.explanation if question else None,
            # Only the option they actually picked, and only when it was wrong — the correct
            # option has no rationale on file by design.
            distractor_rationale=(
                question.distractor_rationale.get(picked)
                if question and picked and not is_correct(picked, correct_option)
                else None
            ),
            shortcut_name=question.shortcut_name if question else None,
            shortcut_how=question.shortcut_how if question else None,
            shortcut_saves_seconds=question.shortcut_saves_seconds if question else None,
        )

    @staticmethod
    def _render(sections: list[SectionOutcome]) -> dict[str, Any]:
        return {
            "headline": build_headline(sections),
            "tiles": [tile.model_dump() for tile in build_tiles(sections)],
            "section_table": [row.model_dump() for row in build_section_table(sections)],
            "findings": [f.model_dump() for f in build_findings(sections)],
            "actions": [a.model_dump() for a in build_actions(sections)],
            "questions": [q.model_dump() for q in build_question_reviews(sections)],
        }

    @staticmethod
    def _as_report_out(report: UserReport) -> ReportOut:
        return ReportOut.model_validate(
            {
                "user_id": report.user_id,
                "cycle_version": report.cycle_version,
                "headline": report.headline,
                "tiles": report.tiles,
                "section_table": report.section_table,
                "findings": report.findings,
                "actions": report.actions,
                "questions": report.questions or [],
                "created_at": report.created_at,
            }
        )

    async def _outcomes_from_stored(
        self, user_id: str, cycle_version: int
    ) -> list[TopicOutcomeIn]:
        """Rebuild the ladder payload from persisted quadrants, for reconcile.

        Reads `evaluation_result` (this module's own table) for the verdicts and the assembled
        test for the DI set's topic — DI's five questions all count towards one topic, which the
        result rows alone do not record.
        """
        results = await self.repo.get_results(user_id, cycle_version)
        if not results:
            return []
        assembled = await self.test_mapping.get_assembled_test(user_id, cycle_version)
        if not assembled:
            return []

        quadrants = {row.question_id: row.quadrant for row in results}
        sections = [
            SectionOutcome(
                section=section.section,
                budget_seconds=section.budget_seconds,
                time_used_seconds=0,
                questions=[
                    _stub_question(slot, section.section, quadrants.get(slot.question_id))
                    for slot in section.questions
                    if slot.question_id in quadrants
                ],
            )
            for section in assembled
        ]
        return topic_outcomes(sections, di_topic_of=_di_topics(assembled))


# ---- ladder payload ----


def topic_outcomes(
    sections: list[SectionOutcome], di_topic_of: dict[str, str] | None = None
) -> list[TopicOutcomeIn]:
    """One outcome per topic, in the two shapes `user_topic_mapping` accepts.

    DI sends a 0-100 score for the section, because its five questions share one chart and one
    topic; every other section sends the quadrant of that topic's single question.
    """
    outcomes: list[TopicOutcomeIn] = []
    for section in sections:
        if not section.questions:
            continue
        if section.section == DI_SECTION:
            topic = (di_topic_of or {}).get(section.section) or section.questions[0].topic
            outcomes.append(
                TopicOutcomeIn(
                    section=DI_SECTION,
                    topic=topic,
                    score=di_section_score(q.quadrant for q in section.questions),
                )
            )
        else:
            outcomes.extend(
                TopicOutcomeIn(
                    section=section.section, topic=question.topic, quadrant=question.quadrant
                )
                for question in section.questions
            )
    return outcomes


def _di_topics(assembled: list[SelectedSection]) -> dict[str, str]:
    return {
        section.section: section.topic
        for section in assembled
        if section.section == DI_SECTION and section.topic
    }


def _stub_question(slot: Any, section: str, quadrant: str | None) -> ClassifiedQuestion:
    """A `ClassifiedQuestion` carrying only what the ladder needs. Used by reconcile, which has
    the stored verdicts but no reason to re-read question content."""
    return ClassifiedQuestion(
        question_id=slot.question_id,
        section=section,
        topic=slot.topic,
        concept=None,
        prerequisite_concept=None,
        quadrant=quadrant or "unreached",  # type: ignore[arg-type]
        picked=None,
        correct_option=None,
        is_correct=False,
        elapsed_seconds=0,
        expected_time_seconds=slot.expected_time_seconds,
        order=slot.order,
        question_text="",
        options=[],
        explanation=None,
        distractor_rationale=None,
        shortcut_name=None,
        shortcut_how=None,
        shortcut_saves_seconds=None,
    )


def _section_index(section: str) -> int:
    return SECTION_ORDER.index(section) if section in SECTION_ORDER else len(SECTION_ORDER)


# ---- background work ----


async def _run_post_evaluation(
    user_id: str, cycle_version: int, outcomes: list[TopicOutcomeIn]
) -> None:
    """Move the ladder, advance the cycle, assemble tomorrow's paper, announce it.

    Runs off the request path via `app.core.tasks.spawn`, so it must not touch the request's
    session — that one is committed and closed by `get_db_session` before this starts.
    """
    await _apply_post_evaluation(user_id, cycle_version, outcomes)
    await _publish_completed(user_id, cycle_version, outcomes)


async def _apply_post_evaluation(
    user_id: str, cycle_version: int, outcomes: list[TopicOutcomeIn]
) -> None:
    """The ladder hop, retried 2-3 times. A fresh session per attempt: a failed transaction
    poisons its session, so retrying on the same one would fail identically."""
    result = EvaluationResultIn(
        user_id=user_id, cycle_version=cycle_version, topic_outcomes=outcomes
    )
    async for attempt in retry_sync_call():
        with attempt:
            async with session_scope() as session:
                await UserTopicMappingService(session).update_from_evaluation(result)


async def _publish_completed(
    user_id: str, cycle_version: int, outcomes: list[TopicOutcomeIn]
) -> None:
    """Best-effort. Nothing consumes this queue yet, and a failure here must not cost the
    candidate their already-applied ladder move."""
    queue_url = get_settings().sqs_evaluation_completed_url
    if not queue_url:
        return
    try:
        await publish(
            queue_url,
            EVENT_EVALUATION_COMPLETED,
            {
                "user_id": user_id,
                "cycle_version": cycle_version,
                "topics_evaluated": len(outcomes),
            },
        )
    except Exception as error:  # noqa: BLE001 - never let announcing break the pipeline
        log.warning(
            "evaluation_completed_publish_failed",
            user_id=user_id,
            cycle=cycle_version,
            error=str(error),
        )
