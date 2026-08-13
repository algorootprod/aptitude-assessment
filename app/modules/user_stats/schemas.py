from pydantic import BaseModel


class SectionWinRates(BaseModel):
    """0-100 mean `mastery_score` per section — null until that section has been evaluated at
    least once (see `user_topic_mapping`'s `SectionProgress.raw_score`, which this mirrors)."""

    quant: float | None = None
    reasoning: float | None = None
    english: float | None = None
    di: float | None = None


class UserStatsOut(BaseModel):
    """`GET /v1/stats/{user_id}` — the profile stats card's Daily-20-native numbers.

    XP, tier and rank are deliberately not here: this service has no concept of any of the
    three (no XP/tier scheme, and college affiliation lives only in Nest/Mongo) — Nest composes
    those from its own existing gamification and leaderboard logic alongside this response.
    """

    user_id: str
    win_rates: SectionWinRates
    tests_taken: int
    questions_solved: int
    avg_time_per_question_seconds: float | None = None
