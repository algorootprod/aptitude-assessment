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


class UserTopicMapOut(BaseModel):
    user_id: str
    cycle_version: int
    topics: list[TopicMastery]


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
