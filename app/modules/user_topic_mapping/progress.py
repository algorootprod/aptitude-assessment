"""How far through the 1-5 ladder each section is, as a pure function.

A level on its own is too coarse to show a candidate — it moves once every few cycles and says
nothing about how the last sitting went. This turns `(level, raw score)` into a single 0-100
figure where each level owns an equal slice of the scale:

    O = (100 / Λ) × [(L - 1) + s / 100]        Λ = settings.max_topic_level = 5

    L1  0-20    L2  20-40    L3  40-60    L4  60-80    L5  80-100

So L5 with a raw 100 is exactly 100 and L1 with a raw 0 is exactly 0, and "moved up a level" and
"scored full marks within this level" are worth the same 20 points.

Ported from apex-assessment's `app/core/cefr.py:skill_progress_score`, which apex applies to
sections as well as skills (`user_test_mapping/service.py`). Only the formula is ported: apex's
CEFR ladder (A1-C2) has no counterpart here, and its `LEVEL_BANDS` table is a *different*,
non-uniform concept that must not be mixed into this one.

Nothing here is persisted. Both inputs already sit on `user_topic_map`, so the score is derived
on read — see `section_progress` for which rows count.
"""

from dataclasses import dataclass
from statistics import multimode
from typing import Protocol

from app.core.config import get_settings
from app.core.constants import SECTION_ORDER


@dataclass(frozen=True)
class SectionStanding:
    """One section's derived standing. `score` is what a candidate sees; `level` and `raw` are
    carried so the figure can be explained ("L3, 62% through") and debugged without a DB query.

    `level` and `raw` are `None` when the section has never been evaluated, which is *not* the
    same as scoring zero. Reporting a number there would have to either contradict the topics'
    real seeded level or contradict the score, so it reports neither.
    """

    level: int | None
    raw: float | None
    score: float


class ScorableTopic(Protocol):
    """The slice of a `UserTopicMastery` row this needs, so it stays testable without a DB."""

    @property
    def section(self) -> str: ...

    @property
    def last_cycle(self) -> int: ...

    @property
    def current_level(self) -> int: ...

    @property
    def mastery_score(self) -> float: ...


def progress_score(level: int, raw_score: float) -> float:
    """Map one `(level, 0-100 raw score)` pair onto the global 0-100 scale.

    Both inputs are clamped: the ladder already guarantees the level is in range, and
    `mastery_score` is 0-100 by construction, so this only guards against a caller passing
    something the ladder never produced.
    """
    settings = get_settings()
    total_levels = settings.max_topic_level
    clamped_level = max(settings.min_topic_level, min(settings.max_topic_level, level))
    clamped_raw = max(0.0, min(100.0, raw_score))
    return (100.0 / total_levels) * ((clamped_level - 1) + clamped_raw / 100.0)


def section_progress(
    rows: list[ScorableTopic], cycle_version: int
) -> dict[str, SectionStanding]:
    """One 0-100 figure per section, from the topics the candidate's **last test** covered.

    `s` is that cohort's mean `mastery_score` — which for a non-DI section is exactly the section
    score the report shows, since each topic there contributes one question's
    `QUADRANT_MASTERY_SCORE`, and for DI is the set's own `di_section_score`. `L` is the most
    repeated level in the cohort, taking the lowest on a tie so the figure never overstates.

    The cohort is `last_cycle == cycle_version - 1`, **not** `times_tested > 0`: `times_tested` is
    incremented when a topic is *scheduled* (`repository.mark_scheduled`), so it counts topics
    queued for the in-flight test whose `mastery_score` is still 0.0, and including those would
    drag every section down. Keying on `last_cycle` also survives the next test not having been
    assembled yet, since it looks backwards rather than at the newest rows.

    Before the first evaluation there is nothing to measure and every section scores 0.0 — the
    candidate is seeded at level 2, so deriving it would otherwise report a meaningless 20.0.
    """
    unmeasured = SectionStanding(level=None, raw=None, score=0.0)
    if cycle_version <= 1:
        return {section: unmeasured for section in SECTION_ORDER}

    evaluated_cycle = cycle_version - 1
    standings: dict[str, SectionStanding] = {}
    for section in SECTION_ORDER:
        cohort = [
            row for row in rows if row.section == section and row.last_cycle == evaluated_cycle
        ]
        if not cohort:
            standings[section] = unmeasured
            continue
        level = min(multimode([row.current_level for row in cohort]))
        raw = sum(row.mastery_score for row in cohort) / len(cohort)
        standings[section] = SectionStanding(
            level=level, raw=raw, score=progress_score(level, raw)
        )
    return standings
