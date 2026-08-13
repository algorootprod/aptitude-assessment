"""All SQL for `user_answers`, `evaluation_result` and `user_reports` lives here — this
module is the sole writer of all three tables (see CLAUDE.md, "Architecture rules")."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.evaluation_report.models import EvaluationResult, UserAnswer, UserReport
from app.modules.evaluation_report.schemas import SubmittedAnswer


class EvaluationReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save_answers(
        self, user_id: str, cycle_version: int, answers: list[SubmittedAnswer]
    ) -> None:
        """Raw submission, exactly as it arrived. `ON CONFLICT DO NOTHING` on
        (user_id, cycle_version, question_id) so a resubmitted paper cannot overwrite the first
        attempt — the candidate's original timings are the evidence the report rests on."""
        if not answers:
            return
        stmt = (
            pg_insert(UserAnswer)
            .values(
                [
                    {
                        "user_id": user_id,
                        "cycle_version": cycle_version,
                        "question_id": answer.question_id,
                        "picked": answer.picked,
                        "elapsed_seconds": answer.elapsed_seconds,
                        "unreached": answer.unreached,
                    }
                    for answer in answers
                ]
            )
            .on_conflict_do_nothing(constraint="uq_user_answers_cycle_question")
        )
        await self.session.execute(stmt)

    async def save_results(
        self, user_id: str, cycle_version: int, results: list[tuple[str, str, str]]
    ) -> None:
        """Per-question quadrants as `(question_id, section, quadrant)`."""
        if not results:
            return
        stmt = (
            pg_insert(EvaluationResult)
            .values(
                [
                    {
                        "user_id": user_id,
                        "cycle_version": cycle_version,
                        "question_id": question_id,
                        "section": section,
                        "quadrant": quadrant,
                    }
                    for question_id, section, quadrant in results
                ]
            )
            .on_conflict_do_nothing(constraint="uq_evaluation_result_cycle_question")
        )
        await self.session.execute(stmt)

    async def get_results(self, user_id: str, cycle_version: int) -> list[EvaluationResult]:
        """The stored quadrants for one cycle. `POST /v1/admin/reconcile` rebuilds a dead
        background task's ladder input from these rather than re-scoring the paper."""
        stmt = (
            select(EvaluationResult)
            .where(
                EvaluationResult.user_id == user_id,
                EvaluationResult.cycle_version == cycle_version,
            )
            .order_by(EvaluationResult.question_id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def save_report(self, report: UserReport) -> None:
        """`ON CONFLICT DO NOTHING` on (user_id, cycle_version) — the report a candidate first
        saw is the report they keep."""
        stmt = (
            pg_insert(UserReport)
            .values(
                user_id=report.user_id,
                cycle_version=report.cycle_version,
                headline=report.headline,
                tiles=report.tiles,
                section_table=report.section_table,
                findings=report.findings,
                actions=report.actions,
                questions=report.questions,
                created_at=report.created_at,
            )
            .on_conflict_do_nothing(constraint="uq_user_reports_cycle")
        )
        await self.session.execute(stmt)

    async def get_report(self, user_id: str, cycle_version: int) -> UserReport | None:
        stmt = select(UserReport).where(
            UserReport.user_id == user_id, UserReport.cycle_version == cycle_version
        )
        result = await self.session.execute(stmt)
        return result.scalars().one_or_none()

    async def get_latest_report(self, user_id: str) -> UserReport | None:
        stmt = (
            select(UserReport)
            .where(UserReport.user_id == user_id)
            .order_by(UserReport.cycle_version.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalars().one_or_none()

    async def answer_summary(self, user_id: str) -> tuple[int, int, float | None]:
        """`(tests_taken, questions_solved, avg_time_per_question_seconds)` across every cycle
        the candidate has sat, straight off `user_answers` — for `user_stats`'s profile card.

        `tests_taken` counts distinct `cycle_version`s rather than reading `user_topic_map`,
        so it reflects cycles actually *sat*, not cycles assembled. `questions_solved` and the
        average exclude `unreached` rows so an expired-clock zero cannot drag the average down
        or count as a "solved" question.
        """
        stmt = select(
            func.count(func.distinct(UserAnswer.cycle_version)),
            func.count().filter(UserAnswer.picked.is_not(None), UserAnswer.unreached.is_(False)),
            func.avg(UserAnswer.elapsed_seconds).filter(UserAnswer.unreached.is_(False)),
        ).where(UserAnswer.user_id == user_id)
        result = await self.session.execute(stmt)
        tests_taken, questions_solved, avg_time = result.one()
        return (
            int(tests_taken),
            int(questions_solved),
            round(float(avg_time), 2) if avg_time is not None else None,
        )

    async def latest_evaluated_cycles(self, user_id: str | None = None) -> dict[str, int]:
        """Highest cycle each candidate has been *scored* for, from `evaluation_result`.

        Reconcile compares this against `user_topic_mapping`'s current cycle: a candidate scored
        for cycle N whose topic map is still on N had their post-evaluation task die. One query
        here, one on the other module's own table, joined in Python — neither module reads the
        other's tables (see CLAUDE.md, "Architecture rules").
        """
        stmt = select(
            EvaluationResult.user_id, func.max(EvaluationResult.cycle_version)
        ).group_by(EvaluationResult.user_id)
        if user_id is not None:
            stmt = stmt.where(EvaluationResult.user_id == user_id)
        result = await self.session.execute(stmt)
        return {row[0]: int(row[1]) for row in result.all()}

    @staticmethod
    def to_model(user_id: str, cycle_version: int, payload: dict[str, Any]) -> UserReport:
        """Build the row from an already-rendered report body.

        `created_at` is stamped here rather than left to the column's server default, so the
        report returned by `POST /v1/tests/complete` carries the same timestamp the stored row
        has. Otherwise the first response would have a null `created_at` and a later re-read of
        the same report a populated one.
        """
        return UserReport(
            user_id=user_id,
            cycle_version=cycle_version,
            headline=payload["headline"],
            tiles=payload["tiles"],
            section_table=payload["section_table"],
            findings=payload["findings"],
            actions=payload["actions"],
            questions=payload["questions"],
            created_at=datetime.now(UTC),
        )
