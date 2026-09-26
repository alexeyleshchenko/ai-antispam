# Operational Process Self-Audit — 2026-09-26

> **Verdict:** `FAILED` · Process 3 (Internal Self-Audit)

---

## 1. Process Health & Delivery Telemetry

| Metric | Value | Reference / Derivation |
|---|---|---|
| **Total Ledger Events** | `45` | Continuous ledger sequence |
| **Closed Tasks** | `6` | Tasks reaching verified close |
| **First-Pass Yield** | `46.2%` | Accepted runs ÷ total runs |
| **Rework Entries** | `3` | Defect count recorded in rework.md |
| **Rework Rate** | `50.0%` | Rework entries ÷ closed tasks |
| **Avg Task Lead Time** | `86790.1s` | Average duration from intake to close |
| **Total Inference Cost** | `$0.0000` | Tracked cost across ledger task telemetry |
| **Avg Cost / Closed Task** | `$0.0000` | Total cost ÷ closed tasks |
| **Total Tokens (In/Out)** | `0 / 0` | Cumulative prompt and completion tokens |
| **Cadence Status** | `HELD` | Last run: 24.2h ago |

---

## 2. Mechanical Gate Verification

| Gate / Command | Outcome | Duration | Notes |
|---|---|---|---|
| `/usr/bin/python3 tools/ledger.py verify` | `PASS` | `0.22s` | ledger clean: 44 row(s), monotonic, all event types known, s |
| `/usr/bin/python3 tests/test_ontology.py` | `PASS` | `0.3s` | vocabulary clean: 13 canonical term(s), 2 banned term(s), 33 |
| `/usr/bin/python3 tests/test_rework.py` | `PASS` | `0.12s` | rework gate passed |
| `/usr/bin/python3 tests/test_single_writer.py` | `PASS` | `0.14s` | single-writer state clean: 7 declared surface(s) audited, lo |
| `/usr/bin/python3 tests/test_ledger_schema.py` | `FAIL` | `0.15s` | ledger schema violations (4 problem(s) in /root/ai-antispam/ |
| `/usr/bin/python3 tools/hygiene.py --clean` | `PASS` | `0.26s` | hygiene cleanup complete: 1 scratch item(s) reaped |
| `/usr/bin/python3 tools/hygiene.py --audit` | `PASS` | `0.29s` | hygiene audit clean: 0 stale scratch items in /tmp/ai-antisp |
| `/usr/bin/python3 tests/test_audit_yield.py` | `PASS` | `3.7s` | yield-artifact guard clean: one rounding site, row == scorec |
| `/usr/bin/python3 tests/test_audit_gate_note.py` | `PASS` | `0.14s` | gate-note guard clean: a FAIL note carries its population, P |

### 2.1 Failing gate output (full)

**`/usr/bin/python3 tests/test_ledger_schema.py`** -- rc=1, 0.15s

```
ledger schema violations (4 problem(s) in /root/ai-antispam/evidence/ledger.jsonl):
  line 15: unauthorized actor 'worker' for event 'intake' (authorized: triage, hq, owner, delegate)
  line 26: unauthorized actor 'worker' for event 'intake' (authorized: triage, hq, owner, delegate)
  line 30: unauthorized actor 'worker' for event 'intake' (authorized: triage, hq, owner, delegate)
  line 35: unauthorized actor 'worker' for event 'intake' (authorized: triage, hq, owner, delegate)
```


---

## 3. Calibration Spot-Check (Sampled Task)

- **Sampled Subject:** `#49`
- **Intake Recorded:** `YES`
- **Claim Lock Recorded:** `YES`
- **Close Verified:** `YES`
- **Sequence Integrity:** `VERIFIED`
