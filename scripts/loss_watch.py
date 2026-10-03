#!/usr/bin/env python3
"""ai-antispam loss watch — the durable loss signal, read off a surface that survives.

Why this exists (2026-09-30)
----------------------------
The 09-29 outage was DETECTED and never REACHED anyone. The daily health probe
fired at 06:00:11Z, 49 minutes into it, and emitted a red ALERT — delivered as
plain text into the Bot topic, where no agent reads it, on a daily clock, with a
CUMULATIVE count that cannot tell "44 in the last hour" from "44 all night".
Detection was never the gap; the wiring was. 62 messages went unmoderated for
three more hours.

Three properties make this instrument different from that digest:

1. It reads the DURABLE store (``classification_verdicts``), not the docker log.
   The log is wiped on container restart — the 11:12Z restart erased the whole
   outage window, leaving 4 ``gave up`` lines where the DB held 44. A monitor on
   the log can miss the exact outage it exists to catch.

2. It gates on the LOSS signal, not the warning. ``Gateway spam classification
   failed`` means the fallback caught it (healthy: 13x in 6h, 0 lost).
   ``status='failed' AND moderated_at IS NULL AND closed_at IS NULL`` means a
   message is sitting in a real group, unmoderated. Only the second is harm.
   A row the recovery runner has CLOSED (``closed_at`` set) is excluded: it is
   a message that is gone from the group, or skipped by group policy, so there
   is nothing left to moderate — counting it would report a false backlog.

3. It is wired to WAKE A LANE (``deliver_to = session:<uuid>``), which starts an
   active turn. A channel post starts nothing.

Two clocks, and they are deliberately different
-----------------------------------------------
DETECTION runs every 30 min so the first loss after a quiet period is seen fast.
WAKES are floored at ``--min-fire-hours`` (default 6), because AGENTS.md forbids
waking a lane more often than every 6 h. New loss inside the floor is ACCUMULATED
in ``pending_ids`` and announced at the next permitted fire — so the floor delays
a wake without ever losing a message.

Window vs cadence is a coverage property, not a taste: a window of W minutes read
every C minutes covers every instant only when W - lag > C. The first version used
W=30 against C=30 and silently missed ~5 min of every 30. W=480, C=30, lag=5.

Quiet by default: prints NOTHING and exits 1 when there is no loss to announce, so
the cron's trigger gate short-circuits and costs 0 tokens.

Exit codes: 0 = fired (report on stdout), 1 = quiet, 2 = read error.

Token hygiene: reads no credential. ``psql`` runs inside the postgres container,
so no password crosses the wire and none is printed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

SSH_TARGET = "apps"
PG_CONTAINER = "postgres"
PG_DB = "ai_spam_bot"
DEFAULT_STATE = "/root/.cache/ai-antispam/loss_watch.json"
DEFAULT_WINDOW_MIN = 480
DEFAULT_LAG_MIN = 5
DEFAULT_MIN_FIRE_HOURS = 6.0

# The free-text title is selected LAST so the parse can split on a bounded number
# of separators: a title containing "|" cannot shift the numeric fields.
# `-f -` reads this over stdin, which keeps every quote out of the ssh command
# string (a `$$` in a double-quoted remote command would expand to the PID).
LOSS_SQL = """
SELECT v.chat_id,
       v.message_id,
       v.attempts,
       to_char(v.created_at, 'HH24:MI:SS'),
       to_char(now(), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
       (SELECT count(*) FROM classification_verdicts
         WHERE status = 'failed' AND moderated_at IS NULL
           AND closed_at IS NULL),
       coalesce(g.title, '?')
FROM classification_verdicts v
LEFT JOIN groups g ON g.group_id = v.chat_id
WHERE v.status = 'failed'
  AND v.moderated_at IS NULL
  AND v.closed_at IS NULL
  AND v.created_at > now() - interval '%(window)d minutes'
  AND v.created_at < now() - interval '%(lag)d minutes'
ORDER BY v.chat_id, v.created_at
"""

# Anchored on the stdlib logger form. Every event is printed TWICE (stdlib +
# logfire), so an unanchored grep double-counts; this form counts events.
CAUSE_CMD = (
    "docker logs --since %(window)dm %(container)s 2>&1 | "
    "grep -cE '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9:]{8} \\[WARNING\\] "
    "app\\.spam\\.spam_classifier: Gateway spam classification failed' || true"
)


def sh(args: list[str], timeout: int = 90, stdin: str | None = None) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, input=stdin
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    return proc.returncode, proc.stdout, proc.stderr


def remote(script: str, timeout: int = 90, stdin: str | None = None) -> tuple[int, str, str]:
    return sh(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", SSH_TARGET, script],
        timeout=timeout,
        stdin=stdin,
    )


def read_losses(window_min: int, lag_min: int) -> tuple[str | None, int | None, list[dict]]:
    """(read_instant_utc, lifetime_unmoderated, rows) from the durable verdict store."""
    sql = LOSS_SQL % {"window": window_min, "lag": lag_min}
    rc, out, err = remote(
        f"docker exec -i {PG_CONTAINER} psql -U postgres -d {PG_DB} -tA -f -",
        timeout=90,
        stdin=sql,
    )
    if rc != 0:
        raise RuntimeError(f"psql rc={rc}: {(err or out).strip()[:300]}")

    rows: list[dict] = []
    instant: str | None = None
    lifetime: int | None = None
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 6)
        if len(parts) != 7:
            continue
        chat_id, message_id, attempts, created, now_utc, total, title = parts
        instant = now_utc
        try:
            lifetime = int(total)
        except ValueError:
            pass
        rows.append(
            {
                "chat_id": int(chat_id),
                "message_id": int(message_id),
                "attempts": int(attempts),
                "created": created,
                "title": title,
                "key": f"{chat_id}:{message_id}",
            }
        )
    return instant, lifetime, rows


def read_cause_count(window_min: int) -> int | None:
    rc, out, _ = remote(
        "docker ps --filter label=com.docker.compose.project=ai-antispam"
        " --filter label=com.docker.compose.service=ai-antispam"
        " --format '{{.Names}}' | head -1",
        timeout=45,
    )
    container = out.strip().splitlines()[0].strip() if rc == 0 and out.strip() else ""
    if not container:
        return None
    rc, out, _ = remote(CAUSE_CMD % {"window": window_min, "container": container}, timeout=90)
    if rc != 0:
        return None
    try:
        return int(out.strip().splitlines()[0])
    except (ValueError, IndexError):
        return None


def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: str, state: dict) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def hours_since(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(UTC) - when).total_seconds() / 3600.0


def render(
    rows: list[dict],
    instant: str | None,
    lifetime: int | None,
    window_min: int,
    lag_min: int,
    cause: int | None,
    newly: int,
) -> str:
    by_chat: dict[int, list[dict]] = {}
    for row in rows:
        by_chat.setdefault(row["chat_id"], []).append(row)

    terminal = sum(1 for r in rows if r["attempts"] >= 3)
    lines = [
        "**ai-antispam loss watch: messages left unmoderated**",
        f"_{instant} · window {window_min}m (lag {lag_min}m excluded) · {newly} new_",
        "",
        f"**{len(rows)} unmoderated message(s)** in {len(by_chat)} chat(s) · "
        f"{terminal} terminal (attempts>=3), {len(rows) - terminal} retryable",
        "",
    ]
    for chat_id, chat_rows in sorted(by_chat.items(), key=lambda kv: -len(kv[1])):
        title = chat_rows[0]["title"]
        ids = ", ".join(str(r["message_id"]) for r in chat_rows)
        lines.append(f"- **{title}** (`{chat_id}`) — {len(chat_rows)} msg: {ids}")
        lines.append(f"  - unmoderated since {chat_rows[0]['created']} UTC")
    lines.append("")
    if lifetime is not None and lifetime != len(rows):
        lines.append(
            f"Standing backlog outside this window: **{lifetime - len(rows)}** more "
            f"unmoderated (all-time total {lifetime})."
        )
    if cause is not None:
        lines.append(f"Gateway leg failures in the same window: **{cause}**.")
    lines.append(
        "Read from `classification_verdicts` (`status='failed'`, `moderated_at "
        "IS NULL`, `closed_at IS NULL`), not from the container log."
    )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--window-min", type=int, default=DEFAULT_WINDOW_MIN)
    ap.add_argument("--lag-min", type=int, default=DEFAULT_LAG_MIN)
    ap.add_argument("--state", default=DEFAULT_STATE)
    ap.add_argument("--min-fire-hours", type=float, default=DEFAULT_MIN_FIRE_HOURS)
    ap.add_argument("--no-cause", action="store_true", help="skip the gateway-failure count")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--force", action="store_true", help="ignore the wake floor (bite proof)")
    args = ap.parse_args()

    if args.window_min - args.lag_min <= 30:
        print(
            f"loss watch: COVERAGE — window {args.window_min}m minus lag {args.lag_min}m "
            f"must exceed the 30m cadence or instants are missed",
            file=sys.stderr,
        )
        return 2

    try:
        instant, lifetime, rows = read_losses(args.window_min, args.lag_min)
    except RuntimeError as exc:
        print(f"loss watch: READ ERROR — {exc}", file=sys.stderr)
        return 2

    state = load_state(args.state)
    reported = set(state.get("reported_ids") or [])
    pending = set(state.get("pending_ids") or [])
    current = {r["key"] for r in rows}

    if not rows:
        # Nothing unmoderated anywhere in the window: clear the slate so the next
        # loss fires immediately rather than waiting out a stale floor.
        if reported or pending:
            save_state(args.state, {"reported_ids": [], "pending_ids": [], "last_fire_at": None})
        return 1

    pending |= current - reported
    age = hours_since(state.get("last_fire_at"))
    floor_open = age is None or age >= args.min_fire_hours

    if not (args.force or floor_open):
        save_state(
            args.state,
            {
                "reported_ids": sorted(reported),
                "pending_ids": sorted(pending),
                "last_fire_at": state.get("last_fire_at"),
            },
        )
        return 1

    newly = len(pending)
    cause = None if args.no_cause else read_cause_count(args.window_min)
    save_state(
        args.state,
        {
            "reported_ids": sorted(current),
            "pending_ids": [],
            "last_fire_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "window_min": args.window_min,
        },
    )

    if args.json:
        print(
            json.dumps(
                {
                    "instant": instant,
                    "new": newly,
                    "window_min": args.window_min,
                    "count": len(rows),
                    "lifetime_unmoderated": lifetime,
                    "terminal": sum(1 for r in rows if r["attempts"] >= 3),
                    "gateway_failures": cause,
                    "rows": rows,
                },
                indent=2,
            )
        )
    else:
        print(render(rows, instant, lifetime, args.window_min, args.lag_min, cause, newly))
        print(f"\n_trigger: {newly} new since the last wake_")
    return 0


if __name__ == "__main__":
    sys.exit(main())
