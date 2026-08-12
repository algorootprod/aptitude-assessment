from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.modules.evaluation_report.schemas import ReportOut, TestCompletedIn
from app.modules.evaluation_report.service import EvaluationReportService
from app.modules.user_test_mapping.schemas import StartTestIn, UserTestMapOut
from app.modules.user_test_mapping.service import UserTestMappingService

router = APIRouter()


@router.post("/start")
async def start_test(
    body: StartTestIn, session: AsyncSession = Depends(get_db_session)
) -> UserTestMapOut:
    """The diagram's "User test start" arrow from Node into `user_test_mapping`.

    A pure read of the test already assembled for the candidate's current cycle, so calling it
    twice returns the same paper. 404 if the candidate has no signup on record.
    """
    service = UserTestMappingService(session)
    return await service.get_for_user(body.user_id)


@router.post("/complete")
async def complete_test(
    body: TestCompletedIn, session: AsyncSession = Depends(get_db_session)
) -> ReportOut:
    """The diagram's "User test end" arrow from Node into `evaluation_report`.

    Returns the finished diagnostic report, not an acknowledgement — the candidate should not
    have to make a second call to find out why they got what they got. Level updates and
    tomorrow's paper are built in the background afterwards.

    Idempotent: resubmitting the same cycle returns the report already produced.
    """
    service = EvaluationReportService(session)
    return await service.evaluate(body)
