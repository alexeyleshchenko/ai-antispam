# Operational Process Self-Audit — 2026-09-22

> **Verdict:** `PASSED` · Process 3 (Internal Self-Audit)

---

## 1. Process Health & Delivery Telemetry

| Metric | Value | Reference / Derivation |
|---|---|---|
| **Total Ledger Events** | `10` | Continuous ledger sequence |
| **Closed Tasks** | `1` | Tasks reaching verified close |
| **First-Pass Yield** | `28.6%` | Accepted runs ÷ total runs |
| **Rework Entries** | `3` | Defect count recorded in rework.md |
| **Rework Rate** | `300.0%` | Rework entries ÷ closed tasks |
| **Avg Task Lead Time** | `216.0s` | Average duration from intake to close |
| **Total Inference Cost** | `$0.0000` | Tracked cost across ledger task telemetry |
| **Avg Cost / Closed Task** | `$0.0000` | Total cost ÷ closed tasks |
| **Total Tokens (In/Out)** | `0 / 0` | Cumulative prompt and completion tokens |
| **Cadence Status** | `HELD` | Last run: 27.1h ago |

---

## 2. Mechanical Gate Verification

| Gate / Command | Outcome | Duration | Notes |
|---|---|---|---|
| `/usr/bin/python3 tools/ledger.py verify` | `PASS` | `0.74s` | ledger clean: 10 row(s), monotonic, all event types known, s |
| `/usr/bin/python3 tests/test_ontology.py` | `PASS` | `0.9s` | vocabulary clean: 13 canonical term(s), 2 banned term(s), 26 |
| `/usr/bin/python3 tests/test_rework.py` | `PASS` | `0.41s` | rework gate passed |
| `/usr/bin/python3 tests/test_single_writer.py` | `PASS` | `0.19s` | single-writer state clean: 7 declared surface(s) audited, lo |
| `/usr/bin/python3 tests/test_ledger_schema.py` | `PASS` | `0.78s` | ledger schema clean: 10 row(s) audited, all domain invariant |
| `/usr/bin/python3 tools/hygiene.py --clean` | `PASS` | `0.99s` | hygiene cleanup complete: 13 scratch item(s) reaped |
| `/usr/bin/python3 tools/hygiene.py --audit` | `PASS` | `0.49s` | hygiene audit clean: 0 stale scratch items in /tmp/ai-antisp |

---

## 3. Calibration Spot-Check (Sampled Task)

- **Sampled Subject:** `#35`
- **Intake Recorded:** `YES`
- **Claim Lock Recorded:** `YES`
- **Close Verified:** `YES`
- **Sequence Integrity:** `VERIFIED`
