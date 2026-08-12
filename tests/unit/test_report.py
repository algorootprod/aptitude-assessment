"""The report builders.

The governing rule is that findings describe *patterns*, so every threshold is tested from both
sides: one slow answer says nothing, two say something.
"""

from app.core.constants import Quadrant
from app.modules.evaluation_report.report import (
    ClassifiedQuestion,
    SectionOutcome,
    build_actions,
    build_findings,
    build_headline,
    build_question_reviews,
    build_section_table,
    build_tiles,
)


def q(
    quadrant: Quadrant,
    *,
    topic: str = "Ages",
    order: int = 1,
    elapsed: int = 50,
    expected: int = 60,
    prerequisite: str | None = "linear equations",
    shortcut: bool = True,
    section: str = "quant",
) -> ClassifiedQuestion:
    return ClassifiedQuestion(
        question_id=f"{section}.{topic}.{order}".lower().replace(" ", ""),
        section=section,
        topic=topic,
        concept="a concept",
        prerequisite_concept=prerequisite,
        quadrant=quadrant,
        picked="A",
        correct_option="A" if quadrant in ("mastered", "fragile") else "B",
        is_correct=quadrant in ("mastered", "fragile"),
        elapsed_seconds=elapsed,
        expected_time_seconds=expected,
        order=order,
        question_text="A question",
        options=["1", "2", "3", "4"],
        explanation="Because of the thing.",
        distractor_rationale=None if quadrant in ("mastered", "fragile") else "Looked right.",
        shortcut_name=f"{topic} shortcut" if shortcut else None,
        shortcut_how="Do it the fast way." if shortcut else None,
        shortcut_saves_seconds=20 if shortcut else None,
    )


def section(
    *questions: ClassifiedQuestion,
    name: str = "quant",
    budget: int = 300,
    used: int = 250,
) -> SectionOutcome:
    return SectionOutcome(
        section=name, budget_seconds=budget, time_used_seconds=used, questions=list(questions)
    )


def paper(*quadrants: Quadrant) -> list[SectionOutcome]:
    return [section(*(q(k, order=i, topic=f"T{i}") for i, k in enumerate(quadrants, 1)))]


# ---- headline ----


def test_headline_leads_with_the_clock_when_slowness_dominates() -> None:
    assert build_headline(paper("fragile", "fragile", "fragile")) == (
        "You know more than the clock is letting you show."
    )


def test_headline_leads_with_haste_when_carelessness_dominates() -> None:
    assert build_headline(paper("careless", "careless", "careless")) == (
        "You're losing marks to speed, not to knowledge."
    )


def test_headline_calls_out_real_gaps() -> None:
    assert build_headline(paper(*(["gap"] * 5))).startswith("There are real gaps")


def test_headline_recognises_a_strong_run() -> None:
    assert build_headline(paper(*(["mastered"] * 15))).startswith("Strong run")


def test_headline_falls_back_to_mixed() -> None:
    assert build_headline(paper("mastered", "fragile", "gap")).startswith("A mixed run")


def test_fragile_outranks_careless_in_the_headline() -> None:
    """First match wins, and slowness is the more actionable finding."""
    assert build_headline(
        paper("fragile", "fragile", "fragile", "careless", "careless", "careless")
    ) == "You know more than the clock is letting you show."


def test_headline_never_contains_a_score() -> None:
    for shape in (["mastered"] * 20, ["gap"] * 20, ["mastered"] * 10 + ["gap"] * 10):
        headline = build_headline(paper(*shape))  # type: ignore[arg-type]
        assert "%" not in headline
        assert not any(character.isdigit() for character in headline)


# ---- tiles ----


def test_tiles_cover_the_four_verdicts_and_sum_to_the_paper() -> None:
    tiles = build_tiles(paper("mastered", "mastered", "fragile", "careless", "gap"))
    assert [tile.quadrant for tile in tiles] == ["mastered", "fragile", "careless", "gap"]
    assert [tile.count for tile in tiles] == [2, 1, 1, 1]
    assert sum(tile.count for tile in tiles) == 5


def test_unreached_tile_is_hidden_when_it_did_not_happen() -> None:
    tiles = build_tiles(paper("mastered", "gap"))
    assert "unreached" not in {tile.quadrant for tile in tiles}


def test_unreached_tile_appears_so_the_counts_still_add_up() -> None:
    tiles = build_tiles(paper("mastered", "unreached", "unreached"))
    assert tiles[-1].quadrant == "unreached"
    assert tiles[-1].count == 2
    assert sum(tile.count for tile in tiles) == 3


def test_tiles_carry_the_explanation_of_each_verdict() -> None:
    mastered = build_tiles(paper("mastered"))[0]
    assert mastered.label == "Mastered"
    assert mastered.tone == "good"
    assert mastered.blurb == "Right, and inside the time budget."


# ---- section table ----


def test_section_row_counts_correct_answers_not_mastered_ones() -> None:
    """A fragile answer is still a right answer."""
    row = build_section_table([section(q("mastered"), q("fragile"), q("gap"))])[0]
    assert (row.correct, row.total) == (2, 3)


def test_section_note_leads_with_unreached_questions() -> None:
    row = build_section_table([section(q("mastered"), q("unreached"), q("unreached"))])[0]
    assert row.note == "2 questions never reached."


def test_section_note_singular_for_one_unreached() -> None:
    row = build_section_table([section(q("unreached"))])[0]
    assert row.note == "1 question never reached."


def test_section_note_flags_running_to_the_limit() -> None:
    row = build_section_table([section(q("mastered"), budget=300, used=295)])[0]
    assert row.note == "Ran right to the limit — no slack for a hard item."


def test_section_note_reports_haste_with_time_left() -> None:
    row = build_section_table(
        [section(q("careless"), q("careless"), budget=300, used=100)]
    )[0]
    assert row.note == "200s left unused, and 2 answers lost to haste."


def test_section_note_reports_being_over_budget() -> None:
    row = build_section_table(
        [section(q("fragile"), q("fragile"), budget=300, used=200)]
    )[0]
    assert row.note == "Finished, but 2 answers came in over budget."


def test_section_note_says_comfortable_when_nothing_stands_out() -> None:
    row = build_section_table([section(q("mastered"), budget=300, used=100)])[0]
    assert row.note == "Comfortable — 200s to spare."


def test_seconds_to_spare_never_goes_negative() -> None:
    row = build_section_table([section(q("fragile"), q("fragile"), budget=100, used=400)])[0]
    assert "-" not in row.note


def test_section_row_carries_a_human_readable_name() -> None:
    row = build_section_table([section(q("mastered"), name="di")])[0]
    assert (row.section, row.section_name) == ("di", "Data Interpretation")


# ---- findings ----


def headings(findings: list[object]) -> list[str]:
    return [f.heading for f in findings]  # type: ignore[attr-defined]


def test_one_slow_answer_is_not_a_pattern() -> None:
    found = build_findings([section(q("fragile"), q("mastered"))])
    assert not any("too slow" in heading for heading in headings(found))


def test_two_slow_answers_are_a_pattern() -> None:
    found = build_findings(
        [section(q("fragile", elapsed=90), q("fragile", elapsed=80), budget=300, used=250)]
    )
    assert "Quantitative: right method, 50s too slow" in headings(found)


def test_the_shortcut_promise_is_only_made_when_shortcuts_exist() -> None:
    """`shortcut_available` is true for barely 30% of the bank, so the prototype's "each of
    these has a shortcut" line cannot be printed unconditionally."""
    with_shortcuts = build_findings(
        [section(q("fragile", elapsed=90), q("fragile", elapsed=90))]
    )
    assert "shortcut you are not using" in with_shortcuts[0].detail

    without = build_findings(
        [
            section(
                q("fragile", elapsed=90, shortcut=False),
                q("fragile", elapsed=90, shortcut=False),
            )
        ]
    )
    assert "shortcut" not in without[0].detail
    assert "the pace is not" in without[0].detail


def test_haste_finding_needs_time_to_actually_have_been_in_hand() -> None:
    rushed = build_findings([section(q("careless"), q("careless"), budget=300, used=100)])
    assert any("rushed with time in hand" in heading for heading in headings(rushed))

    # Same two careless answers, but the section ran to 90% of budget — not rushing, just wrong.
    pressed = build_findings([section(q("careless"), q("careless"), budget=300, used=270)])
    assert not any("rushed with time in hand" in heading for heading in headings(pressed))


def test_one_prerequisite_behind_two_gaps_is_reported_as_one_root_cause() -> None:
    found = build_findings(
        [
            section(
                q("gap", topic="Ages", prerequisite="linear equations"),
                q("gap", topic="Averages", prerequisite="linear equations"),
            )
        ]
    )
    assert "Both errors trace back to one thing: linear equations" in headings(found)


def test_two_gaps_from_different_prerequisites_are_not_merged() -> None:
    found = build_findings(
        [
            section(
                q("gap", topic="Ages", prerequisite="linear equations"),
                q("gap", topic="Trains", prerequisite="relative speed"),
            )
        ]
    )
    assert not any("trace back" in heading for heading in headings(found))


def test_gaps_with_no_prerequisite_on_file_are_skipped_not_grouped() -> None:
    """The column is nullable; two nulls are not a shared root cause."""
    found = build_findings(
        [section(q("gap", topic="A", prerequisite=None), q("gap", topic="B", prerequisite=None))]
    )
    assert not any("trace back" in heading for heading in headings(found))


def test_mastered_questions_get_a_roll_up_naming_at_most_four_topics() -> None:
    found = build_findings(
        [section(*(q("mastered", topic=f"Topic{i}", order=i) for i in range(1, 7)))]
    )
    rollup = next(f for f in found if "clean" in f.heading)
    assert rollup.heading == "6 questions were clean"
    assert rollup.detail.endswith("Topic1, Topic2, Topic3, Topic4.")
    assert "Topic5" not in rollup.detail


def test_a_paper_with_no_patterns_says_so() -> None:
    found = build_findings([section(q("fragile"), q("careless"), budget=300, used=280)])
    assert headings(found) == ["Nothing stands out"]


# ---- actions ----


def test_gaps_come_before_shortcuts_which_come_before_pacing() -> None:
    actions = build_actions(
        [
            section(
                q("careless", topic="A", order=1),
                q("fragile", topic="B", order=2),
                q("gap", topic="C", order=3, prerequisite="algebra"),
            )
        ]
    )
    assert actions[0].heading == "Relearn algebra"
    assert actions[1].heading == "B shortcut"
    assert actions[2].heading == "Give each question its full budget"


def test_a_gap_action_carries_the_worked_explanation() -> None:
    action = build_actions([section(q("gap", prerequisite="algebra"))])[0]
    assert action.detail == "Because of the thing."
    assert action.tag == "Ages · underneath a concept"


def test_a_gap_without_a_prerequisite_still_gets_prescribed() -> None:
    """Falls back to the topic rather than emitting "Relearn None"."""
    action = build_actions([section(q("gap", topic="Ages", prerequisite=None))])[0]
    assert action.heading == "Relearn Ages"
    assert action.detail


def test_fragile_answers_without_a_shortcut_produce_no_action() -> None:
    """Most of the bank has no shortcut on file — better silence than an empty prescription."""
    actions = build_actions([section(q("fragile", shortcut=False))])
    assert actions == []


def test_the_pacing_action_is_emitted_once_however_many_careless_answers() -> None:
    actions = build_actions(
        [section(*(q("careless", topic=f"T{i}", order=i) for i in range(1, 4)))]
    )
    assert len(actions) == 1
    assert "3 questions" in actions[0].detail
    assert actions[0].tag == "T1, T2, T3"


def test_repeated_headings_are_deduped() -> None:
    """Two gaps sharing a prerequisite produce one "Relearn X", not two."""
    actions = build_actions(
        [
            section(
                q("gap", topic="Ages", order=1, prerequisite="algebra"),
                q("gap", topic="Averages", order=2, prerequisite="algebra"),
            )
        ]
    )
    assert [a.heading for a in actions] == ["Relearn algebra"]


def test_actions_are_capped_at_six() -> None:
    actions = build_actions(
        [
            section(
                *(
                    q("gap", topic=f"T{i}", order=i, prerequisite=f"prereq {i}")
                    for i in range(1, 11)
                )
            )
        ]
    )
    assert len(actions) == 6


def test_the_cap_keeps_the_highest_priority_actions() -> None:
    """Seven gaps and a careless answer: pacing advice loses its place to the knowledge gaps."""
    actions = build_actions(
        [
            section(
                *(
                    q("gap", topic=f"T{i}", order=i, prerequisite=f"prereq {i}")
                    for i in range(1, 8)
                ),
                q("careless", topic="Z", order=8),
            )
        ]
    )
    assert len(actions) == 6
    assert all(a.heading.startswith("Relearn") for a in actions)


# ---- per-question review ----


def test_reviews_come_back_in_the_order_the_paper_was_sat() -> None:
    reviews = build_question_reviews(
        [section(q("gap", order=3), q("mastered", order=1), q("fragile", order=2))]
    )
    assert [r.quadrant for r in reviews] == ["mastered", "fragile", "gap"]


def test_a_mastered_question_is_not_told_about_a_faster_route() -> None:
    """They already answered it inside the budget; the shortcut would be noise."""
    review = build_question_reviews([section(q("mastered"))])[0]
    assert review.shortcut_name is None
    assert review.shortcut_how is None
    assert review.shortcut_saves_seconds is None


def test_a_fragile_question_is_told_about_the_faster_route() -> None:
    review = build_question_reviews([section(q("fragile"))])[0]
    assert review.shortcut_name == "Ages shortcut"
    assert review.shortcut_saves_seconds == 20


def test_a_review_carries_the_answer_key_and_the_explanation() -> None:
    review = build_question_reviews([section(q("gap"))])[0]
    assert review.correct_option == "B"
    assert review.picked == "A"
    assert review.is_correct is False
    assert review.explanation == "Because of the thing."
    assert review.distractor_rationale == "Looked right."
