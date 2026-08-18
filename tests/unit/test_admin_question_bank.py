"""Guards on the question-bank admin surface.

The bank is curated and shared: a candidate's paper is a list of question ids held in a JSON blob
with no foreign key behind it, and a DI section is one whole five-question set. So the interesting
behaviour here is not the CRUD, it is what the service *refuses* — and these are the cases that
would otherwise break a live test with no database error to notice.
"""

import pytest

from app.core.exceptions import ConflictError, NotFoundError
from app.modules.user_test_mapping.models import QuestionBank
from app.modules.user_test_mapping.schemas import (
    AdminQuestionCreateIn,
    AdminQuestionUpdateIn,
)
from app.modules.user_test_mapping.service import UserTestMappingService


class StubRepo:
    """Stands in for `UserTestMappingRepository` — the guards are the logic under test, not SQL."""

    def __init__(
        self,
        question: QuestionBank | None = None,
        *,
        pending: int = 0,
        di_set_size: int = 5,
    ) -> None:
        self.question = question
        self.pending = pending
        self.di_set_size = di_set_size
        self.deleted: list[str] = []
        self.created: list[dict] = []

    async def get_question(self, question_id: str) -> QuestionBank | None:
        if self.question is not None and self.question.id == question_id:
            return self.question
        return None

    async def count_pending_paper_references(self, question_id: str) -> int:
        return self.pending

    async def count_di_set_size(self, set_id: str) -> int:
        return self.di_set_size

    async def delete_question(self, question_id: str) -> None:
        self.deleted.append(question_id)

    async def create_question(self, values: dict) -> QuestionBank:
        self.created.append(values)
        return QuestionBank(**values)

    async def update_question(self, row: QuestionBank, values: dict) -> QuestionBank:
        for key, value in values.items():
            setattr(row, key, value)
        return row


def service_with(repo: StubRepo) -> UserTestMappingService:
    svc = UserTestMappingService.__new__(UserTestMappingService)
    svc.session = None  # type: ignore[assignment]
    svc.repo = repo  # type: ignore[assignment]
    return svc


def question(**overrides) -> QuestionBank:  # type: ignore[no-untyped-def]
    defaults = dict(
        id="quant.ages.201",
        section="quant",
        topic="Problems on Ages",
        question_text="How old?",
        option_a="1",
        option_b="2",
        option_c="3",
        option_d="4",
        answer="A",
        difficulty=2,
        expected_time_seconds=70,
        set_id=None,
    )
    defaults.update(overrides)
    return QuestionBank(**defaults)


# ---- delete guards ----


async def test_delete_is_refused_while_the_question_is_in_a_live_paper() -> None:
    """`user_test_questions.sections` holds ids with no foreign key, so this delete would break
    those candidates' `/v1/tests/start` with nothing raised at the database level."""
    repo = StubRepo(question(), pending=2)
    with pytest.raises(ConflictError) as exc:
        await service_with(repo).admin_delete_question("quant.ages.201")

    assert "2 candidates' current papers" in exc.value.message
    assert repo.deleted == []


async def test_the_refusal_is_singular_for_one_candidate() -> None:
    repo = StubRepo(question(), pending=1)
    with pytest.raises(ConflictError) as exc:
        await service_with(repo).admin_delete_question("quant.ages.201")

    assert "1 candidate's current paper" in exc.value.message


async def test_delete_is_refused_when_it_would_shrink_a_di_set_below_five() -> None:
    """A DI section is one whole set of five sharing a chart; four cannot fill a section."""
    repo = StubRepo(question(id="di.bar.004.q1", section="di", set_id="di.bar.004"), di_set_size=5)
    with pytest.raises(ConflictError) as exc:
        await service_with(repo).admin_delete_question("di.bar.004.q1")

    assert "di.bar.004" in exc.value.message
    assert repo.deleted == []


async def test_di_delete_is_allowed_once_the_set_is_oversized() -> None:
    repo = StubRepo(question(id="di.bar.004.q6", section="di", set_id="di.bar.004"), di_set_size=6)
    result = await service_with(repo).admin_delete_question("di.bar.004.q6")

    assert result.id == "di.bar.004.q6"
    assert repo.deleted == ["di.bar.004.q6"]


async def test_a_non_di_question_is_not_subject_to_the_set_guard() -> None:
    repo = StubRepo(question(), di_set_size=1)
    result = await service_with(repo).admin_delete_question("quant.ages.201")

    assert result.status == "deleted"
    assert repo.deleted == ["quant.ages.201"]


async def test_deleting_something_that_does_not_exist_is_a_404_not_a_500() -> None:
    with pytest.raises(NotFoundError):
        await service_with(StubRepo(None)).admin_delete_question("nope")


# ---- create ----


async def test_creating_a_duplicate_id_is_refused() -> None:
    """The id is the natural key every assembled paper stores, so a silent overwrite would
    repoint existing papers at different content."""
    repo = StubRepo(question())
    payload = AdminQuestionCreateIn(
        id="quant.ages.201", section="quant", topic="Problems on Ages", question_text="dupe"
    )
    with pytest.raises(ConflictError) as exc:
        await service_with(repo).admin_create_question(payload)

    assert "already exists" in exc.value.message
    assert repo.created == []


# ---- warnings (non-blocking) ----


async def test_a_missing_answer_key_warns_rather_than_blocking() -> None:
    """`question_bank.answer` is nullable and 0 rows use it today, but an unscoreable question
    reads as a gap for every candidate who sees it — so it is surfaced, not rejected."""
    repo = StubRepo(question())
    result = await service_with(repo).admin_update_question(
        "quant.ages.201", AdminQuestionUpdateIn(answer=None)
    )

    assert result.question.answer is None
    assert any("no answer key" in w.lower() for w in result.warnings)


def test_an_answer_pointing_at_an_empty_option_is_warned_about() -> None:
    warnings = UserTestMappingService._question_warnings(question(option_c=None, answer="C"))
    assert any("option C, which is empty" in w for w in warnings)


def test_a_partially_filled_question_is_warned_about() -> None:
    warnings = UserTestMappingService._question_warnings(question(option_d=None))
    assert any("3 of 4 options" in w for w in warnings)


def test_a_missing_expected_time_is_warned_about() -> None:
    warnings = UserTestMappingService._question_warnings(question(expected_time_seconds=None))
    assert any("no expected time" in w.lower() for w in warnings)


def test_a_complete_question_produces_no_warnings() -> None:
    assert UserTestMappingService._question_warnings(question()) == []
