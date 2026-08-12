"""rename daily20_questions to question_bank

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-12

The real curated question set was already loaded into this database under the name
`daily20_questions`, independently of this scaffold — see CLAUDE.md, "Question bank: real data
now reconciled". This migration reconciles the two: drops the empty
`question_bank` placeholder `0001` created, then renames `daily20_questions` into that name.
No data is copied or dropped — the 1,310 existing rows and the table's own indexes/constraints
(`daily20_questions_pkey`, `daily20_questions_answer_check`, `idx_daily20_section_topic`,
`idx_daily20_difficulty`) carry over under their original names; only the relation itself is
renamed.

`app/modules/user_test_mapping/models.py`'s `QuestionBank` model was rewritten in the same
change to match this table's actual columns exactly (flat `option_a`-`option_d` and
`distractor_rationale_a`-`_d`, split `shortcut_*` fields, `chart_type`/`chart_image`/
`chart_image_svg`/`chart_direction`/`chart_data`) — the original model, guessed from
`daily20_prototype.html` before this table's existence was known, no longer matches reality.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("question_bank")
    op.rename_table("daily20_questions", "question_bank")


def downgrade() -> None:
    op.rename_table("question_bank", "daily20_questions")
    # Recreate the empty placeholder exactly as 0001 originally created it.
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
