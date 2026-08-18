"""Admin surface: pipeline recovery plus the question-bank panel.

No inbound auth, matching every other router here — the service is not publicly reachable and the
Backend's `AdminAuthGuard` is the gate (see CLAUDE.md, "Integration surface"). Keep it that way:
these routes can rewrite the question bank.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.modules.evaluation_report.schemas import ReconcileIn, ReconcileOut
from app.modules.evaluation_report.service import EvaluationReportService
from app.modules.user_test_mapping.schemas import (
    AdminAnalyticsOut,
    AdminDeleteOut,
    AdminQuestionCreateIn,
    AdminQuestionListOut,
    AdminQuestionMutationOut,
    AdminQuestionOut,
    AdminQuestionUpdateIn,
    AdminSectionOut,
)
from app.modules.user_test_mapping.service import UserTestMappingService

router = APIRouter()


@router.post("/reconcile")
async def reconcile(
    body: ReconcileIn, session: AsyncSession = Depends(get_db_session)
) -> ReconcileOut:
    """Re-run post-evaluation work that never completed.

    `POST /v1/tests/complete` scores the paper and returns the report immediately, then moves the
    level ladder and assembles the next paper in a background task. If that task dies — a process
    restart mid-flight, a DB blip that outlasts its retries — the candidate is left scored but not
    advanced, and `POST /v1/tests/start` would keep serving the paper they already sat.

    Send `{"user_id": "..."}` to fix one candidate, or `{}` to sweep every stuck one. Idempotent
    either way: a candidate who is not stuck is skipped, and the ladder's own replay guard would
    reject a duplicate regardless. Safe to run on a schedule.
    """
    service = EvaluationReportService(session)
    return await service.reconcile(body.user_id)


@router.get("/analytics")
async def analytics(session: AsyncSession = Depends(get_db_session)) -> AdminAnalyticsOut:
    """Bank depth per section and difficulty, topic coverage, and the data-quality figures the
    panel's dashboard is built from."""
    return await UserTestMappingService(session).admin_analytics()


@router.get("/sections")
async def list_sections(
    session: AsyncSession = Depends(get_db_session),
) -> list[AdminSectionOut]:
    """Sections in paper order with their topics — populates the panel's filter dropdowns."""
    return await UserTestMappingService(session).admin_list_sections()


@router.get("/questions")
async def list_questions(
    section: str | None = None,
    topic: str | None = None,
    difficulty: Annotated[int | None, Query(ge=1, le=5)] = None,
    set_id: str | None = None,
    q: Annotated[str | None, Query(description="Substring match on question text or id.")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: AsyncSession = Depends(get_db_session),
) -> AdminQuestionListOut:
    """One page of the bank, with the unpaginated total alongside."""
    return await UserTestMappingService(session).admin_list_questions(
        section=section,
        topic=topic,
        difficulty=difficulty,
        set_id=set_id,
        search=q,
        limit=limit,
        offset=offset,
    )


@router.get("/questions/{question_id}")
async def get_question(
    question_id: str, session: AsyncSession = Depends(get_db_session)
) -> AdminQuestionOut:
    return await UserTestMappingService(session).admin_get_question(question_id)


@router.post("/questions", status_code=201)
async def create_question(
    body: AdminQuestionCreateIn, session: AsyncSession = Depends(get_db_session)
) -> AdminQuestionMutationOut:
    """Add a question. `id` is supplied, not generated — it is the natural key assembled papers
    reference. 409 if it is already taken."""
    return await UserTestMappingService(session).admin_create_question(body)


@router.patch("/questions/{question_id}")
async def update_question(
    question_id: str,
    body: AdminQuestionUpdateIn,
    session: AsyncSession = Depends(get_db_session),
) -> AdminQuestionMutationOut:
    """Partial update — only the fields sent are applied. Returns the saved row plus any
    data-quality warnings (a missing answer key, an empty option the answer points at)."""
    return await UserTestMappingService(session).admin_update_question(question_id, body)


@router.delete("/questions/{question_id}")
async def delete_question(
    question_id: str, session: AsyncSession = Depends(get_db_session)
) -> AdminDeleteOut:
    """Guarded delete. Refused with 409 if the question sits in a candidate's current paper, or if
    removing it would leave its DI set short of five."""
    return await UserTestMappingService(session).admin_delete_question(question_id)
