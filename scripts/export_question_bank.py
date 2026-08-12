#!/usr/bin/env python
"""Export the `question_bank` table from a Postgres DB to a local JSON fixture.

Ported from apex-assessment's `scripts/export_question_bank.py`, simplified — this table has
no S3 audio references or a sub-skill taxonomy to validate against, so this script is a
straight dump, no row filtering.

Run where the SOURCE DB is reachable (Neon is public, so this works from a laptop):

    uv run python scripts/export_question_bank.py
    # or, to export from a different DB than the one in .env's DATABASE_URL:
    SRC_DB_URL=postgresql://user:pass@host/db?sslmode=require \
        uv run python scripts/export_question_bank.py

Reads `.env` the same way the app does (via pydantic-settings), so there's no need to shell-
source it — and no risk of a shell choking on the unquoted `&` in a Neon connection string's
`&channel_binding=require`.

Writes data/seed/question_bank.json. Commit it — `app/workers/seed_question_bank.py` loads it
into a target DB (manually; not run automatically — see that module's docstring).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import asyncpg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings  # noqa: E402

SEED_DIR = Path(__file__).resolve().parent.parent / "data" / "seed"
OUT_PATH = SEED_DIR / "question_bank.json"


def to_asyncpg_dsn(url: str) -> str:
    """Normalize a SQLAlchemy/Neon-issued connection string into a raw asyncpg DSN."""
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    url = url.replace("postgres+asyncpg://", "postgresql://")
    url = re.sub(r"([?&])channel_binding=[^&]*", "", url)
    url = re.sub(r"([?&])ssl=", r"\1sslmode=", url)
    return url


def _json_default(obj: Any) -> str:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Cannot JSON-serialize {type(obj).__name__}: {obj!r}")


async def main() -> int:
    src_url = os.environ.get("SRC_DB_URL") or get_settings().database_url

    conn = await asyncpg.connect(to_asyncpg_dsn(src_url))
    for typ in ("json", "jsonb"):
        await conn.set_type_codec(typ, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    try:
        records = await conn.fetch("SELECT * FROM question_bank ORDER BY id")
    finally:
        await conn.close()

    rows = [dict(r) for r in records]

    SEED_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(rows, default=_json_default, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    by_section: dict[str, int] = {}
    for row in rows:
        by_section[row["section"]] = by_section.get(row["section"], 0) + 1

    print(f"Exported {len(rows)} rows -> {OUT_PATH.relative_to(SEED_DIR.parent.parent)}")
    for section, count in sorted(by_section.items()):
        print(f"  {section:<12} {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
