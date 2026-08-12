"""Pure report builders — no I/O.

Ported rule-for-rule from `reference/daily20_prototype.html`'s `report()`. The governing idea,
and the reason none of this is a score: **findings are patterns across the whole paper, not a
list of mistakes.** Two slow-but-correct answers in one section say something; one says nothing.

The report has five parts, in the order the candidate reads them:

  headline        one sentence naming the shape of the run
  tiles           how many questions landed in each quadrant
  section_table   right/total, clock used against budget, and one sentence on the clock
  findings        the patterns
  actions         what to do about them, priority-ordered and capped
  questions       every question, with the worked method and the faster route

One deliberate departure from the prototype: its actions block closed with "Tomorrow's set will
over-sample your weaker topics." That is not what happens — topic rotation is strict round-robin
and ignores performance entirely; only a topic's *level* moves. The line is dropped rather than
shipped as a promise the service does not keep.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass

from app.core.constants import (
    ACTION_PRIORITY_CARELESS,
    ACTION_PRIORITY_FRAGILE,
    ACTION_PRIORITY_GAP,
    CARELESS_BUDGET_FRACTION,
    FINDING_CARELESS_THRESHOLD,
    FINDING_FRAGILE_THRESHOLD,
    FINDING_PREREQUISITE_THRESHOLD,
    HEADLINE_CARELESS_THRESHOLD,
    HEADLINE_FRAGILE_THRESHOLD,
    HEADLINE_GAP_THRESHOLD,
    HEADLINE_MASTERED_THRESHOLD,
    MASTERED_TOPICS_NAMED,
    MAX_ACTIONS,
    QUADRANT_DISPLAY,
    SECTION_DISPLAY_NAMES,
    SECTION_TIME_PRESSURE_FRACTION,
    TILE_ORDER,
    Quadrant,
)
from app.modules.evaluation_report.schemas import (
    ReportAction,
    ReportFinding,
    ReportQuestionReview,
    ReportSectionRow,
    ReportTile,
)


@dataclass(frozen=True)
class ClassifiedQuestion:
    """One scored question — the intermediate record every builder reads.

    Carries the candidate's submission, the question's own metadata and the verdict, so the
    builders never need to reach back to the database or the request payload.
    """

    question_id: str
    section: str
    topic: str
    concept: str | None
    prerequisite_concept: str | None
    quadrant: Quadrant
    picked: str | None
    correct_option: str | None
    is_correct: bool
    elapsed_seconds: int
    expected_time_seconds: int
    order: int
    question_text: str
    options: list[str]
    explanation: str | None
    distractor_rationale: str | None
    shortcut_name: str | None
    shortcut_how: str | None
    shortcut_saves_seconds: int | None

    @property
    def seconds_over_budget(self) -> int:
        return max(self.elapsed_seconds - self.expected_time_seconds, 0)

    @property
    def has_shortcut(self) -> bool:
        return bool(self.shortcut_name and self.shortcut_how)


@dataclass(frozen=True)
class SectionOutcome:
    """One section of a completed paper."""

    section: str
    budget_seconds: int
    time_used_seconds: int
    questions: list[ClassifiedQuestion]

    @property
    def name(self) -> str:
        return SECTION_DISPLAY_NAMES.get(self.section, self.section.title())

    @property
    def seconds_to_spare(self) -> int:
        """Clamped at 0 — the prototype can print a negative here when a section overruns."""
        return max(self.budget_seconds - self.time_used_seconds, 0)

    def count(self, quadrant: Quadrant) -> int:
        return sum(1 for q in self.questions if q.quadrant == quadrant)

    def of(self, quadrant: Quadrant) -> list[ClassifiedQuestion]:
        return [q for q in self.questions if q.quadrant == quadrant]


def _all(sections: list[SectionOutcome]) -> list[ClassifiedQuestion]:
    return [question for section in sections for question in section.questions]


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return singular if count == 1 else (plural or f"{singular}s")


def format_seconds(total: int) -> str:
    """`MM:SS`, as the prototype's `fmt()` writes clock figures."""
    total = max(total, 0)
    return f"{total // 60:02d}:{total % 60:02d}"


# ---- headline ----


def build_headline(sections: list[SectionOutcome]) -> str:
    """One sentence naming the shape of the run. First match wins.

    Deliberately never leads with a score — the prototype carries the comment
    "describe the shape, never lead with a score", and that is the product decision.
    """
    counts = Counter(question.quadrant for question in _all(sections))
    if counts["fragile"] >= HEADLINE_FRAGILE_THRESHOLD:
        return "You know more than the clock is letting you show."
    if counts["careless"] >= HEADLINE_CARELESS_THRESHOLD:
        return "You're losing marks to speed, not to knowledge."
    if counts["gap"] >= HEADLINE_GAP_THRESHOLD:
        return "There are real gaps to close before speed becomes the problem."
    if counts["mastered"] >= HEADLINE_MASTERED_THRESHOLD:
        return "Strong run. The remaining issues are narrow and fixable."
    return "A mixed run — three different things are going on."


# ---- tiles ----


def build_tiles(sections: list[SectionOutcome]) -> list[ReportTile]:
    """Count per quadrant. `unreached` appears only when it happened, so that in the normal case
    four tiles are shown and they sum to the paper."""
    counts = Counter(question.quadrant for question in _all(sections))
    tiles: list[ReportTile] = []
    for quadrant in TILE_ORDER:
        count = counts[quadrant]
        if quadrant == "unreached" and count == 0:
            continue
        display = QUADRANT_DISPLAY[quadrant]
        tiles.append(
            ReportTile(
                quadrant=quadrant,
                label=display.label,
                tone=display.tone,
                count=count,
                blurb=display.blurb,
            )
        )
    return tiles


# ---- section table ----


def build_section_table(sections: list[SectionOutcome]) -> list[ReportSectionRow]:
    return [
        ReportSectionRow(
            section=section.section,
            section_name=section.name,
            correct=sum(1 for q in section.questions if q.is_correct),
            total=len(section.questions),
            time_used_seconds=section.time_used_seconds,
            budget_seconds=section.budget_seconds,
            note=_section_note(section),
        )
        for section in sections
    ]


def _section_note(section: SectionOutcome) -> str:
    """One sentence on how the clock went. Ladder order matters — the first true thing is the
    most important thing about that section."""
    unreached = section.count("unreached")
    if unreached:
        return f"{unreached} {_plural(unreached, 'question')} never reached."

    used_fraction = (
        section.time_used_seconds / section.budget_seconds if section.budget_seconds else 0.0
    )
    if used_fraction > SECTION_TIME_PRESSURE_FRACTION:
        return "Ran right to the limit — no slack for a hard item."

    careless = section.count("careless")
    if careless >= FINDING_CARELESS_THRESHOLD:
        return (
            f"{section.seconds_to_spare}s left unused, and "
            f"{careless} answers lost to haste."
        )

    fragile = section.count("fragile")
    if fragile >= FINDING_FRAGILE_THRESHOLD:
        return f"Finished, but {fragile} answers came in over budget."

    return f"Comfortable — {section.seconds_to_spare}s to spare."


# ---- findings ----


def build_findings(sections: list[SectionOutcome]) -> list[ReportFinding]:
    """Patterns across the paper. Empty is a valid answer, and says so."""
    findings: list[ReportFinding] = []

    for section in sections:
        findings.extend(_section_findings(section))

    findings.extend(_prerequisite_findings(_all(sections)))

    mastered = [q for q in _all(sections) if q.quadrant == "mastered"]
    if mastered:
        topics = list(dict.fromkeys(q.topic for q in mastered))[:MASTERED_TOPICS_NAMED]
        findings.append(
            ReportFinding(
                tone="good",
                heading=f"{len(mastered)} questions were clean",
                detail=(
                    "Right, and inside the budget. No action needed on these — "
                    f"{', '.join(topics)}."
                ),
            )
        )

    if not findings:
        findings.append(
            ReportFinding(
                tone="neutral",
                heading="Nothing stands out",
                detail="Come back tomorrow for a clearer picture.",
            )
        )
    return findings


def _section_findings(section: SectionOutcome) -> list[ReportFinding]:
    findings: list[ReportFinding] = []

    fragile = section.of("fragile")
    if len(fragile) >= FINDING_FRAGILE_THRESHOLD:
        over = sum(q.seconds_over_budget for q in fragile)
        with_shortcut = sum(1 for q in fragile if q.has_shortcut)
        # Only promise a shortcut when the bank actually has one — it does for barely 30% of
        # questions, and 13% in English.
        tail = (
            "each of these has a shortcut you are not using yet."
            if with_shortcut == len(fragile)
            else "the method is there; the pace is not."
        )
        findings.append(
            ReportFinding(
                tone="warning",
                heading=f"{section.name}: right method, {over}s too slow",
                detail=(
                    f"{len(fragile)} answers were correct but over budget. Nothing here is a "
                    f"knowledge problem — {tail}"
                ),
            )
        )

    careless = section.of("careless")
    budget_used_enough = (
        section.time_used_seconds < section.budget_seconds * CARELESS_BUDGET_FRACTION
    )
    if len(careless) >= FINDING_CARELESS_THRESHOLD and budget_used_enough:
        findings.append(
            ReportFinding(
                tone="serious",
                heading=f"{section.name}: you rushed with time in hand",
                detail=(
                    f"{len(careless)} answers went in at well under half the expected time, and "
                    f"you still finished with {format_seconds(section.seconds_to_spare)} unused. "
                    "Slowing down here is free marks."
                ),
            )
        )

    return findings


def _prerequisite_findings(questions: list[ClassifiedQuestion]) -> list[ReportFinding]:
    """The same missing prerequisite behind two or more gaps is one root cause, not two separate
    mistakes — the single most useful thing the report can tell a candidate."""
    by_prerequisite: dict[str, list[ClassifiedQuestion]] = defaultdict(list)
    for question in questions:
        if question.quadrant == "gap" and question.prerequisite_concept:
            by_prerequisite[question.prerequisite_concept].append(question)

    return [
        ReportFinding(
            tone="critical",
            heading=f"Both errors trace back to one thing: {prerequisite}",
            detail=(
                f"{len(group)} questions across the paper failed for the same underlying "
                "reason. Fixing the topics separately will not help — this is the thing to "
                "relearn."
            ),
        )
        for prerequisite, group in sorted(by_prerequisite.items())
        if len(group) >= FINDING_PREREQUISITE_THRESHOLD
    ]


# ---- actions ----


@dataclass(frozen=True)
class _Action:
    priority: int
    heading: str
    detail: str
    tag: str


def build_actions(sections: list[SectionOutcome]) -> list[ReportAction]:
    """Every weak point gets a prescription. Priority-ordered, deduped by heading, capped —
    six is as many as anyone acts on."""
    questions = _all(sections)
    candidates: list[_Action] = []

    for question in questions:
        if question.quadrant == "gap":
            candidates.append(_gap_action(question))

    for question in questions:
        # `shortcut_available` is true for barely 30% of the bank, so a fragile answer often has
        # nothing to prescribe. Skip it rather than emit an empty action.
        if question.quadrant == "fragile" and question.has_shortcut:
            candidates.append(_fragile_action(question))

    careless = [q for q in questions if q.quadrant == "careless"]
    if careless:
        candidates.append(_careless_action(careless))

    candidates.sort(key=lambda action: action.priority)

    seen: set[str] = set()
    actions: list[ReportAction] = []
    for candidate in candidates:
        if candidate.heading in seen:
            continue
        seen.add(candidate.heading)
        actions.append(
            ReportAction(heading=candidate.heading, detail=candidate.detail, tag=candidate.tag)
        )
        if len(actions) == MAX_ACTIONS:
            break
    return actions


def _gap_action(question: ClassifiedQuestion) -> _Action:
    # `prerequisite_concept` is nullable; falling back to the topic keeps every gap prescribed.
    subject = question.prerequisite_concept or question.topic
    tag = (
        f"{question.topic} · underneath {question.concept}"
        if question.concept
        else question.topic
    )
    return _Action(
        priority=ACTION_PRIORITY_GAP,
        heading=f"Relearn {subject}",
        detail=question.explanation
        or f"Work back through {subject} before attempting {question.topic} again.",
        tag=tag,
    )


def _fragile_action(question: ClassifiedQuestion) -> _Action:
    saves = question.shortcut_saves_seconds
    tag = f"{question.topic} · saves about {saves}s" if saves else question.topic
    return _Action(
        priority=ACTION_PRIORITY_FRAGILE,
        heading=question.shortcut_name or question.topic,
        detail=question.shortcut_how or "",
        tag=tag,
    )


def _careless_action(careless: list[ClassifiedQuestion]) -> _Action:
    count = len(careless)
    them = "them" if count > 1 else "it"
    both = "both" if count == 2 else ("all of them" if count > 2 else "this")
    topics = list(dict.fromkeys(q.topic for q in careless))
    return _Action(
        priority=ACTION_PRIORITY_CARELESS,
        heading="Give each question its full budget",
        detail=(
            f"You answered {count} {_plural(count, 'question')} in under half the expected time "
            f"and got {them} wrong. Reading the stem twice costs ten seconds and would have "
            f"caught {both}."
        ),
        tag=", ".join(topics),
    )


# ---- per-question review ----


def build_question_reviews(sections: list[SectionOutcome]) -> list[ReportQuestionReview]:
    """Every question, in the order it was sat. The shortcut is attached only when the candidate
    did not already answer it cleanly — telling someone who nailed it about a faster route is
    noise."""
    return [
        ReportQuestionReview(
            question_id=q.question_id,
            section=q.section,
            topic=q.topic,
            quadrant=q.quadrant,
            question_text=q.question_text,
            options=q.options,
            picked=q.picked,
            correct_option=q.correct_option,
            is_correct=q.is_correct,
            elapsed_seconds=q.elapsed_seconds,
            expected_time_seconds=q.expected_time_seconds,
            explanation=q.explanation,
            distractor_rationale=q.distractor_rationale,
            shortcut_name=q.shortcut_name if q.quadrant != "mastered" else None,
            shortcut_how=q.shortcut_how if q.quadrant != "mastered" else None,
            shortcut_saves_seconds=(
                q.shortcut_saves_seconds if q.quadrant != "mastered" else None
            ),
        )
        for section in sections
        for q in sorted(section.questions, key=lambda q: q.order)
    ]
