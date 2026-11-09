#!/usr/bin/env python3
"""Repair the roster state the boot backfill left behind (#130).

Two cohorts, one transaction, **reset first**.

Reset — rows stamped `activated_at` by a boot, not by their holder
------------------------------------------------------------------
`ADMINISTRATORS_ACTIVATED_AT_BACKFILL` used to run from `ensure_trust_tables`
on EVERY boot. It exists to stamp the users who predate the column, so
`initialize_new_admin` — which keys the welcome and the 100 credits on
`activated_at` — does not pay them twice. Run on every boot it also stamped
every row the roster prefill created afterwards: strangers who have never
opened the bot, marked "activated", permanently denied the welcome and the
credits.

The evidence is positive, never inferred. A real activation leaves an
`initial` transaction; the old bot-add branch paid 100 credits WITHOUT one, so
a non-zero balance is the second, independent guard. A row with neither was
stamped by a boot. `activated_at = created_at` proves nothing: `NOW()` is
transaction-constant, so every activated row in production reads that way.

Promote — seats whose holder has proven they are reachable
---------------------------------------------------------
`is_registered` gates billing, the DM recipient list and `/stats`. A seat
written before its holder activated is roster-only, and nothing ever flipped
it back. After the reset, `activated_at IS NOT NULL` means exactly "this
person has a grant record", so those seats become billing rows.

Order is load-bearing: promote before reset would take the 62 seats instead of
the 6, because at that moment the boot-stamped strangers still carry
`activated_at` — turning 56 people who never touched the bot into billing
contacts. The dry run prints both numbers for that reason.

Dry run is the default
----------------------
`--apply` is required to write. Both passes call the same read
(`group_operations.collect_roster_repair`), so the preview and the write agree
by construction. `--apply` re-reads inside the transaction and reports what
the state became; a second `--apply` therefore reads `reset=0 promote=0`.

Usage
-----
    python3 scripts/repair_roster_activation.py                 # read-only
    python3 scripts/repair_roster_activation.py --apply         # write
    python3 scripts/repair_roster_activation.py -g -1001234567  # one group
    python3 scripts/repair_roster_activation.py --json

Exit codes: 0 = completed, 2 = a guard tripped (nothing was written).
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

# .env BEFORE importing app.database: `postgres_connection` reads PG_* at
# import time. Deliberately NOT importing `app.common.bot` — this script talks
# to Postgres only, so it needs no bot token and runs from anywhere.
try:
    from dotenv import load_dotenv
except ImportError:  # Docker injects the environment; dotenv is optional locally

    def load_dotenv(*_args, **_kwargs) -> bool:
        return False

load_dotenv(os.path.join(os.path.dirname(_HERE), ".env"))

from app.database.group_operations import repair_roster_activation
from app.database.postgres_connection import close_pool, get_pool

logger = logging.getLogger("repair_roster_activation")


def _print_report(report: dict, *, apply: bool) -> None:
    mode = "APPLY" if apply else "DRY RUN (no writes; pass --apply to write)"
    print(f"\nRoster activation repair — {mode}")
    print("-" * 78)
    print(
        f"reset   : {report['reset_admins']} admin(s), "
        f"{report['reset_seats']} seat(s), {report['reset_groups']} group(s)"
    )
    if report["reset_created_at"]:
        print(f"          created_at cohort: {len(report['reset_created_at'])} instant(s)")
        for instant, count in sorted(report["reset_created_at"].items()):
            print(f"            {instant}  {count}")
    print(
        f"          guards: with_any_transaction={report['reset_with_any_transaction']} "
        f"(must be 0), with_registered_seat={report['reset_with_registered_seat']} "
        f"(must be 0)"
    )
    print(
        f"promote : {report['promote_seats']} seat(s), "
        f"{report['promote_admins']} admin(s), {report['promote_groups']} group(s)"
    )
    print(
        f"          without the reset first it would take "
        f"{report['promote_without_reset']} seat(s) — the ordering is load-bearing"
    )
    for row in report["promote_rows"]:
        title = (row.get("title") or "(untitled)")[:30]
        username = row.get("username") or "-"
        print(
            f"            {row['group_id']:>16}  {title:<30}  "
            f"{row['admin_id']:>12}  @{username}"
        )
    print("-" * 78)
    if apply:
        print(
            f"applied: promote wrote {report['promote_written']} seat(s); "
            f"after the writes reset={report['after']['reset_admins']} "
            f"promote={report['after']['promote_seats']}"
        )
    else:
        print("Nothing was written. Re-run with --apply to persist these numbers.")


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repair activated_at / is_registered left by the boot backfill."
    )
    parser.add_argument(
        "-g",
        "--group",
        type=int,
        action="append",
        default=None,
        help="chat id to repair (repeatable); default: the whole fleet",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the repair (default is a read-only dry run)",
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--verbose", "-v", action="store_true", help="debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            report = await repair_roster_activation(
                conn, apply=args.apply, group_ids=args.group
            )
            refused = report.get("refused")
            if refused:
                for failure in refused:
                    logger.error(f"refusing: {failure}")
                if args.json:
                    print(json.dumps(report, indent=2, default=str))
                else:
                    print("\nREFUSED — nothing was written:")
                    for failure in refused:
                        print(f"  - {failure}")
                return 2

        if args.json:
            print(json.dumps(report, indent=2, default=str))
        else:
            _print_report(report, apply=args.apply)
        return 0
    finally:
        await close_pool()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
