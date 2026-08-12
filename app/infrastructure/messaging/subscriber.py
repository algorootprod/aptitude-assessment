import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.infrastructure.messaging.sqs_client import sqs_client

HandlerFn = Callable[[dict[str, Any]], Awaitable[None]]

log = get_logger("subscriber")


async def _forward_to_dlq(sqs: Any, dlq_url: str, msg: dict[str, Any]) -> None:
    """Forward a poison message's raw body to a dead-letter queue.

    Preserves FIFO ordering by reusing the source message's MessageGroupId; a fresh
    MessageDeduplicationId (hash of the body + message id) avoids collisions with the
    original send while keeping the forward idempotent across retries of this branch.
    """
    body = msg["Body"]
    kwargs: dict[str, Any] = {"QueueUrl": dlq_url, "MessageBody": body}
    if dlq_url.endswith(".fifo"):
        attrs = msg.get("Attributes", {})
        group_id = attrs.get("MessageGroupId") or "poison"
        dedup_seed = f"{msg.get('MessageId', '')}:{body}"
        kwargs["MessageGroupId"] = group_id
        kwargs["MessageDeduplicationId"] = hashlib.sha256(dedup_seed.encode()).hexdigest()[:64]
    await sqs.send_message(**kwargs)


async def consume(
    queue_url: str,
    handler: HandlerFn,
    *,
    dlq_url: str | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Long-poll an SQS queue and dispatch each message envelope to `handler`.

    Deletes the message on successful handler completion. On failure the message is
    left undeleted so it becomes visible again after the queue's visibility timeout and
    is redelivered. To avoid a poison message looping forever — and, on FIFO queues,
    blocking every later message in its MessageGroupId — a message that has been received
    at least `sqs_max_receive_count` times is parked: it is logged as poisoned, forwarded to
    `dlq_url` if configured, and deleted from the source queue. Stops cleanly when
    `stop_event` is set.
    """
    settings = get_settings()
    _stop = stop_event or asyncio.Event()

    async with sqs_client() as sqs:
        log.info("consumer_polling", queue_url=queue_url)
        while not _stop.is_set():
            try:
                resp = await sqs.receive_message(
                    QueueUrl=queue_url,
                    MaxNumberOfMessages=settings.sqs_max_messages,
                    WaitTimeSeconds=settings.sqs_long_poll_seconds,
                    AttributeNames=["All"],
                )
            except Exception:
                # Don't let a transient/perms/URL error silently kill the
                # consumer — log it and retry after a short backoff.
                log.exception("message_poll_failed", queue_url=queue_url)
                await asyncio.sleep(5)
                continue
            for msg in resp.get("Messages", []):
                message_id = msg["MessageId"]
                receipt_handle = msg["ReceiptHandle"]
                try:
                    envelope = json.loads(msg["Body"])
                    await handler(envelope)
                    await sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
                    log.info("message_processed", message_id=message_id)
                except Exception:
                    receive_count = int(
                        msg.get("Attributes", {}).get("ApproximateReceiveCount", "1")
                    )
                    if receive_count >= settings.sqs_max_receive_count:
                        # Poison message — stop redelivering so it can't loop forever.
                        log.error(
                            "message_poisoned",
                            message_id=message_id,
                            receive_count=receive_count,
                            dlq=bool(dlq_url),
                        )
                        if dlq_url:
                            try:
                                await _forward_to_dlq(sqs, dlq_url, msg)
                            except Exception:
                                # If the DLQ send fails, leave the message on the source
                                # queue rather than dropping it silently.
                                log.exception("dlq_forward_failed", message_id=message_id)
                                continue
                        await sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
                    else:
                        # Leave undeleted for redelivery after the visibility timeout.
                        log.exception(
                            "message_handler_failed",
                            message_id=message_id,
                            receive_count=receive_count,
                        )
