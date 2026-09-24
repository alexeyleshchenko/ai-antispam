# Operational Process Self-Audit — 2026-09-24

> **Verdict:** `PASSED` · Process 3 (Internal Self-Audit)

---

## 1. Process Health & Delivery Telemetry

| Metric | Value | Reference / Derivation |
|---|---|---|
| **Total Ledger Events** | `14` | Continuous ledger sequence |
| **Closed Tasks** | `1` | Tasks reaching verified close |
| **First-Pass Yield** | `54.5%` | Accepted runs ÷ total runs |
| **Rework Entries** | `3` | Defect count recorded in rework.md |
| **Rework Rate** | `300.0%` | Rework entries ÷ closed tasks |
| **Avg Task Lead Time** | `216.0s` | Average duration from intake to close |
| **Total Inference Cost** | `$0.0000` | Tracked cost across ledger task telemetry |
| **Avg Cost / Closed Task** | `$0.0000` | Total cost ÷ closed tasks |
| **Total Tokens (In/Out)** | `0 / 0` | Cumulative prompt and completion tokens |
| **Cadence Status** | `HELD` | Last run: 24.0h ago |

---

## 2. Mechanical Gate Verification

| Gate / Command | Outcome | Duration | Notes |
|---|---|---|---|
| `/usr/bin/python3 tools/ledger.py verify` | `PASS` | `0.21s` | ledger clean: 13 row(s), monotonic, all event types known, s |
| `/usr/bin/python3 tests/test_ontology.py` | `PASS` | `0.25s` | vocabulary clean: 13 canonical term(s), 2 banned term(s), 28 |
| `/usr/bin/python3 tests/test_rework.py` | `PASS` | `0.07s` | rework gate passed |
| `/usr/bin/python3 tests/test_single_writer.py` | `PASS` | `0.09s` | single-writer state clean: 7 declared surface(s) audited, lo |
| `/usr/bin/python3 tests/test_ledger_schema.py` | `PASS` | `0.16s` | ledger schema clean: 13 row(s) audited, all domain invariant |
| `/usr/bin/python3 tools/hygiene.py --clean` | `PASS` | `0.29s` | hygiene cleanup complete: 8 scratch item(s) reaped |
| `/usr/bin/python3 tools/hygiene.py --audit` | `PASS` | `0.27s` | hygiene audit clean: 0 stale scratch items in /tmp/ai-antisp |
| `/usr/bin/python3 tests/test_audit_yield.py` | `PASS` | `4.2s` | yield-artifact guard clean: one rounding site, row == scorec |

---

## 3. Calibration Spot-Check (Sampled Task)

- **Sampled Subject:** `#35`
- **Intake Recorded:** `YES`
- **Claim Lock Recorded:** `YES`
- **Close Verified:** `YES`
- **Sequence Integrity:** `VERIFIED`
