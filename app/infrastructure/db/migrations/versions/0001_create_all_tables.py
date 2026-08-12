"""create all tables

Revision ID: 0001
Revises:
Create Date: 2026-08-12

Hand-written to match `app/modules/*/models.py` exactly, then run for real with
`alembic upgrade head` against a live Neon database during scaffolding — see CLAUDE.md,
"Question bank: real data now reconciled" for what that database already contained at the
time (a separate, pre-existing `daily20_questions` table with a different schema, later
renamed into `question_bank` by migration `0002`) and CLAUDE.md, "Config and gotchas" for the
connection-string fixes that running this for real surfaced.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_topic_map",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("topic", sa.String(length=128), nullable=False),
        sa.Column("cycle_version", sa.Integer(), nullable=False),
        sa.Column("mastery_score", sa.Float(), nullable=False),
        sa.Column("streak", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("user_id", "topic", name="pk_user_topic_map"),
    )

    op.create_table(
        "question_bank",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("section", sa.String(length=32), nullable=False),
        sa.Column("topic", sa.String(length=128), nullable=False),
        sa.Column("concept", sa.String(length=256), nullable=False),
        sa.Column("prerequisite_concept", sa.String(length=256), nullable=False),
        sa.Column("method_tag", sa.String(length=128), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("answer", sa.CHAR(length=1), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("distractor_rationale", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("shortcut", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("difficulty", sa.SmallInteger(), nullable=False),
        sa.Column("expected_time_seconds", sa.Integer(), nullable=False),
        sa.Column("set_id", sa.String(length=128), nullable=True),
        sa.Column("direction", sa.Text(), nullable=True),
        sa.Column("chart", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("calibration", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_question_bank"),
    )
    op.create_index("ix_question_bank_section", "question_bank", ["section"])
    op.create_index("ix_question_bank_topic", "question_bank", ["topic"])

    op.create_table(
        "user_test_questions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("cycle_version", sa.Integer(), nullable=False),
        sa.Column("sections", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_test_questions"),
        sa.UniqueConstraint("user_id", "cycle_version", name="uq_user_test_questions_cycle"),
    )
    op.create_index("ix_user_test_questions_user_id", "user_test_questions", ["user_id"])

    op.create_table(
        "user_answers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("cycle_version", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.String(length=128), nullable=False),
        sa.Column("picked", sa.CHAR(length=1), nullable=True),
        sa.Column("elapsed_seconds", sa.Integer(), nullable=False),
        sa.Column("unreached", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_answers"),
        sa.UniqueConstraint(
            "user_id", "cycle_version", "question_id", name="uq_user_answers_cycle_question"
        ),
    )
    op.create_index("ix_user_answers_user_id", "user_answers", ["user_id"])

    op.create_table(
        "evaluation_result",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("cycle_version", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.String(length=128), nullable=False),
        sa.Column("section", sa.String(length=32), nullable=False),
        sa.Column("quadrant", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_evaluation_result"),
        sa.UniqueConstraint(
            "user_id", "cycle_version", "question_id", name="uq_evaluation_result_cycle_question"
        ),
    )
    op.create_index("ix_evaluation_result_user_id", "evaluation_result", ["user_id"])

    op.create_table(
        "user_reports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("cycle_version", sa.Integer(), nullable=False),
        sa.Column("headline", sa.String(length=256), nullable=False),
        sa.Column("tiles", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("section_table", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("findings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("actions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_reports"),
        sa.UniqueConstraint("user_id", "cycle_version", name="uq_user_reports_cycle"),
    )
    op.create_index("ix_user_reports_user_id", "user_reports", ["user_id"])


def downgrade() -> None:
    op.drop_table("user_reports")
    op.drop_table("evaluation_result")
    op.drop_table("user_answers")
    op.drop_table("user_test_questions")
    op.drop_table("question_bank")
    op.drop_table("user_topic_map")
