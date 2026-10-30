# Operational Process Self-Audit — 2026-10-30

> **Verdict:** `DEGRADED` · Process 3 (Internal Self-Audit)

---

## 1. Process Health & Delivery Telemetry

| Metric | Value | Reference / Derivation |
|---|---|---|
| **Total Ledger Events** | `275` | Continuous ledger sequence |
| **Closed Tasks** | `45` | Tasks reaching verified close |
| **First-Pass Yield** | `85.7%` | Accepted runs ÷ total runs (floor 5) |
| **Rework Entries** | `4` | Defect count recorded in rework.md |
| **Rework Rate** | `8.9%` | Rework entries ÷ closed tasks (floor 5) |
| **Avg Task Lead Time** | `151823.4s` | Average duration from intake to close |
| **Total Inference Cost** | `$6838.3271` | Tracked cost across ledger task telemetry |
| **Avg Cost / Closed Task** | `$151.9628` | Total cost ÷ closed tasks |
| **Total Tokens (In/Out)** | `711720520 / 20701794676` | Cumulative prompt and completion tokens |
| **Cadence Status** | `HOLE 2026-10-25..2026-10-26` | Last run: 24.0h ago · record read: evidence/ledger.jsonl run/score rows with subject internal-self-audit · latest artifact: 2026-10-29 |

---

## 2. Mechanical Gate Verification

| Gate / Command | Outcome | Duration | Notes |
|---|---|---|---|
| `/usr/bin/python3 tools/ledger.py verify` | `PASS` | `0.26s` | open claim: n=268 #123 claimed by hq — no close after it and no release naming it, so the ledger cannot tell in-flight work from withdrawn w |
| `/usr/bin/python3 tests/test_ontology.py` | `PASS` | `0.26s` | vocabulary clean: 13 canonical term(s), 2 banned term(s), 88 file(s) scanned |
| `/usr/bin/python3 tests/test_rework.py` | `PASS` | `0.07s` | rework gate passed |
| `/usr/bin/python3 tests/test_single_writer.py` | `PASS` | `0.08s` | single-writer state clean: 7 declared surface(s) audited, locking verified |
| `/usr/bin/python3 tests/test_ledger_schema.py` | `PASS` | `0.23s` | ledger schema clean, 4 excused: 275 row(s) audited, all domain invariants & schemas verified |
| `/usr/bin/python3 tools/hygiene.py --clean --namespace ai-antispam` | `PASS` | `0.25s` | hygiene cleanup complete: 0 scratch item(s) reaped |
| `/usr/bin/python3 tools/hygiene.py --audit --namespace ai-antispam` | `PASS` | `0.19s` | hygiene audit clean: 0 stale scratch items in /tmp/ai-antispam-*, 0 stranded dirty paths (grace 60m), 0 advisory item(s), working tree clean |
| `/usr/bin/python3 tools/roadmap.py --audit` | `PASS` | `0.15s` | Product & cadence roadmap clean: 3 canonical products healthy and cadenced |
| `/usr/bin/python3 tests/test_audit_yield.py` | `PASS` | `3.73s` | yield-artifact guard clean: one rounding site, row == scorecard at 14/17 |
| `/usr/bin/python3 tests/test_audit_gate_note.py` | `PASS` | `0.11s` | gate-note guard clean: a FAIL note carries its population, PASS keeps its summary |
| `/usr/bin/python3 tests/test_close_board_recorded.py` | `PASS` | `0.08s` | close-board gate: excused — 9 row(s) (2 admitted, 7 pre-invariant); this is a visible debt, not a clean run |
| `/usr/bin/python3 tests/test_kit_pin.py` | `PASS` | `0.19s` | kit pin passed |
| `/usr/bin/python3 -m pytest tests/test_cron_thinness.py -q` | `PASS` | `11.92s` | 16 passed in 0.14s |
| `/usr/bin/python3 tests/test_patrol_host_state.py` | `PASS` | `2.97s` | patrol-runner gate passed: 109 check(s) |
| `/usr/bin/python3 tests/test_ledger.py` | `PASS` | `25.1s` | ledger gate passed |
| `/usr/bin/python3 tests/test_audit_stamp_duration.py` | `PASS` | `7.68s` | audit-stamp duration guard clean: the stamp site carries no literal, and the row tracks the gate work it describes |
| `/usr/bin/python3 tests/test_audit_cadence_record.py` | `PASS` | `0.14s` | PASS: 4 fixture branches, 3 distinct labels, report row carries the state and the record |
| `/usr/bin/python3 tests/test_audit_cadence_subject.py` | `PASS` | `0.14s` | PASS: hole named with its days, unrelated row rejected by the clock, predicate bound |
| `/usr/bin/python3 tests/test_ledger_commit_cites_no_rows.py` | `PASS` | `0.44s` | ledger clause: excused — 3 exempted violation(s) in cd2a3f874a39aacd0073b88372c15e23a4cbc3cd..HEAD; this is a visible debt, not a clean run |

---

## 3. Calibration Spot-Check (Sampled Task)

- **Sampled Subject:** `#124`
- **Intake Recorded:** `YES`
- **Claim Lock Recorded:** `YES`
- **Close Verified:** `YES`
- **Sequence Integrity:** `VERIFIED`
