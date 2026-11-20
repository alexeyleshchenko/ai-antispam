# Operational Process Self-Audit — 2026-11-20

> **Verdict:** `DEGRADED` · Process 3 (Internal Self-Audit)

> **Ledger read at:** `2026-11-20T05:51:29Z` (515 events) — this artifact describes the ledger AS OF THAT INSTANT, before this run's own row was appended. It is a START-OF-RUN SNAPSHOT, not the ledger's state when you read it: the run row this audit writes lands after the read, and concurrent lanes append throughout the gate window (#142, ruling n=919).

---

## 1. Process Health & Delivery Telemetry

| Metric | Value | Reference / Derivation |
|---|---|---|
| **Total Ledger Events** | `515` | Continuous ledger sequence |
| **Closed Tasks** | `83` | Distinct closed subjects; `89` close rows (6 re-close of a re-opened subject) |
| **First-Pass Yield** | `64.8%` | 35 accepted runs ÷ 54 runs stating an outcome (coverage: 54 of 183 run rows) |
| **Rework Entries** | `11` | Defect count recorded in rework.md |
| **Rework Declarations** | `38/89` (42.7%) | Close rows declaring a `rework=#N` or `rework=none` disposition — canonical trailer read by position: rework=#N 2; rework=none 36; undeclared 51 (printed, never gated — n=386 clause 5) |
| **Rework Share** | `11.7%` | 11 rework entries ÷ (83 closed subjects + 11 rework entries) |
| **Rework per Close** | `13.2%` | 11 rework entries ÷ 83 closed subjects |
| **Change Fail Rate** | `8.4%` | 7 closes that produced a rework entry ÷ 83 closed work units — **linkage coverage `11/11`** entries carry a determinate Subject (100.0%); read the rate only against this coverage |
| **Avg Task Lead Time** | `313844.2s` | Mean intake→close duration over 86 sampled subjects |
| **Total Inference Cost** | `$9231.8359` | Tracked cost across ledger task telemetry — summed from each row's canonical TRAILER only, by position and across every event (issue #90) |
| **Telemetry Outside the Trailer** | `11` row(s) | excluded from the totals above BY DESIGN: these rows carry a telemetry-shaped token outside the canonical trailer — n=141 (claim), n=239 (run), n=365 (run), n=415 (run), n=418 (run), n=419 (run), n=421 (run), n=424 (run), n=425 (run), n=451 (run), n=460 (run). Read each as prose, not as spend |
| **Avg Cost / Closed Task** | `$111.2269` | $9231.8359 total cost ÷ 83 distinct closed subjects |
| **Cost / Successful Task** | `n/a` | $9231.8359 total cost ÷ 0 accepted closed subjects (coverage: 0 of 89 close rows state an outcome) |
| **Total Tokens (In/Out)** | `985186449 / 28908257337` | Cumulative prompt and completion tokens |
| **Cadence Status** | `DROPPED ROW` | Last run: 45.9h ago · record read: evidence/ledger.jsonl run/score rows with subject internal-self-audit · latest artifact: 2026-11-19 |

---

## 2. Mechanical Gate Verification

| Gate / Command | Outcome | Duration | Notes |
|---|---|---|---|
| `/usr/bin/python3 tools/ledger.py verify` | `PASS` | `0.39s` | renumber-paired claim: n=148 #74 — closed by a row filed under #107, the number this item carries since the declared board renumber; the pai |
| `/usr/bin/python3 tests/test_ontology.py` | `PASS` | `3.33s` | vocabulary clean: 13 canonical term(s), 2 banned term(s), 114 file(s) scanned |
| `/usr/bin/python3 tests/test_rework.py` | `PASS` | `0.09s` | rework gate passed |
| `/usr/bin/python3 tests/test_rework_relative_revision.py` | `PASS` | `0.15s` | rework relative-revision gate passed |
| `/usr/bin/python3 tests/test_rework_declared_landed.py` | `PASS` | `0.2s` | rework declared-to-landed gate passed — 2 declaration(s) in scope, all resolved to a landed entry |
| `/usr/bin/python3 tests/test_single_writer.py` | `PASS` | `0.11s` | single-writer state clean: 7 declared surface(s) audited, locking verified |
| `/usr/bin/python3 tests/test_ledger_schema.py` | `PASS` | `0.31s` | ledger schema clean, 4 excused: 515 row(s) audited, all domain invariants & schemas verified |
| `/usr/bin/python3 tools/hygiene.py --clean --namespace ai-antispam` | `PASS` | `9.61s` | hygiene cleanup complete: 0 scratch item(s) reaped |
| `/usr/bin/python3 tools/hygiene.py --audit --namespace ai-antispam` | `PASS` | `0.4s` | hygiene audit clean: 0 stale scratch items in /tmp/ai-antispam-*, 0 stranded dirty paths (grace 60m), 0 advisory item(s), working tree clean |
| `/usr/bin/python3 tools/roadmap.py --audit` | `PASS` | `0.11s` | Product & cadence roadmap clean: 3 canonical products healthy and cadenced |
| `/usr/bin/python3 tests/test_audit_yield.py` | `PASS` | `4.6s` | yield-artifact guard clean: one rounding site, row == scorecard at 14/17 |
| `/usr/bin/python3 tests/test_audit_gate_note.py` | `PASS` | `0.19s` | gate-note guard clean: a FAIL note carries its population, PASS keeps its summary |
| `/usr/bin/python3 tests/test_close_board_recorded.py` | `PASS` | `0.1s` | close-board gate: excused — 9 row(s) (2 admitted, 7 pre-invariant); this is a visible debt, not a clean run |
| `/usr/bin/python3 tests/test_kit_pin.py` | `PASS` | `1.18s` | kit pin passed |
| `/usr/bin/python3 -m pytest tests/test_cron_thinness.py -q` | `FAIL` | `8.17s` | E ModuleNotFoundError: No module named 'aiosqlite' |
| `/usr/bin/python3 tests/test_patrol_host_state.py` | `PASS` | `3.9s` | patrol-runner gate passed: 109 check(s) |
| `/usr/bin/python3 tests/test_ledger.py` | `PASS` | `70.6s` | ledger gate passed |
| `/usr/bin/python3 tests/test_audit_stamp_duration.py` | `PASS` | `8.03s` | audit-stamp duration guard clean: the stamp site carries no literal, and the row tracks the gate work it describes |
| `/usr/bin/python3 tests/test_audit_cadence_record.py` | `PASS` | `0.19s` | PASS: 4 fixture branches, 3 distinct labels, report row carries the state and the record |
| `/usr/bin/python3 tests/test_audit_cadence_subject.py` | `PASS` | `0.22s` | PASS: hole named with its days, unrelated row rejected by the clock, predicate bound |
| `/usr/bin/python3 tests/test_ledger_commit_cites_no_rows.py` | `PASS` | `9.34s` | ledger clause: excused — 3 exempted violation(s) in cd2a3f874a39aacd0073b88372c15e23a4cbc3cd..HEAD; this is a visible debt, not a clean run |
| `/usr/bin/python3 tests/test_audit_cadence_own_run.py` | `PASS` | `0.18s` | PASS: own-run excluded, prior hole still named, parameter present and passed |
| `/usr/bin/python3 tests/test_audit_tree_condition.py` | `PASS` | `0.63s` | audit tree condition: passed |
| `/usr/bin/python3 tests/test_board_intake_recorded.py` | `PASS` | `0.12s` | board-intake gate passed: 15 check(s) |
| `/usr/bin/python3 tests/test_brain_metrics.py` | `PASS` | `0.22s` | brain-metrics gate passed (6 probe(s), 0 failure(s)) |
| `/usr/bin/python3 tests/test_close_row_revision.py` | `PASS` | `0.79s` | close-row revision gate: clean — 73 post-boundary close row(s) declared a revision IN THE CANONICAL RUN and every one RESOLVES; 1 retired by |
| `/usr/bin/python3 tests/test_commit_close_keyword.py` | `PASS` | `0.06s` | close-keyword gate: clean — 9 closing spellings refused, 9 lawful subjects accepted, body surface covered, bite proof intact |
| `/usr/bin/python3 tests/test_commit_pair_hook.py` | `PASS` | `0.18s` | commit pair hook ok: tools/hooks/pre-commit shipped, executable, wired via core.hooksPath |
| `/usr/bin/python3 tests/test_commit_session_trailer.py` | `PASS` | `0.48s` | commit session trailer passed |
| `/usr/bin/python3 tests/test_criteria_count.py` | `PASS` | `0.12s` | criteria count: clean — 0 claim(s) over 46 file(s) all read 19 criteria / 6 families (source: the shipped pin v0.5 — no local rubric in this |
| `/usr/bin/python3 tests/test_docs_sync.py` | `PASS` | `0.06s` | no TEMPLATE/docs tree — nothing to pair |
| `/usr/bin/python3 tests/test_duplicate_prose.py` | `PASS` | `0.06s` | duplicate-prose gate ok: 0 law surface(s), 0 prose block(s), no duplicates |
| `/usr/bin/python3 tests/test_gate_fixtures_closure.py` | `FAIL` | `1.01s` | PASS test_an_explicit_closure_loop_is_lawful |
| `/usr/bin/python3 tests/test_gate_invocation_mode.py` | `PASS` | `0.26s` | SKIP no registry/gates.json in this tree — the invocation-mode declaration has not been adopted here, so there is nothing to assert against. |
| `/usr/bin/python3 tests/test_gate_registration.py` | `PASS` | `3.08s` | gate registry passed |
| `/usr/bin/python3 tests/test_ledger_identity.py` | `PASS` | `0.91s` | ledger identity: SKIPPED — this tree carries a lane resolver and a manifest, but its Worker lane does not resolve to a live session, so no s |
| `/usr/bin/python3 tests/test_ledger_index.py` | `PASS` | `0.94s` | ledger index gate clean |
| `/usr/bin/python3 tests/test_ledger_no_shrink.py` | `PASS` | `4.44s` | ledger no-shrink: clean — no commit removes a row identity |
| `/usr/bin/python3 tests/test_publish.py` | `PASS` | `3.3s` | PASS an in-sync round renders as in-sync |
| `/usr/bin/python3 tests/test_questions.py` | `PASS` | `825.82s` | the questions selftest passed: 520 check(s) examined |
| `/usr/bin/python3 tests/test_registry.py` | `PASS` | `0.3s` | SKIPPED — no docs/factory-registry.md, registry/index.json in this tree, so there is no registry to judge. Both are FACTORY DATA generated f |
| `/usr/bin/python3 tests/test_roadmap_transition.py` | `PASS` | `1.41s` | roadmap transition: clean — fresh/empty/malformed/incomplete all RED, filled declaration GREEN, skeleton-copy not GREEN; this tree: declared |
| `/usr/bin/python3 tests/test_skill_version_contract.py` | `PASS` | `0.22s` | skill-version contract: SKIP — no tracked law file matches skills/*/SKILL.md, TEMPLATE/SKILL.md.tmpl — nothing to judge |
| `/usr/bin/python3 tests/test_subject_anchor.py` | `PASS` | `0.23s` | subject-anchor gate passed (11 probe(s), 0 failure(s)); the live fleet reading above is REPORTED, never judged. |
| `/usr/bin/python3 tests/test_template_integrity.py` | `PASS` | `0.11s` | no TEMPLATE/ tree — the factory is the instantiation, nothing to classify |
| `/usr/bin/python3 -m pytest tests/test_binding_mechanism_exists.py -q` | `FAIL` | `2.49s` | E ModuleNotFoundError: No module named 'aiosqlite' |
| `/usr/bin/python3 -m pytest tests/test_close_telemetry_provenance.py -q` | `FAIL` | `2.39s` | E ModuleNotFoundError: No module named 'aiosqlite' |
| `/usr/bin/python3 -m pytest tests/test_hygiene_inflight.py -q` | `FAIL` | `1.78s` | E ModuleNotFoundError: No module named 'aiosqlite' |
| `/usr/bin/python3 -m pytest tests/test_hygiene_namespace.py -q` | `FAIL` | `1.67s` | E ModuleNotFoundError: No module named 'aiosqlite' |
| `/usr/bin/python3 -m pytest tests/test_hygiene_build_residue.py -q` | `FAIL` | `1.5s` | E ModuleNotFoundError: No module named 'aiosqlite' |
| `/usr/bin/python3 -m pytest tests/test_hygiene_declaration_sweep.py -q` | `FAIL` | `1.8s` | E ModuleNotFoundError: No module named 'aiosqlite' |
| `/usr/bin/python3 -m pytest tests/test_hygiene_stale_dirs.py -q` | `FAIL` | `1.58s` | E ModuleNotFoundError: No module named 'aiosqlite' |
| `/usr/bin/python3 -m pytest tests/test_hygiene_branch_occupancy.py -q` | `FAIL` | `1.54s` | E ModuleNotFoundError: No module named 'aiosqlite' |
| `/usr/bin/python3 -m pytest tests/test_insights_gate_recorded.py -q` | `FAIL` | `1.57s` | E ModuleNotFoundError: No module named 'aiosqlite' |
| `/usr/bin/python3 -m pytest tests/test_ledger_close_preflight.py -q` | `FAIL` | `1.69s` | E ModuleNotFoundError: No module named 'aiosqlite' |
| `/usr/bin/python3 -m pytest tests/test_registry_attest.py -q` | `FAIL` | `1.81s` | E ModuleNotFoundError: No module named 'aiosqlite' |
| `/usr/bin/python3 -m pytest tests/test_registry_render.py -q` | `FAIL` | `2.02s` | E ModuleNotFoundError: No module named 'aiosqlite' |
| `/usr/bin/python3 -m pytest tests/test_score_gate_recorded.py -q` | `FAIL` | `1.84s` | E ModuleNotFoundError: No module named 'aiosqlite' |
| `/usr/bin/python3 -m pytest tests/test_self_audit_push_gate.py -q` | `FAIL` | `2.13s` | E ModuleNotFoundError: No module named 'aiosqlite' |
| `/usr/bin/python3 -m pytest tests/test_audit_rates.py -q` | `FAIL` | `1.93s` | E ModuleNotFoundError: No module named 'aiosqlite' |

### 2.1 Failing gate output (full)

**`/usr/bin/python3 -m pytest tests/test_cron_thinness.py -q`** -- FAIL, rc=4, 8.17s

```
ImportError while loading conftest '/root/ai-antispam/tests/conftest.py'.
tests/conftest.py:61: in <module>
    import aiosqlite
E   ModuleNotFoundError: No module named 'aiosqlite'
```

**`/usr/bin/python3 tests/test_gate_fixtures_closure.py`** -- FAIL, rc=1, 1.01s

```
PASS test_a_staged_closure_is_lawful
  PASS test_an_explicit_closure_loop_is_lawful
```

**`/usr/bin/python3 -m pytest tests/test_binding_mechanism_exists.py -q`** -- FAIL, rc=4, 2.49s

```
ImportError while loading conftest '/root/ai-antispam/tests/conftest.py'.
tests/conftest.py:61: in <module>
    import aiosqlite
E   ModuleNotFoundError: No module named 'aiosqlite'
```

**`/usr/bin/python3 -m pytest tests/test_close_telemetry_provenance.py -q`** -- FAIL, rc=4, 2.39s

```
ImportError while loading conftest '/root/ai-antispam/tests/conftest.py'.
tests/conftest.py:61: in <module>
    import aiosqlite
E   ModuleNotFoundError: No module named 'aiosqlite'
```

**`/usr/bin/python3 -m pytest tests/test_hygiene_inflight.py -q`** -- FAIL, rc=4, 1.78s

```
ImportError while loading conftest '/root/ai-antispam/tests/conftest.py'.
tests/conftest.py:61: in <module>
    import aiosqlite
E   ModuleNotFoundError: No module named 'aiosqlite'
```

**`/usr/bin/python3 -m pytest tests/test_hygiene_namespace.py -q`** -- FAIL, rc=4, 1.67s

```
ImportError while loading conftest '/root/ai-antispam/tests/conftest.py'.
tests/conftest.py:61: in <module>
    import aiosqlite
E   ModuleNotFoundError: No module named 'aiosqlite'
```

**`/usr/bin/python3 -m pytest tests/test_hygiene_build_residue.py -q`** -- FAIL, rc=4, 1.5s

```
ImportError while loading conftest '/root/ai-antispam/tests/conftest.py'.
tests/conftest.py:61: in <module>
    import aiosqlite
E   ModuleNotFoundError: No module named 'aiosqlite'
```

**`/usr/bin/python3 -m pytest tests/test_hygiene_declaration_sweep.py -q`** -- FAIL, rc=4, 1.8s

```
ImportError while loading conftest '/root/ai-antispam/tests/conftest.py'.
tests/conftest.py:61: in <module>
    import aiosqlite
E   ModuleNotFoundError: No module named 'aiosqlite'
```

**`/usr/bin/python3 -m pytest tests/test_hygiene_stale_dirs.py -q`** -- FAIL, rc=4, 1.58s

```
ImportError while loading conftest '/root/ai-antispam/tests/conftest.py'.
tests/conftest.py:61: in <module>
    import aiosqlite
E   ModuleNotFoundError: No module named 'aiosqlite'
```

**`/usr/bin/python3 -m pytest tests/test_hygiene_branch_occupancy.py -q`** -- FAIL, rc=4, 1.54s

```
ImportError while loading conftest '/root/ai-antispam/tests/conftest.py'.
tests/conftest.py:61: in <module>
    import aiosqlite
E   ModuleNotFoundError: No module named 'aiosqlite'
```

**`/usr/bin/python3 -m pytest tests/test_insights_gate_recorded.py -q`** -- FAIL, rc=4, 1.57s

```
ImportError while loading conftest '/root/ai-antispam/tests/conftest.py'.
tests/conftest.py:61: in <module>
    import aiosqlite
E   ModuleNotFoundError: No module named 'aiosqlite'
```

**`/usr/bin/python3 -m pytest tests/test_ledger_close_preflight.py -q`** -- FAIL, rc=4, 1.69s

```
ImportError while loading conftest '/root/ai-antispam/tests/conftest.py'.
tests/conftest.py:61: in <module>
    import aiosqlite
E   ModuleNotFoundError: No module named 'aiosqlite'
```

**`/usr/bin/python3 -m pytest tests/test_registry_attest.py -q`** -- FAIL, rc=4, 1.81s

```
ImportError while loading conftest '/root/ai-antispam/tests/conftest.py'.
tests/conftest.py:61: in <module>
    import aiosqlite
E   ModuleNotFoundError: No module named 'aiosqlite'
```

**`/usr/bin/python3 -m pytest tests/test_registry_render.py -q`** -- FAIL, rc=4, 2.02s

```
ImportError while loading conftest '/root/ai-antispam/tests/conftest.py'.
tests/conftest.py:61: in <module>
    import aiosqlite
E   ModuleNotFoundError: No module named 'aiosqlite'
```

**`/usr/bin/python3 -m pytest tests/test_score_gate_recorded.py -q`** -- FAIL, rc=4, 1.84s

```
ImportError while loading conftest '/root/ai-antispam/tests/conftest.py'.
tests/conftest.py:61: in <module>
    import aiosqlite
E   ModuleNotFoundError: No module named 'aiosqlite'
```

**`/usr/bin/python3 -m pytest tests/test_self_audit_push_gate.py -q`** -- FAIL, rc=4, 2.13s

```
ImportError while loading conftest '/root/ai-antispam/tests/conftest.py'.
tests/conftest.py:61: in <module>
    import aiosqlite
E   ModuleNotFoundError: No module named 'aiosqlite'
```

**`/usr/bin/python3 -m pytest tests/test_audit_rates.py -q`** -- FAIL, rc=4, 1.93s

```
ImportError while loading conftest '/root/ai-antispam/tests/conftest.py'.
tests/conftest.py:61: in <module>
    import aiosqlite
E   ModuleNotFoundError: No module named 'aiosqlite'
```


---

## 3. Calibration Spot-Check (Sampled Task)

- **Sampled Subject:** `#157`
- **Intake Recorded:** `YES`
- **Claim Lock Recorded:** `YES`
- **Close Verified:** `YES`
- **Sequence Integrity:** `VERIFIED`
