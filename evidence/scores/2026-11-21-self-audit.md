# Operational Process Self-Audit — 2026-11-21

> **Verdict:** `DEGRADED` · Process 3 (Internal Self-Audit)

> **Ledger read at:** `2026-11-21T05:51:03Z` (529 events) — this artifact describes the ledger AS OF THAT INSTANT, before this run's own row was appended. It is a START-OF-RUN SNAPSHOT, not the ledger's state when you read it: the run row this audit writes lands after the read, and concurrent lanes append throughout the gate window (#142, ruling n=919).

---

## 1. Process Health & Delivery Telemetry

| Metric | Value | Reference / Derivation |
|---|---|---|
| **Total Ledger Events** | `529` | Continuous ledger sequence |
| **Closed Tasks** | `84` | Distinct closed subjects; `90` close rows (6 re-close of a re-opened subject) |
| **First-Pass Yield** | `65.5%` | 36 accepted runs ÷ 55 runs stating an outcome (coverage: 55 of 186 run rows) |
| **Rework Entries** | `11` | Defect count recorded in rework.md |
| **Rework Declarations** | `39/90` (43.3%) | Close rows declaring a `rework=#N` or `rework=none` disposition — canonical trailer read by position: rework=#N 2; rework=none 37; undeclared 51 (printed, never gated — n=386 clause 5) |
| **Rework Share** | `11.6%` | 11 rework entries ÷ (84 closed subjects + 11 rework entries) |
| **Rework per Close** | `13.1%` | 11 rework entries ÷ 84 closed subjects |
| **Change Fail Rate** | `8.3%` | 7 closes that produced a rework entry ÷ 84 closed work units — **linkage coverage `11/11`** entries carry a determinate Subject (100.0%); read the rate only against this coverage |
| **Avg Task Lead Time** | `310237.2s` | Mean intake→close duration over 87 sampled subjects |
| **Total Inference Cost** | `$9231.8359` | Tracked cost across ledger task telemetry — summed from each row's canonical TRAILER only, by position and across every event (issue #90) |
| **Telemetry Outside the Trailer** | `11` row(s) | excluded from the totals above BY DESIGN: these rows carry a telemetry-shaped token outside the canonical trailer — n=141 (claim), n=239 (run), n=365 (run), n=415 (run), n=418 (run), n=419 (run), n=421 (run), n=424 (run), n=425 (run), n=451 (run), n=460 (run). Read each as prose, not as spend |
| **Avg Cost / Closed Task** | `$109.9028` | $9231.8359 total cost ÷ 84 distinct closed subjects |
| **Cost / Successful Task** | `n/a` | $9231.8359 total cost ÷ 0 accepted closed subjects (coverage: 0 of 90 close rows state an outcome) |
| **Total Tokens (In/Out)** | `985186449 / 28908257337` | Cumulative prompt and completion tokens |
| **Cadence Status** | `HOLE 2026-11-19` | Last run: 23.1h ago · record read: evidence/ledger.jsonl run/score rows with subject internal-self-audit · latest artifact: 2026-11-20 |

---

## 2. Mechanical Gate Verification

| Gate / Command | Outcome | Duration | Notes |
|---|---|---|---|
| `/usr/bin/python3 tools/ledger.py verify` | `PASS` | `0.72s` | renumber-paired claim: n=148 #74 — closed by a row filed under #107, the number this item carries since the declared board renumber; the pai |
| `/usr/bin/python3 tests/test_ontology.py` | `PASS` | `3.29s` | vocabulary clean: 13 canonical term(s), 2 banned term(s), 115 file(s) scanned |
| `/usr/bin/python3 tests/test_rework.py` | `PASS` | `0.08s` | rework gate passed |
| `/usr/bin/python3 tests/test_rework_relative_revision.py` | `PASS` | `0.14s` | rework relative-revision gate passed |
| `/usr/bin/python3 tests/test_rework_declared_landed.py` | `PASS` | `0.25s` | rework declared-to-landed gate passed — 2 declaration(s) in scope, all resolved to a landed entry |
| `/usr/bin/python3 tests/test_single_writer.py` | `PASS` | `0.11s` | single-writer state clean: 7 declared surface(s) audited, locking verified |
| `/usr/bin/python3 tests/test_ledger_schema.py` | `PASS` | `0.46s` | ledger schema clean, 4 excused: 529 row(s) audited, all domain invariants & schemas verified |
| `/usr/bin/python3 tools/hygiene.py --clean --namespace ai-antispam` | `PASS` | `11.96s` | hygiene cleanup complete: 0 scratch item(s) reaped |
| `/usr/bin/python3 tools/hygiene.py --audit --namespace ai-antispam` | `PASS` | `0.34s` | hygiene audit clean: 0 stale scratch items in /tmp/ai-antispam-*, 0 stranded dirty paths (grace 60m), 0 advisory item(s), working tree clean |
| `/usr/bin/python3 tools/roadmap.py --audit` | `PASS` | `0.13s` | Product & cadence roadmap clean: 3 canonical products healthy and cadenced |
| `/usr/bin/python3 tests/test_audit_yield.py` | `PASS` | `4.14s` | yield-artifact guard clean: one rounding site, row == scorecard at 14/17 |
| `/usr/bin/python3 tests/test_audit_gate_note.py` | `PASS` | `0.13s` | gate-note guard clean: a FAIL note carries its population, PASS keeps its summary |
| `/usr/bin/python3 tests/test_close_board_recorded.py` | `PASS` | `0.09s` | close-board gate: excused — 9 row(s) (2 admitted, 7 pre-invariant); this is a visible debt, not a clean run |
| `/usr/bin/python3 tests/test_kit_pin.py` | `PASS` | `1.08s` | kit pin passed |
| `/usr/bin/python3 -m pytest tests/test_cron_thinness.py -q` | `PASS` | `28.7s` | 16 passed in 0.67s |
| `/usr/bin/python3 tests/test_patrol_host_state.py` | `PASS` | `10.49s` | patrol-runner gate passed: 109 check(s) |
| `/usr/bin/python3 tests/test_ledger.py` | `PASS` | `63.51s` | ledger gate passed |
| `/usr/bin/python3 tests/test_audit_stamp_duration.py` | `PASS` | `8.16s` | audit-stamp duration guard clean: the stamp site carries no literal, and the row tracks the gate work it describes |
| `/usr/bin/python3 tests/test_audit_cadence_record.py` | `PASS` | `0.18s` | PASS: 4 fixture branches, 3 distinct labels, report row carries the state and the record |
| `/usr/bin/python3 tests/test_audit_cadence_subject.py` | `PASS` | `0.21s` | PASS: hole named with its days, unrelated row rejected by the clock, predicate bound |
| `/usr/bin/python3 tests/test_ledger_commit_cites_no_rows.py` | `PASS` | `16.91s` | ledger clause: excused — 3 exempted violation(s) in cd2a3f874a39aacd0073b88372c15e23a4cbc3cd..HEAD; this is a visible debt, not a clean run |
| `/usr/bin/python3 tests/test_audit_cadence_own_run.py` | `PASS` | `0.15s` | PASS: own-run excluded, prior hole still named, parameter present and passed |
| `/usr/bin/python3 tests/test_audit_tree_condition.py` | `PASS` | `0.58s` | audit tree condition: passed |
| `/usr/bin/python3 tests/test_board_intake_recorded.py` | `PASS` | `0.16s` | board-intake gate passed: 15 check(s) |
| `/usr/bin/python3 tests/test_brain_metrics.py` | `PASS` | `0.43s` | brain-metrics gate passed (6 probe(s), 0 failure(s)) |
| `/usr/bin/python3 tests/test_close_row_revision.py` | `PASS` | `2.23s` | close-row revision gate: clean — 74 post-boundary close row(s) declared a revision IN THE CANONICAL RUN and every one RESOLVES; 1 retired by |
| `/usr/bin/python3 tests/test_commit_close_keyword.py` | `PASS` | `0.08s` | close-keyword gate: clean — 9 closing spellings refused, 9 lawful subjects accepted, body surface covered, bite proof intact |
| `/usr/bin/python3 tests/test_commit_pair_hook.py` | `PASS` | `0.18s` | commit pair hook ok: tools/hooks/pre-commit shipped, executable, wired via core.hooksPath |
| `/usr/bin/python3 tests/test_commit_session_trailer.py` | `PASS` | `0.53s` | commit session trailer passed |
| `/usr/bin/python3 tests/test_criteria_count.py` | `PASS` | `0.7s` | criteria count: clean — 0 claim(s) over 46 file(s) all read 19 criteria / 6 families (source: the shipped pin v0.5 — no local rubric in this |
| `/usr/bin/python3 tests/test_docs_sync.py` | `PASS` | `0.06s` | no TEMPLATE/docs tree — nothing to pair |
| `/usr/bin/python3 tests/test_duplicate_prose.py` | `PASS` | `0.06s` | duplicate-prose gate ok: 0 law surface(s), 0 prose block(s), no duplicates |
| `/usr/bin/python3 tests/test_gate_fixtures_closure.py` | `PASS` | `3.85s` | gate-fixture closure gate passed |
| `/usr/bin/python3 tests/test_gate_invocation_mode.py` | `PASS` | `0.24s` | SKIP no registry/gates.json in this tree — the invocation-mode declaration has not been adopted here, so there is nothing to assert against. |
| `/usr/bin/python3 tests/test_gate_registration.py` | `PASS` | `3.51s` | gate registry passed |
| `/usr/bin/python3 tests/test_ledger_identity.py` | `PASS` | `1.06s` | ledger identity: SKIPPED — this tree carries a lane resolver and a manifest, but its Worker lane does not resolve to a live session, so no s |
| `/usr/bin/python3 tests/test_ledger_index.py` | `PASS` | `0.72s` | ledger index gate clean |
| `/usr/bin/python3 tests/test_ledger_no_shrink.py` | `PASS` | `6.37s` | ledger no-shrink: clean — no commit removes a row identity |
| `/usr/bin/python3 tests/test_publish.py` | `PASS` | `2.37s` | PASS an in-sync round renders as in-sync |
| `/usr/bin/python3 tests/test_questions.py` | `PASS` | `605.15s` | the questions selftest passed: 520 check(s) examined |
| `/usr/bin/python3 tests/test_registry.py` | `PASS` | `0.36s` | SKIPPED — no docs/factory-registry.md, registry/index.json in this tree, so there is no registry to judge. Both are FACTORY DATA generated f |
| `/usr/bin/python3 tests/test_roadmap_transition.py` | `PASS` | `1.16s` | roadmap transition: clean — fresh/empty/malformed/incomplete all RED, filled declaration GREEN, skeleton-copy not GREEN; this tree: declared |
| `/usr/bin/python3 tests/test_skill_version_contract.py` | `PASS` | `0.24s` | skill-version contract: SKIP — no tracked law file matches skills/*/SKILL.md, TEMPLATE/SKILL.md.tmpl — nothing to judge |
| `/usr/bin/python3 tests/test_subject_anchor.py` | `PASS` | `0.18s` | subject-anchor gate passed (11 probe(s), 0 failure(s)); the live fleet reading above is REPORTED, never judged. |
| `/usr/bin/python3 tests/test_template_integrity.py` | `PASS` | `0.09s` | no TEMPLATE/ tree — the factory is the instantiation, nothing to classify |
| `/usr/bin/python3 -m pytest tests/test_binding_mechanism_exists.py -q` | `PASS` | `17.64s` | 3 passed in 3.13s |
| `/usr/bin/python3 -m pytest tests/test_close_telemetry_provenance.py -q` | `PASS` | `12.41s` | 18 passed in 0.20s |
| `/usr/bin/python3 -m pytest tests/test_hygiene_inflight.py -q` | `PASS` | `11.95s` | 7 passed in 2.73s |
| `/usr/bin/python3 -m pytest tests/test_hygiene_namespace.py -q` | `PASS` | `12.67s` | 14 passed in 3.46s |
| `/usr/bin/python3 -m pytest tests/test_hygiene_build_residue.py -q` | `PASS` | `10.99s` | 9 passed in 0.60s |
| `/usr/bin/python3 -m pytest tests/test_hygiene_declaration_sweep.py -q` | `PASS` | `8.89s` | 13 passed in 0.51s |
| `/usr/bin/python3 -m pytest tests/test_hygiene_stale_dirs.py -q` | `PASS` | `10.04s` | 14 passed in 0.62s |
| `/usr/bin/python3 -m pytest tests/test_hygiene_branch_occupancy.py -q` | `PASS` | `9.34s` | 9 passed in 0.09s |
| `/usr/bin/python3 -m pytest tests/test_insights_gate_recorded.py -q` | `PASS` | `10.04s` | 24 passed, 1 skipped in 0.18s |
| `/usr/bin/python3 -m pytest tests/test_ledger_close_preflight.py -q` | `PASS` | `11.51s` | 7 passed in 2.04s |
| `/usr/bin/python3 -m pytest tests/test_ledger_event_authorized.py -q` | `PASS` | `10.41s` | 4 passed in 0.36s |
| `/usr/bin/python3 -m pytest tests/test_registry_attest.py -q` | `PASS` | `9.64s` | 9 passed in 0.12s |
| `/usr/bin/python3 -m pytest tests/test_registry_render.py -q` | `PASS` | `10.06s` | 29 passed in 0.16s |
| `/usr/bin/python3 -m pytest tests/test_score_gate_recorded.py -q` | `PASS` | `9.71s` | 24 passed, 1 skipped in 0.16s |
| `/usr/bin/python3 -m pytest tests/test_self_audit_push_gate.py -q` | `PASS` | `11.1s` | 5 passed in 0.90s |
| `/usr/bin/python3 -m pytest tests/test_audit_rates.py -q` | `PASS` | `13.83s` | 19 passed in 3.92s |

---

## 3. Calibration Spot-Check (Sampled Task)

- **Sampled Subject:** `#160`
- **Intake Recorded:** `YES`
- **Claim Lock Recorded:** `YES`
- **Close Verified:** `YES`
- **Sequence Integrity:** `VERIFIED`

---

## 4. Cadence Hole `2026-11-19` — diagnosed, record-only

The cadence leg reported `HOLE 2026-11-19`. **The audit did run that day** — the artifact
`evidence/scores/2026-11-19-self-audit.md` exists, verdict `PASSED`, committed `be6674f`.
The hole is in the RECORD, not the run.

**Cause, measured this turn.** `tools/audit.py:1749` binds `SELF_AUDIT_SUBJECT =
"internal-self-audit"` and the cadence leg matches it as an exact string
(`LEDGER_RECORD`, `:1739`). The `--stamp` path writes that literal itself (`:2275`), so a
stamped run can never miss. But the cron route **forbids `--stamp`**, so the runner writes
the run row by hand — and `docs/pacemaker-procedures.md` carries **no `--subject` literal**
(`grep -n "internal-self-audit\|--subject" docs/pacemaker-procedures.md` → 0 hits). The
subject is therefore free text on the only lawful path, and exact-match on the reader.

**Population.** 64 run rows mention self-audit; **63 carry `internal-self-audit`** and
exactly **one** deviates: `n=488 run hq | "daily self-audit 2026-11-19"`. That single row is
the entire hole. `n=519 "self-audit-class"` is a different subject (Triage's), not a miss.

**Why this is the same class as #147 and #149.** The instrument was correct and the operator
was honest; the failure was that the one path a cron session is allowed to take does not
carry the literal the reader requires, and nothing prints the mismatch — the hole surfaces
only as a verdict downgrade hours later, attributed to the wrong cause (a missed run).

**Remedy owed (not taken here).** Either the procedure states the literal explicitly in its
run-row command, or the cadence predicate matches a normalized prefix. The first is smaller
and keeps the reader strict; the second survives a lane inventing a new phrasing. Filed on
the board, not patched in this run — the artifact is a snapshot, and a gate change mid-run
would invalidate the gate table above.
