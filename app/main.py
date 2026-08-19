import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import ConflictError, NotFoundError
from app.core.logging import configure_logging, get_logger
from app.core.tasks import drain as drain_background_tasks
from app.infrastructure.db.session import dispose_engine
from app.infrastructure.keys.handler import close_key_handler


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    log = get_logger("api")
    settings = get_settings()
    log.info("api_startup")

    tasks: list[asyncio.Task[None]] = []
    stop = asyncio.Event()

    if settings.run_consumers:
        from app.infrastructure.messaging.subscriber import consume
        from app.modules.user_topic_mapping.handlers import handle_user_signup

        if settings.sqs_user_signup_url:
            tasks.append(
                asyncio.create_task(
                    consume(
                        settings.sqs_user_signup_url,
                        handle_user_signup,
                        dlq_url=settings.sqs_user_signup_dlq_url or None,
                        stop_event=stop,
                    ),
                    name="consumer-signup",
                )
            )
            log.info("consumer_started", queue="signup")
        else:
            log.warning("consumer_skipped", queue="signup", reason="SQS_USER_SIGNUP_URL not set")

    try:
        yield
    finally:
        stop.set()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # Fire-and-forget post-evaluation work (ladder + next-test assembly). Drains
        # unconditionally: it has nothing to do with whether SQS consumers were started, and
        # anything abandoned here is recoverable via POST /v1/admin/reconcile.
        await drain_background_tasks()
        await close_key_handler()
        await dispose_engine()
        log.info("api_shutdown")


app = FastAPI(
    title="Aptitude Assessment",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix="/v1")


@app.exception_handler(NotFoundError)
async def _not_found_handler(request: Request, exc: NotFoundError) -> JSONResponse:
    """An unknown candidate is a client mistake, not a server fault — 404, not 500."""
    return JSONResponse(
        status_code=404,
        content={"detail": exc.message, "module": exc.module, "version": exc.version},
    )


@app.exception_handler(ConflictError)
async def _conflict_handler(request: Request, exc: ConflictError) -> JSONResponse:
    """A guarded admin edit that would break a pending paper or a DI set — 409, so the panel can
    show why rather than reporting a server fault."""
    return JSONResponse(
        status_code=409,
        content={"detail": exc.message, "module": exc.module, "version": exc.version},
    )


def main() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8090, reload=True)


if __name__ == "__main__":
    main()
