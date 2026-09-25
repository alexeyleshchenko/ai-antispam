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
7. LLM gateway -> OpenRouter fallback counts

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
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime

OK, WARN, ALERT = 0, 1, 2

SSH_TARGET = "apps"
CONTAINER = "ai-antispam"
IMAGE = "ghcr.io/alexeyleshchenko/ai-antispam:main"
REGISTRY_REPO = "alexeyleshchenko/ai-antispam"
PUBLIC_HEALTH_URL = "https://ai-antispam.l1979.ru/health"
EXPECTED_WEBHOOK_URL = "https://ai-antispam.l1979.ru/process-tg-updates"

THRESHOLDS = {
    "health_max_seconds": 2.0,
    "pending_updates_alert": 100,
    "webhook_error_warn_hours": 2.0,
    "malformed_updates_warn": 5,
    "gateway_fallback_warn_count": 5,
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
    r"/\[(DEBUG|INFO|WARNING|ERROR)\] app\.handlers\.status_handlers: Received invalid update format/ {bad_update++}"
    r'END {printf "ingress=%d act_ok=%d act_perm=%d act_tg=%d act_unexp=%d '
    r'llm_gw_fail=%d llm_or_fail=%d bad_update=%d last_ingress=%s\n", ingress+0, '
    r"act_ok+0, act_perm+0, act_tg+0, act_unexp+0, llm_gw_fail+0, llm_or_fail+0, "
    r"bad_update+0, last_ingress}"
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
    """Run a shell snippet on the apps host."""
    return sh(
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
        except ValueError, IndexError:
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
        if (logs.get("llm_or_fail") or 0) > 0:
            bump(ALERT, f"OpenRouter fallback also failing ({logs['llm_or_fail']})")
        elif (logs.get("llm_gw_fail") or 0) >= THRESHOLDS[
            "gateway_fallback_warn_count"
        ]:
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
            f"last error {webhook.get('last_error_age_hours')}h ago |"
        ),
        f"| updates ({report['window_hours']}h) | {logs.get('ingress')} received |",
        (
            f"| @telegram_action | {logs.get('act_ok')} ok · "
            f"{logs.get('act_perm', 0) + logs.get('act_tg', 0) + logs.get('act_unexp', 0)} failed |"
        ),
        (
            f"| LLM fallback | gateway {logs.get('llm_gw_fail')}x · "
            f"OpenRouter {logs.get('llm_or_fail')}x |"
        ),
    ]
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
