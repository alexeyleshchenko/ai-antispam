# Operational Process Self-Audit — 2026-11-12

> **Verdict:** `PASSED` · Process 3 (Internal Self-Audit)

> **Ledger read at:** `2026-11-12T05:51:35Z` (418 events) — this artifact describes the ledger AS OF THAT INSTANT, before this run's own row was appended. It is a START-OF-RUN SNAPSHOT, not the ledger's state when you read it: the run row this audit writes lands after the read, and concurrent lanes append throughout the gate window (#142, ruling n=919).

---

## 1. Process Health & Delivery Telemetry

| Metric | Value | Reference / Derivation |
|---|---|---|
| **Total Ledger Events** | `418` | Continuous ledger sequence |
| **Closed Tasks** | `66` | Distinct closed subjects; `71` close rows (5 re-close of a re-opened subject) |
| **First-Pass Yield** | `64.2%` | 34 accepted runs ÷ 53 runs stating an outcome (coverage: 53 of 140 run rows) |
| **Rework Entries** | `10` | Defect count recorded in rework.md |
| **Rework Declarations** | `20/71` (28.2%) | Close rows declaring a `rework=#N` or `rework=none` disposition — canonical trailer read by position: rework=#N 1; rework=none 19; undeclared 51 (printed, never gated — n=386 clause 5) |
| **Rework Share** | `13.2%` | 10 rework entries ÷ (66 closed subjects + 10 rework entries) |
| **Rework per Close** | `15.2%` | 10 rework entries ÷ 66 closed subjects |
| **Change Fail Rate** | `9.1%` | 6 closes that produced a rework entry ÷ 66 closed work units — **linkage coverage `10/10`** entries carry a determinate Subject (100.0%); read the rate only against this coverage |
| **Avg Task Lead Time** | `167671.1s` | Mean intake→close duration over 68 sampled subjects |
| **Total Inference Cost** | `$7482.1064` | Tracked cost across ledger task telemetry — summed from each row's canonical TRAILER only, by position and across every event (issue #90) |
| **Telemetry Outside the Trailer** | `5` row(s) | excluded from the totals above BY DESIGN: these rows carry a telemetry-shaped token outside the canonical trailer — n=141 (claim), n=239 (run), n=365 (run), n=415 (run), n=418 (run). Read each as prose, not as spend |
| **Avg Cost / Closed Task** | `$113.3652` | $7482.1064 total cost ÷ 66 distinct closed subjects |
| **Cost / Successful Task** | `n/a` | $7482.1064 total cost ÷ 0 accepted closed subjects (coverage: 0 of 71 close rows state an outcome) |
| **Total Tokens (In/Out)** | `770532776 / 22734742351` | Cumulative prompt and completion tokens |
| **Cadence Status** | `HELD` | Last run: 23.5h ago · record read: evidence/ledger.jsonl run/score rows with subject internal-self-audit · latest artifact: 2026-11-11 |

---

## 2. Mechanical Gate Verification

| Gate / Command | Outcome | Duration | Notes |
|---|---|---|---|
| `/usr/bin/python3 tools/ledger.py verify` | `PASS` | `0.29s` | renumber-paired claim: n=148 #74 — closed by a row filed under #107, the number this item carries since the declared board renumber; the pai |
| `/usr/bin/python3 tests/test_ontology.py` | `PASS` | `0.31s` | vocabulary clean: 13 canonical term(s), 2 banned term(s), 108 file(s) scanned |
| `/usr/bin/python3 tests/test_rework.py` | `PASS` | `0.08s` | rework gate passed |
| `/usr/bin/python3 tests/test_rework_relative_revision.py` | `PASS` | `0.09s` | rework relative-revision gate passed |
| `/usr/bin/python3 tests/test_rework_declared_landed.py` | `PASS` | `0.14s` | rework declared-to-landed gate passed — 1 declaration(s) in scope, all resolved to a landed entry |
| `/usr/bin/python3 tests/test_single_writer.py` | `PASS` | `0.07s` | single-writer state clean: 7 declared surface(s) audited, locking verified |
| `/usr/bin/python3 tests/test_ledger_schema.py` | `PASS` | `0.25s` | ledger schema clean, 4 excused: 418 row(s) audited, all domain invariants & schemas verified |
| `/usr/bin/python3 tools/hygiene.py --clean --namespace ai-antispam` | `PASS` | `0.53s` | hygiene cleanup complete: 0 scratch item(s) reaped |
| `/usr/bin/python3 tools/hygiene.py --audit --namespace ai-antispam` | `PASS` | `0.3s` | hygiene audit clean: 0 stale scratch items in /tmp/ai-antispam-*, 0 stranded dirty paths (grace 60m), 0 advisory item(s), working tree clean |
| `/usr/bin/python3 tools/roadmap.py --audit` | `PASS` | `0.11s` | Product & cadence roadmap clean: 3 canonical products healthy and cadenced |
| `/usr/bin/python3 tests/test_audit_yield.py` | `PASS` | `3.59s` | yield-artifact guard clean: one rounding site, row == scorecard at 14/17 |
| `/usr/bin/python3 tests/test_audit_gate_note.py` | `PASS` | `0.09s` | gate-note guard clean: a FAIL note carries its population, PASS keeps its summary |
| `/usr/bin/python3 tests/test_close_board_recorded.py` | `PASS` | `0.07s` | close-board gate: excused — 9 row(s) (2 admitted, 7 pre-invariant); this is a visible debt, not a clean run |
| `/usr/bin/python3 tests/test_kit_pin.py` | `PASS` | `0.15s` | kit pin passed |
| `/usr/bin/python3 -m pytest tests/test_cron_thinness.py -q` | `PASS` | `10.57s` | 16 passed in 0.13s |
| `/usr/bin/python3 tests/test_patrol_host_state.py` | `PASS` | `2.62s` | patrol-runner gate passed: 109 check(s) |
| `/usr/bin/python3 tests/test_ledger.py` | `PASS` | `51.95s` | ledger gate passed |
| `/usr/bin/python3 tests/test_audit_stamp_duration.py` | `PASS` | `8.17s` | audit-stamp duration guard clean: the stamp site carries no literal, and the row tracks the gate work it describes |
| `/usr/bin/python3 tests/test_audit_cadence_record.py` | `PASS` | `0.16s` | PASS: 4 fixture branches, 3 distinct labels, report row carries the state and the record |
| `/usr/bin/python3 tests/test_audit_cadence_subject.py` | `PASS` | `0.14s` | PASS: hole named with its days, unrelated row rejected by the clock, predicate bound |
| `/usr/bin/python3 tests/test_ledger_commit_cites_no_rows.py` | `PASS` | `0.9s` | ledger clause: excused — 3 exempted violation(s) in cd2a3f874a39aacd0073b88372c15e23a4cbc3cd..HEAD; this is a visible debt, not a clean run |
| `/usr/bin/python3 tests/test_audit_cadence_own_run.py` | `PASS` | `0.16s` | PASS: own-run excluded, prior hole still named, parameter present and passed |
| `/usr/bin/python3 tests/test_audit_tree_condition.py` | `PASS` | `0.48s` | audit tree condition: passed |
| `/usr/bin/python3 tests/test_board_intake_recorded.py` | `PASS` | `0.15s` | board-intake gate passed: 15 check(s) |
| `/usr/bin/python3 tests/test_brain_metrics.py` | `PASS` | `0.25s` | brain-metrics gate passed (6 probe(s), 0 failure(s)) |
| `/usr/bin/python3 tests/test_close_row_revision.py` | `PASS` | `0.74s` | close-row revision gate: clean — 55 post-boundary close row(s) declared a revision IN THE CANONICAL RUN and every one RESOLVES; 1 retired by |
| `/usr/bin/python3 tests/test_commit_close_keyword.py` | `PASS` | `0.06s` | close-keyword gate: clean — 9 closing spellings refused, 9 lawful subjects accepted, body surface covered, bite proof intact |
| `/usr/bin/python3 tests/test_commit_pair_hook.py` | `PASS` | `0.11s` | commit pair hook ok: tools/hooks/pre-commit shipped, executable, wired via core.hooksPath |
| `/usr/bin/python3 tests/test_commit_session_trailer.py` | `PASS` | `0.46s` | commit session trailer passed |
| `/usr/bin/python3 tests/test_criteria_count.py` | `PASS` | `0.11s` | criteria count: clean — 0 claim(s) over 46 file(s) all read 19 criteria / 6 families (source: the shipped pin v0.5 — no local rubric in this |
| `/usr/bin/python3 tests/test_docs_sync.py` | `PASS` | `0.05s` | no TEMPLATE/docs tree — nothing to pair |
| `/usr/bin/python3 tests/test_duplicate_prose.py` | `PASS` | `0.06s` | duplicate-prose gate ok: 0 law surface(s), 0 prose block(s), no duplicates |
| `/usr/bin/python3 tests/test_gate_fixtures_closure.py` | `PASS` | `3.11s` | gate-fixture closure gate passed |
| `/usr/bin/python3 tests/test_gate_invocation_mode.py` | `PASS` | `0.25s` | SKIP no registry/gates.json in this tree — the invocation-mode declaration has not been adopted here, so there is nothing to assert against. |
| `/usr/bin/python3 tests/test_gate_registration.py` | `PASS` | `2.13s` | gate registry passed |
| `/usr/bin/python3 tests/test_ledger_identity.py` | `PASS` | `0.13s` | ledger identity: SKIPPED — this tree carries a lane resolver and a manifest, but its Worker lane does not resolve to a live session, so no s |
| `/usr/bin/python3 tests/test_ledger_index.py` | `PASS` | `0.7s` | ledger index gate clean |
| `/usr/bin/python3 tests/test_ledger_no_shrink.py` | `PASS` | `0.78s` | ledger no-shrink: clean — no commit removes a row identity |
| `/usr/bin/python3 tests/test_publish.py` | `PASS` | `1.36s` | PASS an in-sync round renders as in-sync |
| `/usr/bin/python3 tests/test_questions.py` | `PASS` | `617.2s` | the questions selftest passed: 520 check(s) examined |
| `/usr/bin/python3 tests/test_registry.py` | `PASS` | `0.17s` | SKIPPED — no docs/factory-registry.md, registry/index.json in this tree, so there is no registry to judge. Both are FACTORY DATA generated f |
| `/usr/bin/python3 tests/test_roadmap_transition.py` | `PASS` | `1.35s` | roadmap transition: clean — fresh/empty/malformed/incomplete all RED, filled declaration GREEN, skeleton-copy not GREEN; this tree: declared |
| `/usr/bin/python3 tests/test_skill_version_contract.py` | `PASS` | `0.2s` | skill-version contract: SKIP — no tracked law file matches skills/*/SKILL.md, TEMPLATE/SKILL.md.tmpl — nothing to judge |
| `/usr/bin/python3 tests/test_subject_anchor.py` | `PASS` | `0.2s` | subject-anchor gate passed (11 probe(s), 0 failure(s)); the live fleet reading above is REPORTED, never judged. |
| `/usr/bin/python3 tests/test_template_integrity.py` | `PASS` | `0.1s` | no TEMPLATE/ tree — the factory is the instantiation, nothing to classify |
| `/usr/bin/python3 -m pytest tests/test_binding_mechanism_exists.py -q` | `PASS` | `14.75s` | 3 passed in 2.68s |
| `/usr/bin/python3 -m pytest tests/test_close_telemetry_provenance.py -q` | `PASS` | `12.75s` | 18 passed in 0.20s |
| `/usr/bin/python3 -m pytest tests/test_hygiene_inflight.py -q` | `PASS` | `12.41s` | 7 passed in 2.55s |
| `/usr/bin/python3 -m pytest tests/test_hygiene_namespace.py -q` | `PASS` | `14.55s` | 14 passed in 2.34s |
| `/usr/bin/python3 -m pytest tests/test_hygiene_build_residue.py -q` | `PASS` | `12.16s` | 9 passed in 0.66s |
| `/usr/bin/python3 -m pytest tests/test_hygiene_declaration_sweep.py -q` | `PASS` | `10.6s` | 13 passed in 0.53s |
| `/usr/bin/python3 -m pytest tests/test_hygiene_stale_dirs.py -q` | `PASS` | `12.01s` | 14 passed in 0.60s |
| `/usr/bin/python3 -m pytest tests/test_hygiene_branch_occupancy.py -q` | `PASS` | `9.85s` | 9 passed in 0.08s |
| `/usr/bin/python3 -m pytest tests/test_insights_gate_recorded.py -q` | `PASS` | `11.56s` | 24 passed, 1 skipped in 0.18s |
| `/usr/bin/python3 -m pytest tests/test_ledger_close_preflight.py -q` | `PASS` | `12.65s` | 7 passed in 2.29s |
| `/usr/bin/python3 -m pytest tests/test_registry_attest.py -q` | `PASS` | `11.24s` | 9 passed in 0.13s |
| `/usr/bin/python3 -m pytest tests/test_registry_render.py -q` | `PASS` | `10.76s` | 29 passed in 0.18s |
| `/usr/bin/python3 -m pytest tests/test_score_gate_recorded.py -q` | `PASS` | `10.65s` | 24 passed, 1 skipped in 0.14s |
| `/usr/bin/python3 -m pytest tests/test_self_audit_push_gate.py -q` | `PASS` | `13.11s` | 5 passed in 0.76s |
| `/usr/bin/python3 -m pytest tests/test_audit_rates.py -q` | `PASS` | `15.93s` | 19 passed in 4.23s |

---

## 3. Calibration Spot-Check (Sampled Task)

- **Sampled Subject:** `#89`
- **Intake Recorded:** `NO`
- **Claim Lock Recorded:** `NO`
- **Close Verified:** `YES`
- **Sequence Integrity:** `BROKEN`
