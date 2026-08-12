from pydantic import BaseModel, Field, model_validator

from app.core.constants import DI_SECTION, Quadrant


class SignupIn(BaseModel):
    user_id: str


class TopicMastery(BaseModel):
    section: str
    topic: str
    current_level: int
    pending_dir: int = Field(
        description="Probation state: +1 promotion pending, -1 demotion pending, 0 neutral."
    )
    mastery_score: float
    streak: int


class SectionProgress(BaseModel):
    """How far through the 1-5 ladder one section is, on a 0-100 scale — see `progress.py`.

    Distinct from the per-sitting section score in `evaluation_report`: this is standing carried
    across cycles, that is how one paper went, and the two will usually disagree.
    """

    section: str
    progress_score: float = Field(description="0-100. Each level owns an equal 20-point slice.")
    current_level: int | None = Field(
        default=None,
        description="The most repeated level across the topics the last test covered. Null "
        "when the section has never been evaluated — which is not the same as scoring zero.",
    )
    raw_score: float | None = Field(
        default=None,
        description="0-100 mean `mastery_score` of those same topics — the last test's "
        "section score. Null alongside `current_level`.",
    )


class SectionProgressPoint(BaseModel):
    """One section's standing after one evaluated test — a single point on the progress chart."""

    cycle_version: int = Field(description="The cycle that was evaluated to produce this point.")
    current_level: int
    raw_score: float
    progress_score: float


class SectionProgressSeries(BaseModel):
    section: str
    points: list[SectionProgressPoint] = Field(
        default_factory=list, description="Oldest to newest, ready to plot without re-sorting."
    )


class ProgressHistoryOut(BaseModel):
    """`GET /v1/progress/{user_id}` — progress only, no topic or question detail."""

    user_id: str
    tests: int = Field(description="How many evaluated cycles this response actually covers.")
    sections: list[SectionProgressSeries]


class UserTopicMapOut(BaseModel):
    user_id: str
    cycle_version: int
    topics: list[TopicMastery]
    section_progress: list[SectionProgress] = Field(
        default_factory=list,
        description="One entry per section, in `SECTION_ORDER`. All zero until the first "
        "evaluation — no initial score is assigned at signup.",
    )


class TopicOutcomeIn(BaseModel):
    """How one topic went in one completed test.

    Two shapes in one row, because the two kinds of section carry different evidence:

    - **DI** sends `score` (0-100). A DI section is one whole `set_id` — five questions sharing a
      chart and a single topic — so its evidence is a section score, not a per-question quadrant.
      How that score is computed is `evaluation_report`'s business; this module only bands it.
    - **Every other section** sends `quadrant`, the verdict on that topic's one question.
    """

    section: str
    topic: str
    quadrant: Quadrant | None = None
    score: float | None = Field(default=None, ge=0.0, le=100.0)

    @model_validator(mode="after")
    def _exactly_one_signal(self) -> "TopicOutcomeIn":
        if self.section == DI_SECTION:
            if self.score is None:
                raise ValueError(f"section '{DI_SECTION}' requires `score`")
            if self.quadrant is not None:
                raise ValueError(f"section '{DI_SECTION}' must not send `quadrant`")
        else:
            if self.quadrant is None:
                raise ValueError(f"section '{self.section}' requires `quadrant`")
            if self.score is not None:
                raise ValueError(f"section '{self.section}' must not send `score`")
        return self


class EvaluationResultIn(BaseModel):
    """What `evaluation_report.service` hands to `update_from_evaluation` after scoring a
    completed test, via sync + retry (see `infrastructure/messaging/retry.py`).

    One row per *topic*, not per question — the ladder moves topics. `cycle_version` is the cycle
    that was just completed; `update_from_evaluation` rejects anything that is not the
    candidate's current cycle, which is what makes the retry safe to replay.
    """

    user_id: str
    cycle_version: int
    topic_outcomes: list[TopicOutcomeIn]
