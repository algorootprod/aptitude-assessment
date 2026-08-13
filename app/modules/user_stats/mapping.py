"""Pure mapping from `user_topic_mapping`'s section progress onto the profile stats card's
shape. Kept separate from `service.py` so it is testable without a database, following the
pattern `user_topic_mapping.progress` / `.ladder` set (see CLAUDE.md, "Architecture rules")."""

from app.core.constants import DI_SECTION
from app.modules.user_stats.schemas import SectionWinRates
from app.modules.user_topic_mapping.schemas import SectionProgress


def win_rates_from_section_progress(sections: list[SectionProgress]) -> SectionWinRates:
    """A section's `raw_score` (0-100 mean `mastery_score` of the last-tested topics) *is* this
    card's per-section win rate — reused rather than recomputed, so the profile card and the
    progress chart cannot disagree. Null until that section has been evaluated at least once,
    same as `SectionProgress.raw_score` itself.
    """
    by_section = {row.section: row.raw_score for row in sections}
    return SectionWinRates(
        quant=by_section.get("quant"),
        reasoning=by_section.get("reasoning"),
        english=by_section.get("english"),
        di=by_section.get(DI_SECTION),
    )
