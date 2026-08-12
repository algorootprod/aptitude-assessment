from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: Literal["local", "dev", "staging", "prod"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    database_url: str = Field(
        default="postgresql+asyncpg://aptitude:aptitude@localhost:5432/aptitude",
        description=(
            "Async Postgres URL for the aptitude-assessment service database. "
            "Points at Neon in every non-local environment — see CLAUDE.md for the "
            "asyncpg + Neon pooled-endpoint caveats."
        ),
    )
    run_migrations: bool = True

    aws_region: str = "ap-south-1"
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    sqs_endpoint_url: str | None = Field(
        default=None,
        description="Custom SQS endpoint for local dev (e.g. the shared ElasticMQ container).",
    )

    sqs_user_signup_url: str = Field(
        default="",
        description="SQS queue URL Nest publishes user-signup events to.",
    )
    sqs_user_signup_dlq_url: str = Field(
        default="", description="DLQ for poison user-signup messages."
    )
    sqs_evaluation_completed_url: str = Field(
        default="",
        description="SQS queue URL this service publishes evaluation-completed events to.",
    )

    sqs_long_poll_seconds: int = 20
    sqs_max_messages: int = 10
    sqs_max_receive_count: int = Field(
        default=5,
        description=(
            "Max times a message may be received before it is treated as poison: the "
            "consumer stops redelivering it and forwards it to the matching DLQ (if configured)."
        ),
    )

    run_consumers: bool = Field(
        default=False,
        description=(
            "If true, the API process also hosts the signup SQS consumer as a background task."
        ),
    )

    # ---- api-service-handler (ASH) — encrypted API-key pool, own Postgres table ----
    ash_shared_secret: str | None = Field(
        default=None,
        description="AES key for ASH's encrypted key storage. Required to store keys encrypted.",
    )
    ash_connection_string: str | None = Field(
        default=None,
        description=(
            "Plain postgres:// DSN for ASH. Defaults to database_url with +asyncpg stripped."
        ),
    )
    ash_rotation_strategy: str = "round_robin"

    # ---- test assembly: the per-topic level ladder ----
    initial_topic_level: int = Field(
        default=2,
        ge=1,
        le=5,
        description=(
            "Level every topic is seeded at on signup. `level` maps 1:1 onto "
            "`question_bank.difficulty`."
        ),
    )
    min_topic_level: int = Field(default=1, ge=1, le=5)
    max_topic_level: int = Field(default=5, ge=1, le=5)

    di_promote_score_threshold: float = Field(
        default=85.0,
        description=(
            "DI section score (0-100, computed by `evaluation_report` — opaque here) strictly "
            "above which the DI topic gets a promotion signal."
        ),
    )
    di_demote_score_threshold: float = Field(
        default=40.0,
        description="DI section score strictly below which the DI topic gets a demotion signal.",
    )

    time_budget_slack: float = Field(
        default=1.15,
        gt=0,
        description=(
            "Multiplier applied to the sum of a section's `expected_time_seconds` to get its "
            "clock budget. The clock itself runs in Node — this is only the number we hand it."
        ),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
