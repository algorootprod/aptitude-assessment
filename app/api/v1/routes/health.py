from fastapi import APIRouter

from app.core.versioning import module_versions

router = APIRouter()


@router.get("/health")
async def healthz() -> dict[str, object]:
    return {"status": "ok", "modules": module_versions()}


@router.get("/ready")
async def readyz() -> dict[str, str]:
    return {"status": "ready"}
