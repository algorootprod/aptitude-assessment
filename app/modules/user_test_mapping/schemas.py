from typing import Annotated, Any, Literal

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


# ---------------------------------------------------------------------------
# Admin panel
#
# These carry the *whole* row, answer key and coaching material included — the opposite of
# `QuestionOut`, which strips them because it is served to a candidate before the test. Nothing
# here is ever reachable by a candidate: the routes sit behind Nest's AdminAuthGuard.
# ---------------------------------------------------------------------------

AnswerLetter = Literal["A", "B", "C", "D"]


class AdminQuestionOut(BaseModel):
    """One `question_bank` row, verbatim."""

    model_config = {"from_attributes": True}

    id: str
    section: str
    topic: str
    concept: str | None = None
    prerequisite_concept: str | None = None
    method_tag: str | None = None
    question_text: str
    option_a: str | None = None
    option_b: str | None = None
    option_c: str | None = None
    option_d: str | None = None
    answer: AnswerLetter | None = None
    explanation: str | None = None
    distractor_rationale_a: str | None = None
    distractor_rationale_b: str | None = None
    distractor_rationale_c: str | None = None
    distractor_rationale_d: str | None = None
    shortcut_available: bool | None = None
    shortcut_name: str | None = None
    shortcut_how: str | None = None
    shortcut_saves_seconds: int | None = None
    difficulty: int | None = None
    expected_time_seconds: int | None = None
    source: str | None = None
    calibration: str | None = None
    batch_number: int | None = None
    set_id: str | None = None
    chart_type: str | None = None
    chart_image: str | None = None
    chart_image_svg: str | None = None
    chart_direction: str | None = None
    chart_data: Any | None = None


class AdminQuestionListOut(BaseModel):
    rows: list[AdminQuestionOut]
    total: int = Field(description="Matching rows before pagination, so the panel can show n of N.")
    limit: int
    offset: int


class AdminQuestionCreateIn(BaseModel):
    """`id` is required, not generated. It is the natural key the curation pipeline assigns
    (`<section>.<topic>.<n>`, or `<set>.q<n>` for DI) and the value every assembled paper stores,
    so the panel must state it rather than have one invented."""

    id: str = Field(min_length=1)
    section: str
    topic: str
    question_text: str = Field(min_length=1)
    option_a: str | None = None
    option_b: str | None = None
    option_c: str | None = None
    option_d: str | None = None
    answer: AnswerLetter | None = None
    concept: str | None = None
    prerequisite_concept: str | None = None
    method_tag: str | None = None
    explanation: str | None = None
    distractor_rationale_a: str | None = None
    distractor_rationale_b: str | None = None
    distractor_rationale_c: str | None = None
    distractor_rationale_d: str | None = None
    shortcut_available: bool | None = None
    shortcut_name: str | None = None
    shortcut_how: str | None = None
    shortcut_saves_seconds: int | None = None
    difficulty: Annotated[int, Field(ge=1, le=5)] | None = None
    expected_time_seconds: Annotated[int, Field(gt=0)] | None = None
    source: str | None = None
    calibration: str | None = None
    batch_number: int | None = None
    set_id: str | None = None
    chart_type: str | None = None
    chart_image: str | None = None
    chart_image_svg: str | None = None
    chart_direction: str | None = None
    chart_data: Any | None = None


class AdminQuestionUpdateIn(BaseModel):
    """Every field optional — a PATCH applies only what is sent. `id` is absent on purpose: see
    `EDITABLE_QUESTION_FIELDS` in this module's repository."""

    model_config = {"extra": "forbid"}

    section: str | None = None
    topic: str | None = None
    question_text: str | None = Field(default=None, min_length=1)
    option_a: str | None = None
    option_b: str | None = None
    option_c: str | None = None
    option_d: str | None = None
    answer: AnswerLetter | None = None
    concept: str | None = None
    prerequisite_concept: str | None = None
    method_tag: str | None = None
    explanation: str | None = None
    distractor_rationale_a: str | None = None
    distractor_rationale_b: str | None = None
    distractor_rationale_c: str | None = None
    distractor_rationale_d: str | None = None
    shortcut_available: bool | None = None
    shortcut_name: str | None = None
    shortcut_how: str | None = None
    shortcut_saves_seconds: int | None = None
    difficulty: Annotated[int, Field(ge=1, le=5)] | None = None
    expected_time_seconds: Annotated[int, Field(gt=0)] | None = None
    source: str | None = None
    calibration: str | None = None
    batch_number: int | None = None
    set_id: str | None = None
    chart_type: str | None = None
    chart_image: str | None = None
    chart_image_svg: str | None = None
    chart_direction: str | None = None
    chart_data: Any | None = None


class AdminQuestionMutationOut(BaseModel):
    """The saved row plus anything the panel should warn about but that did not block the write."""

    question: AdminQuestionOut
    warnings: list[str] = Field(default_factory=list)


class AdminDeleteOut(BaseModel):
    status: Literal["deleted"] = "deleted"
    id: str


class AdminSectionOut(BaseModel):
    section: str
    display: str
    topics: list[str]
    questions_per_test: int = Field(
        description="Slots this section fills in a paper — 5 for every section today."
    )
    topics_per_test: int = Field(
        description="Topics drawn per cycle. 1 for DI, because a DI section is one whole set."
    )


class AdminSectionDifficultyRow(BaseModel):
    section: str
    display: str
    total: int
    by_difficulty: dict[str, int] = Field(
        description="Difficulty 1-5 to count. The key `unset` holds rows with a null difficulty."
    )
    topics_in_bank: int
    topics_expected: int


class AdminAnalyticsOut(BaseModel):
    total_questions: int
    total_topics: int
    sections: list[AdminSectionDifficultyRow]
    missing_answer_key: int = Field(
        description="Rows with no `answer`. These can never score as mastered."
    )
    malformed_di_sets: list[dict[str, int | str]] = Field(
        default_factory=list,
        description="DI sets not holding exactly five questions, as {set_id, size}.",
    )
