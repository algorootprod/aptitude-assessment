"""Which question fills a slot — the fallback ladder, as pure functions over already-fetched rows.

The bank is thin at the extremes: the median topic holds one level-1 question and zero or one
level-5 questions, and 25 of 54 topics do not span all five levels at all. An exact-level lookup
therefore misses routinely, so falling back is the normal path rather than an error case. Every
selection records *how* it was made so a test that drifted off-level is visible rather than silent.

    exact      the level that was asked for
    adjacent   level +/- 1, nearer level first
    any_level  any level in the topic
    any_topic  DI only: an unseen set from a different DI topic
    repeat     a question the candidate has already seen, least recently first

`service.py` supplies the candidate rows; nothing here touches a session.
"""

from typing import Protocol

from app.core.constants import SelectionFallback


class HasIdAndDifficulty(Protocol):
    """The slice of `QuestionBank` this module needs — kept structural so tests can pass simple
    stand-ins instead of building ORM instances."""

    @property
    def id(self) -> str: ...

    @property
    def difficulty(self) -> int | None: ...


def order_by_level_distance[Q: HasIdAndDifficulty](candidates: list[Q], level: int) -> list[Q]:
    """Nearest level first, then by id so the result is deterministic across replays."""
    return sorted(candidates, key=lambda q: (abs((q.difficulty or 0) - level), q.id))


def pick_question[Q: HasIdAndDifficulty](
    *,
    level: int,
    candidates: list[Q],
    seen_ids: set[str],
    seen_order: dict[str, int],
) -> tuple[Q, SelectionFallback] | None:
    """Choose one question for a non-DI slot from that topic's full candidate pool.

    `seen_order` maps a previously-served question id to the cycle it was served in, so the
    `repeat` fallback can reach for the least recently seen one rather than an arbitrary one.
    Returns `None` only if the topic has no questions at all.
    """
    unseen = [q for q in candidates if q.id not in seen_ids]

    exact = [q for q in unseen if q.difficulty == level]
    if exact:
        return min(exact, key=lambda q: q.id), "exact"

    adjacent = [q for q in unseen if q.difficulty in (level - 1, level + 1)]
    if adjacent:
        return order_by_level_distance(adjacent, level)[0], "adjacent"

    if unseen:
        return order_by_level_distance(unseen, level)[0], "any_level"

    if candidates:
        # Everything in the topic has been served before. Reach for the least recently seen,
        # breaking ties toward the asked-for level.
        oldest = min(
            candidates,
            key=lambda q: (seen_order.get(q.id, 0), abs((q.difficulty or 0) - level), q.id),
        )
        return oldest, "repeat"

    return None


def pick_di_set(
    *,
    level: int,
    topic_sets: list[tuple[str, int]],
    other_topic_sets: list[tuple[str, int]],
    seen_set_ids: set[str],
    seen_set_order: dict[str, int],
) -> tuple[str, SelectionFallback] | None:
    """Choose one DI `set_id` — a set fills the whole DI section.

    `topic_sets` and `other_topic_sets` are `(set_id, rounded mean difficulty)` for the slot's
    topic and for the rest of DI respectively.

    In practice `exact` almost never hits: no set in the current bank has a uniform difficulty
    (each is a 1->4 ramp), so every Bar Charts and Table Charts set means level 3 and most Pie
    Charts sets mean level 2. The ladder below is built for a bank that grows, and degrades to
    "next unseen set in this topic" against the one we have.
    """
    unseen_in_topic = [(sid, lvl) for sid, lvl in topic_sets if sid not in seen_set_ids]

    exact = [sid for sid, lvl in unseen_in_topic if lvl == level]
    if exact:
        return min(exact), "exact"

    adjacent = [sid for sid, lvl in unseen_in_topic if lvl in (level - 1, level + 1)]
    if adjacent:
        return min(adjacent), "adjacent"

    if unseen_in_topic:
        return min(sid for sid, _ in unseen_in_topic), "any_level"

    unseen_elsewhere = [sid for sid, _ in other_topic_sets if sid not in seen_set_ids]
    if unseen_elsewhere:
        return min(unseen_elsewhere), "any_topic"

    all_sets = [sid for sid, _ in topic_sets] or [sid for sid, _ in other_topic_sets]
    if all_sets:
        return min(all_sets, key=lambda sid: (seen_set_order.get(sid, 0), sid)), "repeat"

    return None
