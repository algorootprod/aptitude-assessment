"""Strict round-robin topic selection."""

from collections import Counter
from dataclasses import dataclass

from app.modules.user_topic_mapping.rotation import select_slots


@dataclass
class Row:
    """Stand-in for the slice of `UserTopicMastery` that rotation reads."""

    section: str
    topic: str
    last_cycle: int = 0
    current_level: int = 2


def quant_topics(count: int, **kwargs: int) -> list[Row]:
    return [Row(section="quant", topic=f"topic_{i:02d}", **kwargs) for i in range(1, count + 1)]


def test_first_cycle_takes_the_first_five_topics() -> None:
    slots = select_slots(quant_topics(17))
    assert [s.topic for s in slots] == [
        "topic_01",
        "topic_02",
        "topic_03",
        "topic_04",
        "topic_05",
    ]


def test_second_cycle_takes_the_next_five() -> None:
    rows = quant_topics(17)
    for row in rows[:5]:
        row.last_cycle = 1
    assert [s.topic for s in select_slots(rows)] == [
        "topic_06",
        "topic_07",
        "topic_08",
        "topic_09",
        "topic_10",
    ]


def test_seventeen_topics_come_all_the_way_around_in_four_cycles() -> None:
    rows = quant_topics(17)
    served: list[str] = []
    for cycle in range(1, 5):
        slots = select_slots(rows)
        served.extend(s.topic for s in slots)
        chosen = {s.topic for s in slots}
        for row in rows:
            if row.topic in chosen:
                row.last_cycle = cycle

    assert len(served) == 20
    assert set(served) == {row.topic for row in rows}, "every topic tested at least once"
    # 20 slots over 17 topics: the three earliest topics come back around for a second turn.
    assert sorted(t for t in served if served.count(t) == 2) == [
        "topic_01",
        "topic_01",
        "topic_02",
        "topic_02",
        "topic_03",
        "topic_03",
    ]


def test_di_contributes_one_slot_not_five() -> None:
    """A DI section is one whole set — five questions sharing a chart and a topic — so DI needs
    a single topic per cycle, not five."""
    rows = [
        Row(section="di", topic="Bar Charts"),
        Row(section="di", topic="Pie Charts"),
        Row(section="di", topic="Table Charts"),
    ]
    slots = select_slots(rows)
    assert [(s.section, s.topic) for s in slots] == [("di", "Bar Charts")]


def test_di_rotates_across_its_three_topics() -> None:
    rows = [
        Row(section="di", topic="Bar Charts"),
        Row(section="di", topic="Pie Charts"),
        Row(section="di", topic="Table Charts"),
    ]
    served = []
    for cycle in range(1, 4):
        (slot,) = select_slots(rows)
        served.append(slot.topic)
        for row in rows:
            if row.topic == slot.topic:
                row.last_cycle = cycle
    assert served == ["Bar Charts", "Pie Charts", "Table Charts"]


def test_a_full_paper_is_one_di_topic_and_five_of_each_other_section() -> None:
    rows = (
        [Row(section="di", topic=t) for t in ("Bar Charts", "Pie Charts", "Table Charts")]
        + quant_topics(17)
        + [Row(section="reasoning", topic=f"r_{i:02d}") for i in range(17)]
        + [Row(section="english", topic=f"e_{i:02d}") for i in range(17)]
    )
    slots = select_slots(rows)
    counts = Counter(slot.section for slot in slots)
    assert counts == {"di": 1, "quant": 5, "reasoning": 5, "english": 5}
    assert len(slots) == 16, "16 slots produce 20 questions — the DI slot is a set of five"
    # Sections come back in the order the paper is sat in.
    assert [slot.section for slot in slots] == ["di"] + ["quant"] * 5 + ["reasoning"] * 5 + [
        "english"
    ] * 5


def test_slots_carry_each_topic_s_own_current_level() -> None:
    """After a few cycles topics sit at different levels, so one section's five questions are
    generally *not* all the same level."""
    rows = quant_topics(5)
    rows[0].current_level = 4
    rows[3].current_level = 1
    assert [s.level for s in select_slots(rows)] == [4, 2, 2, 1, 2]


def test_a_section_with_fewer_topics_than_slots_contributes_fewer_questions() -> None:
    """Better a short section than the same topic asked twice in one paper."""
    slots = select_slots(quant_topics(3))
    assert len(slots) == 3


def test_ties_on_last_cycle_break_by_topic_name_so_replays_match() -> None:
    rows = [
        Row(section="quant", topic="Zeta", last_cycle=2),
        Row(section="quant", topic="Alpha", last_cycle=2),
        Row(section="quant", topic="Mid", last_cycle=1),
    ]
    assert [s.topic for s in select_slots(rows)] == ["Mid", "Alpha", "Zeta"]
