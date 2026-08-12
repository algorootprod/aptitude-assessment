"""Fixed shape of the Daily 20 and the quadrant -> ladder-signal mapping.

Tunable values (thresholds, initial level, budget slack) live in `app.core.config` so they can be
overridden per environment; only things that are structural — the section list, the questions per
section, the meaning of a quadrant — belong here.

The evaluation model's thresholds live at the bottom of this file. They are structural rather
than tunable on purpose: they are ported rule-for-rule from `reference/daily20_prototype.html`,
which is the design for the report, not a sketch of it. See CLAUDE.md, "Evaluation model".
"""

from typing import Final, Literal, NamedTuple

Quadrant = Literal["mastered", "fragile", "careless", "gap", "unreached"]

DI_SECTION: Final = "di"

#: Order sections are presented in, and the order `UserTestMapOut.sections` is built in.
SECTION_ORDER: Final[tuple[str, ...]] = ("di", "quant", "reasoning", "english")

QUESTIONS_PER_SECTION: Final = 5

#: How many *topics* each section draws per cycle. DI is 1 because a DI section is one whole
#: `set_id` — all 5 questions share a chart, a topic and a set (see CLAUDE.md, "Question bank").
TOPICS_PER_SECTION: Final[dict[str, int]] = {
    "di": 1,
    "quant": QUESTIONS_PER_SECTION,
    "reasoning": QUESTIONS_PER_SECTION,
    "english": QUESTIONS_PER_SECTION,
}

#: Fallback when a question row has a null `expected_time_seconds` (none do today, but the column
#: is nullable). Per-question, not per-section.
DEFAULT_EXPECTED_TIME_SECONDS: Final[dict[str, int]] = {
    "di": 96,
    "quant": 72,
    "reasoning": 72,
    "english": 60,
}

#: Ladder signal for a non-DI topic, keyed on the quadrant of its single question.
#: +1 opens/confirms a promotion, -1 a demotion, 0 holds the current probation untouched.
#: `fragile` (right but slow) and `careless` (wrong but rushed) are deliberately neutral: neither
#: proves the level is wrong. DI topics ignore this map and band a 0-100 score instead.
QUADRANT_SIGNAL: Final[dict[str, int]] = {
    "mastered": 1,
    "fragile": 0,
    "careless": 0,
    "gap": -1,
    "unreached": 0,
}

#: **The** 0-100 quadrant score for this app — one scale, one meaning, everywhere it is averaged:
#: `user_topic_map.mastery_score` (display, reaches clients via `TopicMastery`), the DI ladder
#: signal (`evaluation_report.scoring.di_section_score`), and the report's per-section score card.
#:
#: Ported from `reference/daily20_prototype.html`: correct and inside the budget earns full credit,
#: correct-but-slow earns half (the method is there, the clock isn't), and everything else earns
#: nothing. Averaging this map is arithmetically identical to the prototype's
#: `(mastered + 0.5 * fragile) / n`, which is what keeps the frontend card and the backend in
#: agreement.
#:
#: `careless` earns 0 because `scoring.classify()` buckets a *deliberate skip* there: crediting it
#: would make skipping a question score higher than attempting it and getting it wrong, and would
#: contradict "a blank scores the same as a wrong answer".
#:
#: Non-DI topics do **not** ladder on this — they use `QUADRANT_SIGNAL` above, which still separates
#: `careless` (neutral) from `gap` (demote). This map cannot: both are 0, so no threshold can tell
#: them apart. That costs DI the distinction, which is tolerable only while DI's ladder is inert for
#: lack of uniform-difficulty sets (see CLAUDE.md, "The level ladder"). If that changes, give DI its
#: own signal function rather than re-splitting this scale.
QUADRANT_MASTERY_SCORE: Final[dict[str, float]] = {
    "mastered": 100.0,
    "fragile": 50.0,
    "careless": 0.0,
    "gap": 0.0,
    "unreached": 0.0,
}

#: Why a question ended up in the test, recorded per slot on `user_test_questions.sections`.
#: The bank is thin at levels 1 and 5 (median 1 and 0-1 questions per topic), so anything other
#: than "exact" is expected traffic, not an anomaly — see CLAUDE.md, "Level supply".
SelectionFallback = Literal["exact", "adjacent", "any_level", "any_topic", "repeat"]

class QuadrantDisplay(NamedTuple):
    label: str
    tone: str
    blurb: str


#: How each quadrant is named and explained to the candidate, verbatim from the prototype's
#: `QUAD` table. The blurb is the whole diagnostic in one sentence, so it is worth keeping exact.
QUADRANT_DISPLAY: Final[dict[str, QuadrantDisplay]] = {
    "mastered": QuadrantDisplay(
        "Mastered", "good", "Right, and inside the time budget."
    ),
    "fragile": QuadrantDisplay(
        "Fragile",
        "warning",
        "Right — but slow enough that the real clock would have caught you.",
    ),
    "careless": QuadrantDisplay(
        "Careless", "serious", "Wrong, and answered far too fast to have worked it through."
    ),
    "gap": QuadrantDisplay(
        "Gap", "critical", "Wrong after real effort. The method itself isn't there yet."
    ),
    "unreached": QuadrantDisplay(
        "Not reached", "critical", "The clock ran out before you got here."
    ),
}

#: Tile order in the report. `unreached` trails the four real verdicts and is shown only when
#: it happened — otherwise the tiles visibly fail to sum to 20.
TILE_ORDER: Final[tuple[Quadrant, ...]] = (
    "mastered",
    "fragile",
    "careless",
    "gap",
    "unreached",
)

#: Human-readable section names, as the prototype writes them in the report.
SECTION_DISPLAY_NAMES: Final[dict[str, str]] = {
    "di": "Data Interpretation",
    "quant": "Quantitative",
    "reasoning": "Reasoning",
    "english": "English",
}


# ---- the evaluation model, ported from daily20_prototype.html's quadOf() and report() ----

#: A correct answer is `mastered` at or under this multiple of `expected_time_seconds`, and
#: `fragile` beyond it.
EXPECTED_TIME_MASTERED_FACTOR: Final = 1.0

#: A wrong answer given in under this fraction of the expected time is `careless` — too fast to
#: have been worked through — rather than a genuine `gap`.
CARELESS_TIME_FRACTION: Final = 0.5

#: Findings fire on patterns, not on single questions: it takes this many in one section.
FINDING_FRAGILE_THRESHOLD: Final = 2
FINDING_CARELESS_THRESHOLD: Final = 2

#: "Rushed with time in hand" only counts as rushing if time really was in hand.
CARELESS_BUDGET_FRACTION: Final = 0.75

#: The same `prerequisite_concept` behind this many gaps anywhere in the paper is one root
#: cause, not two separate mistakes.
FINDING_PREREQUISITE_THRESHOLD: Final = 2

#: Above this fraction of a section's budget, the candidate ran right to the limit.
SECTION_TIME_PRESSURE_FRACTION: Final = 0.92

#: Headline thresholds, first match wins (see `report.build_headline`).
HEADLINE_FRAGILE_THRESHOLD: Final = 3
HEADLINE_CARELESS_THRESHOLD: Final = 3
HEADLINE_GAP_THRESHOLD: Final = 5
HEADLINE_MASTERED_THRESHOLD: Final = 15

#: Actions are a prescription, not an inventory — six is as many as anyone acts on.
MAX_ACTIONS: Final = 6

#: Topics named in the "these were clean" roll-up before it trails off.
MASTERED_TOPICS_NAMED: Final = 4

#: Priority order for actions, lowest first. A knowledge gap outranks a missing shortcut, which
#: outranks pacing advice.
ACTION_PRIORITY_GAP: Final = 0
ACTION_PRIORITY_FRAGILE: Final = 1
ACTION_PRIORITY_CARELESS: Final = 2

#: Option letters, in order. Indexes into `options` and keys `distractor_rationale`.
OPTION_LETTERS: Final[tuple[str, ...]] = ("A", "B", "C", "D")

#: SQS event name published after a completed evaluation.
EVENT_EVALUATION_COMPLETED: Final = "evaluation-completed"
