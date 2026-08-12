import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def to_asyncpg_url(database_url: str) -> str:
    """Normalize a Neon-issued connection string into one SQLAlchemy's asyncpg dialect accepts.

    The connection string Neon's dashboard hands you is a plain libpq URL
    (`postgresql://...?sslmode=require&channel_binding=require`), not an asyncpg one — three
    fixes needed, confirmed against a real Neon string during scaffolding (see CLAUDE.md,
    "Neon gotchas"):
      1. `postgresql://` / `postgres://` -> `postgresql+asyncpg://`, else SQLAlchemy picks the
         sync psycopg2 dialect and `create_async_engine` fails with `ModuleNotFoundError:
         psycopg2` (there's no async driver to select without the `+asyncpg` suffix).
      2. Drop `sslmode=` — asyncpg treats it as an unknown server-side setting; TLS is
         requested instead via `connect_args={"ssl": ...}`.
      3. Drop `channel_binding=` — a libpq/psycopg-only negotiation parameter asyncpg doesn't
         understand at all.

    Shared with `app/infrastructure/db/migrations/env.py`, which needs the same transform for
    Alembic's own engine.
    """
    parts = urlsplit(database_url)
    scheme = re.sub(r"^postgres(ql)?$", "postgresql+asyncpg", parts.scheme)
    query = re.sub(r"(^|&)(sslmode|channel_binding)=[^&]*", "", parts.query)
    query = re.sub(r"^&+", "", query)
    return urlunsplit((scheme, parts.netloc, parts.path, query, parts.fragment))


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            to_asyncpg_url(settings.database_url),
            pool_pre_ping=True,
            pool_recycle=300,
            connect_args={
                "command_timeout": 60,
                "ssl": "require",
                # Neon's pooled (`-pooler`) endpoint runs PgBouncer in transaction mode, which
                # breaks asyncpg's server-side prepared-statement cache. Disabling it costs a
                # little per-query overhead but is required against that endpoint; the direct
                # (non-pooled) endpoint tolerates it fine too. See CLAUDE.md ("Neon gotchas").
                "statement_cache_size": 0,
            },
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
