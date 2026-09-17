# Operational Process Self-Audit — 2026-09-17

> **Verdict:** `FAILED` · Process 3 (Internal Self-Audit)

---

## 1. Process Health & Delivery Telemetry

| Metric | Value | Reference / Derivation |
|---|---|---|
| **Total Ledger Events** | `2` | Continuous ledger sequence |
| **Closed Tasks** | `0` | Tasks reaching verified close |
| **First-Pass Yield** | `100.0%` | Accepted runs ÷ total runs |
| **Rework Entries** | `2` | Defect count recorded in rework.md |
| **Rework Rate** | `0.0%` | Rework entries ÷ closed tasks |
| **Avg Task Lead Time** | `0.0s` | Average duration from intake to close |
| **Total Inference Cost** | `$0.0000` | Tracked cost across ledger task telemetry |
| **Avg Cost / Closed Task** | `$0.0000` | Total cost ÷ closed tasks |
| **Total Tokens (In/Out)** | `0 / 0` | Cumulative prompt and completion tokens |
| **Cadence Status** | `HELD` | Last run: Noneh ago |

---

## 2. Mechanical Gate Verification

| Gate / Command | Outcome | Duration | Notes |
|---|---|---|---|
| `/usr/bin/python3 tools/ledger.py verify` | `PASS` | `0.41s` | ledger clean: 2 row(s), monotonic, all event types known, se |
| `/usr/bin/python3 tests/test_ontology.py` | `PASS` | `0.4s` | vocabulary clean: 13 canonical term(s), 2 banned term(s), 22 |
| `/usr/bin/python3 tests/test_rework.py` | `PASS` | `0.18s` | rework gate passed |
| `/usr/bin/python3 tests/test_single_writer.py` | `PASS` | `0.15s` | single-writer state clean: 7 declared surface(s) audited, lo |
| `/usr/bin/python3 tests/test_ledger_schema.py` | `PASS` | `0.21s` | ledger schema clean: 2 row(s) audited, all domain invariants |
| `/usr/bin/python3 tools/hygiene.py --audit` | `FAIL` | `1.32s` |   - untracked file: tools/ |

---

## 3. Calibration Spot-Check (Sampled Task)

*No closed tasks available to spot-check.*
