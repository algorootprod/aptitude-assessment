"""Which topics the candidate's next test draws from — strict round-robin, as a pure function.

Every topic carries `last_cycle`, the cycle it was last *scheduled* into a test. Ordering a
section's topics by `(last_cycle, topic)` and taking the first N is round-robin without needing a
cursor anywhere: a never-scheduled topic sits at `last_cycle = 0` and sorts first, so cycle 1
takes topics 1-5, cycle 2 takes 6-10, and a section of 17 topics comes all the way around every
four cycles.

Rotation is deliberately blind to probation state. A topic sitting on a pending promotion waits
its turn like any other, which means a probation opened in cycle N is resolved in cycle N+4 for
the 17-topic sections and N+3 for DI.
"""

from typing import Protocol

from app.core.constants import DI_SECTION, SECTION_ORDER, TOPICS_PER_SECTION
from app.modules.user_test_mapping.schemas import TopicSlot


class RotatableTopic(Protocol):
    """The slice of a `UserTopicMastery` row rotation needs, so this stays testable without a DB."""

    @property
    def section(self) -> str: ...

    @property
    def topic(self) -> str: ...

    @property
    def last_cycle(self) -> int: ...

    @property
    def current_level(self) -> int: ...


def select_slots(rows: list[RotatableTopic]) -> list[TopicSlot]:
    """Pick the topics for the next test, at each topic's *current* level.

    DI contributes one slot rather than five: a DI section is one whole `set_id`, five questions
    sharing a chart and a topic. A section holding fewer topics than it wants slots simply
    contributes fewer questions — better a short section than the same topic asked twice in one
    paper.
    """
    slots: list[TopicSlot] = []
    for section in SECTION_ORDER:
        wanted = TOPICS_PER_SECTION.get(section, 1 if section == DI_SECTION else 5)
        in_section = sorted(
            (row for row in rows if row.section == section),
            key=lambda row: (row.last_cycle, row.topic),
        )
        slots.extend(
            TopicSlot(section=row.section, topic=row.topic, level=row.current_level)
            for row in in_section[:wanted]
        )
    return slots
