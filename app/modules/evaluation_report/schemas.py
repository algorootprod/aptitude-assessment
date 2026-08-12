from datetime import datetime

from pydantic import BaseModel, Field

from app.core.constants import Quadrant


class SubmittedAnswer(BaseModel):
    question_id: str
    picked: str | None = Field(
        default=None,
        description="Option letter A-D, or null if the candidate skipped or never reached it.",
    )
    elapsed_seconds: int = 0
    unreached: bool = Field(
        default=False,
        description=(
            "True only when the section clock expired before this question was reached. A "
            "deliberate skip with time still on the clock is a different thing and is scored "
            "as carelessness."
        ),
    )


class SectionTimingIn(BaseModel):
    """Optional true wall-clock per section, if Node tracked it.

    Without this, time used is taken as the sum of the section's `elapsed_seconds`, which
    slightly under-counts (it misses reading time between questions). The "rushed with time in
    hand" finding compares that figure against the section budget, so sending real timings makes
    that finding sharper.
    """

    section: str
    time_used_seconds: int


class TestCompletedIn(BaseModel):
    """`POST /v1/tests/complete`. The clock runs in Node; these numbers are taken as fact."""

    user_id: str
    cycle_version: int
    answers: list[SubmittedAnswer]
    sections: list[SectionTimingIn] = Field(default_factory=list)


class ReportTile(BaseModel):
    quadrant: Quadrant
    label: str
    tone: str
    count: int
    blurb: str


class ReportSectionRow(BaseModel):
    section: str
    section_name: str
    correct: int
    total: int
    time_used_seconds: int
    budget_seconds: int
    note: str = Field(description="One plain sentence on how the clock went in this section.")


class ReportFinding(BaseModel):
    """A pattern across the paper, not a single mistake."""

    tone: str
    heading: str
    detail: str


class ReportAction(BaseModel):
    heading: str
    detail: str
    tag: str


class ReportQuestionReview(BaseModel):
    """One question, after the fact — the only place answer keys reach the candidate."""

    question_id: str
    section: str
    topic: str
    quadrant: Quadrant
    question_text: str
    options: list[str]
    picked: str | None
    correct_option: str | None
    is_correct: bool
    elapsed_seconds: int
    expected_time_seconds: int
    explanation: str | None = None
    distractor_rationale: str | None = Field(
        default=None,
        description="Why the option the candidate actually picked looked right. Null unless "
        "they picked a wrong option that has one on file.",
    )
    shortcut_name: str | None = None
    shortcut_how: str | None = None
    shortcut_saves_seconds: int | None = None


class ReportOut(BaseModel):
    """The diagnostic. Returned by `POST /v1/tests/complete` directly — the candidate should not
    have to make a second call to see why they got what they got."""

    user_id: str
    cycle_version: int
    headline: str
    tiles: list[ReportTile]
    section_table: list[ReportSectionRow]
    findings: list[ReportFinding]
    actions: list[ReportAction]
    questions: list[ReportQuestionReview]
    created_at: datetime | None = None


class ReconcileIn(BaseModel):
    user_id: str | None = Field(
        default=None,
        description="Reconcile one candidate. Omit to sweep every candidate that is stuck.",
    )


class ReconciledCandidate(BaseModel):
    user_id: str
    cycle_version: int
    action: str


class ReconcileOut(BaseModel):
    scanned: int
    reconciled: list[ReconciledCandidate]
    skipped: int
