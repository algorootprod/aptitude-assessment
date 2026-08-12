"""The 0-100 per-section progress score."""

from dataclasses import dataclass

import pytest

from app.core.constants import SECTION_ORDER
from app.modules.user_topic_mapping.progress import progress_score, section_progress


@dataclass
class Row:
    """Stand-in for the slice of `UserTopicMastery` the progress score reads."""

    section: str
    last_cycle: int
    current_level: int = 2
    mastery_score: float = 0.0


# ---- the formula ----


def test_the_scale_ends_are_exact() -> None:
    """The two anchors that define the scale: bottom of L1 is 0, top of L5 is 100."""
    assert progress_score(1, 0.0) == 0.0
    assert progress_score(5, 100.0) == 100.0


@pytest.mark.parametrize(
    ("level", "expected"),
    [(1, 0.0), (2, 20.0), (3, 40.0), (4, 60.0), (5, 80.0)],
)
def test_each_level_starts_one_equal_slice_higher(level: int, expected: float) -> None:
    """A level's floor. Five levels, so each owns exactly 20 points."""
    assert progress_score(level, 0.0) == pytest.approx(expected)


def test_a_full_raw_score_is_worth_exactly_one_level() -> None:
    """Scoring 100 within a level is worth the same as being promoted — that is the point of
    the uniform-slice formula."""
    assert progress_score(2, 100.0) == progress_score(3, 0.0) == pytest.approx(40.0)


def test_raw_score_is_clamped() -> None:
    assert progress_score(3, 150.0) == progress_score(3, 100.0)
    assert progress_score(3, -20.0) == progress_score(3, 0.0)


def test_level_is_clamped() -> None:
    """The ladder never emits an out-of-range level; this only guards a bad caller."""
    assert progress_score(9, 0.0) == progress_score(5, 0.0)
    assert progress_score(0, 0.0) == progress_score(1, 0.0)


# ---- picking the cohort ----


def test_no_score_before_the_first_evaluation() -> None:
    """Signup assigns no score. Deriving one would report 20.0 off the seeded level 2."""
    rows = [Row(section="quant", last_cycle=1, current_level=2) for _ in range(5)]
    standings = section_progress(rows, cycle_version=1)
    assert set(standings) == set(SECTION_ORDER)
    assert all(s.score == 0.0 for s in standings.values())


def test_unmeasured_reports_no_level_rather_than_a_misleading_one() -> None:
    """Topics are seeded at level 2, so reporting a level here would have to either contradict
    that or contradict the 0.0 score. It reports neither."""
    standings = section_progress([], cycle_version=1)
    assert all(s.level is None and s.raw is None for s in standings.values())


def test_uses_the_topics_the_last_test_covered() -> None:
    """5 mastered answers: raw 100, level still 2 (one signal only opens probation)."""
    rows = [
        Row(section="quant", last_cycle=1, current_level=2, mastery_score=100.0) for _ in range(5)
    ]
    standing = section_progress(rows, cycle_version=2)["quant"]
    assert (standing.level, standing.raw) == (2, 100.0)
    assert standing.score == pytest.approx(40.0)


def test_topics_queued_for_the_next_test_are_excluded() -> None:
    """The `times_tested` trap: it is incremented at *scheduling* time, so the topics queued for
    the in-flight test still read `mastery_score = 0.0`. Counting them would halve this score."""
    answered = [
        Row(section="quant", last_cycle=1, current_level=2, mastery_score=100.0) for _ in range(5)
    ]
    queued = [
        Row(section="quant", last_cycle=2, current_level=2, mastery_score=0.0) for _ in range(5)
    ]
    assert section_progress(answered + queued, cycle_version=2)["quant"].score == pytest.approx(
        40.0
    )


def test_never_scheduled_topics_are_excluded() -> None:
    """A section is scored on what it was asked, not on its whole 17-topic backlog."""
    answered = [Row(section="quant", last_cycle=1, current_level=3, mastery_score=100.0)]
    untouched = [Row(section="quant", last_cycle=0) for _ in range(16)]
    assert section_progress(answered + untouched, cycle_version=2)["quant"].score == pytest.approx(
        60.0
    )


def test_a_section_with_nothing_in_the_last_cycle_scores_zero() -> None:
    rows = [Row(section="quant", last_cycle=1, current_level=5, mastery_score=100.0)]
    assert section_progress(rows, cycle_version=2)["english"].score == 0.0


def test_di_is_scored_from_its_single_topic() -> None:
    """A DI section is one whole set, so its cohort is one row carrying `di_section_score`."""
    rows = [Row(section="di", last_cycle=3, current_level=4, mastery_score=50.0)]
    standing = section_progress(rows, cycle_version=4)["di"]
    assert (standing.level, standing.raw) == (4, 50.0)
    assert standing.score == pytest.approx(70.0)


# ---- the mode ----


def test_the_most_repeated_level_wins() -> None:
    rows = [
        Row(section="quant", last_cycle=1, current_level=level, mastery_score=0.0)
        for level in (2, 4, 4, 4, 5)
    ]
    assert section_progress(rows, cycle_version=2)["quant"].level == 4


def test_a_tied_mode_takes_the_lower_level() -> None:
    """Deterministic, and never overstates. `statistics.mode` would resolve this by input order."""
    rows = [
        Row(section="quant", last_cycle=1, current_level=level, mastery_score=0.0)
        for level in (3, 3, 2, 2, 5)
    ]
    assert section_progress(rows, cycle_version=2)["quant"].level == 2
