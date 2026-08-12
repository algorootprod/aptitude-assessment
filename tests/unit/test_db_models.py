"""Proves the scaffold isn't vacuous: every table declared across the modules' models.py
files is registered on the shared metadata, so `alembic upgrade head` has something real to
create. See CLAUDE.md, "Verification"."""

from app.infrastructure.db.base import Base

# Import every module's models so they register on Base.metadata.
from app.modules.evaluation_report import models as _evaluation_report_models  # noqa: F401
from app.modules.user_test_mapping import models as _utm_models  # noqa: F401
from app.modules.user_topic_mapping import models as _utop_models  # noqa: F401

EXPECTED_TABLES = {
    "user_topic_map",
    "user_section_progress",
    "question_bank",
    "user_test_questions",
    "user_answers",
    "evaluation_result",
    "user_reports",
}


def test_all_expected_tables_are_registered() -> None:
    assert EXPECTED_TABLES <= set(Base.metadata.tables.keys())
