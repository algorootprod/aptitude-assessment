"""The per-topic level ladder: pure functions, no I/O.

A candidate holds a level 1-5 per topic. A level never moves on a single result — it takes two
consecutive signals in the same direction. The intermediate state is a *probation*
(`pending_dir`), and a contradictory signal cancels it back to neutral rather than flipping it,
so a level only moves on consistent evidence.

    level 2, neutral   --mastered-->  level 2, promotion pending
                       --mastered-->  level 3, neutral
                       --gap------->  level 3, demotion pending
                       --mastered-->  level 3, neutral        (cancelled, no move)

`service.py` is the only caller; everything here is deliberately testable without a database.
"""

from app.core.config import get_settings
from app.core.constants import DI_SECTION, QUADRANT_MASTERY_SCORE, QUADRANT_SIGNAL
from app.modules.user_topic_mapping.schemas import TopicOutcomeIn


def signal_for(outcome: TopicOutcomeIn) -> int:
    """Collapse one topic's result into a ladder signal: +1 promote, -1 demote, 0 hold.

    DI bands its 0-100 section score against the configured thresholds (strictly above/below —
    a score sitting exactly on a threshold holds). Every other section maps the quadrant of its
    single question through `QUADRANT_SIGNAL`, where `fragile` and `careless` are neutral:
    right-but-slow and wrong-but-rushed are both evidence about pacing, not about level.
    """
    if outcome.section == DI_SECTION:
        if outcome.score is None:  # pragma: no cover - schema validator guarantees this
            raise ValueError("DI outcome without a score")
        settings = get_settings()
        if outcome.score > settings.di_promote_score_threshold:
            return 1
        if outcome.score < settings.di_demote_score_threshold:
            return -1
        return 0

    if outcome.quadrant is None:  # pragma: no cover - schema validator guarantees this
        raise ValueError(f"non-DI outcome for '{outcome.topic}' without a quadrant")
    return QUADRANT_SIGNAL[outcome.quadrant]


def mastery_score_for(outcome: TopicOutcomeIn) -> float:
    """Display-only 0-100 value for `user_topic_map.mastery_score`. Never feeds the ladder."""
    if outcome.section == DI_SECTION:
        return outcome.score if outcome.score is not None else 0.0
    return QUADRANT_MASTERY_SCORE[outcome.quadrant] if outcome.quadrant else 0.0


def apply_signal(current_level: int, pending_dir: int, signal: int) -> tuple[int, int]:
    """Advance the ladder one step. Returns the new `(current_level, pending_dir)`.

    - `signal == 0` holds: the level and any open probation both survive untouched, so a
      probation opened in cycle N can still be confirmed in cycle N+4 when strict round-robin
      brings the topic back around.
    - No probation open -> open one in the signal's direction.
    - Signal confirms the open probation -> move the level and clear the probation. If the level
      is already at a bound the probation is still *consumed*, not left dangling: a candidate
      pinned at level 5 re-earns their probation each time rather than promoting the instant a
      level 6 becomes available.
    - Signal contradicts the open probation -> cancel to neutral. Never flips straight to the
      opposite probation.
    """
    if signal == 0:
        return current_level, pending_dir
    if pending_dir == 0:
        return current_level, signal
    if signal != pending_dir:
        return current_level, 0

    settings = get_settings()
    new_level = min(max(current_level + signal, settings.min_topic_level), settings.max_topic_level)
    return new_level, 0


def next_streak(current_streak: int, signal: int) -> int:
    """Consecutive positive signals for a topic. Display-only, like `mastery_score`."""
    return current_streak + 1 if signal > 0 else 0
