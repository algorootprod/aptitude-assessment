"""Per-candidate topic state — this module's tables, and their sole writer.

`user_topic_map` holds one row per (candidate, topic) and only *current* state. No ladder history
lives there: the per-cycle record of what was asked is `user_test_questions`, and of how it went is
`evaluation_result`, so any level movement is reconstructible by joining those two on
`cycle_version` rather than by duplicating it.

`user_section_progress` is the deliberate exception — one row per (candidate, section, cycle),
appended on every evaluation. It is not a duplicate of the above: it is the 0-100 figure the
candidate is actually shown, and recomputing a whole time series on read would mean reaching into
two other modules' tables and risking a number that disagrees with the one already on screen.
"""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class UserTopicMastery(Base):
    """One row per (user, topic). `cycle_version` mirrors the candidate's evaluation-cycle
    counter used across every pipeline table (see CLAUDE.md, "Architecture rules") and is
    owned here: `update_from_evaluation` is the only thing that increments it."""

    __tablename__ = "user_topic_map"
    __table_args__ = (Index("ix_user_topic_map_user_id_section", "user_id", "section"),)

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    topic: Mapped[str] = mapped_column(String(128), primary_key=True)

    section: Mapped[str] = mapped_column(String(32))
    """Denormalized from `question_bank` so rotation can be ordered per section without a join."""

    current_level: Mapped[int] = mapped_column(SmallInteger, default=2)
    """1-5, seeded from `settings.initial_topic_level`. Maps 1:1 onto `question_bank.difficulty`."""

    pending_dir: Mapped[int] = mapped_column(SmallInteger, default=0)
    """Probation: +1 promotion pending, -1 demotion pending, 0 neutral. A second signal in the
    same direction moves the level; a contradictory one cancels back to 0. See `ladder.py`."""

    last_cycle: Mapped[int] = mapped_column(Integer, default=0)
    """Cycle this topic was last *scheduled* into a test (not last answered). Drives strict
    round-robin: order by (last_cycle, topic) and take the first N. 0 = never scheduled."""

    times_tested: Mapped[int] = mapped_column(Integer, default=0)

    cycle_version: Mapped[int] = mapped_column(Integer, default=1)

    mastery_score: Mapped[float] = mapped_column(Float, default=0.0)
    """Last observed 0-100 score. **This column** is display-only — nothing reads it back to move a
    level. DI stores its section score, other sections `QUADRANT_MASTERY_SCORE[quadrant]`; since the
    former is an average of the latter, the column is on one scale across every section.

    (The *scale* does feed the DI ladder, via `evaluation_report.scoring.di_section_score`. Non-DI
    topics ladder on `QUADRANT_SIGNAL` instead.)"""

    streak: Mapped[int] = mapped_column(Integer, default=0)
    """Consecutive cycles with a positive signal for this topic; reset by anything else."""

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UserSectionProgress(Base):
    """One section's 0-100 standing after one evaluated test — the progress chart's data points.

    Appended by `update_from_evaluation` and never updated in place, so the series is the record of
    what the candidate was shown at each cycle. `cycle_version` is the cycle that was **evaluated**,
    not the one it advanced to. Added by migration 0005.
    """

    __tablename__ = "user_section_progress"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "section", "cycle_version", name="uq_user_section_progress_cycle"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    section: Mapped[str] = mapped_column(String(32))
    cycle_version: Mapped[int] = mapped_column(Integer)

    current_level: Mapped[int] = mapped_column(SmallInteger)
    """Most repeated level among the topics that test covered — see `progress.section_progress`."""

    raw_score: Mapped[float] = mapped_column(Float)
    """0-100 mean `mastery_score` of those same topics: that sitting's section score."""

    progress_score: Mapped[float] = mapped_column(Float)
    """0-100. Stored rather than recomputed so this point can never disagree with the figure the
    candidate saw, even if the formula or its inputs change later."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
