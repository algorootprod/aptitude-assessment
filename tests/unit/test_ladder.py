"""The level ladder: signal derivation and the probation state machine.

These are the rules a candidate's level actually moves by, so they are pinned exhaustively
rather than sampled — every (pending_dir, signal) pair, both clamps, and both sides of each
DI score threshold.
"""

import pytest

from app.core.config import get_settings
from app.modules.user_topic_mapping.ladder import (
    apply_signal,
    mastery_score_for,
    next_streak,
    signal_for,
)
from app.modules.user_topic_mapping.schemas import TopicOutcomeIn


def quant(quadrant: str) -> TopicOutcomeIn:
    return TopicOutcomeIn(section="quant", topic="Ages", quadrant=quadrant)  # type: ignore[arg-type]


def di(score: float) -> TopicOutcomeIn:
    return TopicOutcomeIn(section="di", topic="Bar Charts", score=score)


# ---- signal_for: non-DI quadrants ----


@pytest.mark.parametrize(
    ("quadrant", "expected"),
    [
        ("mastered", 1),
        ("gap", -1),
        # Right-but-slow and wrong-but-rushed are evidence about pacing, not about level.
        ("fragile", 0),
        ("careless", 0),
        ("unreached", 0),
    ],
)
def test_quadrant_signal(quadrant: str, expected: int) -> None:
    assert signal_for(quant(quadrant)) == expected


# ---- signal_for: DI score bands ----


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (100.0, 1),
        (85.01, 1),
        (85.0, 0),  # the threshold itself holds — promotion needs *strictly* above
        (60.0, 0),
        (40.0, 0),  # likewise on the way down
        (39.99, -1),
        (0.0, -1),
    ],
)
def test_di_score_bands(score: float, expected: int) -> None:
    assert signal_for(di(score)) == expected


def test_di_thresholds_come_from_settings() -> None:
    settings = get_settings()
    assert settings.di_promote_score_threshold == 85.0
    assert settings.di_demote_score_threshold == 40.0


# ---- apply_signal: the full transition table ----


@pytest.mark.parametrize(
    ("pending", "signal", "expected_level", "expected_pending"),
    [
        # No signal: the level holds and so does any open probation, which is what lets a
        # probation survive the four cycles strict round-robin takes to revisit a topic.
        (0, 0, 3, 0),
        (1, 0, 3, 1),
        (-1, 0, 3, -1),
        # Neutral -> open a probation in the signal's direction.
        (0, 1, 3, 1),
        (0, -1, 3, -1),
        # Confirmed -> move the level, close the probation.
        (1, 1, 4, 0),
        (-1, -1, 2, 0),
        # Contradicted -> cancel to neutral. Never flips straight to the opposite probation.
        (1, -1, 3, 0),
        (-1, 1, 3, 0),
    ],
)
def test_transition_table(
    pending: int, signal: int, expected_level: int, expected_pending: int
) -> None:
    assert apply_signal(3, pending, signal) == (expected_level, expected_pending)


def test_promotion_at_max_level_consumes_the_probation() -> None:
    """Pinned at the top: the level holds, but the probation is spent rather than left open, so
    the candidate re-earns it instead of promoting the instant a level 6 appears."""
    assert apply_signal(5, 1, 1) == (5, 0)


def test_demotion_at_min_level_consumes_the_probation() -> None:
    assert apply_signal(1, -1, -1) == (1, 0)


def test_two_consecutive_mastered_promotes() -> None:
    level, pending = 2, 0
    level, pending = apply_signal(level, pending, signal_for(quant("mastered")))
    assert (level, pending) == (2, 1), "first success only opens probation"
    level, pending = apply_signal(level, pending, signal_for(quant("mastered")))
    assert (level, pending) == (3, 0)


def test_a_gap_between_two_masters_prevents_promotion() -> None:
    """mastered, gap, mastered ends where it started: the gap cancels, and the third result
    only re-opens the probation."""
    level, pending = 2, 0
    for quadrant in ("mastered", "gap", "mastered"):
        level, pending = apply_signal(level, pending, signal_for(quant(quadrant)))
    assert (level, pending) == (2, 1)


def test_fragile_does_not_disturb_an_open_probation() -> None:
    level, pending = apply_signal(2, 0, signal_for(quant("mastered")))
    level, pending = apply_signal(level, pending, signal_for(quant("fragile")))
    assert (level, pending) == (2, 1)
    level, pending = apply_signal(level, pending, signal_for(quant("mastered")))
    assert (level, pending) == (3, 0)


def test_di_promotes_on_two_high_scores() -> None:
    level, pending = 2, 0
    for score in (88.0, 91.0):
        level, pending = apply_signal(level, pending, signal_for(di(score)))
    assert (level, pending) == (3, 0)


def test_di_mid_band_score_does_not_move_anything() -> None:
    assert apply_signal(3, 0, signal_for(di(61.0))) == (3, 0)


# ---- display-only fields ----


def test_mastery_score_uses_the_raw_di_score() -> None:
    assert mastery_score_for(di(88.0)) == 88.0


@pytest.mark.parametrize(
    ("quadrant", "expected"),
    [("mastered", 100.0), ("fragile", 50.0), ("careless", 0.0), ("gap", 0.0)],
)
def test_mastery_score_maps_quadrants(quadrant: str, expected: float) -> None:
    assert mastery_score_for(quant(quadrant)) == expected


def test_a_skip_scores_no_higher_than_a_wrong_answer() -> None:
    """`classify()` files a deliberate skip under `careless`, so crediting that quadrant would make
    skipping a question outscore attempting it and getting it wrong."""
    assert mastery_score_for(quant("careless")) == mastery_score_for(quant("gap")) == 0.0


def test_streak_counts_only_consecutive_positive_signals() -> None:
    assert next_streak(0, 1) == 1
    assert next_streak(3, 1) == 4
    assert next_streak(3, 0) == 0
    assert next_streak(3, -1) == 0


# ---- schema validation: the two-shaped outcome row ----


def test_di_outcome_requires_a_score() -> None:
    with pytest.raises(ValueError, match="requires `score`"):
        TopicOutcomeIn(section="di", topic="Bar Charts", quadrant="mastered")


def test_non_di_outcome_requires_a_quadrant() -> None:
    with pytest.raises(ValueError, match="requires `quadrant`"):
        TopicOutcomeIn(section="quant", topic="Ages", score=88.0)


def test_outcome_rejects_both_signals_at_once() -> None:
    with pytest.raises(ValueError, match="must not send `score`"):
        TopicOutcomeIn(section="quant", topic="Ages", quadrant="mastered", score=88.0)
