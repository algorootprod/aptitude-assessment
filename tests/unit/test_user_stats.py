"""The profile stats card's win-rate mapping — pure, so it is tested without a database."""

from app.modules.user_stats.mapping import win_rates_from_section_progress
from app.modules.user_topic_mapping.schemas import SectionProgress


def test_maps_each_section_by_name() -> None:
    sections = [
        SectionProgress(section="di", progress_score=70.0, current_level=4, raw_score=50.0),
        SectionProgress(section="quant", progress_score=40.0, current_level=2, raw_score=100.0),
        SectionProgress(
            section="reasoning", progress_score=20.0, current_level=1, raw_score=0.0
        ),
        SectionProgress(section="english", progress_score=60.0, current_level=3, raw_score=80.0),
    ]
    win_rates = win_rates_from_section_progress(sections)
    assert win_rates.di == 50.0
    assert win_rates.quant == 100.0
    assert win_rates.reasoning == 0.0
    assert win_rates.english == 80.0


def test_unevaluated_section_is_null_not_zero() -> None:
    """`SectionProgress.raw_score` is null before a section's first evaluation — a candidate
    fresh off signup must read as "not yet measured", not a 0% win rate."""
    sections = [SectionProgress(section="quant", progress_score=0.0)]
    win_rates = win_rates_from_section_progress(sections)
    assert win_rates.quant is None
    assert win_rates.reasoning is None
    assert win_rates.english is None
    assert win_rates.di is None
