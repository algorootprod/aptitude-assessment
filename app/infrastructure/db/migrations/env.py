import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.infrastructure.db.base import Base
from app.infrastructure.db.session import to_asyncpg_url

# Import every module's models so Alembic autogenerate sees their tables.
# ASH's `api_keys` table is created lazily by api-service-handler itself on first init —
# deliberately NOT imported here (see CLAUDE.md, "Config and gotchas").
from app.modules.evaluation_report import models as _evaluation_report_models  # noqa: F401
from app.modules.user_test_mapping import models as _utm_models  # noqa: F401
from app.modules.user_topic_mapping import models as _utop_models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Arbitrary but stable across deploys — serializes concurrent replicas (e.g. ECS tasks
# booting together) so they queue on the lock instead of racing each other on
# alembic_version. Distinct from apex-assessment's own lock ids (91347701, 728491001) —
# see CLAUDE.md, "Config and gotchas".
MIGRATION_LOCK_ID = 48213907


def run_migrations_offline() -> None:
    context.configure(
        url=to_asyncpg_url(get_settings().database_url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        # Blocking, transaction-scoped: a second replica waits here instead
        # of colliding with the first; released automatically on commit.
        connection.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": MIGRATION_LOCK_ID})
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = create_async_engine(
        to_asyncpg_url(get_settings().database_url),
        poolclass=pool.NullPool,
        connect_args={"ssl": "require", "statement_cache_size": 0},
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
