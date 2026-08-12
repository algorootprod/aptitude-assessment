"""Fire-and-forget background tasks that actually survive to completion.

`asyncio.create_task` returns a task the event loop holds only a *weak* reference to, so a task
nobody keeps a handle on can be garbage-collected mid-flight — the failure is silent and
intermittent, which is the worst kind. This module keeps a strong reference until the task
finishes, logs anything it raises instead of swallowing it into a never-awaited future, and lets
the app's shutdown drain what is still in flight.

Used by `evaluation_report.service.evaluate` to move the level ladder and next-test assembly off
the request path — the candidate gets their report back without waiting for work that only
matters when they next sit down. Anything spawned here must therefore be recoverable if it never
runs at all: see `POST /v1/admin/reconcile`.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any

from app.core.logging import get_logger

log = get_logger("tasks")

_running: set[asyncio.Task[None]] = set()


def spawn(coro: Coroutine[Any, Any, None], *, name: str) -> asyncio.Task[None]:
    """Run `coro` in the background, holding a reference until it completes."""
    task = asyncio.create_task(coro, name=name)
    _running.add(task)
    task.add_done_callback(_finished)
    return task


def _finished(task: asyncio.Task[None]) -> None:
    _running.discard(task)
    if task.cancelled():
        log.warning("background_task_cancelled", task_name=task.get_name())
        return
    error = task.exception()
    if error is not None:
        # Nothing is awaiting this task, so an unlogged exception would vanish entirely.
        log.error(
            "background_task_failed",
            task_name=task.get_name(),
            error=str(error),
            exc_info=error,
        )


def pending() -> int:
    return len(_running)


async def drain(grace_seconds: float = 10.0) -> None:
    """Wait for in-flight tasks during shutdown, cancelling whatever is still going after the
    grace period.

    Anything cancelled here is left for `POST /v1/admin/reconcile` to pick up, which is why the
    grace period can be short: correctness does not depend on it.
    """
    if not _running:
        return
    log.info("background_tasks_draining", count=len(_running))
    outstanding = list(_running)
    done, still_running = await asyncio.wait(outstanding, timeout=grace_seconds)
    for task in still_running:
        task.cancel()
    if still_running:
        log.warning("background_tasks_abandoned", count=len(still_running))
        await asyncio.gather(*still_running, return_exceptions=True)
    log.info("background_tasks_drained", completed=len(done))
