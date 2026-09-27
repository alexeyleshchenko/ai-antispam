# Operational Process Self-Audit — 2026-09-27

> **Verdict:** `FAILED` · Process 3 (Internal Self-Audit)

---

## 1. Process Health & Delivery Telemetry

| Metric | Value | Reference / Derivation |
|---|---|---|
| **Total Ledger Events** | `62` | Continuous ledger sequence |
| **Closed Tasks** | `8` | Tasks reaching verified close |
| **First-Pass Yield** | `42.9%` | Accepted runs ÷ total runs |
| **Rework Entries** | `3` | Defect count recorded in rework.md |
| **Rework Rate** | `37.5%` | Rework entries ÷ closed tasks |
| **Avg Task Lead Time** | `83760.2s` | Average duration from intake to close |
| **Total Inference Cost** | `$0.0000` | Tracked cost across ledger task telemetry |
| **Avg Cost / Closed Task** | `$0.0000` | Total cost ÷ closed tasks |
| **Total Tokens (In/Out)** | `0 / 0` | Cumulative prompt and completion tokens |
| **Cadence Status** | `HELD` | Last run: 23.8h ago |

---

## 2. Mechanical Gate Verification

| Gate / Command | Outcome | Duration | Notes |
|---|---|---|---|
| `/usr/bin/python3 tools/ledger.py verify` | `PASS` | `0.2s` | ledger clean: 61 row(s), monotonic, all event types known, s |
| `/usr/bin/python3 tests/test_ontology.py` | `PASS` | `0.25s` | vocabulary clean: 13 canonical term(s), 2 banned term(s), 34 |
| `/usr/bin/python3 tests/test_rework.py` | `PASS` | `0.1s` | rework gate passed |
| `/usr/bin/python3 tests/test_single_writer.py` | `PASS` | `0.1s` | single-writer state clean: 7 declared surface(s) audited, lo |
| `/usr/bin/python3 tests/test_ledger_schema.py` | `FAIL` | `0.12s` | ledger schema violations (4 problem(s) in /root/ai-antispam/ |
| `/usr/bin/python3 tools/hygiene.py --clean` | `PASS` | `0.38s` | hygiene cleanup complete: 1 scratch item(s) reaped |
| `/usr/bin/python3 tools/hygiene.py --audit` | `PASS` | `0.34s` | hygiene audit clean: 0 stale scratch items in /tmp/ai-antisp |
| `/usr/bin/python3 tests/test_audit_yield.py` | `PASS` | `3.84s` | yield-artifact guard clean: one rounding site, row == scorec |
| `/usr/bin/python3 tests/test_audit_gate_note.py` | `PASS` | `0.13s` | gate-note guard clean: a FAIL note carries its population, P |
| `/usr/bin/python3 tests/test_close_board_recorded.py` | `FAIL` | `0.08s` | excused: n=5 (2026-09-17T09:33:06Z) predates the invariant ( |

### 2.1 Failing gate output (full)

**`/usr/bin/python3 tests/test_ledger_schema.py`** -- rc=1, 0.12s

```
ledger schema violations (4 problem(s) in /root/ai-antispam/evidence/ledger.jsonl):
  line 15: unauthorized actor 'worker' for event 'intake' (authorized: triage, hq, owner, delegate)
  line 26: unauthorized actor 'worker' for event 'intake' (authorized: triage, hq, owner, delegate)
  line 30: unauthorized actor 'worker' for event 'intake' (authorized: triage, hq, owner, delegate)
  line 35: unauthorized actor 'worker' for event 'intake' (authorized: triage, hq, owner, delegate)
```

**`/usr/bin/python3 tests/test_close_board_recorded.py`** -- rc=1, 0.08s

```
excused: n=5 (2026-09-17T09:33:06Z) predates the invariant (2026-09-26T15:25:00Z)
  excused: n=19 (2026-09-24T09:22:01Z) predates the invariant (2026-09-26T15:25:00Z)
  excused: n=22 (2026-09-24T09:25:56Z) predates the invariant (2026-09-26T15:25:00Z)
  excused: n=23 (2026-09-24T09:27:57Z) predates the invariant (2026-09-26T15:25:00Z)
  excused: n=27 (2026-09-24T09:43:56Z) predates the invariant (2026-09-26T15:25:00Z)
  excused: n=39 (2026-09-25T17:19:44Z) predates the invariant (2026-09-26T15:25:00Z)
  excused: n=44 (2026-09-25T20:45:20Z) predates the invariant (2026-09-26T15:25:00Z)
```


---

## 3. Calibration Spot-Check (Sampled Task)

- **Sampled Subject:** `#46`
- **Intake Recorded:** `YES`
- **Claim Lock Recorded:** `YES`
- **Close Verified:** `YES`
- **Sequence Integrity:** `VERIFIED`
