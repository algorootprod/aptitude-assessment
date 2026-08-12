"""Idempotent load of `data/seed/question_bank.json` into the `question_bank` table.

**Not run automatically anywhere in this codebase** — not in `scripts/docker-entrypoint.sh`
(which only runs `alembic upgrade head`), not in `app.main`'s lifespan, not in
`scripts/run_api.sh`. This is deliberate, per CLAUDE.md ("Question bank seed data + script"):
seeding a fresh environment with 1,310 real curated questions is an operator action, not
something that should happen implicitly on every boot. Invoke it manually:

    ./scripts/run_worker.sh seed_question_bank
    # or, inside a running container:
    docker exec -it <container> uv run python -m app.workers.seed_question_bank

Every insert is `ON CONFLICT (id) DO NOTHING`, so re-running against a partially-seeded or
already-seeded DB is safe. The fixture is produced by `scripts/export_question_bank.py`.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

import asyncpg

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

log = get_logger("seed_question_bank")

SEED_PATH = Path(__file__).resolve().parents[2] / "data" / "seed" / "question_bank.json"

# Column order matches app/modules/user_test_mapping/models.py:QuestionBank exactly.
COLUMNS = (
    "id",
    "section",
    "topic",
    "concept",
    "prerequisite_concept",
    "method_tag",
    "question_text",
    "option_a",
    "option_b",
    "option_c",
    "option_d",
    "answer",
    "explanation",
    "distractor_rationale_a",
    "distractor_rationale_b",
    "distractor_rationale_c",
    "distractor_rationale_d",
    "shortcut_available",
    "shortcut_name",
    "shortcut_how",
    "shortcut_saves_seconds",
    "difficulty",
    "expected_time_seconds",
    "source",
    "calibration",
    "batch_number",
    "set_id",
    "chart_type",
    "chart_image",
    "chart_image_svg",
    "chart_direction",
    "chart_data",
)


def to_asyncpg_dsn(url: str) -> str:
    """Normalize a SQLAlchemy/Neon-issued connection string into a raw asyncpg DSN."""
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    url = url.replace("postgres+asyncpg://", "postgresql://")
    url = re.sub(r"([?&])channel_binding=[^&]*", "", url)
    url = re.sub(r"([?&])ssl=", r"\1sslmode=", url)
    return url


async def seed_question_bank(dsn: str) -> tuple[int, int]:
    """Load the fixture into `question_bank` at *dsn*. Returns (inserted, already_present)."""
    if not SEED_PATH.exists():
        raise FileNotFoundError(
            f"{SEED_PATH} not found — run scripts/export_question_bank.py first."
        )
    rows: list[dict[str, Any]] = json.loads(SEED_PATH.read_text(encoding="utf-8"))

    conn = await asyncpg.connect(to_asyncpg_dsn(dsn))
    for typ in ("json", "jsonb"):
        await conn.set_type_codec(typ, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    try:
        placeholders = ", ".join(f"${i + 1}" for i in range(len(COLUMNS)))
        stmt = (
            f"INSERT INTO question_bank ({', '.join(COLUMNS)}) VALUES ({placeholders}) "
            "ON CONFLICT (id) DO NOTHING RETURNING id"
        )
        inserted = 0
        for row in rows:
            result = await conn.fetchval(stmt, *(row.get(c) for c in COLUMNS))
            if result is not None:
                inserted += 1
        skipped = len(rows) - inserted
        log.info("seed_loaded", fixture_rows=len(rows), inserted=inserted, already_present=skipped)
        return inserted, skipped
    finally:
        await conn.close()


async def main() -> int:
    configure_logging()
    dsn = get_settings().database_url
    inserted, skipped = await seed_question_bank(dsn)
    print(f"Inserted {inserted} new row(s); {skipped} already present.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
