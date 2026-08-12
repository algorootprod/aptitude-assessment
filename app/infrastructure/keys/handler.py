"""Singleton async wrapper around api-service-handler (ASH), backed by this service's own
Postgres DB. Ported from apex-assessment's `app/infrastructure/keys/handler.py` — see CLAUDE.md
("Keep ASH") for why this is scaffolded now with no consumer yet: it's a complete working
subsystem the moment Phase 2 question generation needs provider API keys.
"""

from __future__ import annotations

import asyncio
import re

from api_service_handler import APIServiceHandler, NoAvailableKeyError
from api_service_handler.enums import Provider

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger("keys")

_handler: APIServiceHandler | None = None
_lock = asyncio.Lock()


def _ash_dsn() -> str:
    """Derive a plain postgres:// DSN from DATABASE_URL by stripping the asyncpg driver."""
    settings = get_settings()
    if settings.ash_connection_string:
        return settings.ash_connection_string
    dsn = settings.database_url
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgres+asyncpg://", "postgresql://"
    )
    # Raw asyncpg DSN parsing treats an unknown `ssl=` query param as a server
    # setting (-> "parameter ssl cannot be changed now"); it expects libpq's
    # `sslmode=`. SQLAlchemy accepts `ssl=`, ASH's plain asyncpg pool does not.
    return re.sub(r"([?&])ssl=", r"\1sslmode=", dsn)


async def get_key_handler() -> APIServiceHandler:
    global _handler
    if _handler is not None and _handler.is_initialized:
        return _handler
    async with _lock:
        if _handler is not None and _handler.is_initialized:
            return _handler
        settings = get_settings()
        secret = settings.ash_shared_secret or ""
        if not secret:
            log.warning(
                "ash_no_shared_secret",
                msg="ASH_SHARED_SECRET not set — keys will be stored unencrypted",
            )
        h = APIServiceHandler(
            storage_backend="postgresql",
            connection_string=_ash_dsn(),
            shared_secret=secret,
            encrypt_keys=bool(secret),
            rotation_strategy=settings.ash_rotation_strategy,
            auto_reset_counters=True,
        )
        await h.initialize()
        _handler = h
        log.info("ash_initialized", backend="postgresql")
        return _handler


async def get_key_value(provider: Provider | str) -> str:
    """Return the next available key value for *provider*.

    No LLM SDK depends on this yet in this pass (question_generation is a Phase 2 stub) —
    this exists so the key pool is provisioned and testable ahead of that work.
    """
    handler = await get_key_handler()
    try:
        key = await handler.get_next_key(provider, decrypt=True)
        return key.key_value
    except NoAvailableKeyError:
        prov = provider if isinstance(provider, str) else provider.value
        raise RuntimeError(
            f"No API key available for provider '{prov}'. "
            f"Add one via: ./scripts/run_worker.sh keys add --provider {prov} --key <value>"
        ) from None


async def close_key_handler() -> None:
    global _handler
    if _handler is not None:
        await _handler.close()
        _handler = None
