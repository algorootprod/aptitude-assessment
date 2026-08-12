from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.modules.evaluation_report.schemas import ReconcileIn, ReconcileOut
from app.modules.evaluation_report.service import EvaluationReportService

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
