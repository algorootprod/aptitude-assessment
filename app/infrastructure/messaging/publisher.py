import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.infrastructure.messaging.sqs_client import sqs_client

log = get_logger("publisher")


def build_envelope(event: str, payload: dict[str, Any], version: str = "1") -> dict[str, Any]:
    """Build the standard SQS message envelope: {event, version, occurred_at, payload}.

    This is the same shape Nest's own `buildEnvelope()` produces
    (`algojob_nest/src/algoapex/services/algoapex-producer.service.ts`) and the one
    `apex-assessment` publishes/consumes — see CLAUDE.md ("SQS envelope").
    """
    return {
        "event": event,
        "version": version,
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": payload,
    }


async def publish(queue_url: str, event: str, payload: dict[str, Any], version: str = "1") -> str:
    """Send a message envelope to an SQS queue and return the MessageId.

    Handles both Standard and FIFO queues. For FIFO queues (URL ending in `.fifo`),
    MessageGroupId is taken from `payload["user_id"]` and MessageDeduplicationId is
    derived from a stable hash of (event, version, user_id) so retries are idempotent.
    """
    envelope = build_envelope(event, payload, version=version)
    kwargs: dict[str, Any] = {
        "QueueUrl": queue_url,
        "MessageBody": json.dumps(envelope),
    }
    if queue_url.endswith(".fifo"):
        user_id = str(payload.get("user_id", "default"))
        dedup_key = f"{event}:{version}:{user_id}:{payload.get('cycle_version', '')}"
        kwargs["MessageGroupId"] = user_id
        kwargs["MessageDeduplicationId"] = hashlib.sha256(dedup_key.encode()).hexdigest()[:64]

    async with sqs_client() as sqs:
        resp = await sqs.send_message(**kwargs)
        message_id: str = resp["MessageId"]
        log.info("message_published", sqs_event=event, version=version, message_id=message_id)
        return message_id
