# Operational Process Self-Audit — 2026-10-06

> **Verdict:** `DEGRADED` · Process 3 (Internal Self-Audit)

---

## 1. Process Health & Delivery Telemetry

| Metric | Value | Reference / Derivation |
|---|---|---|
| **Total Ledger Events** | `181` | Continuous ledger sequence |
| **Closed Tasks** | `30` | Tasks reaching verified close |
| **First-Pass Yield** | `78.7%` | Accepted runs ÷ total runs (floor 5) |
| **Rework Entries** | `4` | Defect count recorded in rework.md |
| **Rework Rate** | `13.3%` | Rework entries ÷ closed tasks (floor 5) |
| **Avg Task Lead Time** | `82543.3s` | Average duration from intake to close |
| **Total Inference Cost** | `$2087.9373` | Tracked cost across ledger task telemetry |
| **Avg Cost / Closed Task** | `$69.5979` | Total cost ÷ closed tasks |
| **Total Tokens (In/Out)** | `305458667 / 8173186901` | Cumulative prompt and completion tokens |
| **Cadence Status** | `HELD` | Last run: 15.1h ago |

---

## 2. Mechanical Gate Verification

| Gate / Command | Outcome | Duration | Notes |
|---|---|---|---|
| `/usr/bin/python3 tools/ledger.py verify` | `PASS` | `0.41s` | open claim: n=148 #74 claimed by hq — no close after it and no release naming it, so the ledger cannot tell in-flight work from withdrawn wo |
| `/usr/bin/python3 tests/test_ontology.py` | `PASS` | `0.49s` | vocabulary clean: 13 canonical term(s), 2 banned term(s), 64 file(s) scanned |
| `/usr/bin/python3 tests/test_rework.py` | `PASS` | `0.1s` | rework gate passed |
| `/usr/bin/python3 tests/test_single_writer.py` | `PASS` | `0.09s` | single-writer state clean: 7 declared surface(s) audited, locking verified |
| `/usr/bin/python3 tests/test_ledger_schema.py` | `PASS` | `0.33s` | ledger schema clean, 4 excused: 181 row(s) audited, all domain invariants & schemas verified |
| `/usr/bin/python3 tools/hygiene.py --clean --namespace ai-antispam` | `PASS` | `0.42s` | hygiene cleanup complete: 9 scratch item(s) reaped |
| `/usr/bin/python3 tools/hygiene.py --audit --namespace ai-antispam` | `FAIL` | `0.32s` | hygiene declaration: 0 path(s) declared live, 0 declared scratch (docs/hygiene-protected.json) |
| `/usr/bin/python3 tools/roadmap.py --audit` | `PASS` | `0.25s` | Product & cadence roadmap clean: 3 canonical products healthy and cadenced |
| `/usr/bin/python3 tests/test_audit_yield.py` | `PASS` | `7.3s` | yield-artifact guard clean: one rounding site, row == scorecard at 14/17 |
| `/usr/bin/python3 tests/test_audit_gate_note.py` | `PASS` | `0.21s` | gate-note guard clean: a FAIL note carries its population, PASS keeps its summary |
| `/usr/bin/python3 tests/test_close_board_recorded.py` | `PASS` | `0.17s` | close-board gate: excused — 9 row(s) (2 admitted, 7 pre-invariant); this is a visible debt, not a clean run |
| `/usr/bin/python3 tests/test_kit_pin.py` | `PASS` | `0.53s` | kit pin passed |
| `/usr/bin/python3 -m pytest tests/test_cron_thinness.py -q` | `PASS` | `20.24s` | 16 passed in 0.21s |
| `/usr/bin/python3 tests/test_patrol_host_state.py` | `PASS` | `6.51s` | patrol-runner gate passed: 109 check(s) |
| `/usr/bin/python3 tests/test_ledger.py` | `PASS` | `110.29s` | ledger gate passed |
| `/usr/bin/python3 tests/test_audit_stamp_duration.py` | `PASS` | `9.29s` | audit-stamp duration guard clean: the stamp site carries no literal, and the row tracks the gate work it describes |
| `/usr/bin/python3 tests/test_ledger_commit_cites_no_rows.py` | `PASS` | `0.95s` | ledger clause: excused — 3 exempted violation(s) in cd2a3f874a39aacd0073b88372c15e23a4cbc3cd..HEAD; this is a visible debt, not a clean run |

### 2.1 Failing gate output (full)

**`/usr/bin/python3 tools/hygiene.py --audit --namespace ai-antispam`** -- FAIL, rc=1, 0.32s

```
hygiene namespace: ai-antispam (default, the repository directory name)
hygiene declaration: 0 path(s) declared live, 0 declared scratch (docs/hygiene-protected.json)
```


---

## 3. Calibration Spot-Check (Sampled Task)

- **Sampled Subject:** `#108`
- **Intake Recorded:** `YES`
- **Claim Lock Recorded:** `YES`
- **Close Verified:** `YES`
- **Sequence Integrity:** `VERIFIED`
