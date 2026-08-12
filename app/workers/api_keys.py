"""API key seeding CLI for aptitude-assessment.

Wraps api-service-handler with the same DSN + encryption secret the running
service uses, so keys seeded here are immediately usable by the app.

Ported from apex-assessment's `app/workers/api_keys.py`, dropping the Gemini-specific
daily-limit default (no LLM provider is wired up in this pass — see CLAUDE.md, "Keep ASH").

Usage:
    ./scripts/run_worker.sh keys add --provider openai --key sk-... [--daily-limit 10]
    ./scripts/run_worker.sh keys bulk-add --provider openai --keys-file openai_keys.txt
    ./scripts/run_worker.sh keys list [--provider openai]
    ./scripts/run_worker.sh keys delete --id <key-id>
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from api_service_handler.enums import Provider

from app.core.logging import configure_logging
from app.infrastructure.keys.handler import get_key_handler

# ── helpers ───────────────────────────────────────────────────────────────────


def _resolve_provider(value: str) -> Provider:
    p = Provider.from_string(value)
    if p == Provider.CUSTOM:
        # Accept exact enum values (e.g. "openai") or friendly aliases
        raise SystemExit(f"Unknown provider '{value}'. Run with --help to see valid names.")
    return p


# ── subcommands ───────────────────────────────────────────────────────────────


async def _cmd_add(args: argparse.Namespace) -> None:
    handler = await get_key_handler()
    provider = _resolve_provider(args.provider)
    key = await handler.add_key(
        provider=provider,
        key_value=args.key,
        alias=args.alias,
        daily_limit=args.daily_limit,
    )
    print(
        f"Added key  id={key.id}  provider={provider.value}"
        f"  alias={key.alias or '—'}  daily_limit={args.daily_limit or '∞'}"
    )


def _read_keys_file(keys_file: str) -> list[str]:
    path = Path(keys_file)
    if not path.exists():
        raise SystemExit(f"File not found: {path}")
    lines = [
        ln.strip() for ln in path.read_text().splitlines() if ln.strip() and not ln.startswith("#")
    ]
    if not lines:
        raise SystemExit("No keys found in file.")
    return lines


async def _cmd_bulk_add(args: argparse.Namespace) -> None:
    lines = await asyncio.to_thread(_read_keys_file, args.keys_file)
    handler = await get_key_handler()
    provider = _resolve_provider(args.provider)

    added = 0
    for i, key_value in enumerate(lines, 1):
        alias = f"{provider.value}-{i}"
        try:
            key = await handler.add_key(
                provider=provider,
                key_value=key_value,
                alias=alias,
                daily_limit=args.daily_limit,
            )
            print(f"  + {key.id}  {alias}  daily_limit={args.daily_limit or '∞'}")
            added += 1
        except Exception as exc:
            print(f"  ✗ line {i}: {exc}", file=sys.stderr)

    print(f"\nAdded {added}/{len(lines)} keys for provider '{provider.value}'.")


async def _cmd_list(args: argparse.Namespace) -> None:
    handler = await get_key_handler()
    provider = _resolve_provider(args.provider) if args.provider else None
    keys = await handler.get_all_keys(provider=provider)
    if not keys:
        print("No keys found.")
        return

    header = f"{'ID':<38}  {'PROVIDER':<16}  {'ALIAS':<20}  {'STATUS':<12}  {'DAILY USE/LIMIT'}"
    print(header)
    print("─" * len(header))
    for k in keys:
        limit = str(k.daily_limit) if k.daily_limit is not None else "∞"
        print(
            f"{k.id:<38}  {k.provider.value:<16}  {(k.alias or '—'):<20}  "
            f"{k.status.value:<12}  {k.daily_usage_count}/{limit}"
        )


async def _cmd_delete(args: argparse.Namespace) -> None:
    handler = await get_key_handler()
    ok = await handler.delete_key(args.id, hard=args.hard)
    if ok:
        action = "deleted" if args.hard else "revoked (soft-deleted)"
        print(f"Key {args.id} {action}.")
    else:
        print(f"Key {args.id} not found.", file=sys.stderr)
        sys.exit(1)


# ── parser ────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="api_keys",
        description="Manage API keys in the aptitude-assessment key pool",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="Add a single API key")
    p_add.add_argument("--provider", required=True, help="e.g. openai, anthropic, deepgram")
    p_add.add_argument("--key", required=True, help="The raw API key value")
    p_add.add_argument("--alias", default=None, help="Human-friendly label")
    p_add.add_argument("--daily-limit", type=int, default=None, help="Override daily request cap")

    p_bulk = sub.add_parser("bulk-add", help="Add keys from a file (one key per line)")
    p_bulk.add_argument("--provider", required=True)
    p_bulk.add_argument("--keys-file", required=True, help="Path to file with one key per line")
    p_bulk.add_argument("--daily-limit", type=int, default=None)

    p_list = sub.add_parser("list", help="List all keys (optionally filter by provider)")
    p_list.add_argument("--provider", default=None)

    p_del = sub.add_parser("delete", help="Revoke (soft-delete) a key by ID")
    p_del.add_argument("--id", required=True, help="Key ID from 'list'")
    p_del.add_argument("--hard", action="store_true", default=False, help="Permanently delete")

    return parser


_CMDS = {
    "add": _cmd_add,
    "bulk-add": _cmd_bulk_add,
    "list": _cmd_list,
    "delete": _cmd_delete,
}


def main() -> None:
    configure_logging()
    parser = _build_parser()
    args = parser.parse_args()
    try:
        asyncio.run(_CMDS[args.cmd](args))
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
