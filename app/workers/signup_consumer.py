"""Standalone worker process for the `algoaptitude-user-signup.fifo` queue.
Started as `python -m app.workers.signup_consumer` (or `./scripts/run_worker.sh signup`).
Ported from apex-assessment's `app/workers/signup_consumer.py`.
"""

import asyncio

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.infrastructure.messaging.subscriber import consume
from app.modules.user_topic_mapping.handlers import handle_user_signup


async def _run() -> None:
    configure_logging()
    log = get_logger("user_topic_mapping")
    settings = get_settings()
    if not settings.sqs_user_signup_url:
        log.error("worker_missing_queue_url", queue="signup")
        raise SystemExit(1)
    log.info("worker_starting", queue=settings.sqs_user_signup_url)
    await consume(
        settings.sqs_user_signup_url,
        handle_user_signup,
        dlq_url=settings.sqs_user_signup_dlq_url or None,
    )


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
