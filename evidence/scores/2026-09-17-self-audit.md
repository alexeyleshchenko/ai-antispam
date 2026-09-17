# Operational Process Self-Audit — 2026-09-17

> **Verdict:** `PASSED` · Process 3 (Internal Self-Audit)

---

## 1. Process Health & Delivery Telemetry

| Metric | Value | Reference / Derivation |
|---|---|---|
| **Total Ledger Events** | `6` | Continuous ledger sequence |
| **Closed Tasks** | `1` | Tasks reaching verified close |
| **First-Pass Yield** | `33.3%` | Accepted runs ÷ total runs |
| **Rework Entries** | `2` | Defect count recorded in rework.md |
| **Rework Rate** | `200.0%` | Rework entries ÷ closed tasks |
| **Avg Task Lead Time** | `216.0s` | Average duration from intake to close |
| **Total Inference Cost** | `$0.0000` | Tracked cost across ledger task telemetry |
| **Avg Cost / Closed Task** | `$0.0000` | Total cost ÷ closed tasks |
| **Total Tokens (In/Out)** | `0 / 0` | Cumulative prompt and completion tokens |
| **Cadence Status** | `HELD` | Last run: 0.0h ago |

---

## 2. Mechanical Gate Verification

| Gate / Command | Outcome | Duration | Notes |
|---|---|---|---|
| `/usr/bin/python3 tools/ledger.py verify` | `PASS` | `0.18s` | ledger clean: 6 row(s), monotonic, all event types known, se |
| `/usr/bin/python3 tests/test_ontology.py` | `PASS` | `0.18s` | vocabulary clean: 13 canonical term(s), 2 banned term(s), 23 |
| `/usr/bin/python3 tests/test_rework.py` | `PASS` | `0.09s` | rework gate passed |
| `/usr/bin/python3 tests/test_single_writer.py` | `PASS` | `0.11s` | single-writer state clean: 7 declared surface(s) audited, lo |
| `/usr/bin/python3 tests/test_ledger_schema.py` | `PASS` | `0.21s` | ledger schema clean: 6 row(s) audited, all domain invariants |
| `/usr/bin/python3 tools/hygiene.py --audit` | `PASS` | `0.51s` | hygiene audit clean: 0 stale scratch items, 0 untracked clut |

---

## 3. Calibration Spot-Check (Sampled Task)

- **Sampled Subject:** `#35`
- **Intake Recorded:** `YES`
- **Claim Lock Recorded:** `YES`
- **Close Verified:** `YES`
- **Sequence Integrity:** `VERIFIED`
