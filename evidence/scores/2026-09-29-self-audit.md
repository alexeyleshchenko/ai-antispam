# Operational Process Self-Audit — 2026-09-29

> **Verdict:** `FAILED` · Process 3 (Internal Self-Audit)

---

## 1. Process Health & Delivery Telemetry

| Metric | Value | Reference / Derivation |
|---|---|---|
| **Total Ledger Events** | `115` | Continuous ledger sequence |
| **Closed Tasks** | `18` | Tasks reaching verified close |
| **First-Pass Yield** | `68.0%` | Accepted runs ÷ total runs |
| **Rework Entries** | `3` | Defect count recorded in rework.md |
| **Rework Rate** | `16.7%` | Rework entries ÷ closed tasks |
| **Avg Task Lead Time** | `66049.6s` | Average duration from intake to close |
| **Total Inference Cost** | `$2087.9373` | Tracked cost across ledger task telemetry |
| **Avg Cost / Closed Task** | `$115.9965` | Total cost ÷ closed tasks |
| **Total Tokens (In/Out)** | `305458667 / 8173186901` | Cumulative prompt and completion tokens |
| **Cadence Status** | `HELD` | Last run: 4.2h ago |

---

## 2. Mechanical Gate Verification

| Gate / Command | Outcome | Duration | Notes |
|---|---|---|---|
| `/usr/bin/python3 tools/ledger.py verify` | `PASS` | `0.29s` | reconstructed claim: n=110 (subject #67) interval=1070s (cla |
| `/usr/bin/python3 tests/test_ontology.py` | `PASS` | `0.55s` | vocabulary clean: 13 canonical term(s), 2 banned term(s), 51 |
| `/usr/bin/python3 tests/test_rework.py` | `PASS` | `0.15s` | rework gate passed |
| `/usr/bin/python3 tests/test_single_writer.py` | `PASS` | `0.09s` | single-writer state clean: 7 declared surface(s) audited, lo |
| `/usr/bin/python3 tests/test_ledger_schema.py` | `PASS` | `0.37s` | ledger schema clean, 4 excused: 115 row(s) audited, all doma |
| `/usr/bin/python3 tools/hygiene.py --clean --namespace ai-antispam` | `PASS` | `0.66s` | hygiene cleanup complete: 2 scratch item(s) reaped |
| `/usr/bin/python3 tools/hygiene.py --audit --namespace ai-antispam` | `FAIL` | `0.44s` | hygiene namespace: ai-antispam (default, the repository dire |
| `/usr/bin/python3 tools/roadmap.py --audit` | `PASS` | `0.27s` | Product & cadence roadmap clean: 3 canonical products health |
| `/usr/bin/python3 tests/test_audit_yield.py` | `PASS` | `7.17s` | yield-artifact guard clean: one rounding site, row == scorec |
| `/usr/bin/python3 tests/test_audit_gate_note.py` | `PASS` | `0.24s` | gate-note guard clean: a FAIL note carries its population, P |
| `/usr/bin/python3 tests/test_close_board_recorded.py` | `PASS` | `0.11s` | close-board gate: excused — 9 row(s) (2 admitted, 7 pre-inva |
| `/usr/bin/python3 tests/test_kit_pin.py` | `FAIL` | `0.42s` | kit pin — a factory's tree against the pin it vendored (plan |
| `/usr/bin/python3 -m pytest tests/test_cron_thinness.py -q` | `PASS` | `23.23s` | 16 passed in 0.32s |
| `/usr/bin/python3 tests/test_patrol_host_state.py` | `PASS` | `6.8s` | patrol-runner gate passed: 102 check(s) |
| `/usr/bin/python3 tests/test_ledger.py` | `PASS` | `31.65s` | ledger gate passed |
| `/usr/bin/python3 tests/test_ledger_commit_cites_no_rows.py` | `PASS` | `1.42s` | ledger clause: excused — 3 exempted violation(s) in cd2a3f87 |

### 2.1 Failing gate output (full)

**`/usr/bin/python3 tools/hygiene.py --audit --namespace ai-antispam`** -- rc=1, 0.44s

```
hygiene namespace: ai-antispam (default, the repository directory name)
hygiene declaration: 0 path(s) declared live, 0 declared scratch (docs/hygiene-protected.json)
```

**`/usr/bin/python3 tests/test_kit_pin.py`** -- rc=1, 0.42s

```
kit pin — a factory's tree against the pin it vendored (plan 2646d31a step 8)
  kit pin — /root/ai-antispam
    pin registry/kit.json: version bbfbdb5147cf, 135 path(s) declared
    judged 94 carried path(s); 1 factory-class path(s) excluded by class; 5 declared exempt
  FAIL — carried paths that diverge from YOUR OWN pin and are not declared exempt:
    tests/test_close_row_revision.py  class=standalone  pin=d68182215f62 tree=ac16b6f601d7
    tests/test_ledger_commit_cites_no_rows.py  class=standalone  pin=dee90333b4ab tree=ddeaf90339e7
    Two lawful answers, and the choice is the factory's: take the update (python3 tools/kit_deliver.py --to . --dry-run), or declare the fork in registry/kit-exemptions.json with a reason.
  FAIL  the live tree is judged and green —   Two lawful answers, and the choice is the factory's: take the update (python3 tools/kit_deliver.py --to . --
  PASS  the live population is not empty — the green examined real paths — judged 94 carried path(s); 1 factory-class path(s) excluded by class; 5 declared exempt
  SKIPPED  the fixture arms — their inputs are TEMPLATE/-only and this tree
           carries none of them. The live arm above is this gate's verdict here.
  PASS  our own tree was never written to — manifest and predicate digests identical
kit pin FAILED: 1 check(s)
```


---

## 3. Calibration Spot-Check (Sampled Task)

- **Sampled Subject:** `campaign-poll-null-residue-redrive`
- **Intake Recorded:** `YES`
- **Claim Lock Recorded:** `YES`
- **Close Verified:** `YES`
- **Sequence Integrity:** `VERIFIED`
