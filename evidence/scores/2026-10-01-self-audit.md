# Operational Process Self-Audit — 2026-10-01

> **Verdict:** `PASSED` · Process 3 (Internal Self-Audit)

---

## 1. Process Health & Delivery Telemetry

| Metric | Value | Reference / Derivation |
|---|---|---|
| **Total Ledger Events** | `148` | Continuous ledger sequence |
| **Closed Tasks** | `23` | Tasks reaching verified close |
| **First-Pass Yield** | `74.3%` | Accepted runs ÷ total runs |
| **Rework Entries** | `3` | Defect count recorded in rework.md |
| **Rework Rate** | `13.0%` | Rework entries ÷ closed tasks |
| **Avg Task Lead Time** | `78725.4s` | Average duration from intake to close |
| **Total Inference Cost** | `$2087.9373` | Tracked cost across ledger task telemetry |
| **Avg Cost / Closed Task** | `$90.7799` | Total cost ÷ closed tasks |
| **Total Tokens (In/Out)** | `305458667 / 8173186901` | Cumulative prompt and completion tokens |
| **Cadence Status** | `HELD` | Last run: 5.4h ago |

---

## 2. Mechanical Gate Verification

| Gate / Command | Outcome | Duration | Notes |
|---|---|---|---|
| `/usr/bin/python3 tools/ledger.py verify` | `PASS` | `0.3s` | open claim: n=148 #74 claimed by hq — no close after it and  |
| `/usr/bin/python3 tests/test_ontology.py` | `PASS` | `0.31s` | vocabulary clean: 13 canonical term(s), 2 banned term(s), 53 |
| `/usr/bin/python3 tests/test_rework.py` | `PASS` | `0.12s` | rework gate passed |
| `/usr/bin/python3 tests/test_single_writer.py` | `PASS` | `0.13s` | single-writer state clean: 7 declared surface(s) audited, lo |
| `/usr/bin/python3 tests/test_ledger_schema.py` | `PASS` | `0.31s` | ledger schema clean, 4 excused: 148 row(s) audited, all doma |
| `/usr/bin/python3 tools/hygiene.py --clean --namespace ai-antispam` | `PASS` | `0.53s` | hygiene cleanup complete: 0 scratch item(s) reaped |
| `/usr/bin/python3 tools/hygiene.py --audit --namespace ai-antispam` | `PASS` | `0.4s` | hygiene audit clean: 0 stale scratch items in /tmp/ai-antisp |
| `/usr/bin/python3 tools/roadmap.py --audit` | `PASS` | `0.2s` | Product & cadence roadmap clean: 3 canonical products health |
| `/usr/bin/python3 tests/test_audit_yield.py` | `PASS` | `6.03s` | yield-artifact guard clean: one rounding site, row == scorec |
| `/usr/bin/python3 tests/test_audit_gate_note.py` | `PASS` | `0.18s` | gate-note guard clean: a FAIL note carries its population, P |
| `/usr/bin/python3 tests/test_close_board_recorded.py` | `PASS` | `0.12s` | close-board gate: excused — 9 row(s) (2 admitted, 7 pre-inva |
| `/usr/bin/python3 tests/test_kit_pin.py` | `PASS` | `0.33s` | kit pin passed |
| `/usr/bin/python3 -m pytest tests/test_cron_thinness.py -q` | `PASS` | `17.05s` | 16 passed in 0.21s |
| `/usr/bin/python3 tests/test_patrol_host_state.py` | `PASS` | `4.5s` | patrol-runner gate passed: 109 check(s) |
| `/usr/bin/python3 tests/test_ledger.py` | `PASS` | `88.15s` | ledger gate passed |
| `/usr/bin/python3 tests/test_audit_stamp_duration.py` | `PASS` | `8.67s` | audit-stamp duration guard clean: the stamp site carries no  |
| `/usr/bin/python3 tests/test_ledger_commit_cites_no_rows.py` | `PASS` | `0.69s` | ledger clause: excused — 3 exempted violation(s) in cd2a3f87 |

---

## 3. Calibration Spot-Check (Sampled Task)

- **Sampled Subject:** `#71`
- **Intake Recorded:** `YES`
- **Claim Lock Recorded:** `YES`
- **Close Verified:** `YES`
- **Sequence Integrity:** `VERIFIED`
