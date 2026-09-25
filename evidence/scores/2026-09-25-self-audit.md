# Operational Process Self-Audit — 2026-09-25

> **Verdict:** `FAILED` · Process 3 (Internal Self-Audit)

---

## 1. Process Health & Delivery Telemetry

| Metric | Value | Reference / Derivation |
|---|---|---|
| **Total Ledger Events** | `32` | Continuous ledger sequence |
| **Closed Tasks** | `4` | Tasks reaching verified close |
| **First-Pass Yield** | `50.0%` | Accepted runs ÷ total runs |
| **Rework Entries** | `3` | Defect count recorded in rework.md |
| **Rework Rate** | `75.0%` | Rework entries ÷ closed tasks |
| **Avg Task Lead Time** | `121275.6s` | Average duration from intake to close |
| **Total Inference Cost** | `$0.0000` | Tracked cost across ledger task telemetry |
| **Avg Cost / Closed Task** | `$0.0000` | Total cost ÷ closed tasks |
| **Total Tokens (In/Out)** | `0 / 0` | Cumulative prompt and completion tokens |
| **Cadence Status** | `HELD` | Last run: 24.0h ago |

---

## 2. Mechanical Gate Verification

| Gate / Command | Outcome | Duration | Notes |
|---|---|---|---|
| `/usr/bin/python3 tools/ledger.py verify` | `PASS` | `0.19s` | ledger clean: 31 row(s), monotonic, all event types known, s |
| `/usr/bin/python3 tests/test_ontology.py` | `PASS` | `0.25s` | vocabulary clean: 13 canonical term(s), 2 banned term(s), 30 |
| `/usr/bin/python3 tests/test_rework.py` | `PASS` | `0.1s` | rework gate passed |
| `/usr/bin/python3 tests/test_single_writer.py` | `PASS` | `0.08s` | single-writer state clean: 7 declared surface(s) audited, lo |
| `/usr/bin/python3 tests/test_ledger_schema.py` | `FAIL` | `0.12s` |   line 30: unauthorized actor 'worker' for event 'intake' (a |
| `/usr/bin/python3 tools/hygiene.py --clean` | `PASS` | `0.21s` | hygiene cleanup complete: 5 scratch item(s) reaped |
| `/usr/bin/python3 tools/hygiene.py --audit` | `FAIL` | `0.26s` |   - modified tracked file: [ M] evidence/ledger.jsonl (untou |
| `/usr/bin/python3 tests/test_audit_yield.py` | `PASS` | `3.81s` | yield-artifact guard clean: one rounding site, row == scorec |

---

## 3. Calibration Spot-Check (Sampled Task)

- **Sampled Subject:** `campaign-tg-login-recovery`
- **Intake Recorded:** `YES`
- **Claim Lock Recorded:** `YES`
- **Close Verified:** `YES`
- **Sequence Integrity:** `VERIFIED`
