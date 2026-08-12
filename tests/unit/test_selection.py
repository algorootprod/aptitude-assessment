"""The question-selection fallback ladder.

The bank is thin at the extremes — the median topic holds one level-1 question and zero or one
level-5 questions — so falling back off the asked-for level is the normal path, and each of these
cases is real traffic rather than a defensive branch.
"""

from dataclasses import dataclass

from app.modules.user_test_mapping.selection import pick_di_set, pick_question


@dataclass
class Q:
    """Stand-in for the slice of `QuestionBank` selection reads."""

    id: str
    difficulty: int | None


def bank() -> list[Q]:
    """A topic shaped like a typical real one: {L1:1, L2:2, L3:2, L4:1}, no L5."""
    return [
        Q("t.001", 1),
        Q("t.002", 2),
        Q("t.003", 2),
        Q("t.004", 3),
        Q("t.005", 3),
        Q("t.006", 4),
    ]


def pick(level: int, seen: set[str] | None = None, order: dict[str, int] | None = None):  # type: ignore[no-untyped-def]
    return pick_question(
        level=level, candidates=bank(), seen_ids=seen or set(), seen_order=order or {}
    )


# ---- the four steps ----


def test_exact_level_is_preferred() -> None:
    question, fallback = pick(3)
    assert (question.id, fallback) == ("t.004", "exact")


def test_lowest_id_wins_within_a_level_so_replays_are_identical() -> None:
    question, _ = pick(2)
    assert question.id == "t.002"


def test_falls_back_to_an_adjacent_level_when_the_exact_one_is_used_up() -> None:
    question, fallback = pick(1, seen={"t.001"})
    assert (question.id, fallback) == ("t.002", "adjacent")


def test_adjacent_prefers_the_nearer_level() -> None:
    """Asking for 5 in a topic whose highest is 4: L4 beats L3 even though L3 has more supply."""
    question, fallback = pick(5)
    assert (question.id, fallback) == ("t.006", "adjacent")


def test_falls_back_to_any_level_when_neither_neighbour_is_left() -> None:
    question, fallback = pick(1, seen={"t.001", "t.002", "t.003"})
    assert fallback == "any_level"
    assert question.id in {"t.004", "t.005", "t.006"}


def test_any_level_still_reaches_for_the_nearest_available() -> None:
    question, _ = pick(1, seen={"t.001", "t.002", "t.003"})
    assert question.id == "t.004", "level 3 is nearer to 1 than level 4 is"


def test_repeats_only_once_the_whole_topic_is_exhausted() -> None:
    seen = {q.id for q in bank()}
    question, fallback = pick(3, seen=seen, order={q.id: i for i, q in enumerate(bank(), 1)})
    assert fallback == "repeat"
    assert question.id == "t.001", "the least recently seen question comes back first"


def test_repeat_prefers_the_least_recently_seen_over_the_closest_level() -> None:
    seen = {q.id for q in bank()}
    order = {"t.004": 9, "t.005": 9, "t.001": 2, "t.002": 3, "t.003": 4, "t.006": 5}
    question, fallback = pick(3, seen=seen, order=order)
    assert (question.id, fallback) == ("t.001", "repeat")


def test_a_topic_with_no_questions_at_all_yields_nothing() -> None:
    assert pick_question(level=2, candidates=[], seen_ids=set(), seen_order={}) is None


def test_null_difficulty_rows_do_not_crash_selection() -> None:
    result = pick_question(
        level=3, candidates=[Q("t.007", None)], seen_ids=set(), seen_order={}
    )
    assert result is not None
    assert result[1] == "any_level"


# ---- DI sets ----


def di_sets() -> list[tuple[str, int]]:
    """Bar Charts as it really is: 20 sets, every one a 1->4 ramp averaging to level 3."""
    return [(f"di.barcharts.{i:03d}", 3) for i in range(1, 21)]


def test_di_picks_the_exact_level_when_one_exists() -> None:
    set_id, fallback = pick_di_set(
        level=3,
        topic_sets=di_sets(),
        other_topic_sets=[],
        seen_set_ids=set(),
        seen_set_order={},
    )
    assert (set_id, fallback) == ("di.barcharts.001", "exact")


def test_di_level_selection_is_inert_against_the_real_bank() -> None:
    """No DI set has a uniform difficulty — every Bar Charts set means level 3 — so asking for
    level 4 cannot be honoured and degrades to the next unseen set in the topic."""
    set_id, fallback = pick_di_set(
        level=4,
        topic_sets=di_sets(),
        other_topic_sets=[],
        seen_set_ids=set(),
        seen_set_order={},
    )
    assert (set_id, fallback) == ("di.barcharts.001", "adjacent")


def test_di_walks_through_unseen_sets_before_repeating() -> None:
    seen = {"di.barcharts.001", "di.barcharts.002"}
    set_id, _ = pick_di_set(
        level=3,
        topic_sets=di_sets(),
        other_topic_sets=[],
        seen_set_ids=seen,
        seen_set_order={},
    )
    assert set_id == "di.barcharts.003"


def test_di_borrows_another_topic_s_set_before_repeating() -> None:
    seen = {sid for sid, _ in di_sets()}
    set_id, fallback = pick_di_set(
        level=3,
        topic_sets=di_sets(),
        other_topic_sets=[("di.piecharts.001", 2)],
        seen_set_ids=seen,
        seen_set_order={},
    )
    assert (set_id, fallback) == ("di.piecharts.001", "any_topic")


def test_di_repeats_the_least_recently_seen_set_last() -> None:
    seen = {sid for sid, _ in di_sets()}
    order = {sid: i for i, (sid, _) in enumerate(reversed(di_sets()), start=1)}
    set_id, fallback = pick_di_set(
        level=3,
        topic_sets=di_sets(),
        other_topic_sets=[],
        seen_set_ids=seen,
        seen_set_order=order,
    )
    assert (set_id, fallback) == ("di.barcharts.020", "repeat")


def test_di_with_no_sets_at_all_yields_nothing() -> None:
    assert (
        pick_di_set(
            level=3,
            topic_sets=[],
            other_topic_sets=[],
            seen_set_ids=set(),
            seen_set_order={},
        )
        is None
    )
