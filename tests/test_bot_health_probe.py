"""Unit tests for the bot-service health probe.

These exercise the pure decision logic only — no SSH, no network, no Docker.
The probe's whole job is to be trustworthy, so its verdict rules are pinned
here: a threshold that silently drifts would make the digest lie.

Run: pytest tests/test_bot_health_probe.py -v
"""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

PROBE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "bot_health_probe.py"


def _load_probe():
    spec = importlib.util.spec_from_file_location("bot_health_probe", PROBE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


probe = _load_probe()


def make_report(**overrides) -> dict:
    """A fully healthy report; override one field at a time per test."""
    report = {
        "public_health": {"status": 200, "seconds": 0.2, "error": None},
        "webhook": {
            "url_matches_expected": True,
            "pending_update_count": 0,
            "last_error_age_hours": None,
            "last_error_date": None,
            "last_error_message": None,
        },
        "container": {"status": "running", "health": "healthy", "restart_count": 0},
        "image_digest": {
            "local_digest": "sha256:abc",
            "published_digest": "sha256:abc",
            "match": True,
        },
        "log_window": {
            "ingress": 10,
            "bad_update": 0,
            "llm_gw_fail": 0,
            "llm_or_fail": 0,
            "act_ok": 5,
            "act_perm": 0,
            "act_tg": 0,
            "act_unexp": 0,
            "last_ingress": "2026-09-12T09:00:00",
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(report.get(key), dict):
            report[key] = {**report[key], **value}
        else:
            report[key] = value
    return report


def test_healthy_report_is_ok():
    verdict, reasons = probe.evaluate(make_report())
    assert verdict == probe.OK
    assert reasons == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", 503),
        ("status", 0),
    ],
)
def test_unhealthy_public_endpoint_alerts(field, value):
    verdict, reasons = probe.evaluate(
        make_report(public_health={field: value, "error": "boom"})
    )
    assert verdict == probe.ALERT
    assert any("public /health" in r for r in reasons)


def test_slow_public_endpoint_warns():
    verdict, _ = probe.evaluate(make_report(public_health={"seconds": 5.0}))
    assert verdict == probe.WARN


def test_webhook_url_drift_alerts():
    verdict, reasons = probe.evaluate(
        make_report(webhook={"url_matches_expected": False})
    )
    assert verdict == probe.ALERT
    assert any("webhook URL" in r for r in reasons)


def test_pending_update_backlog_alerts():
    verdict, _ = probe.evaluate(
        make_report(
            webhook={"pending_update_count": probe.THRESHOLDS["pending_updates_alert"]}
        )
    )
    assert verdict == probe.ALERT


def test_stale_webhook_error_after_successful_ingress_is_not_escalated():
    """Telegram's last_error_date is sticky — a later success means recovered.

    Receipt for the stickiness (2026-09-12): last_error_date 2026-09-11T14:52:40Z
    with last_ingress 2026-09-12T08:44:15Z — 71 updates delivered *after* the
    recorded error, which it still reported.
    """
    error_at = int(datetime(2026, 9, 11, 14, 52, 40, tzinfo=UTC).timestamp())
    verdict, reasons = probe.evaluate(
        make_report(
            webhook={
                "last_error_age_hours": 18.0,
                "last_error_date": error_at,
                "last_error_message": "Wrong response from the webhook: 503",
            },
            log_window={"last_ingress": "2026-09-12T08:44:15"},
        )
    )
    assert verdict == probe.OK, reasons


def test_fresh_webhook_error_without_ingress_alerts():
    error_at = int(datetime(2026, 9, 12, 9, 0, 0, tzinfo=UTC).timestamp())
    verdict, reasons = probe.evaluate(
        make_report(
            webhook={
                "last_error_age_hours": 0.5,
                "last_error_date": error_at,
                "last_error_message": "Wrong response from the webhook: 503",
            },
            log_window={"ingress": 0, "last_ingress": None},
        )
    )
    assert verdict == probe.ALERT
    assert any("nothing delivered since" in r for r in reasons)


def test_container_not_healthy_alerts():
    verdict, _ = probe.evaluate(make_report(container={"health": "unhealthy"}))
    assert verdict == probe.ALERT


def test_restart_count_warns():
    verdict, reasons = probe.evaluate(make_report(container={"restart_count": 3}))
    assert verdict == probe.WARN
    assert any("restart count" in r for r in reasons)


def test_image_digest_mismatch_warns():
    verdict, reasons = probe.evaluate(
        make_report(image_digest={"published_digest": "sha256:other", "match": False})
    )
    assert verdict == probe.WARN
    assert any("digest" in r for r in reasons)


def test_missing_registry_leaves_reason_without_escalating():
    verdict, reasons = probe.evaluate(
        make_report(
            image_digest={
                "local_digest": "sha256:abc",
                "published_digest": None,
                "registry_error": "URLError",
            }
        )
    )
    assert verdict == probe.OK
    assert any("digest check skipped" in r for r in reasons)


def test_no_ingress_in_window_warns():
    verdict, reasons = probe.evaluate(make_report(log_window={"ingress": 0}))
    assert verdict == probe.WARN
    assert any("no webhook updates" in r for r in reasons)


def test_openrouter_failure_alerts_but_gateway_fallback_only_warns():
    alert, _ = probe.evaluate(make_report(log_window={"llm_or_fail": 1}))
    assert alert == probe.ALERT

    warn, reasons = probe.evaluate(
        make_report(
            log_window={"llm_gw_fail": probe.THRESHOLDS["gateway_fallback_warn_count"]}
        )
    )
    assert warn == probe.WARN
    assert any("gateway fallback" in r for r in reasons)


def test_telegram_action_failure_warns():
    verdict, reasons = probe.evaluate(make_report(log_window={"act_unexp": 2}))
    assert verdict == probe.WARN
    assert any("@telegram_action" in r for r in reasons)


@pytest.mark.parametrize(("count", "expected"), [(4, probe.OK), (5, probe.WARN)])
def test_malformed_update_threshold_boundary(count, expected):
    verdict, _ = probe.evaluate(make_report(log_window={"bad_update": count}))
    assert verdict == expected


def test_unreachable_log_window_warns_rather_than_crashing():
    verdict, reasons = probe.evaluate(
        make_report(log_window={"error": "ssh: Could not resolve hostname"})
    )
    assert verdict == probe.WARN
    assert any("log window unavailable" in r for r in reasons)


def test_alerts_outrank_warnings():
    verdict, _ = probe.evaluate(
        make_report(
            public_health={"status": 503, "error": "down"},
            container={"restart_count": 1},
        )
    )
    assert verdict == probe.ALERT


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-09-12T08:44:15", datetime(2026, 9, 12, 8, 44, 15, tzinfo=UTC)),
        (None, None),
        ("", None),
        ("not-a-timestamp", None),
    ],
)
def test_parse_last_ingress(raw, expected):
    assert probe.parse_last_ingress(raw) == expected


def test_redact_removes_token():
    probe._TOKEN = "123456:ABCDEF-secret"
    try:
        assert probe.redact(
            "url=https://api.telegram.org/bot123456:ABCDEF-secret/x"
        ) == ("url=https://api.telegram.org/bot<BOT_TOKEN>/x")
        assert probe.redact("no token here") == "no token here"
        assert probe.redact("") == ""
    finally:
        probe._TOKEN = None


def test_rendered_markdown_lists_reasons_and_verdict():
    report = make_report(container={"restart_count": 1})
    verdict, reasons = probe.evaluate(report)
    report["generated_at"] = "2026-09-12 09:00 UTC"
    report["window_hours"] = 24
    text = probe.render_markdown(report, verdict, reasons)
    assert "🟡 WARN" in text
    assert "restart count" in text
    assert text.index("| Check | Value |") > 0


def test_rendered_markdown_blank_line_before_table():
    """Telegram renders a table abutting text as raw pipes — keep the blank line."""
    report = make_report()
    report["generated_at"] = "2026-09-12 09:00 UTC"
    report["window_hours"] = 24
    text = probe.render_markdown(report, probe.OK, [])
    lines = text.split("\n")
    header_index = lines.index("| Check | Value |")
    assert lines[header_index - 1] == ""
    assert lines[header_index + 1].startswith("|---")


def test_log_window_awk_parses_last_ingress_field():
    """The awk END block must emit a word-splittable last_ingress field."""
    assert "last_ingress=%s" in probe.LOG_WINDOW_AWK
    assert 'gsub(/ /,"T",ts)' in probe.LOG_WINDOW_AWK
    # DEBUG must be in every level anchor: the ingress line is DEBUG-level.
    assert probe.LOG_WINDOW_AWK.count("(DEBUG|INFO|WARNING|ERROR)") == 8


def test_json_output_is_parseable():
    """--json must round-trip; a digest the cron cannot parse is a silent failure."""
    payload = {"verdict": "OK", "exit_code": 0, "reasons": []}
    assert json.loads(json.dumps(payload)) == payload
