# Operational Process Self-Audit — 2026-10-04

> **Verdict:** `DEGRADED` · Process 3 (Internal Self-Audit)

---

## 1. Process Health & Delivery Telemetry

| Metric | Value | Reference / Derivation |
|---|---|---|
| **Total Ledger Events** | `156` | Continuous ledger sequence |
| **Closed Tasks** | `25` | Tasks reaching verified close |
| **First-Pass Yield** | `76.9%` | Accepted runs ÷ total runs |
| **Rework Entries** | `3` | Defect count recorded in rework.md |
| **Rework Rate** | `12.0%` | Rework entries ÷ closed tasks |
| **Avg Task Lead Time** | `97268.0s` | Average duration from intake to close |
| **Total Inference Cost** | `$2087.9373` | Tracked cost across ledger task telemetry |
| **Avg Cost / Closed Task** | `$83.5175` | Total cost ÷ closed tasks |
| **Total Tokens (In/Out)** | `305458667 / 8173186901` | Cumulative prompt and completion tokens |
| **Cadence Status** | `MISSED` | Last run: 39.9h ago |

---

## 2. Mechanical Gate Verification

| Gate / Command | Outcome | Duration | Notes |
|---|---|---|---|
| `/usr/bin/python3 tools/ledger.py verify` | `PASS` | `0.38s` | open claim: n=148 #74 claimed by hq — no close after it and no release naming it, so the ledger cannot tell in-flight work from withdrawn wo |
| `/usr/bin/python3 tests/test_ontology.py` | `PASS` | `0.4s` | vocabulary clean: 13 canonical term(s), 2 banned term(s), 58 file(s) scanned |
| `/usr/bin/python3 tests/test_rework.py` | `PASS` | `0.13s` | rework gate passed |
| `/usr/bin/python3 tests/test_single_writer.py` | `PASS` | `0.09s` | single-writer state clean: 7 declared surface(s) audited, locking verified |
| `/usr/bin/python3 tests/test_ledger_schema.py` | `PASS` | `0.33s` | ledger schema clean, 4 excused: 156 row(s) audited, all domain invariants & schemas verified |
| `/usr/bin/python3 tools/hygiene.py --clean --namespace ai-antispam` | `PASS` | `0.44s` | hygiene cleanup complete: 0 scratch item(s) reaped |
| `/usr/bin/python3 tools/hygiene.py --audit --namespace ai-antispam` | `PASS` | `0.42s` | hygiene audit clean: 0 stale scratch items in /tmp/ai-antispam-*, 0 stranded dirty paths (grace 60m), 0 advisory item(s), working tree clean |
| `/usr/bin/python3 tools/roadmap.py --audit` | `PASS` | `0.27s` | Product & cadence roadmap clean: 3 canonical products healthy and cadenced |
| `/usr/bin/python3 tests/test_audit_yield.py` | `PASS` | `6.95s` | yield-artifact guard clean: one rounding site, row == scorecard at 14/17 |
| `/usr/bin/python3 tests/test_audit_gate_note.py` | `PASS` | `0.22s` | gate-note guard clean: a FAIL note carries its population, PASS keeps its summary |
| `/usr/bin/python3 tests/test_close_board_recorded.py` | `PASS` | `0.19s` | close-board gate: excused — 9 row(s) (2 admitted, 7 pre-invariant); this is a visible debt, not a clean run |
| `/usr/bin/python3 tests/test_kit_pin.py` | `PASS` | `0.32s` | kit pin passed |
| `/usr/bin/python3 -m pytest tests/test_cron_thinness.py -q` | `PASS` | `16.19s` | 16 passed in 0.15s |
| `/usr/bin/python3 tests/test_patrol_host_state.py` | `PASS` | `5.55s` | patrol-runner gate passed: 109 check(s) |
| `/usr/bin/python3 tests/test_ledger.py` | `PASS` | `102.95s` | ledger gate passed |
| `/usr/bin/python3 tests/test_audit_stamp_duration.py` | `PASS` | `9.03s` | audit-stamp duration guard clean: the stamp site carries no literal, and the row tracks the gate work it describes |
| `/usr/bin/python3 tests/test_ledger_commit_cites_no_rows.py` | `PASS` | `0.35s` | ledger clause: excused — 3 exempted violation(s) in cd2a3f874a39aacd0073b88372c15e23a4cbc3cd..HEAD; this is a visible debt, not a clean run |

---

## 3. Calibration Spot-Check (Sampled Task)

- **Sampled Subject:** `#72`
- **Intake Recorded:** `YES`
- **Claim Lock Recorded:** `YES`
- **Close Verified:** `YES`
- **Sequence Integrity:** `VERIFIED`
