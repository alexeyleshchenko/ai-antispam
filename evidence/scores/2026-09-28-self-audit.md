# Operational Process Self-Audit — 2026-09-28

> **Verdict:** `PASSED` · Process 3 (Internal Self-Audit)

---

## 1. Process Health & Delivery Telemetry

| Metric | Value | Reference / Derivation |
|---|---|---|
| **Total Ledger Events** | `86` | Continuous ledger sequence |
| **Closed Tasks** | `11` | Tasks reaching verified close |
| **First-Pass Yield** | `52.9%` | Accepted runs ÷ total runs |
| **Rework Entries** | `3` | Defect count recorded in rework.md |
| **Rework Rate** | `27.3%` | Rework entries ÷ closed tasks |
| **Avg Task Lead Time** | `67839.6s` | Average duration from intake to close |
| **Total Inference Cost** | `$17.7928` | Tracked cost across ledger task telemetry |
| **Avg Cost / Closed Task** | `$1.6175` | Total cost ÷ closed tasks |
| **Total Tokens (In/Out)** | `1564464 / 65106940` | Cumulative prompt and completion tokens |
| **Cadence Status** | `HELD` | Last run: 3.4h ago |

---

## 2. Mechanical Gate Verification

| Gate / Command | Outcome | Duration | Notes |
|---|---|---|---|
| `/usr/bin/python3 tools/ledger.py verify` | `PASS` | `0.33s` | reconstructed claim: n=81 (subject #62) interval=no close ro |
| `/usr/bin/python3 tests/test_ontology.py` | `PASS` | `0.41s` | vocabulary clean: 13 canonical term(s), 2 banned term(s), 51 |
| `/usr/bin/python3 tests/test_rework.py` | `PASS` | `0.13s` | rework gate passed |
| `/usr/bin/python3 tests/test_single_writer.py` | `PASS` | `0.1s` | single-writer state clean: 7 declared surface(s) audited, lo |
| `/usr/bin/python3 tests/test_ledger_schema.py` | `PASS` | `0.32s` | ledger schema clean, 4 excused: 86 row(s) audited, all domai |
| `/usr/bin/python3 tools/hygiene.py --clean --namespace ai-antispam` | `PASS` | `0.41s` | hygiene cleanup complete: 1 scratch item(s) reaped |
| `/usr/bin/python3 tools/hygiene.py --audit --namespace ai-antispam` | `PASS` | `0.45s` | hygiene audit clean: 0 stale scratch items in /tmp/ai-antisp |
| `/usr/bin/python3 tools/roadmap.py --audit` | `PASS` | `0.36s` | Product & cadence roadmap clean: 3 canonical products health |
| `/usr/bin/python3 tests/test_audit_yield.py` | `PASS` | `6.01s` | yield-artifact guard clean: one rounding site, row == scorec |
| `/usr/bin/python3 tests/test_audit_gate_note.py` | `PASS` | `0.15s` | gate-note guard clean: a FAIL note carries its population, P |
| `/usr/bin/python3 tests/test_close_board_recorded.py` | `PASS` | `0.2s` | close-board gate: excused — 9 row(s) (2 admitted, 7 pre-inva |
| `/usr/bin/python3 tests/test_kit_pin.py` | `PASS` | `0.19s` | kit pin passed |
| `/usr/bin/python3 tests/test_cron_thinness.py` | `PASS` | `0.1s` |  |
| `/usr/bin/python3 tests/test_patrol_host_state.py` | `PASS` | `4.7s` | patrol-runner gate passed: 102 check(s) |
| `/usr/bin/python3 tests/test_ledger.py` | `PASS` | `31.05s` | ledger gate passed |

---

## 3. Calibration Spot-Check (Sampled Task)

- **Sampled Subject:** `#59`
- **Intake Recorded:** `YES`
- **Claim Lock Recorded:** `YES`
- **Close Verified:** `YES`
- **Sequence Integrity:** `VERIFIED`
