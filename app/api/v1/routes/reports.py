from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.modules.evaluation_report.schemas import ReportOut
from app.modules.evaluation_report.service import EvaluationReportService

router = APIRouter()


@router.get("/{user_id}")
async def get_report(
    user_id: str,
    cycle_version: int | None = Query(
        default=None, description="A past cycle. Omit for the most recent report."
    ),
    session: AsyncSession = Depends(get_db_session),
) -> ReportOut:
    """Re-read a stored report. 404 if the candidate has none.

    Reports are stored fully rendered, so this returns exactly what the candidate saw at the
    time — not a rebuild against a question bank that may have moved since.
    """
    service = EvaluationReportService(session)
    return await service.get_report(user_id, cycle_version)
