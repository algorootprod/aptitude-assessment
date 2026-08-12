"""Schema only; the four-step `evaluate()` pipeline is not implemented yet (see service.py).
Whether `UserReport` should store the fully-derived report or a leaner set of fields re-derived
at read time is an open question for this module's design pass (see CLAUDE.md,
"Open questions")."""

from datetime import datetime

from sqlalchemy import (
    CHAR,
    Boolean,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class UserAnswer(Base):
    """Raw submitted answers — persisted in their own transaction before any scoring, so
    they survive a downstream scoring failure (see CLAUDE.md, "Architecture rules",
    two-transaction evaluate())."""

    __tablename__ = "user_answers"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "cycle_version", "question_id", name="uq_user_answers_cycle_question"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    cycle_version: Mapped[int] = mapped_column(Integer)
    question_id: Mapped[str] = mapped_column(String(128))
    picked: Mapped[str | None] = mapped_column(CHAR(1), nullable=True)
    elapsed_seconds: Mapped[int] = mapped_column(Integer, default=0)
    unreached: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvaluationResult(Base):
    """Per-question quadrant classification — the output of `scoring.classify()`
    (see CLAUDE.md, "Evaluation model")."""

    __tablename__ = "evaluation_result"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "cycle_version", "question_id", name="uq_evaluation_result_cycle_question"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    cycle_version: Mapped[int] = mapped_column(Integer)
    question_id: Mapped[str] = mapped_column(String(128))
    section: Mapped[str] = mapped_column(String(32))
    quadrant: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserReport(Base):
    """The rendered diagnostic report for one candidate cycle — built by `report.py`.

    Stored fully rendered rather than re-derived at read time, so a report is an immutable record
    of what the candidate was actually shown: correcting an explanation in `question_bank`, or
    retiring a question altogether, must not silently rewrite a report they already read.
    """

    __tablename__ = "user_reports"
    __table_args__ = (
        UniqueConstraint("user_id", "cycle_version", name="uq_user_reports_cycle"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    cycle_version: Mapped[int] = mapped_column(Integer)
    headline: Mapped[str] = mapped_column(String(256))
    tiles: Mapped[list[object]] = mapped_column(JSONB)
    section_table: Mapped[list[object]] = mapped_column(JSONB)
    findings: Mapped[list[object]] = mapped_column(JSONB)
    actions: Mapped[list[object]] = mapped_column(JSONB)
    questions: Mapped[list[object]] = mapped_column(JSONB, default=list)
    """Per-question review — the worked explanation, why the picked option was tempting, and the
    faster route. Added by migration 0004."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
