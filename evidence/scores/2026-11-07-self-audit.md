# Operational Process Self-Audit — 2026-11-07

> **Verdict:** `DEGRADED` · Process 3 (Internal Self-Audit)

> **Ledger read at:** `2026-11-07T05:51:52Z` (350 events) — this artifact describes the ledger AS OF THAT INSTANT, before this run's own row was appended. It is a START-OF-RUN SNAPSHOT, not the ledger's state when you read it: the run row this audit writes lands after the read, and concurrent lanes append throughout the gate window (#142, ruling n=919).

---

## 1. Process Health & Delivery Telemetry

| Metric | Value | Reference / Derivation |
|---|---|---|
| **Total Ledger Events** | `350` | Continuous ledger sequence |
| **Closed Tasks** | `53` | Distinct closed subjects; `58` close rows (5 re-close of a re-opened subject) |
| **First-Pass Yield** | `64.7%` | 33 accepted runs ÷ 51 runs stating an outcome (coverage: 51 of 113 run rows) |
| **Rework Entries** | `10` | Defect count recorded in rework.md |
| **Rework Declarations** | `7/58` (12.1%) | Close rows declaring a `rework=#N` or `rework=none` disposition — canonical trailer read by position: rework=#N 1; rework=none 6; undeclared 51 (printed, never gated — n=386 clause 5) |
| **Rework Share** | `15.9%` | 10 rework entries ÷ (53 closed subjects + 10 rework entries) |
| **Rework per Close** | `18.9%` | 10 rework entries ÷ 53 closed subjects |
| **Change Fail Rate** | `9.4%` | 5 closes that produced a rework entry ÷ 53 closed work units — **linkage coverage `10/10`** entries carry a determinate Subject (100.0%); read the rate only against this coverage |
| **Avg Task Lead Time** | `189824.5s` | Mean intake→close duration over 57 sampled subjects |
| **Total Inference Cost** | `$6859.8127` | Tracked cost across ledger task telemetry — summed from each row's canonical TRAILER only, by position and across every event (issue #90) |
| **Telemetry Outside the Trailer** | `2` row(s) | excluded from the totals above BY DESIGN: these rows carry a telemetry-shaped token outside the canonical trailer — n=141 (claim), n=239 (run). Read each as prose, not as spend |
| **Avg Cost / Closed Task** | `$129.4304` | $6859.8127 total cost ÷ 53 distinct closed subjects |
| **Cost / Successful Task** | `n/a` | $6859.8127 total cost ÷ 0 accepted closed subjects (coverage: 0 of 58 close rows state an outcome) |
| **Total Tokens (In/Out)** | `713420996 / 20780098161` | Cumulative prompt and completion tokens |
| **Cadence Status** | `HELD` | Last run: 23.7h ago · record read: evidence/ledger.jsonl run/score rows with subject internal-self-audit · latest artifact: 2026-11-06 |

---

## 2. Mechanical Gate Verification

| Gate / Command | Outcome | Duration | Notes |
|---|---|---|---|
| `/usr/bin/python3 tools/ledger.py verify` | `PASS` | `0.23s` | renumber-paired claim: n=148 #74 — closed by a row filed under #107, the number this item carries since the declared board renumber; the pai |
| `/usr/bin/python3 tests/test_ontology.py` | `PASS` | `0.23s` | vocabulary clean: 13 canonical term(s), 2 banned term(s), 98 file(s) scanned |
| `/usr/bin/python3 tests/test_rework.py` | `PASS` | `0.06s` | rework gate passed |
| `/usr/bin/python3 tests/test_rework_relative_revision.py` | `PASS` | `0.09s` | rework relative-revision gate passed |
| `/usr/bin/python3 tests/test_rework_declared_landed.py` | `PASS` | `0.14s` | rework declared-to-landed gate passed — 1 declaration(s) in scope, all resolved to a landed entry |
| `/usr/bin/python3 tests/test_single_writer.py` | `PASS` | `0.05s` | single-writer state clean: 7 declared surface(s) audited, locking verified |
| `/usr/bin/python3 tests/test_ledger_schema.py` | `PASS` | `0.21s` | ledger schema clean, 4 excused: 350 row(s) audited, all domain invariants & schemas verified |
| `/usr/bin/python3 tools/hygiene.py --clean --namespace ai-antispam` | `PASS` | `0.17s` | hygiene cleanup complete: 0 scratch item(s) reaped |
| `/usr/bin/python3 tools/hygiene.py --audit --namespace ai-antispam` | `PASS` | `0.14s` | hygiene audit clean: 0 stale scratch items in /tmp/ai-antispam-*, 0 stranded dirty paths (grace 60m), 0 advisory item(s), working tree clean |
| `/usr/bin/python3 tools/roadmap.py --audit` | `PASS` | `0.11s` | Product & cadence roadmap clean: 3 canonical products healthy and cadenced |
| `/usr/bin/python3 tests/test_audit_yield.py` | `PASS` | `3.8s` | yield-artifact guard clean: one rounding site, row == scorecard at 14/17 |
| `/usr/bin/python3 tests/test_audit_gate_note.py` | `PASS` | `0.1s` | gate-note guard clean: a FAIL note carries its population, PASS keeps its summary |
| `/usr/bin/python3 tests/test_close_board_recorded.py` | `PASS` | `0.07s` | close-board gate: excused — 9 row(s) (2 admitted, 7 pre-invariant); this is a visible debt, not a clean run |
| `/usr/bin/python3 tests/test_kit_pin.py` | `FAIL` | `0.16s` | kit pin FAILED: 1 check(s) |
| `/usr/bin/python3 -m pytest tests/test_cron_thinness.py -q` | `PASS` | `9.31s` | 16 passed in 0.10s |
| `/usr/bin/python3 tests/test_patrol_host_state.py` | `PASS` | `1.95s` | patrol-runner gate passed: 109 check(s) |
| `/usr/bin/python3 tests/test_ledger.py` | `PASS` | `38.39s` | ledger gate passed |
| `/usr/bin/python3 tests/test_audit_stamp_duration.py` | `PASS` | `7.73s` | audit-stamp duration guard clean: the stamp site carries no literal, and the row tracks the gate work it describes |
| `/usr/bin/python3 tests/test_audit_cadence_record.py` | `PASS` | `0.13s` | PASS: 4 fixture branches, 3 distinct labels, report row carries the state and the record |
| `/usr/bin/python3 tests/test_audit_cadence_subject.py` | `PASS` | `0.12s` | PASS: hole named with its days, unrelated row rejected by the clock, predicate bound |
| `/usr/bin/python3 tests/test_ledger_commit_cites_no_rows.py` | `PASS` | `0.69s` | ledger clause: excused — 3 exempted violation(s) in cd2a3f874a39aacd0073b88372c15e23a4cbc3cd..HEAD; this is a visible debt, not a clean run |
| `/usr/bin/python3 tests/test_audit_cadence_own_run.py` | `PASS` | `0.13s` | PASS: own-run excluded, prior hole still named, parameter present and passed |
| `/usr/bin/python3 tests/test_audit_tree_condition.py` | `PASS` | `0.44s` | audit tree condition: passed |
| `/usr/bin/python3 tests/test_board_intake_recorded.py` | `PASS` | `0.13s` | board-intake gate passed: 15 check(s) |
| `/usr/bin/python3 tests/test_brain_metrics.py` | `PASS` | `0.21s` | brain-metrics gate passed (6 probe(s), 0 failure(s)) |
| `/usr/bin/python3 tests/test_close_row_revision.py` | `PASS` | `0.5s` | close-row revision gate: clean — 42 post-boundary close row(s) declared a revision IN THE CANONICAL RUN and every one RESOLVES; 1 retired by |
| `/usr/bin/python3 tests/test_commit_pair_hook.py` | `PASS` | `0.1s` | commit pair hook ok: tools/hooks/pre-commit shipped, executable, wired via core.hooksPath |
| `/usr/bin/python3 tests/test_commit_session_trailer.py` | `PASS` | `0.43s` | commit session trailer passed |
| `/usr/bin/python3 tests/test_criteria_count.py` | `PASS` | `0.1s` | criteria count: clean — 0 claim(s) over 43 file(s) all read 19 criteria / 6 families (source: the shipped pin v0.5 — no local rubric in this |
| `/usr/bin/python3 tests/test_docs_sync.py` | `PASS` | `0.07s` | no TEMPLATE/docs tree — nothing to pair |
| `/usr/bin/python3 tests/test_duplicate_prose.py` | `PASS` | `0.06s` | duplicate-prose gate ok: 0 law surface(s), 0 prose block(s), no duplicates |
| `/usr/bin/python3 tests/test_gate_fixtures_closure.py` | `PASS` | `3.63s` | gate-fixture closure gate passed |
| `/usr/bin/python3 tests/test_gate_invocation_mode.py` | `PASS` | `0.35s` | SKIP no registry/gates.json in this tree — the invocation-mode declaration has not been adopted here, so there is nothing to assert against. |
| `/usr/bin/python3 tests/test_gate_registration.py` | `PASS` | `1.61s` | gate registry passed |
| `/usr/bin/python3 tests/test_ledger_identity.py` | `PASS` | `0.14s` | ledger identity: SKIPPED — this tree carries a lane resolver and a manifest, but its Worker lane does not resolve to a live session, so no s |
| `/usr/bin/python3 tests/test_ledger_index.py` | `PASS` | `0.68s` | ledger index gate clean |
| `/usr/bin/python3 tests/test_ledger_no_shrink.py` | `PASS` | `0.53s` | ledger no-shrink: clean — no commit removes a row identity |
| `/usr/bin/python3 tests/test_publish.py` | `PASS` | `1.23s` | PASS an in-sync round renders as in-sync |
| `/usr/bin/python3 tests/test_questions.py` | `PASS` | `491.51s` | the questions selftest passed: 514 check(s) examined |
| `/usr/bin/python3 tests/test_registry.py` | `PASS` | `0.13s` | SKIPPED — no docs/factory-registry.md, registry/index.json in this tree, so there is no registry to judge. Both are FACTORY DATA generated f |
| `/usr/bin/python3 tests/test_roadmap_transition.py` | `PASS` | `0.93s` | roadmap transition: clean — fresh/empty/malformed/incomplete all RED, filled declaration GREEN, skeleton-copy not GREEN; this tree: declared |
| `/usr/bin/python3 tests/test_skill_version_contract.py` | `PASS` | `0.1s` | skill-version contract: SKIP — no tracked law file matches skills/*/SKILL.md, TEMPLATE/SKILL.md.tmpl — nothing to judge |
| `/usr/bin/python3 tests/test_subject_anchor.py` | `PASS` | `0.15s` | subject-anchor gate passed (11 probe(s), 0 failure(s)); the live fleet reading above is REPORTED, never judged. |
| `/usr/bin/python3 tests/test_template_integrity.py` | `PASS` | `0.08s` | no TEMPLATE/ tree — the factory is the instantiation, nothing to classify |
| `/usr/bin/python3 -m pytest tests/test_binding_mechanism_exists.py -q` | `PASS` | `12.71s` | 3 passed in 3.06s |
| `/usr/bin/python3 -m pytest tests/test_close_telemetry_provenance.py -q` | `PASS` | `9.53s` | 18 passed in 0.15s |
| `/usr/bin/python3 -m pytest tests/test_hygiene_inflight.py -q` | `PASS` | `11.56s` | 7 passed in 1.78s |
| `/usr/bin/python3 -m pytest tests/test_hygiene_namespace.py -q` | `PASS` | `11.16s` | 14 passed in 1.31s |
| `/usr/bin/python3 -m pytest tests/test_insights_gate_recorded.py -q` | `PASS` | `9.75s` | 24 passed, 1 skipped in 0.16s |
| `/usr/bin/python3 -m pytest tests/test_ledger_close_preflight.py -q` | `PASS` | `12.02s` | 7 passed in 2.03s |
| `/usr/bin/python3 -m pytest tests/test_registry_attest.py -q` | `PASS` | `9.58s` | 9 passed in 0.09s |
| `/usr/bin/python3 -m pytest tests/test_registry_render.py -q` | `PASS` | `9.66s` | 29 passed in 0.11s |
| `/usr/bin/python3 -m pytest tests/test_score_gate_recorded.py -q` | `PASS` | `9.22s` | 24 passed, 1 skipped in 0.11s |
| `/usr/bin/python3 -m pytest tests/test_audit_rates.py -q` | `PASS` | `12.42s` | 19 passed in 3.05s |

### 2.1 Failing gate output (full)

**`/usr/bin/python3 tests/test_kit_pin.py`** -- FAIL, rc=1, 0.16s

```
kit pin — a factory's tree against the pin it vendored (plan 2646d31a step 8)
  kit pin — /root/ai-antispam
    pin registry/kit.json: version 7e2a2281c13c, 137 path(s) declared
    judged 96 carried path(s); 2 factory-class path(s) excluded by class; 14 declared exempt
  FAIL — carried paths that diverge from YOUR OWN pin and are not declared exempt:
    tests/test_ledger_commit_cites_no_rows.py  class=standalone  pin=ddeaf90339e7 tree=af7af652b780
    tools/hooks/commit-msg  class=standalone  pin=16ede51dbfdd tree=ac35968777be
    Two lawful answers, and the choice is the factory's: take the update (python3 tools/kit_deliver.py --to . --dry-run), or declare the fork in registry/kit-exemptions.json with a reason.
  FAIL  the live tree is judged and green —   Two lawful answers, and the choice is the factory's: take the update (python3 tools/kit_deliver.py --to . --
  PASS  the live population is not empty — the green examined real paths — judged 96 carried path(s); 2 factory-class path(s) excluded by class; 14 declared exempt
  SKIPPED  the fixture arms — their inputs are TEMPLATE/-only and this tree
           carries none of them. The live arm above is this gate's verdict here.
  PASS  #522 a class the PIN declares but this READER does not know names the READER and the port — the pin /tmp/tmplqgtv_xs/kit.json declares class(es) ['brand-new-class'], which THIS READER does not know: this reader k
  PASS  #522 a class in NEITHER list still reads as unknown, vocabulary present — the pin /tmp/tmplqgtv_xs/kit.json declares unknown class(es) ['standlone']; expected ['standalone', 'closure', 'seed', '
  PASS  #522 with no `class_vocabulary` the original wording is unchanged — the pin /tmp/tmplqgtv_xs/kit.json declares unknown class(es) ['standlone']; expected ['standalone', 'closure', 'seed', '
  PASS  #522 a class this reader KNOWS is accepted (the split did not break the lawful path) — loaded
  PASS  #522 a malformed `class_vocabulary` is IGNORED, never read as a licence — the pin /tmp/tmplqgtv_xs/kit.json declares unknown class(es) ['brand-new-class']; expected ['standalone', 'closure', 'se
  PASS  CONTROL: with the vocabulary key unread, case (1) collapses to the OLD single message — the split is what makes it bite — the pin /tmp/tmplqgtv_xs/kit.json declares unknown class(es) ['brand-new-class']; expected ['standalone', 'clo
  PASS  our own tree was never written to — manifest and predicate digests identical
kit pin FAILED: 1 check(s)
```


---

## 3. Calibration Spot-Check (Sampled Task)

- **Sampled Subject:** `#133`
- **Intake Recorded:** `YES`
- **Claim Lock Recorded:** `YES`
- **Close Verified:** `YES`
- **Sequence Integrity:** `VERIFIED`
