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
            "llm_all_fail": 0,
            "act_ok": 5,
            "act_perm": 0,
            "act_tg": 0,
            "act_unexp": 0,
            "last_ingress": "2026-09-12T09:00:00",
        },
        # Live topology: the gateway hop is disabled (config.yaml
        # llm.gateway_enabled: false), so the pool IS the classification path.
        "llm_topology": {"gateway_enabled": False},
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


def test_absorbed_pool_rotation_is_never_an_alert():
    """Close condition leg 5 (board #117), fixture half one.

    A single pool agent failing while the rotation recovers is the DESIGNED
    case. Before 2026-10-11 this read ALERT and paged the reader on a
    self-healed transient — the exact defect the issue records.
    """
    verdict, reasons = probe.evaluate(make_report(log_window={"llm_or_fail": 1}))
    assert verdict == probe.OK, reasons
    assert reasons == []

def test_pool_rotation_warns_only_in_quantity():
    verdict, reasons = probe.evaluate(
        make_report(
            log_window={"llm_or_fail": probe.THRESHOLDS["pool_rotation_warn_count"]}
        )
    )
    assert verdict == probe.WARN
    assert any("rotation absorbed" in r for r in reasons)

def test_all_pool_models_failed_alerts():
    """Close condition leg 5 (board #117), fixture half two.

    Exhausting every pool model is the terminal condition: classification is
    dead and the message goes unmoderated.
    """
    verdict, reasons = probe.evaluate(make_report(log_window={"llm_all_fail": 1}))
    assert verdict == probe.ALERT
    assert any("all classification pool models failed" in r for r in reasons)

def test_gateway_leg_skipped_when_hop_disabled():
    """Close condition leg 2 (board #117): a disabled hop cannot fail."""
    verdict, reasons = probe.evaluate(
        make_report(
            log_window={"llm_gw_fail": probe.THRESHOLDS["gateway_fallback_warn_count"]},
            llm_topology={"gateway_enabled": False},
        )
    )
    assert verdict == probe.OK, reasons
    assert not any("gateway fallback" in r for r in reasons)

def test_gateway_leg_active_when_hop_enabled():
    """The WARN survives a rollback that re-enables the hop."""
    verdict, reasons = probe.evaluate(
        make_report(
            log_window={"llm_gw_fail": probe.THRESHOLDS["gateway_fallback_warn_count"]},
            llm_topology={"gateway_enabled": True},
        )
    )
    assert verdict == probe.WARN
    assert any("gateway fallback" in r for r in reasons)

def test_gateway_leg_kept_when_hop_state_unreadable():
    """``None`` is unknown, not disabled — never silently drop the check."""
    verdict, reasons = probe.evaluate(
        make_report(
            log_window={"llm_gw_fail": probe.THRESHOLDS["gateway_fallback_warn_count"]},
            llm_topology={"gateway_enabled": None},
        )
    )
    assert verdict == probe.WARN
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
    # Every log-line anchor must carry the level group: the ingress line is
    # DEBUG-level, so a pattern missing it silently never matches. Assert the
    # relationship, not a magic count that every new counter would bump.
    anchors = probe.LOG_WINDOW_AWK.count(r"\] app\.")
    assert anchors > 0
    assert probe.LOG_WINDOW_AWK.count("(DEBUG|INFO|WARNING|ERROR)") == anchors


def test_json_output_is_parseable():
    """--json must round-trip; a digest the cron cannot parse is a silent failure."""
    payload = {"verdict": "OK", "exit_code": 0, "reasons": []}
    assert json.loads(json.dumps(payload)) == payload

# --- #161 obligation 1: orphans are inventory, never silence ---------------

def test_orphan_inventory_line_names_each_group():
    """One line per orphaned group, with its id, title and pause date."""
    report = make_report(
        generated_at="2026-11-21 15:00 UTC",
        window_hours=24,
        orphaned_groups={
            "groups": [
                {
                    "group_id": "-1003751652625",
                    "title": "Сервер Shadowraze",
                    "since": "2026-11-16",
                    "bot_left": "",
                }
            ]
        }
    )
    text = probe.render_markdown(report, probe.OK, [])
    assert "**Orphaned groups — inventory" in text
    assert "- `-1003751652625` Сервер Shadowraze · paused since 2026-11-16" in text
    # The bot never left, and the row says so rather than assuming it.
    assert "bot stays" in text
    assert "bot LEFT" not in text

def test_orphan_inventory_states_a_leave_rather_than_hiding_it():
    """A cleanup-stamped row reads `paused` too; the line must not claim a hold.

    `bot_removed_at` is the only field that separates "paused and still in the
    chat" from "paused because we left" — reading `status` alone cannot.
    """
    report = make_report(
        generated_at="2026-11-21 15:00 UTC",
        window_hours=24,
        orphaned_groups={
            "groups": [
                {
                    "group_id": "-1002496127723",
                    "title": "g",
                    "since": "2026-11-21",
                    "bot_left": "2026-11-22",
                }
            ]
        }
    )
    text = probe.render_markdown(report, probe.OK, [])
    assert "bot LEFT 2026-11-22" in text
    assert "bot stays" not in text

def test_empty_orphan_inventory_is_stated_not_omitted():
    """ "None" must be PRINTED — an absent section is indistinguishable from a
    section that failed to run, which is how a group would hide by being silent.
    """
    text = probe.render_markdown(
        make_report(
            generated_at="2026-11-21 15:00 UTC",
            window_hours=24,
            orphaned_groups={"groups": []},
        ),
        probe.OK,
        [],
    )
    assert "**Orphaned groups — inventory" in text
    assert "- none" in text

def test_unreadable_orphan_inventory_is_not_reported_as_none():
    """A read failure is UNREADABLE, never "none": the two are different answers."""
    report = make_report(
        generated_at="2026-11-21 15:00 UTC",
        window_hours=24,
        orphaned_groups={"error": "ssh: connect to host apps port 22", "groups": []}
    )
    text = probe.render_markdown(report, probe.OK, [])
    assert "inventory UNREADABLE" in text
    assert "- none" not in text

def test_orphan_query_matches_the_legacy_reason_too():
    """#150's name for the same class is still on rows already written.

    A rename cannot rewrite history, so a digest matching only the new string
    would silently drop the older orphans — the exact failure obligation 1
    forbids.
    """
    # Assert against the QUERY the runner actually sends, not the helper: a
    # guard on the helper alone passes even if the query stops using it.
    query = probe._orphan_query()
    assert probe.ORPHAN_PAUSE_REASON in query
    assert probe.LEGACY_ORPHAN_PAUSE_REASONS, "the legacy set must not be empty"
    for legacy in probe.LEGACY_ORPHAN_PAUSE_REASONS:
        assert legacy in query
    # A paused group the bot LEFT is still an orphan: status alone cannot tell
    # a hold from a leave, so the query must not filter on bot_removed_at.
    assert "bot_removed_at::date::text" in query
    assert "bot_removed_at IS NULL" not in query

# --- #162: terminal rows reconciled against live membership -----------------
#
# A row stamped `left`/`paused`/`retired` is outside BOTH grace populations
# (they filter `status = 'active'`), so no job ever revisits it. If the bot is
# still in that chat, nothing else in the system can notice. These tests pin
# both directions plus the third outcome that is neither: we could not ask.

def _fake_remote(rows: str, rc: int = 0, err: str = ""):
    def _remote(command, *args, **kwargs):
        return rc, rows, err

    return _remote

def _fake_tg(member: dict | None, member_error: str | None = None):
    """getMe answers with a bot id; getChatMember answers with `member`."""

    def _call(token, method, params):
        if method == "getMe":
            return {"id": 4242, "is_bot": True}, None
        assert method == "getChatMember"
        assert params["user_id"] == 4242
        return member, member_error

    return _call

TERMINAL_ROW = "-1002496127723|блатные чупани|left|2026-11-16 21:01:08+00|true\n"

def test_terminal_row_with_bot_still_present_is_a_divergence(monkeypatch):
    """The row says the bot left; Telegram says it is an administrator."""
    monkeypatch.setattr(probe, "remote", _fake_remote(TERMINAL_ROW))
    monkeypatch.setattr(
        probe, "_tg_call", _fake_tg({"status": "administrator", "user": {"id": 4242}})
    )

    result = probe.check_terminal_row_membership("token")

    assert result["examined"] == 1
    assert len(result["divergences"]) == 1
    d = result["divergences"][0]
    assert d["group_id"] == "-1002496127723"
    assert d["stored_status"] == "left"
    assert d["live_membership"] == "administrator"
    assert d["bot_removed_at"].startswith("2026-11-16 21:01:08")
    assert d["moderation_enabled"] is True

def test_terminal_row_with_bot_genuinely_absent_is_not_a_divergence(monkeypatch):
    """`chat not found` is Telegram confirming the stamp, not an error.

    This is the measured normal for -1002496127723. If this case were reported
    as a divergence the leg would cry wolf on every terminal row in the table.
    """
    monkeypatch.setattr(probe, "remote", _fake_remote(TERMINAL_ROW))
    monkeypatch.setattr(probe, "_tg_call", _fake_tg(None, "absent"))

    result = probe.check_terminal_row_membership("token")

    assert result["examined"] == 1
    assert result["divergences"] == []
    assert result["unverified"] == []
    # Confirmed-absent is its OWN count, not merely "not a divergence": the
    # digest prints it, and a leg that cannot say how many stamps it confirmed
    # cannot distinguish a verified inventory from an empty one.
    assert result["verified_absent"] == 1

def test_terminal_row_with_live_left_status_confirms_the_stamp(monkeypatch):
    """The OTHER shape of absent: Telegram returns a record whose status is `left`.

    Measured live at 2026-11-21: 20 terminal rows, 15 answering `chat not found`
    and 4 answering with a `left` record. Both confirm the stamp. Counting only
    the first shape leaves `examined` larger than the sum of its buckets, and a
    total that cannot be reconciled against its own breakdown is a broken
    instrument — the leg could then read clean while skipping rows.
    """
    monkeypatch.setattr(probe, "remote", _fake_remote(TERMINAL_ROW))
    monkeypatch.setattr(probe, "_tg_call", _fake_tg({"status": "left", "user": {"id": 4242}}))

    result = probe.check_terminal_row_membership("token")

    assert result["examined"] == 1
    assert result["divergences"] == []
    assert result["verified_absent"] == 1

def test_terminal_reconcile_buckets_account_for_every_row_examined(monkeypatch):
    """Checksum: examined == verified_absent + divergences + unverified.

    Four rows, one per outcome and one repeat, so no bucket can be satisfied by
    a coincidental count. If any row can be read and land in no bucket, this
    fails — which is what stops the leg reporting a clean bill over rows it
    silently dropped.
    """
    rows = (
        "-1|present|left|2026-11-16 21:01:08+00|true\n"
        "-2|gone|left|2026-11-16 21:01:08+00|true\n"
        "-3|leftrec|left|2026-11-16 21:01:08+00|true\n"
        "-4|err|paused|2026-11-16 21:01:08+00|false\n"
    )
    monkeypatch.setattr(probe, "remote", _fake_remote(rows))

    def _call(token, method, params):
        if method == "getMe":
            return {"id": 4242, "is_bot": True}, None
        return {
            "-1": ({"status": "administrator"}, None),
            "-2": (None, "absent"),
            "-3": ({"status": "left"}, None),
            "-4": (None, "HTTP 500"),
        }[params["chat_id"]]

    monkeypatch.setattr(probe, "_tg_call", _call)

    result = probe.check_terminal_row_membership("token")

    assert result["examined"] == 4
    assert len(result["divergences"]) == 1
    assert result["verified_absent"] == 2
    assert len(result["unverified"]) == 1
    assert result["examined"] == (
        result["verified_absent"] + len(result["divergences"]) + len(result["unverified"])
    )

def test_terminal_reconcile_read_failure_is_an_error_not_a_clean_bill(monkeypatch):
    """`could not look` must never render as `nothing wrong`."""
    monkeypatch.setattr(probe, "remote", _fake_remote("", rc=2, err="ssh: connect failed"))
    monkeypatch.setattr(probe, "_tg_call", _fake_tg({"status": "member"}))

    result = probe.check_terminal_row_membership("token")

    assert "connect failed" in result["error"]
    # The dangerous shape would be divergences == [] with no error key.
    assert result["examined"] == 0

def test_terminal_reconcile_unverifiable_row_is_reported_not_silently_passed(monkeypatch):
    """A 5xx/timeout is neither healthy nor divergent — it gets its own bucket."""
    monkeypatch.setattr(probe, "remote", _fake_remote(TERMINAL_ROW))
    monkeypatch.setattr(probe, "_tg_call", _fake_tg(None, "HTTP 500"))

    result = probe.check_terminal_row_membership("token")

    assert result["divergences"] == []
    assert len(result["unverified"]) == 1
    assert result["unverified"][0]["reason"] == "HTTP 500"

def test_restricted_counts_as_present_only_when_still_a_member(monkeypatch):
    """A restricted bot that is still in the chat is present; one that is not is gone."""
    monkeypatch.setattr(probe, "remote", _fake_remote(TERMINAL_ROW))
    monkeypatch.setattr(probe, "_tg_call", _fake_tg({"status": "restricted", "is_member": True}))
    assert len(probe.check_terminal_row_membership("token")["divergences"]) == 1

    monkeypatch.setattr(
        probe, "_tg_call", _fake_tg({"status": "restricted", "is_member": False})
    )
    assert probe.check_terminal_row_membership("token")["divergences"] == []

def test_terminal_reconcile_is_report_only_by_construction():
    """The leg must contain no write. #144 keeps `status` with the grace job."""
    import inspect

    source = inspect.getsource(probe.check_terminal_row_membership)
    assert "UPDATE" not in source.upper()
    assert "pause_group_preserving_data" not in source
    assert "cleanup_group_data" not in source
    # The SELECT is the only DB statement and it only reads.
    assert probe._terminal_rows_query().lstrip().upper().startswith("SELECT")
    assert "BOT_REMOVED_AT IS NOT NULL" in probe._terminal_rows_query().upper()

def test_rendered_digest_names_the_divergence_with_both_artifacts():
    """The digest must show stored status AND live status, or it is unusable."""
    report = make_report(
        generated_at="2026-11-21 15:00 UTC",
        window_hours=24,
        terminal_rows={
            "examined": 20,
            "divergences": [
                {
                    "group_id": "-1003751652625",
                    "title": "Сервер Shadowraze",
                    "stored_status": "paused",
                    "bot_removed_at": "2026-11-16 21:01:08+00",
                    "live_membership": "administrator",
                    "moderation_enabled": True,
                }
            ],
            "unverified": [],
        },
    )
    text = probe.render_markdown(report, probe.OK, [])
    assert "**Terminal rows — reconciled against live membership (#162)**" in text
    assert "`-1003751652625` Сервер Shadowraze" in text
    assert "stored `paused`" in text
    assert "LIVE `administrator`" in text
    assert "moderation ON" in text

def test_rendered_digest_says_so_when_reconcile_could_not_run():
    report = make_report(
        generated_at="2026-11-21 15:00 UTC",
        window_hours=24,
        terminal_rows={"error": "ssh: connect failed", "divergences": [], "examined": 0},
    )
    text = probe.render_markdown(report, probe.OK, [])
    assert "reconcile UNREADABLE" in text
    assert "no divergence" not in text
