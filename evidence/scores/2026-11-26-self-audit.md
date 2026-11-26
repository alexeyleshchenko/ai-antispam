# Operational Process Self-Audit — 2026-11-26

> **Verdict:** `PASSED` · Process 3 (Internal Self-Audit)

> **Interpreter:** sys.version `3.14.6` · sys.executable `/usr/bin/python3.14` · realpath `/usr/bin/python3.14` · GATE_PYTHON `/usr/bin/python3.14`

> **Ledger read at:** `2026-11-26T06:12:05Z` (572 events) — this artifact describes the ledger AS OF THAT INSTANT, before this run's own row was appended. It is a START-OF-RUN SNAPSHOT, not the ledger's state when you read it: the run row this audit writes lands after the read, and concurrent lanes append throughout the gate window (#142, ruling n=919).

---

## 1. Process Health & Delivery Telemetry

| Metric | Value | Reference / Derivation |
|---|---|---|
| **Total Ledger Events** | `572` | Continuous ledger sequence |
| **Closed Tasks** | `91` | Distinct closed subjects; `97` close rows (6 re-close of a re-opened subject) |
| **First-Pass Yield** | `67.2%` | 39 accepted runs ÷ 58 runs stating an outcome (coverage: 58 of 204 run rows) |
| **Rework Entries** | `11` | Defect count recorded in rework.md |
| **Rework Declarations** | `46/97` (47.4%) | Close rows declaring a `rework=#N` or `rework=none` disposition — canonical trailer read by position: rework=#N 2; rework=none 44; undeclared 51 (printed, never gated — n=386 clause 5) |
| **Rework Share** | `10.8%` | 11 rework entries ÷ (91 closed subjects + 11 rework entries) |
| **Rework per Close** | `12.1%` | 11 rework entries ÷ 91 closed subjects |
| **Change Fail Rate** | `7.7%` | 7 closes that produced a rework entry ÷ 91 closed work units — **linkage coverage `11/11`** entries carry a determinate Subject (100.0%); read the rate only against this coverage |
| **Avg Task Lead Time** | `306879.0s` | Mean intake→close duration over 94 sampled subjects |
| **Total Inference Cost** | `$9846.5993` | Tracked cost across ledger task telemetry — summed from each row's canonical TRAILER only, by position and across every event (issue #90) |
| **Telemetry Outside the Trailer** | `11` row(s) | excluded from the totals above BY DESIGN: these rows carry a telemetry-shaped token outside the canonical trailer — n=141 (claim), n=239 (run), n=365 (run), n=415 (run), n=418 (run), n=419 (run), n=421 (run), n=424 (run), n=425 (run), n=451 (run), n=460 (run). Read each as prose, not as spend |
| **Avg Cost / Closed Task** | `$108.2044` | $9846.5993 total cost ÷ 91 distinct closed subjects |
| **Cost / Successful Task** | `n/a` | $9846.5993 total cost ÷ 0 accepted closed subjects (coverage: 0 of 97 close rows state an outcome) |
| **Total Tokens (In/Out)** | `1000041692 / 29220822925` | Cumulative prompt and completion tokens |
| **Cadence Status** | `HELD` | Last run: 22.6h ago · record read: evidence/ledger.jsonl run/score rows whose subject matches is_self_audit_subject (internal-self-audit, or daily self-audit YYYY-MM-DD) · matched 69 · rejected 1 (self-audit-class@2026-11-20) · latest artifact: 2026-11-26 |

---

## 2. Mechanical Gate Verification

| Gate / Command | Outcome | Duration | Notes |
|---|---|---|---|
| `/usr/bin/python3.14 tools/ledger.py verify` | `PASS` | `0.57s` | renumber-paired claim: n=148 #74 — closed by a row filed under #107, the number this item carries since the declared board renumber; the pai |
| `/usr/bin/python3.14 tests/test_ontology.py` | `PASS` | `4.48s` | vocabulary clean: 13 canonical term(s), 2 banned term(s), 121 file(s) scanned |
| `/usr/bin/python3.14 tests/test_rework.py` | `PASS` | `0.15s` | rework gate passed |
| `/usr/bin/python3.14 tests/test_rework_relative_revision.py` | `PASS` | `0.13s` | rework relative-revision gate passed |
| `/usr/bin/python3.14 tests/test_rework_declared_landed.py` | `PASS` | `0.35s` | rework declared-to-landed gate passed — 2 declaration(s) in scope, all resolved to a landed entry |
| `/usr/bin/python3.14 tests/test_single_writer.py` | `PASS` | `0.1s` | single-writer state clean: 7 declared surface(s) audited, locking verified |
| `/usr/bin/python3.14 tests/test_ledger_schema.py` | `PASS` | `0.57s` | ledger schema clean, 4 excused: 572 row(s) audited, all domain invariants & schemas verified |
| `/usr/bin/python3.14 tools/hygiene.py --clean --namespace ai-antispam` | `PASS` | `12.86s` | hygiene cleanup complete: 0 scratch item(s) reaped |
| `/usr/bin/python3.14 tools/hygiene.py --audit --namespace ai-antispam` | `PASS` | `0.42s` | hygiene audit clean: 0 stale scratch items in /tmp/ai-antispam-*, 0 stranded dirty paths (grace 60m), 1 advisory item(s), working tree clean |
| `/usr/bin/python3.14 tools/roadmap.py --audit` | `PASS` | `0.13s` | Product & cadence roadmap clean: 3 canonical products healthy and cadenced |
| `/usr/bin/python3.14 tests/test_audit_yield.py` | `PASS` | `6.53s` | yield-artifact guard clean: one rounding site, row == scorecard at 14/17 |
| `/usr/bin/python3.14 tests/test_audit_gate_note.py` | `PASS` | `0.5s` | gate-note guard clean: a FAIL note carries its population, PASS keeps its summary |
| `/usr/bin/python3.14 tests/test_close_board_recorded.py` | `PASS` | `0.12s` | close-board gate: excused — 9 row(s) (2 admitted, 7 pre-invariant); this is a visible debt, not a clean run |
| `/usr/bin/python3.14 tests/test_kit_pin.py` | `PASS` | `1.45s` | kit pin passed |
| `/usr/bin/python3.14 -m pytest tests/test_cron_thinness.py -q` | `PASS` | `21.27s` | 16 passed in 0.38s |
| `/usr/bin/python3.14 tests/test_patrol_host_state.py` | `PASS` | `6.24s` | patrol-runner gate passed: 109 check(s) |
| `/usr/bin/python3.14 tests/test_ledger.py` | `PASS` | `72.9s` | ledger gate passed |
| `/usr/bin/python3.14 tests/test_audit_stamp_duration.py` | `PASS` | `9.21s` | audit-stamp duration guard clean: the stamp site carries no literal, and the row tracks the gate work it describes |
| `/usr/bin/python3.14 tests/test_audit_cadence_record.py` | `PASS` | `0.52s` | and the printed record describes the subject PREDICATE (#159) |
| `/usr/bin/python3.14 tests/test_audit_cadence_subject.py` | `PASS` | `0.24s` | variant spelling (#159) recognised, `self-audit-class` rejected, hole still bites |
| `/usr/bin/python3.14 tests/test_audit_cadence_artifact_crosscheck.py` | `PASS` | `0.26s` | PASS: every artifact date carries a self-audit run row, or a dated exemption |
| `/usr/bin/python3.14 tests/test_ledger_commit_cites_no_rows.py` | `PASS` | `25.91s` | ledger clause: excused — 3 exempted violation(s) in cd2a3f874a39aacd0073b88372c15e23a4cbc3cd..HEAD; this is a visible debt, not a clean run |
| `/usr/bin/python3.14 tests/test_audit_cadence_own_run.py` | `PASS` | `0.15s` | PASS: own-run excluded, prior hole still named, parameter present and passed |
| `/usr/bin/python3.14 -m pytest tests/test_audit_interpreter_binding.py -q` | `PASS` | `17.22s` | 6 passed in 1.37s |
| `/usr/bin/python3.14 -m pytest tests/test_audit_requires_python_preflight.py -q` | `PASS` | `12.75s` | 6 passed in 1.08s |
| `/usr/bin/python3.14 -m pytest tests/test_audit_env_classification.py -q` | `PASS` | `13.57s` | 8 passed in 3.15s |
| `/usr/bin/python3.14 tests/test_audit_tree_condition.py` | `PASS` | `1.11s` | audit tree condition: passed |
| `/usr/bin/python3.14 tests/test_board_intake_recorded.py` | `PASS` | `0.22s` | board-intake gate passed: 15 check(s) |
| `/usr/bin/python3.14 tests/test_brain_metrics.py` | `PASS` | `0.65s` | brain-metrics gate passed (6 probe(s), 0 failure(s)) |
| `/usr/bin/python3.14 tests/test_close_row_revision.py` | `PASS` | `1.66s` | close-row revision gate: clean — 81 post-boundary close row(s) declared a revision IN THE CANONICAL RUN and every one RESOLVES; 1 retired by |
| `/usr/bin/python3.14 tests/test_commit_close_keyword.py` | `PASS` | `0.09s` | close-keyword gate: clean — 9 closing spellings refused, 9 lawful subjects accepted, body surface covered, bite proof intact |
| `/usr/bin/python3.14 tests/test_commit_pair_hook.py` | `PASS` | `0.16s` | commit pair hook ok: tools/hooks/pre-commit shipped, executable, wired via core.hooksPath |
| `/usr/bin/python3.14 tests/test_commit_session_trailer.py` | `PASS` | `0.63s` | commit session trailer passed |
| `/usr/bin/python3.14 tests/test_criteria_count.py` | `PASS` | `0.7s` | criteria count: clean — 0 claim(s) over 46 file(s) all read 19 criteria / 6 families (source: the shipped pin v0.5 — no local rubric in this |
| `/usr/bin/python3.14 tests/test_docs_sync.py` | `PASS` | `0.11s` | no TEMPLATE/docs tree — nothing to pair |
| `/usr/bin/python3.14 tests/test_duplicate_prose.py` | `PASS` | `0.07s` | duplicate-prose gate ok: 0 law surface(s), 0 prose block(s), no duplicates |
| `/usr/bin/python3.14 tests/test_gate_fixtures_closure.py` | `PASS` | `7.08s` | gate-fixture closure gate passed |
| `/usr/bin/python3.14 tests/test_gate_invocation_mode.py` | `PASS` | `0.33s` | SKIP no registry/gates.json in this tree — the invocation-mode declaration has not been adopted here, so there is nothing to assert against. |
| `/usr/bin/python3.14 tests/test_gate_registration.py` | `PASS` | `5.35s` | gate registry passed |
| `/usr/bin/python3.14 tests/test_ledger_identity.py` | `PASS` | `1.9s` | ledger identity: SKIPPED — this tree carries a lane resolver and a manifest, but its Worker lane does not resolve to a live session, so no s |
| `/usr/bin/python3.14 tests/test_ledger_index.py` | `PASS` | `1.09s` | ledger index gate clean |
| `/usr/bin/python3.14 tests/test_ledger_no_shrink.py` | `PASS` | `9.6s` | ledger no-shrink: clean — no commit removes a row identity |
| `/usr/bin/python3.14 tests/test_publish.py` | `PASS` | `2.43s` | PASS an in-sync round renders as in-sync |
| `/usr/bin/python3.14 tests/test_questions.py` | `PASS` | `700.0s` | the questions selftest passed: 520 check(s) examined |
| `/usr/bin/python3.14 tests/test_registry.py` | `PASS` | `0.28s` | SKIPPED — no docs/factory-registry.md, registry/index.json in this tree, so there is no registry to judge. Both are FACTORY DATA generated f |
| `/usr/bin/python3.14 tests/test_roadmap_transition.py` | `PASS` | `1.48s` | roadmap transition: clean — fresh/empty/malformed/incomplete all RED, filled declaration GREEN, skeleton-copy not GREEN; this tree: declared |
| `/usr/bin/python3.14 tests/test_skill_version_contract.py` | `PASS` | `0.15s` | skill-version contract: SKIP — no tracked law file matches skills/*/SKILL.md, TEMPLATE/SKILL.md.tmpl — nothing to judge |
| `/usr/bin/python3.14 tests/test_subject_anchor.py` | `PASS` | `0.25s` | subject-anchor gate passed (11 probe(s), 0 failure(s)); the live fleet reading above is REPORTED, never judged. |
| `/usr/bin/python3.14 tests/test_template_integrity.py` | `PASS` | `0.1s` | no TEMPLATE/ tree — the factory is the instantiation, nothing to classify |
| `/usr/bin/python3.14 -m pytest tests/test_binding_mechanism_exists.py -q` | `PASS` | `30.17s` | 3 passed in 4.38s |
| `/usr/bin/python3.14 -m pytest tests/test_close_telemetry_provenance.py -q` | `PASS` | `19.32s` | 18 passed in 0.33s |
| `/usr/bin/python3.14 -m pytest tests/test_hygiene_inflight.py -q` | `PASS` | `29.18s` | 7 passed in 17.94s |
| `/usr/bin/python3.14 -m pytest tests/test_hygiene_namespace.py -q` | `PASS` | `29.82s` | 14 passed in 17.92s |
| `/usr/bin/python3.14 -m pytest tests/test_hygiene_build_residue.py -q` | `PASS` | `11.86s` | 9 passed in 0.67s |
| `/usr/bin/python3.14 -m pytest tests/test_hygiene_declaration_sweep.py -q` | `PASS` | `12.69s` | 13 passed in 1.06s |
| `/usr/bin/python3.14 -m pytest tests/test_hygiene_stale_dirs.py -q` | `PASS` | `12.68s` | 14 passed in 0.81s |
| `/usr/bin/python3.14 -m pytest tests/test_hygiene_branch_occupancy.py -q` | `PASS` | `11.7s` | 9 passed in 0.34s |
| `/usr/bin/python3.14 -m pytest tests/test_insights_gate_recorded.py -q` | `PASS` | `11.16s` | 24 passed, 1 skipped in 0.19s |
| `/usr/bin/python3.14 -m pytest tests/test_ledger_close_preflight.py -q` | `PASS` | `13.97s` | 7 passed in 2.72s |
| `/usr/bin/python3.14 -m pytest tests/test_ledger_event_authorized.py -q` | `PASS` | `11.87s` | 4 passed in 0.26s |
| `/usr/bin/python3.14 -m pytest tests/test_registry_attest.py -q` | `PASS` | `11.17s` | 9 passed in 0.18s |
| `/usr/bin/python3.14 -m pytest tests/test_registry_render.py -q` | `PASS` | `12.0s` | 29 passed in 0.79s |
| `/usr/bin/python3.14 -m pytest tests/test_score_gate_recorded.py -q` | `PASS` | `11.0s` | 24 passed, 1 skipped in 0.17s |
| `/usr/bin/python3.14 -m pytest tests/test_self_audit_push_gate.py -q` | `PASS` | `12.36s` | 5 passed in 1.15s |
| `/usr/bin/python3.14 -m pytest tests/test_audit_rates.py -q` | `PASS` | `15.39s` | 19 passed in 5.71s |

---

## 3. Calibration Spot-Check (Sampled Task)

- **Sampled Subject:** `#164`
- **Intake Recorded:** `YES`
- **Claim Lock Recorded:** `YES`
- **Close Verified:** `YES`
- **Sequence Integrity:** `VERIFIED`
