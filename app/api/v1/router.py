from fastapi import APIRouter

from app.api.v1.routes import admin, health, progress, reports, tests, users

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(tests.router, prefix="/tests", tags=["tests"])
api_router.include_router(reports.router, prefix="/reports", tags=["reports"])
api_router.include_router(progress.router, prefix="/progress", tags=["progress"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
