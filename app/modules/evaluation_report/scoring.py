"""Pure classifier — no I/O.

The quadrant rule, ported from `reference/daily20_prototype.html`'s `quadOf()`. It is the whole
point of the product: not *whether* the candidate got it right, but what kind of mistake it was.

    mastered   correct, inside the time budget
    fragile    correct, over the time budget          knows it, too slow
    careless   wrong,   under half the budget          rushed, not a knowledge gap
    gap        wrong,   at or over half the budget     the method isn't there yet
    unreached  the section clock expired first

Every threshold lives in `app/core/constants.py`.
"""

from collections.abc import Iterable

from app.core.constants import (
    CARELESS_TIME_FRACTION,
    EXPECTED_TIME_MASTERED_FACTOR,
    QUADRANT_MASTERY_SCORE,
    Quadrant,
)
from app.modules.evaluation_report.schemas import SubmittedAnswer


def is_correct(picked: str | None, correct_option: str | None) -> bool:
    """Case-insensitive option-letter match. False if either side is missing — a question with
    no answer key on file (the column is nullable) can never be scored correct."""
    if picked is None or correct_option is None:
        return False
    return picked.strip().upper() == correct_option.strip().upper()


def classify(
    answer: SubmittedAnswer, correct_option: str | None, expected_time_seconds: int
) -> Quadrant:
    """Return the quadrant for one submitted answer.

    Note the second branch: a **skip** — no answer picked, but the clock had not run out — is
    `careless`, not `gap`. The candidate had the time and chose not to use it, which is the same
    failure mode as answering too fast, and the prototype scores it the same way. Only a
    genuinely unreached question is `unreached`.
    """
    if answer.unreached:
        return "unreached"
    if answer.picked is None:
        return "careless"

    if is_correct(answer.picked, correct_option):
        mastered_limit = expected_time_seconds * EXPECTED_TIME_MASTERED_FACTOR
        return "mastered" if answer.elapsed_seconds <= mastered_limit else "fragile"

    careless_limit = expected_time_seconds * CARELESS_TIME_FRACTION
    return "careless" if answer.elapsed_seconds < careless_limit else "gap"


def di_section_score(quadrants: Iterable[Quadrant]) -> float:
    """The 0-100 score for a DI section, which `user_topic_mapping` bands at 85/40.

    A DI section is one whole `set_id` — five questions sharing a chart and a single topic — so
    its ladder signal has to come from the section as a whole rather than from one question's
    quadrant. This is the only place that number is produced; `user_topic_mapping` never derives
    it, and the prototype has no scoring function to port because it deliberately never shows a
    score.

    Averaging `QUADRANT_MASTERY_SCORE` — the app's single quadrant scale, shared with
    `user_topic_mapping`'s display field and the report's section-score card — keeps speed in the
    signal, which is the product's whole thesis: five correct-but-slow answers score 50 and hold
    the level rather than promoting it.

    Note this scale scores `careless` and `gap` alike (both 0), so a DI section cannot signal
    "rushed" apart from "doesn't know it" — see the constant's own note for why that is accepted.

    An empty section scores 0 — nothing attempted is not evidence of mastery.
    """
    values = [QUADRANT_MASTERY_SCORE[quadrant] for quadrant in quadrants]
    if not values:
        return 0.0
    return sum(values) / len(values)
