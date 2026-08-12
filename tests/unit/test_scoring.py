"""The quadrant classifier and the DI section score.

These two decide what the candidate is told about their mistake *and* which way their level
moves, so the boundaries are pinned from both sides rather than sampled.
"""

import pytest

from app.core.config import get_settings
from app.modules.evaluation_report.schemas import SubmittedAnswer
from app.modules.evaluation_report.scoring import classify, di_section_score, is_correct

EXPECTED = 60  # so the careless cutoff sits at a round 30s


def answer(
    picked: str | None = "A", elapsed: int = 30, unreached: bool = False
) -> SubmittedAnswer:
    return SubmittedAnswer(
        question_id="q1", picked=picked, elapsed_seconds=elapsed, unreached=unreached
    )


# ---- correct answers: mastered vs fragile ----


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [
        (1, "mastered"),
        (59, "mastered"),
        (60, "mastered"),  # exactly on budget is still clean
        (61, "fragile"),
        (600, "fragile"),
    ],
)
def test_correct_answers_split_on_the_time_budget(elapsed: int, expected: str) -> None:
    assert classify(answer("A", elapsed), "A", EXPECTED) == expected


# ---- wrong answers: careless vs gap ----


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [
        (0, "careless"),
        (29, "careless"),
        (30, "gap"),  # exactly half the budget is real effort, not haste
        (31, "gap"),
        (600, "gap"),
    ],
)
def test_wrong_answers_split_on_half_the_budget(elapsed: int, expected: str) -> None:
    assert classify(answer("B", elapsed), "A", EXPECTED) == expected


# ---- the two ways of not answering ----


def test_unreached_beats_everything() -> None:
    """The clock ran out. Nothing else about the answer matters."""
    assert classify(answer(None, 0, unreached=True), "A", EXPECTED) == "unreached"
    assert classify(answer("A", 45, unreached=True), "A", EXPECTED) == "unreached"


def test_a_skip_is_careless_not_a_gap() -> None:
    """The prototype's rule: leaving a question blank with time still on the clock is the same
    failure as answering too fast, not evidence of a missing method."""
    assert classify(answer(None, 45), "A", EXPECTED) == "careless"


def test_a_slow_skip_is_still_careless() -> None:
    """Even after burning the whole budget, a blank is a blank."""
    assert classify(answer(None, 120), "A", EXPECTED) == "careless"


# ---- answer-key edge cases ----


def test_missing_answer_key_can_never_be_correct() -> None:
    """`question_bank.answer` is nullable. An unscoreable question must not read as mastered."""
    assert classify(answer("A", 10), None, EXPECTED) == "careless"
    assert classify(answer("A", 50), None, EXPECTED) == "gap"


def test_option_letters_compare_case_insensitively() -> None:
    assert is_correct("a", "A")
    assert is_correct("A", "a")
    assert is_correct(" b ", "B")
    assert not is_correct("B", "A")
    assert not is_correct(None, "A")
    assert not is_correct("A", None)


# ---- DI section score ----


@pytest.mark.parametrize(
    ("quadrants", "expected"),
    [
        (["mastered"] * 5, 100.0),
        (["mastered"] * 4 + ["fragile"], 90.0),
        (["mastered"] * 4 + ["gap"], 80.0),
        (["mastered"] * 3 + ["fragile"] * 2, 80.0),
        (["mastered"] * 2 + ["gap"] * 3, 40.0),
        (["mastered"] + ["gap"] * 4, 20.0),
        (["fragile"] * 5, 50.0),
        (["careless"] * 5, 0.0),
        (["gap"] * 5, 0.0),
        (["unreached"] * 5, 0.0),
    ],
)
def test_di_score_for_representative_mixes(quadrants: list[str], expected: float) -> None:
    assert di_section_score(quadrants) == pytest.approx(expected)  # type: ignore[arg-type]


def test_an_empty_di_section_scores_zero() -> None:
    """Nothing attempted is not evidence of mastery."""
    assert di_section_score([]) == 0.0


@pytest.mark.parametrize(
    ("quadrants", "signal"),
    [
        (["mastered"] * 5, "promote"),  # 100 > 85
        (["mastered"] * 4 + ["fragile"], "promote"),  # 90 > 85
        (["mastered"] * 3 + ["fragile"] * 2, "hold"),  # 80 — two slow answers cost the promotion
        (["mastered"] * 4 + ["gap"], "hold"),  # 80, inside the band
        (["fragile"] * 5, "hold"),  # 50 — knows it, too slow: no move
        (["mastered"] * 2 + ["gap"] * 3, "hold"),  # exactly 40 is not < 40
        (["careless"] * 5, "demote"),  # 0 < 40
        # Scored identically to four gaps: this scale cannot separate rushing from not knowing,
        # which is the distinction DI gives up by sharing the app-wide quadrant score.
        (["mastered"] + ["careless"] * 4, "demote"),  # 20 < 40
        (["mastered"] + ["gap"] * 4, "demote"),  # 20 < 40
        (["gap"] * 5, "demote"),
    ],
)
def test_di_score_lands_in_the_intended_ladder_band(
    quadrants: list[str], signal: str
) -> None:
    """The score only means something against the bands `user_topic_mapping` applies to it, so
    the two are pinned together."""
    settings = get_settings()
    score = di_section_score(quadrants)  # type: ignore[arg-type]
    if score > settings.di_promote_score_threshold:
        assert signal == "promote"
    elif score < settings.di_demote_score_threshold:
        assert signal == "demote"
    else:
        assert signal == "hold"
