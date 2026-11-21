#!/usr/bin/env python3
"""ai-antispam bot-service health probe.

Dependency-free (stdlib only) probe for the deployed ai-antispam bot service.
Runs on the agents box, reaches the service through ``ssh apps``, and emits a
Telegram-ready markdown digest plus a machine-readable ``--json`` form.

Checks
------
1. public ``/health`` liveness + latency (the endpoint Gatus already probes)
2. Telegram webhook registration, pending updates, last delivery error
3. container state / health / restart count / uptime
4. running image digest vs the published ghcr ``:main`` manifest digest
5. update ingress count over the log window
6. ``@telegram_action`` failure counts (permission / Telegram / unexpected)
7. LLM pool rotation / all-models-failed counts (the pool IS the path; the
   gateway hop is disabled by config)

Exit codes: 0 = OK, 1 = WARN, 2 = ALERT.

Token hygiene
-------------
The bot token is read from the container environment and is NEVER printed.
Every string that could carry it passes through :func:`redact` before it can
reach stdout or stderr, and no failure path interpolates the request URL.

Thresholds live in :data:`THRESHOLDS` so the skill can cite them by name.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

OK, WARN, ALERT = 0, 1, 2

SSH_TARGET = "apps"

# Transport retry (see remote()). rc=255 is ssh's own error, rc=124 our own
# timeout; neither is a verdict about the host, so both get another attempt.
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_S = (2, 5)
CONTAINER = "ai-antispam"
IMAGE = "ghcr.io/leshchenko1979/ai-antispam:main"
REGISTRY_REPO = "leshchenko1979/ai-antispam"
PUBLIC_HEALTH_URL = "https://ai-antispam.l1979.ru/health"
EXPECTED_WEBHOOK_URL = "https://ai-antispam.l1979.ru/process-tg-updates"

# #161: the pause reason that marks a group ORPHANED — the bot is an
# administrator with a right withheld and no live human admin is left to tick
# the checkbox. Such a group gets a status write and nothing else: no DM, no
# grace clock, no leave.
ORPHAN_PAUSE_REASON = "no_rights_orphaned_group"

# #150's name for the SAME class, withdrawn by #161 but still carried by rows
# already written to `entity_events` (2 as of 2026-11-21). The reason string is
# stored on the event, so renaming the writer cannot rename history: the
# inventory must match BOTH names or the older orphans vanish from the digest —
# a group hiding by being silent, which is exactly what obligation 1 forbids.
LEGACY_ORPHAN_PAUSE_REASONS = ("no_rights_no_reachable_admin",)

# #162: a TERMINAL row is outside every grace population, because both
# `get_groups_with_no_rights_past_grace` and `sweep_group_administrators` filter
# `status = 'active'`. So once a row is stamped terminal nothing ever looks at
# it again — if the bot is in fact still sitting in that supergroup, no job will
# notice, forever. This leg is the only reader.
#
# It REPORTS and never rewrites. Re-stamping a row `active` to make a job visit
# it is a `status` write, and #144's scope keeps `status` ownership with the
# grace job alone; a second writer of `status` is exactly what #144 refused.
TERMINAL_STATUSES = ("left", "paused", "retired")

# Telegram member statuses that mean the bot IS in the chat. `restricted` is
# deliberately absent: it counts only when `is_member` is true, which the caller
# checks separately, because a restricted non-member has been thrown out and
# still reports `restricted`.
PRESENT_MEMBER_STATUSES = ("creator", "administrator", "member")

THRESHOLDS = {
    "health_max_seconds": 2.0,
    "pending_updates_alert": 100,
    "webhook_error_warn_hours": 2.0,
    "malformed_updates_warn": 5,
    "gateway_fallback_warn_count": 5,
    # A pool rotation the classifier absorbs is designed behaviour (board #117):
    # it is a WARN only in quantity, never an ALERT on a single occurrence.
    "pool_rotation_warn_count": 5,
}

# One awk pass over the docker log window. Anchored on the canonical
# ``[LEVEL] app.<logger>:`` prefix so logfire's pretty-printed trace lines
# (which repeat the same message text without a logger) are not double-counted.
# DEBUG must be included: the webhook-ingress line is logged at DEBUG level, so
# an INFO|WARNING|ERROR anchor reports "0 updates received" while the action
# counters show traffic — a self-contradicting digest (caught 2026-09-12).
LOG_WINDOW_AWK = (
    r'/\[(DEBUG|INFO|WARNING|ERROR)\] app\.handlers\.status_handlers: Webhook update received/ {ingress++; ts=substr($0,1,19); gsub(/ /,"T",ts); last_ingress=ts}'
    r"/\[(DEBUG|INFO|WARNING|ERROR)\] app\.handlers\.handle_spam: .* succeeded for/ {act_ok++}"
    r"/\[(DEBUG|INFO|WARNING|ERROR)\] app\.handlers\.handle_spam: .* failed \(permission error\)/ {act_perm++}"
    r"/\[(DEBUG|INFO|WARNING|ERROR)\] app\.handlers\.handle_spam: .* failed \(Telegram error\)/ {act_tg++}"
    r"/\[(DEBUG|INFO|WARNING|ERROR)\] app\.handlers\.handle_spam: .* failed \(unexpected\)/ {act_unexp++}"
    r"/\[(DEBUG|INFO|WARNING|ERROR)\] app\.spam\.spam_classifier: Gateway spam classification failed/ {llm_gw_fail++}"
    r"/\[(DEBUG|INFO|WARNING|ERROR)\] app\.spam\.spam_classifier: OpenRouter agent [0-9]+\/[0-9]+ failed/ {llm_or_fail++}"
    r"/\[(DEBUG|INFO|WARNING|ERROR)\] app\.spam\.spam_classifier: All spam classifiers failed/ {llm_all_fail++}"
    r"/\[(DEBUG|INFO|WARNING|ERROR)\] app\.handlers\.status_handlers: Received invalid update format/ {bad_update++}"
    r'END {printf "ingress=%d act_ok=%d act_perm=%d act_tg=%d act_unexp=%d '
    r'llm_gw_fail=%d llm_or_fail=%d llm_all_fail=%d bad_update=%d last_ingress=%s\n", ingress+0, '
    r"act_ok+0, act_perm+0, act_tg+0, act_unexp+0, llm_gw_fail+0, llm_or_fail+0, "
    r"llm_all_fail+0, bad_update+0, last_ingress}"
)

_TOKEN: str | None = None


def redact(text: str) -> str:
    """Strip the bot token from anything bound for stdout/stderr."""
    if _TOKEN and text:
        return text.replace(_TOKEN, "<BOT_TOKEN>")
    return text


def sh(args: list[str], timeout: int = 60) -> tuple[int, str, str]:
    """Run a local command, returning (rc, stdout, stderr) with a hard timeout."""
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except OSError as exc:
        return 125, "", type(exc).__name__


def remote(script: str, timeout: int = 90) -> tuple[int, str, str]:
    """Run a shell snippet on the apps host, retrying TRANSPORT failures only.

    Same defect class as ``loss_watch.remote``: a dropped TCP handshake is
    ssh's own rc=255 and says nothing about the host, so one transient reset
    must not fail a whole daily digest leg. Measured 2026-10-31 18:44:58Z —
    sshd's MaxStartups throttle dropped our connection during a brute-force
    burst ("past MaxStartups"), client side "Connection reset by peer".
    """
    last: tuple[int, str, str] = (255, "", "no attempt made")
    for attempt in range(RETRY_ATTEMPTS):
        rc, out, err = sh(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                SSH_TARGET,
                script,
            ],
            timeout=timeout,
        )
        if rc not in (255, 124):
            return rc, out, err
        last = (rc, out, err)
        if attempt + 1 < RETRY_ATTEMPTS:
            time.sleep(RETRY_BACKOFF_S[attempt])
    return last


_RESOLVED_CONTAINER: str | None = None


def container_name() -> str:
    """Resolve the running container by COMPOSE LABELS, not by name.

    The container NAME is not stable: when a recreate is interrupted, compose
    leaves the replacement under a temporary "<replaced-container-id>_<service>"
    name, and that prefix then persists across later recreates (2026-09-25).
    The compose labels are stable, so resolve by them and fall back to the
    configured name only when the lookup yields nothing.
    """
    global _RESOLVED_CONTAINER
    if _RESOLVED_CONTAINER is None:
        rc, out, _ = remote(
            "docker ps"
            " --filter label=com.docker.compose.project=ai-antispam"
            " --filter label=com.docker.compose.service=ai-antispam"
            " --format '{{.Names}}' | head -1",
            timeout=45,
        )
        name = out.strip().splitlines()[0].strip() if rc == 0 and out.strip() else ""
        _RESOLVED_CONTAINER = name or CONTAINER
    return _RESOLVED_CONTAINER


def read_bot_token() -> str | None:
    """Read BOT_TOKEN from the container env. Value is never logged."""
    rc, out, _ = remote(f"docker exec {container_name()} printenv BOT_TOKEN", timeout=45)
    if rc != 0:
        return None
    token = out.strip()
    return token or None

def parse_gateway_enabled(raw: str | None) -> bool | None:
    """Parse the ``gateway_enabled:`` value out of a config.yaml snippet.

    ``None`` means "could not read it" — callers must treat that as unknown and
    keep the legacy behaviour rather than assume the hop is off.
    """
    if not raw:
        return None
    for line in raw.splitlines():
        if ":" not in line:
            continue
        value = line.split(":", 1)[1].split("#", 1)[0].strip().lower()
        if value in ("true", "false"):
            return value == "true"
    return None

def read_gateway_enabled() -> bool | None:
    """Read the container's effective ``llm.gateway_enabled``.

    The hop's state is not visible in the log window — a disabled hop simply
    never logs — so read it from the running container's own config. Returns
    ``None`` when it cannot be read (board #117).
    """
    rc, out, _ = remote(
        f"docker exec {container_name()} sh -lc "
        "\"grep -E '^[[:space:]]*gateway_enabled:' /app/config.yaml\"",
        timeout=45,
    )
    if rc != 0 or not out.strip():
        return None
    return parse_gateway_enabled(out)


def check_public_health() -> dict:
    started = time.monotonic()
    try:
        req = urllib.request.Request(
            PUBLIC_HEALTH_URL, headers={"User-Agent": "ai-antispam-health-probe/1"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            code = resp.status
            body = resp.read(200).decode("utf-8", "replace").strip()
    except urllib.error.HTTPError as exc:
        return {
            "status": exc.code,
            "seconds": round(time.monotonic() - started, 3),
            "body": "",
            "error": f"HTTP {exc.code}",
        }
    except Exception as exc:  # noqa: BLE001 - any transport failure is a result
        return {
            "status": 0,
            "seconds": round(time.monotonic() - started, 3),
            "body": "",
            "error": type(exc).__name__,
        }
    return {
        "status": code,
        "seconds": round(time.monotonic() - started, 3),
        "body": body,
        "error": None,
    }


def check_webhook(token: str) -> dict:
    url = f"https://api.telegram.org/bot{token}/getWebhookInfo"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "ai-antispam-health-probe/1"}
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": type(exc).__name__}
    if not payload.get("ok"):
        return {"error": "telegram returned ok=false"}
    result = payload.get("result", {})
    last_error_ts = result.get("last_error_date")
    last_error_age_hours = None
    if last_error_ts:
        delta = datetime.now(UTC) - datetime.fromtimestamp(last_error_ts, UTC)
        last_error_age_hours = round(delta.total_seconds() / 3600, 2)
    return {
        "url": result.get("url"),
        "url_matches_expected": result.get("url") == EXPECTED_WEBHOOK_URL,
        "pending_update_count": result.get("pending_update_count"),
        "last_error_message": result.get("last_error_message"),
        "last_error_date": last_error_ts,
        "last_error_age_hours": last_error_age_hours,
        "max_connections": result.get("max_connections"),
    }


def check_container() -> dict:
    fmt = (
        "{{.Config.Image}}|{{.State.Status}}|{{.State.Health.Status}}|"
        "{{.State.StartedAt}}|{{.RestartCount}}|{{.Image}}"
    )
    rc, out, _ = remote(f"docker inspect {container_name()} --format '{fmt}'", timeout=45)
    if rc != 0 or not out.strip():
        return {"error": "docker inspect failed"}
    parts = out.strip().split("|")
    if len(parts) < 6:
        return {"error": "unexpected docker inspect output"}
    image, status, health, started_at, restarts, image_id = parts[:6]
    uptime_hours = None
    try:
        started = datetime.fromisoformat(started_at)
        uptime_hours = round((datetime.now(UTC) - started).total_seconds() / 3600, 1)
    except ValueError:
        pass
    return {
        "image": image,
        "status": status,
        "health": health,
        "restart_count": int(restarts or 0),
        "uptime_hours": uptime_hours,
        "image_id": image_id,
    }


def check_image_digest(container: dict) -> dict:
    """Compare the running image's recorded RepoDigest to the published :main."""
    tag = container.get("image") or IMAGE
    rc, out, _ = remote(
        f"docker image inspect {tag} --format '{{{{json .RepoDigests}}}}'", timeout=45
    )
    local_digest = None
    if rc == 0 and out.strip():
        try:
            digests = json.loads(out.strip())
            if digests:
                local_digest = digests[0].split("@")[-1]
        except (ValueError, IndexError):
            local_digest = None
    published = None
    registry_error = None
    try:
        token_url = f"https://ghcr.io/token?scope=repository:{REGISTRY_REPO}:pull&service=ghcr.io"
        with urllib.request.urlopen(token_url, timeout=20) as resp:
            bearer = json.loads(resp.read().decode())["token"]
        req = urllib.request.Request(
            f"https://ghcr.io/v2/{REGISTRY_REPO}/manifests/main",
            method="HEAD",
            headers={
                "Authorization": f"Bearer {bearer}",
                "Accept": (
                    "application/vnd.oci.image.index.v1+json,"
                    "application/vnd.docker.distribution.manifest.list.v2+json"
                ),
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            published = resp.headers.get("Docker-Content-Digest")
    except Exception as exc:  # noqa: BLE001
        registry_error = type(exc).__name__
    return {
        "local_digest": local_digest,
        "published_digest": published,
        "match": bool(local_digest and published and local_digest == published),
        "registry_error": registry_error,
    }


def _orphan_reason_list() -> str:
    """SQL literal list of every pause reason that means ORPHANED."""
    reasons = (ORPHAN_PAUSE_REASON, *LEGACY_ORPHAN_PAUSE_REASONS)
    return ", ".join(f"'{r}'" for r in reasons)


def _orphan_query() -> str:
    """The inventory SELECT. Separate from the runner so a test can read it."""
    return (
        "SELECT DISTINCT ON (g.group_id) g.group_id, g.title, e.created_at::date, "
        "coalesce(g.bot_removed_at::date::text, '') "
        "FROM entity_events e JOIN groups g ON g.group_id = e.entity_id "
        "WHERE e.entity_type = 'group' AND e.action = 'group_paused' "
        f"AND e.reason IN ({_orphan_reason_list()}) AND g.status = 'paused' "
        "ORDER BY g.group_id, e.created_at DESC"
    )


def check_orphaned_groups() -> dict:
    """INVENTORY of paused ORPHANED groups (#161 obligation 1).

    An orphan leaves the NOTIFICATION population — no DM, because every
    registered admin is gone — but it must NOT leave the INVENTORY population.
    This leg is the carrier: one line per orphaned group, printed every day, so
    the state is VISIBLE instead of silently absent. "Never able to hide by
    being silent" is the obligation's own wording.

    An orphan is identified by its PAUSE RECORD, not by inferring it from the
    current roster: `entity_events` carries `action='group_paused'` with the
    reason `no_rights_orphaned_group`. The roster is not consulted because a
    group can be paused for several reasons and re-deriving "why" from live
    state after the fact is a guess; the event is the record of what happened.

    `g.status = 'paused'` keeps the list honest over time: a re-add reactivates
    the group (E+C policy), and a reactivated group is no longer an orphan.

    A read failure is reported as an ERROR, never as "none" — the distinction
    between "no orphans" and "I could not look" is the whole point of the leg.
    """
    rc, out, err = remote(
        "docker exec postgres psql -U postgres -d ai_spam_bot -At -F'|' -c "
        + shlex.quote(_orphan_query())
    )
    if rc != 0:
        return {"error": (err or out).strip()[:200] or f"rc={rc}", "groups": []}

    groups = []
    for line in out.splitlines():
        parts = line.split("|", 3)
        if len(parts) != 4:
            continue
        groups.append(
            {
                "group_id": parts[0],
                "title": parts[1],
                "since": parts[2],
                # Empty means the bot never left — the #161 disposition. A date
                # means a cleanup stamped it, which the row must SAY rather than
                # let the header's "bot stays" assert falsely.
                "bot_left": parts[3],
            }
        )
    return {"groups": groups}

def _terminal_rows_query() -> str:
    """The reconcile SELECT. Separate from the runner so a test can read it.

    `bot_removed_at IS NOT NULL` is the whole premise: a row with no stamp has
    never claimed the bot left, so there is nothing to contradict.
    """
    quoted = ", ".join(f"'{s}'" for s in TERMINAL_STATUSES)
    return (
        "SELECT group_id, coalesce(title, ''), status, "
        "bot_removed_at::text, coalesce(moderation_enabled::text, '') "
        "FROM groups "
        f"WHERE status IN ({quoted}) AND bot_removed_at IS NOT NULL "
        "ORDER BY bot_removed_at"
    )

def _tg_call(token: str, method: str, params: dict) -> tuple[dict | None, str | None]:
    """One Bot API call, returning (result, error_kind).

    `error_kind` is a CLASS, never a response body: the request URL carries the
    token and Telegram echoes request context in some error strings. The one
    body inspected is matched to a fixed literal ("chat not found") and then
    discarded — that answer is not an error, it is Telegram telling us the bot
    is genuinely out of the chat, which is the NO-DIVERGENCE case.
    """
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params).encode()
    try:
        req = urllib.request.Request(
            url, data=data, headers={"User-Agent": "ai-antispam-health-probe/1"}
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            description = str(json.loads(exc.read().decode()).get("description", ""))
        except Exception:  # noqa: BLE001
            description = ""
        if "chat not found" in description.lower():
            return None, "absent"
        return None, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return None, type(exc).__name__
    if not payload.get("ok"):
        return None, "ok=false"
    return payload.get("result"), None

def check_terminal_row_membership(token: str | None) -> dict:
    """RECONCILE terminal group rows against live membership (#162).

    A row stamped `left`/`paused`/`retired` with a `bot_removed_at` is outside
    both grace populations by status, so no job ever visits it again. If the bot
    is in fact still in that supergroup, the stamp is wrong and nothing will
    ever say so — the group would sit unmoderated, outside every population,
    silently, which is #41's failure shape one surface over.

    This leg reads live membership for every such row and reports each
    contradiction. It NEVER rewrites the row: deciding which of the two
    artifacts is wrong is a `status` write, and #144 keeps that with the grace
    job alone. The output is an INVENTORY the digest prints daily.

    Three outcomes are kept distinct, because collapsing them is how a broken
    instrument reads clean:
      - `divergences` — live says the bot is present, the row says it left.
      - `verified_absent` — Telegram itself answered "chat not found"; the
        stamp is right and this is the expected case.
      - `unverified` — we could not ask (network, 5xx, timeout). Neither
        healthy nor divergent; reported so "could not look" never prints as
        "none".
    """
    rc, out, err = remote(
        "docker exec postgres psql -U postgres -d ai_spam_bot -At -F'|' -c "
        + shlex.quote(_terminal_rows_query())
    )
    if rc != 0:
        return {
            "error": (err or out).strip()[:200] or f"rc={rc}",
            "divergences": [],
            "examined": 0,
            "verified_absent": 0,
        }
    if not token:
        return {
            "error": "bot token unavailable",
            "divergences": [],
            "examined": 0,
            "verified_absent": 0,
        }

    me, me_error = _tg_call(token, "getMe", {})
    if me_error or not me:
        return {
            "error": f"getMe failed: {me_error or 'no result'}",
            "divergences": [],
            "examined": 0,
            "verified_absent": 0,
        }
    bot_id = me.get("id")

    divergences: list[dict] = []
    unverified: list[dict] = []
    examined = 0
    verified_absent = 0
    for line in out.splitlines():
        parts = line.split("|", 4)
        if len(parts) != 5:
            continue
        group_id, title, status, removed_at, moderation = parts
        examined += 1
        member, member_error = _tg_call(
            token, "getChatMember", {"chat_id": group_id, "user_id": bot_id}
        )
        if member_error == "absent":
            # Telegram answered "chat not found": the stamp is CONFIRMED, which
            # is the expected case and must not be folded into `examined`.
            verified_absent += 1
            continue
        if member_error or not member:
            unverified.append(
                {"group_id": group_id, "title": title, "reason": member_error or "no result"}
            )
            continue
        live_status = str(member.get("status", ""))
        present = live_status in PRESENT_MEMBER_STATUSES or (
            live_status == "restricted" and bool(member.get("is_member"))
        )
        if present:
            divergences.append(
                {
                    "group_id": group_id,
                    "title": title,
                    "stored_status": status,
                    "bot_removed_at": removed_at,
                    "live_membership": live_status,
                    "moderation_enabled": moderation == "true",
                }
            )
        else:
            # Telegram returned a member record whose status is `left`/`kicked`:
            # the bot is genuinely out, so the stamp is CONFIRMED. This is the
            # second shape of "absent" and must be counted, or the three buckets
            # stop summing to `examined` and the leg can no longer prove it
            # accounted for every row it read.
            verified_absent += 1
    return {
        "examined": examined,
        "verified_absent": verified_absent,
        "divergences": divergences,
        "unverified": unverified,
    }

def check_log_window(hours: int) -> dict:
    script = f"docker logs --since {hours}h {container_name()} 2>&1 | awk '{LOG_WINDOW_AWK}'"
    rc, out, err = remote(script, timeout=120)
    if rc != 0 or "ingress=" not in out:
        return {"error": redact(err.strip()[:200] or f"rc={rc}")}
    counts: dict = {}
    for pair in out.strip().split():
        key, _, value = pair.partition("=")
        if key == "last_ingress":
            counts[key] = value or None
            continue
        try:
            counts[key] = int(value)
        except ValueError:
            continue
    return counts


def parse_last_ingress(value: str | None) -> datetime | None:
    """Parse the ``last_ingress=`` field into an aware UTC datetime.

    The container logs in UTC with a naive ``YYYY-MM-DD HH:MM:SS`` prefix (the
    awk pass rewrites the space to ``T`` so the field survives word splitting).
    """
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def evaluate(report: dict) -> tuple[int, list[str]]:
    """Return (exit_code, list of human-readable reasons)."""
    reasons: list[str] = []
    verdict = OK

    def bump(level: int, reason: str) -> None:
        nonlocal verdict
        verdict = max(verdict, level)
        reasons.append(reason)

    health = report["public_health"]
    if health.get("error") or health.get("status") != 200:
        bump(
            ALERT,
            f"public /health not 200 ({health.get('error') or health.get('status')})",
        )
    elif health.get("seconds", 0) > THRESHOLDS["health_max_seconds"]:
        bump(WARN, f"public /health slow ({health['seconds']}s)")

    logs = report["log_window"]
    last_ingress = parse_last_ingress(logs.get("last_ingress"))

    webhook = report["webhook"]
    if webhook.get("error"):
        bump(WARN, f"webhook info unavailable ({webhook['error']})")
    else:
        if not webhook.get("url_matches_expected"):
            bump(ALERT, "webhook URL does not match TELEGRAM_WEBHOOK_URL")
        pending = webhook.get("pending_update_count") or 0
        if pending >= THRESHOLDS["pending_updates_alert"]:
            bump(ALERT, f"pending updates backed up ({pending})")
        elif pending > 0:
            bump(WARN, f"pending updates present ({pending})")
        age = webhook.get("last_error_age_hours")
        if age is not None:
            # ``last_error_date`` is sticky: Telegram keeps the most recent
            # delivery error until a *new* one overwrites it. So an old error
            # followed by successful ingress is history, not a live fault —
            # escalating it would paint the digest yellow forever and train the
            # reader to ignore it. Escalate only when nothing has been delivered
            # since the error.
            err_epoch = webhook.get("last_error_date")
            err_at = datetime.fromtimestamp(err_epoch, UTC) if err_epoch else None
            recovered = bool(err_at and last_ingress and last_ingress > err_at)
            if not recovered:
                if age <= THRESHOLDS["webhook_error_warn_hours"]:
                    bump(
                        ALERT,
                        f"webhook delivery error {age}h ago, nothing "
                        f"delivered since: {webhook.get('last_error_message')}",
                    )
                else:
                    bump(
                        WARN,
                        f"last webhook delivery error {age}h ago and no "
                        f"ingress since (window may be too short)",
                    )

    container = report["container"]
    if container.get("error"):
        bump(ALERT, f"container inspect failed ({container['error']})")
    else:
        if container.get("status") != "running":
            bump(ALERT, f"container status={container.get('status')}")
        if container.get("health") != "healthy":
            bump(ALERT, f"container health={container.get('health')}")
        if (container.get("restart_count") or 0) > 0:
            bump(WARN, f"restart count {container['restart_count']}")

    digest = report["image_digest"]
    if digest.get("local_digest") and digest.get("published_digest"):
        if not digest["match"]:
            bump(WARN, "running image digest differs from published :main")
    elif digest.get("registry_error"):
        reasons.append(f"digest check skipped (registry {digest['registry_error']})")

    if logs.get("error"):
        bump(WARN, f"log window unavailable ({logs['error']})")
    else:
        if (logs.get("ingress") or 0) == 0:
            bump(WARN, "no webhook updates seen in the window")
        # The pool IS the classification path while the gateway hop is disabled
        # (config.yaml ``llm.gateway_enabled: false``, owner directive
        # 2026-10-01). A single agent failure the rotation absorbs is the
        # DESIGNED case, not a fault — only exhausting every pool model is
        # fatal. Before 2026-10-11 the probe ALERTed on any ``N/M failed`` line,
        # so a recovered transient read the same as a dead classifier (#117).
        if (logs.get("llm_all_fail") or 0) > 0:
            bump(
                ALERT,
                f"all classification pool models failed ({logs['llm_all_fail']}x)",
            )
        elif (logs.get("llm_or_fail") or 0) >= THRESHOLDS["pool_rotation_warn_count"]:
            bump(
                WARN,
                f"classification pool rotation absorbed {logs['llm_or_fail']} failures",
            )
        # A disabled hop cannot fail, so the gateway leg applies only when the
        # hop is known to be enabled. ``None`` (unreadable) keeps the legacy
        # behaviour rather than silently dropping the check.
        if report.get("llm_topology", {}).get("gateway_enabled") is not False and (
            logs.get("llm_gw_fail") or 0
        ) >= THRESHOLDS["gateway_fallback_warn_count"]:
            bump(WARN, f"gateway fallback engaged {logs['llm_gw_fail']}x")
        action_failures = (
            (logs.get("act_perm") or 0)
            + (logs.get("act_tg") or 0)
            + (logs.get("act_unexp") or 0)
        )
        if action_failures > 0:
            bump(WARN, f"@telegram_action failures ({action_failures})")
        if (logs.get("bad_update") or 0) >= THRESHOLDS["malformed_updates_warn"]:
            bump(WARN, f"malformed updates received ({logs['bad_update']})")

    return verdict, reasons


def render_last_error_age(age_hours: float | None) -> str:
    """Render the webhook delivery-error age.

    ``None`` means there is no error at all, so it must read ``none`` — the old
    ``f"{None}h ago"`` rendered the literal ``Noneh ago`` (board #117).
    """
    return "none" if age_hours is None else f"{age_hours}h ago"

def render_llm_row(logs: dict, topology: dict) -> str:
    """One digest row describing the classification path.

    With the gateway hop disabled the pool IS the path, so the legacy
    ``gateway Nx`` cell would read as a healthy gateway that is not even in the
    request path (board #117).
    """
    pool = (
        f"{logs.get('llm_or_fail') or 0} rotation · "
        f"{logs.get('llm_all_fail') or 0} all-failed"
    )
    if topology.get("gateway_enabled") is False:
        return f"| LLM pool | {pool} (gateway hop disabled) |"
    return f"| LLM pool | gateway {logs.get('llm_gw_fail') or 0}x · {pool} |"

def render_markdown(report: dict, verdict: int, reasons: list[str]) -> str:
    badge = {OK: "🟢 OK", WARN: "🟡 WARN", ALERT: "🔴 ALERT"}[verdict]
    health = report["public_health"]
    webhook = report["webhook"]
    container = report["container"]
    digest = report["image_digest"]
    logs = report["log_window"]

    lines = [
        f"**ai-antispam health — {badge}**",
        "",
        f"_{report['generated_at']} · window {report['window_hours']}h_",
        "",
        "| Check | Value |",
        "|---|---|",
        f"| /health | {health.get('status')} in {health.get('seconds')}s |",
        (
            f"| container | {container.get('status')}/{container.get('health')} "
            f"· up {container.get('uptime_hours')}h · restarts {container.get('restart_count')} |"
        ),
        f"| image | {'digest matches :main' if digest.get('match') else 'DIGEST MISMATCH'} |",
        (
            f"| webhook | {webhook.get('pending_update_count')} pending · "
            f"last error {render_last_error_age(webhook.get('last_error_age_hours'))} |"
        ),
        f"| updates ({report['window_hours']}h) | {logs.get('ingress')} received |",
        (
            f"| @telegram_action | {logs.get('act_ok')} ok · "
            f"{logs.get('act_perm', 0) + logs.get('act_tg', 0) + logs.get('act_unexp', 0)} failed |"
        ),
        render_llm_row(logs, report.get("llm_topology", {})),
    ]
    orphans = report.get("orphaned_groups") or {}
    lines += ["", "**Orphaned groups — inventory (paused, no live human admin)**"]
    if orphans.get("error"):
        lines.append(f"- ⚠️ inventory UNREADABLE: {orphans['error']}")
    elif not orphans.get("groups"):
        lines.append("- none")
    else:
        lines += [
            f"- `{g['group_id']}` {g['title']} · paused since {g['since']} · "
            + (f"bot LEFT {g['bot_left']}" if g.get("bot_left") else "bot stays")
            for g in orphans["groups"]
        ]

    # #162: the only daily reader of a terminal row's stamp. A divergence means
    # the row says the bot is gone while Telegram says it is still in the chat —
    # and no job will ever revisit it, because both grace populations filter
    # `status = 'active'`. Printed every run so it cannot hide by being silent.
    terminal = report.get("terminal_rows") or {}
    if terminal:
        lines += ["", "**Terminal rows — reconciled against live membership (#162)**"]
        if terminal.get("error"):
            lines.append(f"- ⚠️ reconcile UNREADABLE: {terminal['error']}")
        elif not terminal.get("divergences"):
            lines.append(
                f"- no divergence ({terminal.get('examined', 0)} terminal row(s) checked: "
                f"{terminal.get('verified_absent', 0)} confirmed absent by Telegram, "
                f"{len(terminal.get('unverified', []))} unverifiable)"
            )
        else:
            lines += [
                f"- ⚠️ `{d['group_id']}` {d['title']} · stored `{d['stored_status']}` "
                f"stamped {d['bot_removed_at']} · LIVE `{d['live_membership']}`"
                + (" · moderation ON" if d["moderation_enabled"] else "")
                for d in terminal["divergences"]
            ]
        for u in terminal.get("unverified", []):
            lines.append(
                f"- ⚠️ `{u['group_id']}` {u['title']} · could not verify: {u['reason']}"
            )

    if reasons:
        lines += ["", "**Why not OK**"] + [f"- {r}" for r in reasons]
    return "\n".join(lines)


def main() -> int:
    global _TOKEN
    parser = argparse.ArgumentParser(description="ai-antispam bot-service health probe")
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    parser.add_argument(
        "--window-hours",
        type=int,
        default=24,
        help="docker log window in hours (default 24)",
    )
    args = parser.parse_args()

    _TOKEN = read_bot_token()

    report: dict = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "window_hours": args.window_hours,
        "public_health": check_public_health(),
        "webhook": check_webhook(_TOKEN)
        if _TOKEN
        else {"error": "bot token unavailable"},
        "container": check_container(),
        "image_digest": {},
        "log_window": check_log_window(args.window_hours),
        # Read the hop's state so the digest can be honest about which path
        # actually served (board #117).
        "llm_topology": {"gateway_enabled": read_gateway_enabled()},
        # #161 obligation 1: orphans leave the NOTIFICATION population but stay
        # in the INVENTORY one. Read every run, so the list cannot rot.
        "orphaned_groups": check_orphaned_groups(),
        # #162: a terminal row is outside every grace population, so this is the
        # only place its stamp is ever checked against live membership.
        "terminal_rows": check_terminal_row_membership(_TOKEN),
    }
    report["image_digest"] = check_image_digest(report["container"])

    verdict, reasons = evaluate(report)
    report["verdict"] = {OK: "OK", WARN: "WARN", ALERT: "ALERT"}[verdict]
    report["reasons"] = reasons
    report["exit_code"] = verdict

    if args.json:
        sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(render_markdown(report, verdict, reasons) + "\n")
        sys.stdout.write(f"\nverdict={report['verdict']} exit={verdict}\n")
    return verdict


if __name__ == "__main__":
    sys.exit(main())
