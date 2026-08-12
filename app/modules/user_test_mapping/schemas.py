from pydantic import BaseModel, Field

from app.core.constants import SelectionFallback


class StartTestIn(BaseModel):
    user_id: str


class TopicRef(BaseModel):
    """A (section, topic) pair that exists in `question_bank`. Returned by
    `UserTestMappingService.list_topics()` so `user_topic_mapping` can seed and reconcile its
    rows without reading this module's tables directly (see CLAUDE.md, "Architecture rules")."""

    section: str
    topic: str


class TopicSlot(BaseModel):
    """One question slot the candidate's next test must be filled from, chosen by
    `user_topic_mapping` (which owns rotation state) and handed to `on_topic_change`.

    For DI a single slot fills the whole section: one `set_id` is five questions sharing a chart.
    """

    section: str
    topic: str
    level: int


class QuestionOut(BaseModel):
    """Full question content inline — Node never queries this service's DB directly
    (see CLAUDE.md, "Integration surface").

    Answer keys, explanations, distractor rationales and shortcuts are deliberately absent: this
    is served *before* the candidate takes the test.
    """

    id: str
    section: str
    topic: str
    question_text: str
    options: list[str]
    direction: str | None = None
    chart: str | None = Field(
        default=None,
        description=(
            "Always null today. DI charts are shared by all five questions of a set and are "
            "carried once on the section instead — see `SectionQuestions.chart_svg`."
        ),
    )
    expected_time_seconds: int


class SectionQuestions(BaseModel):
    section: str
    budget_seconds: int
    direction: str | None = Field(
        default=None,
        description="DI only: the shared prose describing the set's chart.",
    )
    chart_svg: str | None = Field(
        default=None,
        description=(
            "DI only: the set's inline SVG (~37KB), carried once per section rather than "
            "repeated on each of its five questions."
        ),
    )
    questions: list[QuestionOut]


class UserTestMapOut(BaseModel):
    user_id: str
    cycle_version: int
    sections: list[SectionQuestions]


class SelectedQuestion(BaseModel):
    """One resolved slot as persisted in `user_test_questions.sections` — ids and selection
    provenance only. Question *content* is never denormalized here; it is read back from
    `question_bank` at `/v1/tests/start` time, and answer keys are never copied anywhere."""

    question_id: str
    topic: str
    level: int
    expected_time_seconds: int
    order: int
    selection_fallback: SelectionFallback


class QuestionScoringOut(BaseModel):
    """Everything `evaluation_report` needs about one question to score it and write it up.

    **This is the only route to an answer key.** `QuestionOut` (served before the test) strips
    `answer`, `explanation`, `distractor_rationale` and the shortcut deliberately; this is the
    after-the-fact counterpart, and it exists so `evaluation_report` never reads `question_bank`
    directly (see CLAUDE.md, "Architecture rules"). Keeping both shapes in one file makes the
    difference between them greppable.
    """

    id: str
    section: str
    topic: str
    concept: str | None = None
    prerequisite_concept: str | None = None
    question_text: str
    options: list[str]
    answer: str | None = None
    expected_time_seconds: int
    explanation: str | None = None
    distractor_rationale: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Option letter -> why that wrong option was tempting. The correct option has no "
            "entry, so ~75% fill is expected rather than missing data."
        ),
    )
    shortcut_name: str | None = None
    shortcut_how: str | None = None
    shortcut_saves_seconds: int | None = None


class SelectedSection(BaseModel):
    """The persisted shape of one section of an assembled test."""

    section: str
    budget_seconds: int
    questions: list[SelectedQuestion]
    set_id: str | None = None
    topic: str | None = Field(
        default=None, description="DI only: the single topic the whole set belongs to."
    )
    level: int | None = Field(default=None, description="DI only: the slot level asked for.")
    selection_fallback: SelectionFallback | None = Field(
        default=None, description="DI only: how the set was chosen."
    )
