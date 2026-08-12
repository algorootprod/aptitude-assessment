"""Schema only; no assembly logic (see service.py). Column choices for `UserTestQuestions` —
especially whether `sections` should store a resolved question list or a selection recipe —
are an open question for this module's design pass (see CLAUDE.md, "Open questions").

`QuestionBank` is different: it's a straight mapping of an already-real table. The curated
question set was loaded into Neon as `daily20_questions` independently of this scaffold, then
renamed to `question_bank` by migration `0002` once that was discovered — see CLAUDE.md, "Live
Neon database already had a question table". These columns are copied from that table's actual
`information_schema.columns`, not guessed from `daily20_prototype.html` (an earlier version of
this model did, and was wrong — flat `option_a`-`option_d` here, not a JSONB `options` array;
`chart_type`/`chart_image`/`chart_image_svg`/`chart_direction`/`chart_data` here, not one
`chart` text column).
"""

from datetime import datetime

from sqlalchemy import (
    CHAR,
    JSON,
    Boolean,
    DateTime,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class QuestionBank(Base):
    """The curated question set. Owned here as a placeholder — ownership moves to
    `question_generation` in Phase 2, per apex-assessment's convention where the generating
    module owns the bank and other modules read it only through its `service.py`.

    Existing physical indexes/constraints from before the `0002` rename keep their original
    names (`daily20_questions_pkey`, `daily20_questions_answer_check`,
    `idx_daily20_section_topic`, `idx_daily20_difficulty`) — not re-declared here to avoid a
    future `alembic revision --autogenerate` proposing to rename them for no functional gain.
    """

    __tablename__ = "question_bank"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    section: Mapped[str] = mapped_column(Text)
    topic: Mapped[str] = mapped_column(Text)
    concept: Mapped[str | None] = mapped_column(Text, nullable=True)
    prerequisite_concept: Mapped[str | None] = mapped_column(Text, nullable=True)
    method_tag: Mapped[str | None] = mapped_column(Text, nullable=True)
    question_text: Mapped[str] = mapped_column(Text)
    option_a: Mapped[str | None] = mapped_column(Text, nullable=True)
    option_b: Mapped[str | None] = mapped_column(Text, nullable=True)
    option_c: Mapped[str | None] = mapped_column(Text, nullable=True)
    option_d: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer: Mapped[str | None] = mapped_column(CHAR(1), nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    distractor_rationale_a: Mapped[str | None] = mapped_column(Text, nullable=True)
    distractor_rationale_b: Mapped[str | None] = mapped_column(Text, nullable=True)
    distractor_rationale_c: Mapped[str | None] = mapped_column(Text, nullable=True)
    distractor_rationale_d: Mapped[str | None] = mapped_column(Text, nullable=True)
    shortcut_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    shortcut_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    shortcut_how: Mapped[str | None] = mapped_column(Text, nullable=True)
    shortcut_saves_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    difficulty: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    expected_time_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    calibration: Mapped[str | None] = mapped_column(Text, nullable=True)
    batch_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    set_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    chart_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    chart_image: Mapped[str | None] = mapped_column(Text, nullable=True)
    chart_image_svg: Mapped[str | None] = mapped_column(Text, nullable=True)
    chart_direction: Mapped[str | None] = mapped_column(Text, nullable=True)
    chart_data: Mapped[dict[str, object] | list[object] | None] = mapped_column(
        JSONB, nullable=True
    )


class UserTestQuestions(Base):
    """The assembled test for one candidate cycle. `sections` shape TBD — see module
    docstring above."""

    __tablename__ = "user_test_questions"
    __table_args__ = (
        UniqueConstraint("user_id", "cycle_version", name="uq_user_test_questions_cycle"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    cycle_version: Mapped[int] = mapped_column(Integer)
    sections: Mapped[dict[str, object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
