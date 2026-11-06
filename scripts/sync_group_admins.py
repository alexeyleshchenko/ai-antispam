#!/usr/bin/env python3
"""Reconcile the stored admin roster against Telegram's own admin lists.

Why this exists (#130)
----------------------
`group_administrators` had exactly one production writer: the moment the bot
was added to a group, which stored whoever added it. `get_chat_administrators`
was called at seven sites and persisted at none, so the roster went stale the
moment a group's admins changed. Measured across the fleet: 84 admin seats
across 72 humans against 39 roster rows, 45 seats invisible in 13 groups —
and «Тишина: сообщество инвесторов» had 18 admins against 1 row.

The cost of a stale roster is not cosmetic. `validation` skips admins so they
are never classified and never charged; with the roster stale it classified
one of them at 0.90, DELETED his message (irreversible), tried to ban him,
was refused by Telegram, and charged a 1★ review to the group's owner. That
is the exact shape this script prevents.

Dry run is the default
----------------------
`--apply` is required to write. The dry pass reports what it WOULD write using
the same read the write pass uses, so the preview and the write agree by
construction, not by a second counting path.

Usage
-----
    python3 scripts/sync_group_admins.py                 # every active group, read-only
    python3 scripts/sync_group_admins.py --apply         # every active group, write
    python3 scripts/sync_group_admins.py -g -1001234567  # one group
    python3 scripts/sync_group_admins.py --json          # machine-readable

Exit codes: 0 = completed (including "nothing to do"), 2 = read/API error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

# `app` is importable in the image (PYTHONPATH=/app); from the repo it lives
# under src/. Add whichever parent holds it, so the script runs in both places.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (os.path.dirname(_HERE), os.path.join(os.path.dirname(_HERE), "src")):
    if os.path.isdir(os.path.join(_cand, "app")) and _cand not in sys.path:
        sys.path.insert(0, _cand)

# .env BEFORE importing app.common.bot: that module raises ValueError at import
# when BOT_TOKEN is absent, so a load-after-import would never run.
try:
    from dotenv import load_dotenv
except ImportError:  # Docker injects the environment; dotenv is optional locally

    def load_dotenv(*_args, **_kwargs) -> bool:
        return False


load_dotenv(os.path.join(os.path.dirname(_HERE), ".env"))

from app.common.bot import bot
from app.database.group_operations import sync_group_administrators
from app.database.postgres_connection import close_pool, get_pool

logger = logging.getLogger("sync_group_admins")

SWEEP_CONCURRENCY = 5


async def _active_group_ids() -> list[int]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT group_id FROM groups WHERE status = 'active' ORDER BY group_id"
        )
    return [row["group_id"] for row in rows]


async def _sync_all(chat_ids: list[int], *, apply: bool) -> list[dict]:
    """Sync each group, at most SWEEP_CONCURRENCY at a time.

    A failure on one group is reported and skipped, never fatal: the sweep is
    how the fleet gets its roster back, and one group the bot was kicked from
    must not cost the other 46 their refresh.
    """
    semaphore = asyncio.Semaphore(SWEEP_CONCURRENCY)
    results: list[dict] = []

    async def one(chat_id: int) -> None:
        async with semaphore:
            try:
                report = await sync_group_administrators(chat_id, apply=apply)
            except Exception as exc:  # noqa: BLE001 — per-group isolation
                results.append(
                    {
                        "chat_id": chat_id,
                        "title": None,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                return
            results.append(
                {
                    "chat_id": report.chat_id,
                    "title": report.title,
                    "telegram": report.telegram,
                    "known": report.known,
                    "new": report.new,
                    "demoted": report.demoted,
                    "applied": report.applied,
                }
            )

    await asyncio.gather(*(one(chat_id) for chat_id in chat_ids))
    results.sort(key=lambda row: row["chat_id"])
    return results


def _print_report(results: list[dict], *, apply: bool) -> None:
    mode = "APPLY" if apply else "DRY RUN (no writes; pass --apply to write)"
    print(f"\nAdmin roster sync — {mode}")
    print("-" * 78)
    totals = {"telegram": 0, "known": 0, "new": 0, "demoted": 0}
    failed = 0
    for row in results:
        if "error" in row:
            failed += 1
            print(f"{row['chat_id']:>16}  ERROR  {row['error']}")
            continue
        for key in totals:
            totals[key] += row[key]
        title = row["title"] or "(untitled)"
        print(
            f"{row['chat_id']:>16}  {title[:34]:<34} "
            f"telegram={row['telegram']:<3} known={row['known']:<3} "
            f"new={row['new']:<3} demoted={row['demoted']:<3}"
        )
    print("-" * 78)
    print(
        f"{len(results)} group(s), {failed} error(s) — "
        f"seats={totals['telegram']} already_known={totals['known']} "
        f"new={totals['new']} demoted={totals['demoted']}"
    )
    if not apply:
        print("Nothing was written. Re-run with --apply to persist these numbers.")


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile group_administrators against Telegram's admin lists."
    )
    parser.add_argument(
        "-g",
        "--group",
        type=int,
        action="append",
        default=None,
        help="chat id to sync (repeatable); default: every active group",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the reconciled roster (default is a read-only dry run)",
    )
    parser.add_argument(
        "--json", action="store_true", help="print the report as JSON"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="debug logging"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        chat_ids = args.group if args.group else await _active_group_ids()
        if not chat_ids:
            print("No groups to sync.")
            return 0

        results = await _sync_all(chat_ids, apply=args.apply)
        if args.json:
            print(json.dumps(results, indent=2, default=str))
        else:
            _print_report(results, apply=args.apply)
        return 2 if any("error" in row for row in results) else 0
    finally:
        await bot.session.close()
        await close_pool()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
