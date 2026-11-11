# Operational Process Self-Audit — 2026-11-11

> **Verdict:** `DEGRADED` · Process 3 (Internal Self-Audit)

> **Ledger read at:** `2026-11-11T05:51:11Z` (417 events) — this artifact describes the ledger AS OF THAT INSTANT, before this run's own row was appended. It is a START-OF-RUN SNAPSHOT, not the ledger's state when you read it: the run row this audit writes lands after the read, and concurrent lanes append throughout the gate window (#142, ruling n=919).

---

## 1. Process Health & Delivery Telemetry

| Metric | Value | Reference / Derivation |
|---|---|---|
| **Total Ledger Events** | `417` | Continuous ledger sequence |
| **Closed Tasks** | `66` | Distinct closed subjects; `71` close rows (5 re-close of a re-opened subject) |
| **First-Pass Yield** | `64.2%` | 34 accepted runs ÷ 53 runs stating an outcome (coverage: 53 of 139 run rows) |
| **Rework Entries** | `10` | Defect count recorded in rework.md |
| **Rework Declarations** | `20/71` (28.2%) | Close rows declaring a `rework=#N` or `rework=none` disposition — canonical trailer read by position: rework=#N 1; rework=none 19; undeclared 51 (printed, never gated — n=386 clause 5) |
| **Rework Share** | `13.2%` | 10 rework entries ÷ (66 closed subjects + 10 rework entries) |
| **Rework per Close** | `15.2%` | 10 rework entries ÷ 66 closed subjects |
| **Change Fail Rate** | `9.1%` | 6 closes that produced a rework entry ÷ 66 closed work units — **linkage coverage `10/10`** entries carry a determinate Subject (100.0%); read the rate only against this coverage |
| **Avg Task Lead Time** | `167671.1s` | Mean intake→close duration over 68 sampled subjects |
| **Total Inference Cost** | `$7482.1064` | Tracked cost across ledger task telemetry — summed from each row's canonical TRAILER only, by position and across every event (issue #90) |
| **Telemetry Outside the Trailer** | `4` row(s) | excluded from the totals above BY DESIGN: these rows carry a telemetry-shaped token outside the canonical trailer — n=141 (claim), n=239 (run), n=365 (run), n=415 (run). Read each as prose, not as spend |
| **Avg Cost / Closed Task** | `$113.3652` | $7482.1064 total cost ÷ 66 distinct closed subjects |
| **Cost / Successful Task** | `n/a` | $7482.1064 total cost ÷ 0 accepted closed subjects (coverage: 0 of 71 close rows state an outcome) |
| **Total Tokens (In/Out)** | `770532776 / 22734742351` | Cumulative prompt and completion tokens |
| **Cadence Status** | `HELD` | Last run: 23.7h ago · record read: evidence/ledger.jsonl run/score rows with subject internal-self-audit · latest artifact: 2026-11-10 |

---

## 2. Mechanical Gate Verification

| Gate / Command | Outcome | Duration | Notes |
|---|---|---|---|
| `/usr/bin/python3 tools/ledger.py verify` | `PASS` | `0.46s` | renumber-paired claim: n=148 #74 — closed by a row filed under #107, the number this item carries since the declared board renumber; the pai |
| `/usr/bin/python3 tests/test_ontology.py` | `PASS` | `0.47s` | vocabulary clean: 13 canonical term(s), 2 banned term(s), 107 file(s) scanned |
| `/usr/bin/python3 tests/test_rework.py` | `PASS` | `0.09s` | rework gate passed |
| `/usr/bin/python3 tests/test_rework_relative_revision.py` | `PASS` | `0.12s` | rework relative-revision gate passed |
| `/usr/bin/python3 tests/test_rework_declared_landed.py` | `PASS` | `0.19s` | rework declared-to-landed gate passed — 1 declaration(s) in scope, all resolved to a landed entry |
| `/usr/bin/python3 tests/test_single_writer.py` | `PASS` | `0.08s` | single-writer state clean: 7 declared surface(s) audited, locking verified |
| `/usr/bin/python3 tests/test_ledger_schema.py` | `PASS` | `0.32s` | ledger schema clean, 4 excused: 417 row(s) audited, all domain invariants & schemas verified |
| `/usr/bin/python3 tools/hygiene.py --clean --namespace ai-antispam` | `PASS` | `0.81s` | hygiene cleanup complete: 0 scratch item(s) reaped |
| `/usr/bin/python3 tools/hygiene.py --audit --namespace ai-antispam` | `PASS` | `0.46s` | hygiene audit clean: 0 stale scratch items in /tmp/ai-antispam-*, 0 stranded dirty paths (grace 60m), 0 advisory item(s), working tree clean |
| `/usr/bin/python3 tools/roadmap.py --audit` | `PASS` | `0.16s` | Product & cadence roadmap clean: 3 canonical products healthy and cadenced |
| `/usr/bin/python3 tests/test_audit_yield.py` | `PASS` | `5.27s` | yield-artifact guard clean: one rounding site, row == scorecard at 14/17 |
| `/usr/bin/python3 tests/test_audit_gate_note.py` | `PASS` | `0.17s` | gate-note guard clean: a FAIL note carries its population, PASS keeps its summary |
| `/usr/bin/python3 tests/test_close_board_recorded.py` | `PASS` | `0.09s` | close-board gate: excused — 9 row(s) (2 admitted, 7 pre-invariant); this is a visible debt, not a clean run |
| `/usr/bin/python3 tests/test_kit_pin.py` | `FAIL` | `0.17s` | kit pin FAILED: 1 check(s) |
| `/usr/bin/python3 -m pytest tests/test_cron_thinness.py -q` | `PASS` | `11.78s` | 16 passed in 0.14s |
| `/usr/bin/python3 tests/test_patrol_host_state.py` | `PASS` | `3.3s` | patrol-runner gate passed: 109 check(s) |
| `/usr/bin/python3 tests/test_ledger.py` | `PASS` | `60.49s` | ledger gate passed |
| `/usr/bin/python3 tests/test_audit_stamp_duration.py` | `PASS` | `8.02s` | audit-stamp duration guard clean: the stamp site carries no literal, and the row tracks the gate work it describes |
| `/usr/bin/python3 tests/test_audit_cadence_record.py` | `PASS` | `0.13s` | PASS: 4 fixture branches, 3 distinct labels, report row carries the state and the record |
| `/usr/bin/python3 tests/test_audit_cadence_subject.py` | `PASS` | `0.14s` | PASS: hole named with its days, unrelated row rejected by the clock, predicate bound |
| `/usr/bin/python3 tests/test_ledger_commit_cites_no_rows.py` | `PASS` | `0.89s` | ledger clause: excused — 3 exempted violation(s) in cd2a3f874a39aacd0073b88372c15e23a4cbc3cd..HEAD; this is a visible debt, not a clean run |
| `/usr/bin/python3 tests/test_audit_cadence_own_run.py` | `PASS` | `0.15s` | PASS: own-run excluded, prior hole still named, parameter present and passed |
| `/usr/bin/python3 tests/test_audit_tree_condition.py` | `PASS` | `0.59s` | audit tree condition: passed |
| `/usr/bin/python3 tests/test_board_intake_recorded.py` | `PASS` | `0.16s` | board-intake gate passed: 15 check(s) |
| `/usr/bin/python3 tests/test_brain_metrics.py` | `PASS` | `0.3s` | brain-metrics gate passed (6 probe(s), 0 failure(s)) |
| `/usr/bin/python3 tests/test_close_row_revision.py` | `PASS` | `0.76s` | close-row revision gate: clean — 55 post-boundary close row(s) declared a revision IN THE CANONICAL RUN and every one RESOLVES; 1 retired by |
| `/usr/bin/python3 tests/test_commit_close_keyword.py` | `PASS` | `0.08s` | close-keyword gate: clean — 9 closing spellings refused, 9 lawful subjects accepted, body surface covered, bite proof intact |
| `/usr/bin/python3 tests/test_commit_pair_hook.py` | `PASS` | `0.14s` | commit pair hook ok: tools/hooks/pre-commit shipped, executable, wired via core.hooksPath |
| `/usr/bin/python3 tests/test_commit_session_trailer.py` | `PASS` | `0.62s` | commit session trailer passed |
| `/usr/bin/python3 tests/test_criteria_count.py` | `PASS` | `0.15s` | criteria count: clean — 0 claim(s) over 46 file(s) all read 19 criteria / 6 families (source: the shipped pin v0.5 — no local rubric in this |
| `/usr/bin/python3 tests/test_docs_sync.py` | `PASS` | `0.07s` | no TEMPLATE/docs tree — nothing to pair |
| `/usr/bin/python3 tests/test_duplicate_prose.py` | `PASS` | `0.08s` | duplicate-prose gate ok: 0 law surface(s), 0 prose block(s), no duplicates |
| `/usr/bin/python3 tests/test_gate_fixtures_closure.py` | `PASS` | `4.96s` | gate-fixture closure gate passed |
| `/usr/bin/python3 tests/test_gate_invocation_mode.py` | `PASS` | `0.35s` | SKIP no registry/gates.json in this tree — the invocation-mode declaration has not been adopted here, so there is nothing to assert against. |
| `/usr/bin/python3 tests/test_gate_registration.py` | `FAIL` | `2.76s` | examined 79 module(s); 60 declared a gate, 53 tests/ paths are in the run list, 4 run-list entr(ies) are tool invocations (<sys.executable>  |
| `/usr/bin/python3 tests/test_ledger_identity.py` | `PASS` | `0.17s` | ledger identity: SKIPPED — this tree carries a lane resolver and a manifest, but its Worker lane does not resolve to a live session, so no s |
| `/usr/bin/python3 tests/test_ledger_index.py` | `PASS` | `0.86s` | ledger index gate clean |
| `/usr/bin/python3 tests/test_ledger_no_shrink.py` | `PASS` | `0.86s` | ledger no-shrink: clean — no commit removes a row identity |
| `/usr/bin/python3 tests/test_publish.py` | `PASS` | `1.5s` | PASS an in-sync round renders as in-sync |
| `/usr/bin/python3 tests/test_questions.py` | `PASS` | `817.12s` | the questions selftest passed: 520 check(s) examined |
| `/usr/bin/python3 tests/test_registry.py` | `PASS` | `0.77s` | SKIPPED — no docs/factory-registry.md, registry/index.json in this tree, so there is no registry to judge. Both are FACTORY DATA generated f |
| `/usr/bin/python3 tests/test_roadmap_transition.py` | `PASS` | `3.17s` | roadmap transition: clean — fresh/empty/malformed/incomplete all RED, filled declaration GREEN, skeleton-copy not GREEN; this tree: declared |
| `/usr/bin/python3 tests/test_skill_version_contract.py` | `PASS` | `0.32s` | skill-version contract: SKIP — no tracked law file matches skills/*/SKILL.md, TEMPLATE/SKILL.md.tmpl — nothing to judge |
| `/usr/bin/python3 tests/test_subject_anchor.py` | `PASS` | `0.71s` | subject-anchor gate passed (11 probe(s), 0 failure(s)); the live fleet reading above is REPORTED, never judged. |
| `/usr/bin/python3 tests/test_template_integrity.py` | `PASS` | `0.39s` | no TEMPLATE/ tree — the factory is the instantiation, nothing to classify |
| `/usr/bin/python3 -m pytest tests/test_binding_mechanism_exists.py -q` | `PASS` | `31.07s` | 3 passed in 3.67s |
| `/usr/bin/python3 -m pytest tests/test_close_telemetry_provenance.py -q` | `PASS` | `20.35s` | 18 passed in 0.26s |
| `/usr/bin/python3 -m pytest tests/test_hygiene_inflight.py -q` | `PASS` | `24.53s` | 7 passed in 3.79s |
| `/usr/bin/python3 -m pytest tests/test_hygiene_namespace.py -q` | `PASS` | `27.84s` | 14 passed in 6.52s |
| `/usr/bin/python3 -m pytest tests/test_insights_gate_recorded.py -q` | `PASS` | `26.89s` | 24 passed, 1 skipped in 0.48s |
| `/usr/bin/python3 -m pytest tests/test_ledger_close_preflight.py -q` | `PASS` | `33.41s` | 7 passed in 7.53s |
| `/usr/bin/python3 -m pytest tests/test_registry_attest.py -q` | `PASS` | `25.72s` | 9 passed in 0.15s |
| `/usr/bin/python3 -m pytest tests/test_registry_render.py -q` | `PASS` | `31.1s` | 29 passed in 0.31s |
| `/usr/bin/python3 -m pytest tests/test_score_gate_recorded.py -q` | `PASS` | `29.37s` | 24 passed, 1 skipped in 0.22s |
| `/usr/bin/python3 -m pytest tests/test_self_audit_push_gate.py -q` | `PASS` | `20.97s` | 5 passed in 0.71s |
| `/usr/bin/python3 -m pytest tests/test_audit_rates.py -q` | `PASS` | `27.67s` | 19 passed in 6.43s |

### 2.1 Failing gate output (full)

**`/usr/bin/python3 tests/test_kit_pin.py`** -- FAIL, rc=1, 0.17s

```
kit pin — a factory's tree against the pin it vendored (plan 2646d31a step 8)
  kit pin — /root/ai-antispam
    pin registry/kit.json: version 7e2a2281c13c, 137 path(s) declared
    judged 96 carried path(s); 2 factory-class path(s) excluded by class; 17 declared exempt
  FAIL — carried paths that diverge from YOUR OWN pin and are not declared exempt:
    tools/hygiene.py  class=standalone  pin=425a16526e78 tree=530199b6612f
    Two lawful answers, and the choice is the factory's: take the update (python3 tools/kit_deliver.py --to . --dry-run), or declare the fork in registry/kit-exemptions.json with a reason.
  FAIL  the live tree is judged and green —   Two lawful answers, and the choice is the factory's: take the update (python3 tools/kit_deliver.py --to . --
  PASS  the live population is not empty — the green examined real paths — judged 96 carried path(s); 2 factory-class path(s) excluded by class; 17 declared exempt
  SKIPPED  the fixture arms — their inputs are TEMPLATE/-only and this tree
           carries none of them. The live arm above is this gate's verdict here.
  PASS  #522 a class the PIN declares but this READER does not know names the READER and the port — the pin /tmp/tmp6koxn7qm/kit.json declares class(es) ['brand-new-class'], which THIS READER does not know: this reader k
  PASS  #522 a class in NEITHER list still reads as unknown, vocabulary present — the pin /tmp/tmp6koxn7qm/kit.json declares unknown class(es) ['standlone']; expected ['standalone', 'closure', 'seed', '
  PASS  #522 with no `class_vocabulary` the original wording is unchanged — the pin /tmp/tmp6koxn7qm/kit.json declares unknown class(es) ['standlone']; expected ['standalone', 'closure', 'seed', '
  PASS  #522 a class this reader KNOWS is accepted (the split did not break the lawful path) — loaded
  PASS  #522 a malformed `class_vocabulary` is IGNORED, never read as a licence — the pin /tmp/tmp6koxn7qm/kit.json declares unknown class(es) ['brand-new-class']; expected ['standalone', 'closure', 'se
  PASS  CONTROL: with the vocabulary key unread, case (1) collapses to the OLD single message — the split is what makes it bite — the pin /tmp/tmp6koxn7qm/kit.json declares unknown class(es) ['brand-new-class']; expected ['standalone', 'clo
  PASS  our own tree was never written to — manifest and predicate digests identical
kit pin FAILED: 1 check(s)
```

**`/usr/bin/python3 tests/test_gate_registration.py`** -- FAIL, rc=1, 2.76s

```
gate registry — an unregistered gate never runs (P29, issues #59, #68)
  synthetic probes
  PASS  direction 1 — an unregistered gate is NAMED — tests/test_orphan.py declares itself a gate but is never registered in
  PASS  direction 1 — the report names it too — ['test_orphan.py']
  PASS  direction 2 — a registered gate that stays silent is NAMED — tests/test_silent.py is run as a gate by audit.py but does not declare
  PASS  direction 2 — the report names it too — ['test_silent.py']
  PASS  scope (ii) — a body-only 'gate' does not count as a declaration — ['test_bodyonly.py']
  PASS  scope (ii) — and it is reported as a problem — ['tests/test_bodyonly.py is run as a gate by audit.py but does not declare itself one — a registered gate that stays silent is invisible to the declared->registered direction (P29)']
  PASS  a non-canonical opener is NAMED, not silently skipped — tests/test_loose.py declares a gate without the canonical 'Gate' opene
  PASS  the non-canonical gate is reported as such — ['test_loose.py']
  PASS  a registered, canonical gate raises no problem
  PASS  and the report shows both directions satisfied — declared=['test_ok.py'] undeclared=[]
  PASS  a file that declares no gate and is not run raises nothing — problems=[] declared=[]
  PASS  a guard that appends nothing is not a registration — tests/test_mentioned.py declares itself a gate but is never registered
  synthetic probes — direction 3, required -> present
  PASS  direction 3 — an absent required gate is NAMED — required gate tests/test_absent.py is ABSENT — the template ships it, so every b
  PASS  direction 3 — the report separates present from absent — absent=['test_absent.py'] present=['test_present.py']
  PASS  direction 3 — the message says the gate is ABSENT, not merely missing from a list — ['required gate tests/test_absent.py is ABSENT — the template ships it, so every
  PASS  direction 3 — a tree carrying every required gate raises nothing
  PASS  direction 3 — a declared deferral is NOT a problem — clean
  PASS  direction 3 — and it lands in the report WITH its reason — [('test_absent.py', 'port owed, closure unread')]
  PASS  direction 3 — an undeclared absence is still separated out — []
  PASS  direction 3 — CONTROL: a deferral with NO REASON is REFUSED — deferral for tests/test_absent.py carries NO REASON — a deferral must say why the gate is 
  PASS  direction 3 — CONTROL: and it is NOT counted as deferred — deferred=[] undeclared=['test_absent.py']
  PASS  direction 3 — CONTROL: a deferral for a gate the tree DOES carry is STALE — deferral for tests/test_present.py is STALE — the gate is PRESENT, so the entry declares a
  PASS  direction 3 — CONTROL: a deferral the manifest does not list is a PHANTOM — deferral names tests/test_typo.py, which the REQUIRED manifest does not list — a phantom e
  PASS  direction 3 — the live deferral store is READ, not skipped — True
  PASS  direction 3 — every live deferral carries a non-blank reason — 48 entries, 0 blank
  PASS  direction 3 — a MISSING store is a skip, not a failure — a missing store read as read=True
  PASS  the coupling leg SKIPS when no TEMPLATE tree exists — problems=[] skipped=True
  PASS  and the skip states WHY, naming the leg as meta-factory-only — no TEMPLATE tree — this leg is meta-factory-only (n=432 Part 3)
  PASS  a gate the template ships but the manifest omits FAILS — the template ships tests/test_shipped.py as a declared gate but the manifest omi
  PASS  a gate the manifest requires but the template does not ship FAILS — the manifest requires tests/test_ghost.py but the template does not ship it as a
  PASS  an exact manifest/template match raises nothing
  synthetic probes — direction 4, law -> registered
  PASS  direction 4 — a law-named unregistered mechanism is NAMED — law-named mechanism not registered in tools/audit.py: test_orphan.py — named by 
  PASS  direction 4 — the report says WHERE the law named it — ['test_orphan.py — named by rework log, Entries row 4']
  PASS  direction 4 — the report states the sources it read — ['rework log: 1 row(s) read, reason=None', 'skill: 1 line(s) read', 'mechanism-splits store: NOT READ']
  PASS  direction 4 — a registered law-named mechanism is clean — problems=[] named=1
  PASS  direction 4 — the skill arm names an unregistered mechanism — ['test_skillgate.py — named by skill line 1']
  PASS  direction 4 — the rework arm stays silent on a 'nothing yet' entry — ['test_skillgate.py — named by skill line 1']
  PASS  direction 4 — the word 'gate' in prose names no mechanism — named=0 problems=[]
  PASS  direction 4 — a file named outside `Prevented by` is not a mechanism — named=0 problems=[]
  PASS  direction 4 — a law-named file that does not exist is reported — law-named mechanism is a dead reference: test_missing.py — named by rework log, 
  PASS  direction 4 — a dead reference is not counted as merely unregistered — ['test_missing.py — named by rework log, Entries row 4, no such file under tests/']
  PASS  direction 4 — a nested live pytest suite is DECLARED present, not dead — ["test_nested.py — named by rework log, Entries row 4, present at /tmp/tmp0n0rjol3/tests/handlers/test_nested.py (a pytest suite outside the audit's script-gate namespace, so registration does not apply to it)"]
  PASS  direction 4 — CONTROL: a name that exists nowhere is STILL a dead reference — ['test_nowhere.py — named by rework log, Entries row 6, no such file under tests/']
  PASS  direction 4 — CONTROL: a TOP-LEVEL pytest-shaped gate is still UNREGISTERED, so the nested bucket is not an escape hatch — ['test_toplevel_pytest.py — named by rework log, Entries row 5']
  synthetic probes — #602: a declared SPLIT resolves a retired name
  PASS  direction 4 — a declared split RESOLVES the retired name — problems=[] dead=[]
  PASS  direction 4 — and the resolution NAMES its successors — ['test_retired.py -> test_leg_a.py, test_leg_b.py']
  PASS  direction 4 — the store is printed as READ, not silently skipped — ['rework log: 1 row(s) read, reason=None', 'skill: 1 line(s) read', 'mechanism-splits store: 1 split(s) declared']
  PASS  direction 4 — a split naming an ABSENT successor FAILS, naming it — law-named mechanism's declared split does not resolve: test_retired.py — declared split into test_leg_a.py, te
  PASS  direction 4 — and it is NOT counted as resolved — ['test_retired.py — declared split into test_leg_a.py, test_leg_missing.py, but absent under tests/: test_leg_missing.py — named by rework log, Entries row 4']
  PASS  direction 4 — a split naming an UNREGISTERED successor FAILS, naming it — law-named mechanism's declared split does not resolve: test_retired.py — declared split into test_leg_a.py, te
  PASS  direction 4 — a split whose retired file is STILL present is STALE — mechanism-splits store is stale: test_still.py — declared split, but the retired file is STILL present under t
  PASS  direction 4 — staleness is reported even when NO law names the file — named=0 stale=['test_still.py — declared split, but the retired file is STILL present under tests/']
  PASS  direction 4 — a one-successor entry is REFUSED as a rename — ['mechanism-splits entry 1 (test_old.py) needs `successors` — at least TWO file names, because a one-successor entry is a rename and a rename is followed, not declared']
  PASS  direction 4 — and the refusal says WHY — ['mechanism-splits entry 1 (test_old.py) needs `successors` — at least TWO file names, because a one-successor entry is a rename and a rename is followed, not declared']
  PASS  direction 4 — an ABSENT split store declares nothing and is not read — declared={} problems=[] read=False
  PASS  direction 4 — an unreadable split store FAILS and states why — ['mechanism-splits store is unreadable: Expecting property name enclosed in double quotes: line 1 column 3 (char 2)']
  PASS  direction 4 — a store with no `splits` list FAILS, naming the missing key — ['mechanism-splits store carries no `splits` list']
  PASS  direction 4 — an unreadable law source FAILS and states why — law source unreadable — the rework log is present but its `Prevented by` column could not 
  PASS  direction 4 — the stated reason is the column reader's own — the 'Entries' table has no 'Prevented by' column
  PASS  direction 4 — an ABSENT law source is a reported skip, not a failure — problems=[] sources=['rework log: 0 row(s) read, reason=None', 'skill: NOT READ', 'mechanism-splits store: NOT READ']
  PASS  the law-coverage predicate is a module constant — a tests/test_*.py that a RECORDED LAW names as an upholding 
  PASS  it carries the SCOPE BOUND that keeps #48 out of its population — ad on every run — an unprinted population is the exempt-by-silence surface ruled at n=571.
  PASS  it states that a failure NAMES the mechanism — a tests/test_*.py that a RECORDED LAW names as an upholding mechanism must be RE
  synthetic probes — direction 5, registered -> CAN RUN
  PASS  direction 5 — a pytest-style target under a script runner is NAMED — runner form cannot run its target: test_pytestish.py carries module-level `def t
  PASS  direction 5 — the report carries the mismatch, naming file and runner read — ['test_pytestish.py carries module-level `def test_*` but is registered as a script (<sys.
  PASS  direction 5 — the same target under the pytest runner is CLEAN — []
  PASS  direction 5 — a script-style target under the pytest runner is NOT reported — []
  PASS  direction 5 — and the report still states the runner it read — ['<sys.executable> -m pytest tests/test_scripty.py — runner=pytest, target form=script']
  PASS  direction 5 — a `__main__` guard does NOT make a test-carrying file script-form — got pytest
  PASS  direction 5 — without the guard it is pytest-form too — got pytest
  PASS  direction 5 — a file with NO test function stays script-form — got script
  PASS  direction 5 — a `def test_*` inside a STRING is not a module-level definition — got script
  PASS  direction 5 — and it names NO module-level test def — got ()
  PASS  direction 5 — CONTROL: the same def OUTSIDE a string IS one — got pytest / ('test_one',)
  PASS  direction 5 — this gate's OWN file reads script-form, not pytest-form — got script / ()
  PASS  direction 5 — a dual-form target (direct globals loop) is NOT pytest-form — got dual
  PASS  direction 5 — and it reports NO dead wrapper (direct) — got (True, ())
  PASS  direction 5 — a dual-form target (collected globals loop) is NOT pytest-form — got dual
  PASS  direction 5 — and it reports NO dead wrapper (collected) — got (True, ())
  PASS  direction 5 — CONTROL: the same file WITHOUT `main()` is still pytest-form — got pytest
  PASS  direction 5 — CONTROL: and dual_form refuses it — got (False, ())
  PASS  direction 5 — a wrapper `main()` never reaches is still dual-form — got True
  PASS  direction 5 — and the gap is NAMED, not swallowed — got ('test_a_meta_assertion',)
  PASS  direction 5 — a dual-form registration is not a problem — []
  PASS  direction 5 — but its unreached wrapper is in the report by name — {'test_gapful.py': ('test_a_meta_assertion',)}
  PASS  direction 5 — and the count is printed beside the form line — test_gapful.py — dual-form: 1 wrapper(s) the script path does not reproduce: test_a_meta_assertion']
  PASS  direction 5 — the runner is read from each append's own argv — {'test_pytestish.py': 'script', 'test_scripty.py': 'pytest'}
  PASS  direction 5 — an absent target is NOT a runner-form problem — []
  PASS  direction 5 — but it is NAMED in the report as skipped — ["<sys.executable> tests/test_ghost.py — runner=script, target absent (presence is directi
  PASS  direction 5 — a tool invocation is reported as carrying no target shape — ['<sys.executable> tools/ledger.py verify — tool invocation, no target shape to read']
  PASS  clause 3 — a non-literal argument is rendered as an explicit PLACEHOLDER — <sys.executable> tools/hygiene.py --audit --namespace <hygiene_namespace(repo_root)>
  PASS  clause 3 — the interpreter is a placeholder too, never a bare token — <sys.executable> tools/hygiene.py --audit --namespace <hygiene_namespace(repo_root)>
  PASS  clause 3 — the truncated literal-only form is never produced — <sys.executable> tools/hygiene.py --audit --namespace <hygiene_namespace(repo_root)>
  PASS  clause 3 — the value is still a token list, so `split()` keeps the target — <sys.executable> tools/hygiene.py --audit --namespace <hygiene_namespace(repo_root)>
  PASS  direction 5 — registered_entries does NOT re-scan for appends — registered_entries still scans _APPEND itself
  PASS  direction 5 — registered_entries delegates to registration_entries — def registered_entries(audit_text: str) -> tuple[list[str], 
  PASS  direction 5 — a target named test_pytest_form.py in script form reads as script — script
  PASS  direction 5 — and the mismatch is still reported, not hidden by the name — runner form cannot run its target: test_pytest_form.py carries module-level `def
  PASS  direction 5 — the predicate states the one-way bound — nswers only whether a given registration CAN run its target.
  live tree
  PASS  the live scan finds gates to check — 60 declared gate(s)
  PASS  the reverse direction finds registered gates to check — 53 registered tests/ path(s)
  PASS  the resolver finds every gate that declares itself in words — missed=[] extra=[]
  PASS  this gate finds itself — 60 declared gate(s)
  PASS  the scan is not vacuous — it found gates to check — 60 declared gate(s)
  (this tree carries 0 non-canonical declaration(s) found by the loose pass: none — the synthetic probes cover that form)
  PASS  scope (i) — the tool invocations are counted and named — 4: ['<sys.executable> tools/ledger.py verify', '<sys.executable> tools/hygiene.py --clean --namespace <hygiene_namespace(repo_root)>', '<sys.executable> tools/hygiene.py --audit --namespace <hygiene_namespace(repo_root)>', '<sys.executable> tools/roadmap.py --audit']
  PASS  scope (i) — the scope note says a tests/-scoped parse cannot see them — a tests/-scoped parse covers the tests/ entries of the run list only; 
  PASS  CONTROL: an EMPTY run list still FAILS the vacuity leg — registered=[]
  PASS  CONTROL: an EMPTY run list still FAILS the named-tool leg — tools=[]
  PASS  CONTROL: a registered-but-ABSENT target is a lawful conditional registration — problems=[]
  PASS  clause 3 — every live hygiene invocation carries its COMPUTED namespace — ['<sys.executable> tools/hygiene.py --clean --namespace <hygiene_namespace(repo_root)>', '
  PASS  clause 3 — no live tool invocation ends at a bare `--namespace` — ['<sys.executable> tools/ledger.py verify', '<sys.executable> tools/hygiene.py --clean --namespace <hygiene_namespace(re
  PASS  clause 3 — every live tool invocation is a DECLARED FORM, never a command line — ['<sys.executable> tools/ledger.py verify', '<sys.executable> tools/hygiene.py --clean --namespace <hygiene_namespace(re
  PASS  an r-prefixed opener IS recognised as a docstring — r"""Gate: a raw-prefixed gate.
  PASS  and its first line is read with the prefix stripped — 'Gate: a raw-prefixed gate.'
  PASS  a raw-prefixed gate is DECLARED, so no silent-gate problem is raised — problems=[] declared=['test_raw.py']
  PASS  a bare-quote opener still reads (no regression) — """Gate: a plain gate.
  PASS  the predicate is a module constant — a tests/test_*.py whose opening docstring's FIRST line contains 'gate'
  PASS  the predicate names both directions — LARE itself, so a gate the audit runs is never silent about being one.
  PASS  the predicate names the scan's file set — a tests/test_*.py whose opening docstring's FIRST line conta
    tests/test_hygiene_branch_occupancy.py declares itself a gate but is never registered in audit.py — an unregistered gate never runs (P29)
    tests/test_hygiene_build_residue.py declares itself a gate but is never registered in audit.py — an unregistered gate never runs (P29)
    tests/test_hygiene_declaration_sweep.py declares itself a gate but is never registered in audit.py — an unregistered gate never runs (P29)
    tests/test_hygiene_stale_dirs.py declares itself a gate but is never registered in audit.py — an unregistered gate never runs (P29)
  predicate: a tests/test_*.py whose opening docstring's FIRST line contains 'gate' (case-insensitive) is a declared gate; each must be REGISTERED (its path appears quoted in a gates_to_run.append(...) in tools/audit.py) and CANONICAL (that first line starts with 'Gate'). The REVERSE also holds: every tests/ path in the run list must DECLARE itself, so a gate the audit runs is never silent about being one.
  scope (i): a tests/-scoped parse covers the tests/ entries of the run list only; tool invocations (tools/*.py) are outside any test-file predicate and are named in the report so the gap is stated — and each is rendered as a DECLARED FORM, a computed argument appearing as the placeholder `<expr>` rather than as text that reads like a command anyone could run
  scope (ii): the OPENER is matched, never the whole file — a whole-file search for 'gate' picks up a comment and a data field, neither of them a gate
  examined 79 module(s); 60 declared a gate, 53 tests/ paths are in the run list, 4 run-list entr(ies) are tool invocations (<sys.executable> tools/ledger.py verify, <sys.executable> tools/hygiene.py --clean --namespace <hygiene_namespace(repo_root)>, <sys.executable> tools/hygiene.py --audit --namespace <hygiene_namespace(repo_root)>, <sys.executable> tools/roadmap.py --audit) — declared-but-unregistered: 4, registered-but-undeclared: 0, non-canonical: 0, 4 problem(s)
  FAIL  every declared gate is registered, and every registered gate declares itself — tests/test_hygiene_branch_occupancy.py declares itself a gate but is never registered in a
  live tree — direction 3
    deferred  tests/test_reconstructed_claim_declared.py — PORT OWED. The kit ships this gate and this tree does not carry it. Not taken in the #107 pass: that pass was scoped to making the registration leg itself green, and each port needs its own closure read and its own discriminating control before it can be trusted green. The absence is declared here so the population is PRINTED, because the gate's own FAIL message truncates at 90 characters and 54 undeclared absences read exactly like one.
    deferred  tests/test_criterion_path_exemptions.py — PORT OWED. The kit ships this gate and this tree does not carry it. Not taken in the #107 pass: that pass was scoped to making the registration leg itself green, and each port needs its own closure read and its own discriminating control before it can be trusted green. The absence is declared here so the population is PRINTED, because the gate's own FAIL message truncates at 90 characters and 54 undeclared absences read exactly like one.
    deferred  tests/test_cron_landing_route.py — PORT OWED. The kit ships this gate and this tree does not carry it. Not taken in the #107 pass: that pass was scoped to making the registration leg itself green, and each port needs its own closure read and its own discriminating control before it can be trusted green. The absence is declared here so the population is PRINTED, because the gate's own FAIL message truncates at 90 characters and 54 undeclared absences read exactly like one.
    deferred  tests/test_declaration_drift.py — PORT OWED. The kit ships this gate and this tree does not carry it. Not taken in the #107 pass: that pass was scoped to making the registration leg itself green, and each port needs its own closure read and its own discriminating control before it can be trusted green. The absence is declared here so the population is PRINTED, because the gate's own FAIL message truncates at 90 characters and 54 undeclared absences read exactly like one.
    deferred  tests/test_declared_boundary_census.py — PORT OWED. The kit ships this gate and this tree does not carry it. Not taken in the #107 pass: that pass was scoped to making the registration leg itself green, and each port needs its own closure read and its own discriminating control before it can be trusted green. The absence is declared here so the population is PRINTED, because the gate's own FAIL message truncates at 90 characters and 54 undeclared absences read exactly like one.
    deferred  tests/test_law_mechanism_same_commit.py — PORT OWED AFTER PARAMETERISING. This factory's law lives in skills/ai-antispam/*.md and this router, not in the kit's AGENTS.md + roles/ layout, so the predicate would have to be pointed at our own surfaces before it could read anything. That is the same 'PARAMETERIZE FIRST' class the kit's own OPTIONAL_GATES records for test_law_coverage.py, and it is why the gate is not simply copied.
    deferred  tests/test_gate_triggers.py — PORT OWED. The kit ships this gate and this tree does not carry it. Not taken in the #107 pass: that pass was scoped to making the registration leg itself green, and each port needs its own closure read and its own discriminating control before it can be trusted green. The absence is declared here so the population is PRINTED, because the gate's own FAIL message truncates at 90 characters and 54 undeclared absences read exactly like one.
    deferred  tests/test_hq_delegation.py — PORT OWED. The kit ships this gate and this tree does not carry it. Not taken in the #107 pass: that pass was scoped to making the registration leg itself green, and each port needs its own closure read and its own discriminating control before it can be trusted green. The absence is declared here so the population is PRINTED, because the gate's own FAIL message truncates at 90 characters and 54 undeclared absences read exactly like one.
    deferred  tests/test_law_no_raw_session_uuid.py — PORT OWED AFTER PARAMETERISING. This factory's law lives in skills/ai-antispam/*.md and this router, not in the kit's AGENTS.md + roles/ layout, so the predicate would have to be pointed at our own surfaces before it could read anything. That is the same 'PARAMETERIZE FIRST' class the kit's own OPTIONAL_GATES records for test_law_coverage.py, and it is why the gate is not simply copied.
    deferred  tests/test_law_structure.py — PORT OWED AFTER PARAMETERISING. This factory's law lives in skills/ai-antispam/*.md and this router, not in the kit's AGENTS.md + roles/ layout, so the predicate would have to be pointed at our own surfaces before it could read anything. That is the same 'PARAMETERIZE FIRST' class the kit's own OPTIONAL_GATES records for test_law_coverage.py, and it is why the gate is not simply copied.
    deferred  tests/test_ledger_claim_preflight.py — PORT OWED. Ours is the template basis for tools/ledger.py (migrated 2026-09-27), so the closure exists; what is missing is the gate file and its registration. Not taken in the #107 pass because each of these asserts a distinct ledger invariant, and this factory carries TWO subject namespaces (a service-board item is a bare `#N`; a campaign unit is a descriptive stem), so each predicate has to be read against that split before it can be trusted green.
    deferred  tests/test_commit_subject_issue_refs.py — PORT OWED. The kit ships this gate and this tree does not carry it. Not taken in the #107 pass: that pass was scoped to making the registration leg itself green, and each port needs its own closure read and its own discriminating control before it can be trusted green. The absence is declared here so the population is PRINTED, because the gate's own FAIL message truncates at 90 characters and 54 undeclared absences read exactly like one.
    deferred  tests/test_close_ruling_paired.py — PORT OWED. The kit ships this gate and this tree does not carry it. Not taken in the #107 pass: that pass was scoped to making the registration leg itself green, and each port needs its own closure read and its own discriminating control before it can be trusted green. The absence is declared here so the population is PRINTED, because the gate's own FAIL message truncates at 90 characters and 54 undeclared absences read exactly like one.
    deferred  tests/test_ledger_factory_scope.py — PORT OWED. Ours is the template basis for tools/ledger.py (migrated 2026-09-27), so the closure exists; what is missing is the gate file and its registration. Not taken in the #107 pass because each of these asserts a distinct ledger invariant, and this factory carries TWO subject namespaces (a service-board item is a bare `#N`; a campaign unit is a descriptive stem), so each predicate has to be read against that split before it can be trusted green.
    deferred  tests/test_ledger_header_closure.py — PORT OWED. Ours is the template basis for tools/ledger.py (migrated 2026-09-27), so the closure exists; what is missing is the gate file and its registration. Not taken in the #107 pass because each of these asserts a distinct ledger invariant, and this factory carries TWO subject namespaces (a service-board item is a bare `#N`; a campaign unit is a descriptive stem), so each predicate has to be read against that split before it can be trusted green.
    deferred  tests/test_ledger_refs_preflight.py — PORT OWED. Ours is the template basis for tools/ledger.py (migrated 2026-09-27), so the closure exists; what is missing is the gate file and its registration. Not taken in the #107 pass because each of these asserts a distinct ledger invariant, and this factory carries TWO subject namespaces (a service-board item is a bare `#N`; a campaign unit is a descriptive stem), so each predicate has to be read against that split before it can be trusted green.
    deferred  tests/test_duty_receipt_exemptions.py — PORT OWED. The kit ships this gate and this tree does not carry it. Not taken in the #107 pass: that pass was scoped to making the registration leg itself green, and each port needs its own closure read and its own discriminating control before it can be trusted green. The absence is declared here so the population is PRINTED, because the gate's own FAIL message truncates at 90 characters and 54 undeclared absences read exactly like one.
    deferred  tests/test_ledger_ref_roles.py — PORT OWED. Ours is the template basis for tools/ledger.py (migrated 2026-09-27), so the closure exists; what is missing is the gate file and its registration. Not taken in the #107 pass because each of these asserts a distinct ledger invariant, and this factory carries TWO subject namespaces (a service-board item is a bare `#N`; a campaign unit is a descriptive stem), so each predicate has to be read against that split before it can be trusted green.
    deferred  tests/test_patrol_criterion.py — PORT OWED, AND THE KIT'S OWN CLASSIFICATION IS THE THING TO FIX. `test_patrol_*` guards the meta-factory's patrol — its worktrees, its publish pusher, its round receipts, its census and its render. This factory has no patrol runner, no workers roster and no round receipts, so the subject does not exist here and the gate could not pass. The kit lists these as REQUIRED on the stated premise that every REQUIRED gate passes in a bootstrapped factory; measured 2026-11-05 that premise is FALSE for this class, so the repair is upstream — they belong in OPTIONAL_GATES with a reason, the pattern the kit already uses for test_shipped_mechanism_law.py — not twelve ports here.
    deferred  tests/test_patrol_ruling.py — PORT OWED, AND THE KIT'S OWN CLASSIFICATION IS THE THING TO FIX. `test_patrol_*` guards the meta-factory's patrol — its worktrees, its publish pusher, its round receipts, its census and its render. This factory has no patrol runner, no workers roster and no round receipts, so the subject does not exist here and the gate could not pass. The kit lists these as REQUIRED on the stated premise that every REQUIRED gate passes in a bootstrapped factory; measured 2026-11-05 that premise is FALSE for this class, so the repair is upstream — they belong in OPTIONAL_GATES with a reason, the pattern the kit already uses for test_shipped_mechanism_law.py — not twelve ports here.
    deferred  tests/test_patrol_tree_instants.py — PORT OWED, AND THE KIT'S OWN CLASSIFICATION IS THE THING TO FIX. `test_patrol_*` guards the meta-factory's patrol — its worktrees, its publish pusher, its round receipts, its census and its render. This factory has no patrol runner, no workers roster and no round receipts, so the subject does not exist here and the gate could not pass. The kit lists these as REQUIRED on the stated premise that every REQUIRED gate passes in a bootstrapped factory; measured 2026-11-05 that premise is FALSE for this class, so the repair is upstream — they belong in OPTIONAL_GATES with a reason, the pattern the kit already uses for test_shipped_mechanism_law.py — not twelve ports here.
    deferred  tests/test_patrol_worktree.py — PORT OWED, AND THE KIT'S OWN CLASSIFICATION IS THE THING TO FIX. `test_patrol_*` guards the meta-factory's patrol — its worktrees, its publish pusher, its round receipts, its census and its render. This factory has no patrol runner, no workers roster and no round receipts, so the subject does not exist here and the gate could not pass. The kit lists these as REQUIRED on the stated premise that every REQUIRED gate passes in a bootstrapped factory; measured 2026-11-05 that premise is FALSE for this class, so the repair is upstream — they belong in OPTIONAL_GATES with a reason, the pattern the kit already uses for test_shipped_mechanism_law.py — not twelve ports here.
    deferred  tests/test_patrol_publish.py — PORT OWED, AND THE KIT'S OWN CLASSIFICATION IS THE THING TO FIX. `test_patrol_*` guards the meta-factory's patrol — its worktrees, its publish pusher, its round receipts, its census and its render. This factory has no patrol runner, no workers roster and no round receipts, so the subject does not exist here and the gate could not pass. The kit lists these as REQUIRED on the stated premise that every REQUIRED gate passes in a bootstrapped factory; measured 2026-11-05 that premise is FALSE for this class, so the repair is upstream — they belong in OPTIONAL_GATES with a reason, the pattern the kit already uses for test_shipped_mechanism_law.py — not twelve ports here.
    deferred  tests/test_patrol_workspace.py — PORT OWED, AND THE KIT'S OWN CLASSIFICATION IS THE THING TO FIX. `test_patrol_*` guards the meta-factory's patrol — its worktrees, its publish pusher, its round receipts, its census and its render. This factory has no patrol runner, no workers roster and no round receipts, so the subject does not exist here and the gate could not pass. The kit lists these as REQUIRED on the stated premise that every REQUIRED gate passes in a bootstrapped factory; measured 2026-11-05 that premise is FALSE for this class, so the repair is upstream — they belong in OPTIONAL_GATES with a reason, the pattern the kit already uses for test_shipped_mechanism_law.py — not twelve ports here.
    deferred  tests/test_patrol_round_receipts.py — PORT OWED, AND THE KIT'S OWN CLASSIFICATION IS THE THING TO FIX. `test_patrol_*` guards the meta-factory's patrol — its worktrees, its publish pusher, its round receipts, its census and its render. This factory has no patrol runner, no workers roster and no round receipts, so the subject does not exist here and the gate could not pass. The kit lists these as REQUIRED on the stated premise that every REQUIRED gate passes in a bootstrapped factory; measured 2026-11-05 that premise is FALSE for this class, so the repair is upstream — they belong in OPTIONAL_GATES with a reason, the pattern the kit already uses for test_shipped_mechanism_law.py — not twelve ports here.
    deferred  tests/test_patrol_cron_duty.py — PORT OWED, AND THE KIT'S OWN CLASSIFICATION IS THE THING TO FIX. `test_patrol_*` guards the meta-factory's patrol — its worktrees, its publish pusher, its round receipts, its census and its render. This factory has no patrol runner, no workers roster and no round receipts, so the subject does not exist here and the gate could not pass. The kit lists these as REQUIRED on the stated premise that every REQUIRED gate passes in a bootstrapped factory; measured 2026-11-05 that premise is FALSE for this class, so the repair is upstream — they belong in OPTIONAL_GATES with a reason, the pattern the kit already uses for test_shipped_mechanism_law.py — not twelve ports here.
    deferred  tests/test_patrol_render.py — PORT OWED, AND THE KIT'S OWN CLASSIFICATION IS THE THING TO FIX. `test_patrol_*` guards the meta-factory's patrol — its worktrees, its publish pusher, its round receipts, its census and its render. This factory has no patrol runner, no workers roster and no round receipts, so the subject does not exist here and the gate could not pass. The kit lists these as REQUIRED on the stated premise that every REQUIRED gate passes in a bootstrapped factory; measured 2026-11-05 that premise is FALSE for this class, so the repair is upstream — they belong in OPTIONAL_GATES with a reason, the pattern the kit already uses for test_shipped_mechanism_law.py — not twelve ports here.
    deferred  tests/test_patrol_census.py — PORT OWED, AND THE KIT'S OWN CLASSIFICATION IS THE THING TO FIX. `test_patrol_*` guards the meta-factory's patrol — its worktrees, its publish pusher, its round receipts, its census and its render. This factory has no patrol runner, no workers roster and no round receipts, so the subject does not exist here and the gate could not pass. The kit lists these as REQUIRED on the stated premise that every REQUIRED gate passes in a bootstrapped factory; measured 2026-11-05 that premise is FALSE for this class, so the repair is upstream — they belong in OPTIONAL_GATES with a reason, the pattern the kit already uses for test_shipped_mechanism_law.py — not twelve ports here.
    deferred  tests/test_patrol_board.py — PORT OWED, AND THE KIT'S OWN CLASSIFICATION IS THE THING TO FIX. `test_patrol_*` guards the meta-factory's patrol — its worktrees, its publish pusher, its round receipts, its census and its render. This factory has no patrol runner, no workers roster and no round receipts, so the subject does not exist here and the gate could not pass. The kit lists these as REQUIRED on the stated premise that every REQUIRED gate passes in a bootstrapped factory; measured 2026-11-05 that premise is FALSE for this class, so the repair is upstream — they belong in OPTIONAL_GATES with a reason, the pattern the kit already uses for test_shipped_mechanism_law.py — not twelve ports here.
    deferred  tests/test_patrol_preamble.py — PORT OWED, AND THE KIT'S OWN CLASSIFICATION IS THE THING TO FIX. `test_patrol_*` guards the meta-factory's patrol — its worktrees, its publish pusher, its round receipts, its census and its render. This factory has no patrol runner, no workers roster and no round receipts, so the subject does not exist here and the gate could not pass. The kit lists these as REQUIRED on the stated premise that every REQUIRED gate passes in a bootstrapped factory; measured 2026-11-05 that premise is FALSE for this class, so the repair is upstream — they belong in OPTIONAL_GATES with a reason, the pattern the kit already uses for test_shipped_mechanism_law.py — not twelve ports here.
    deferred  tests/test_citation_published.py — PORT OWED. The kit ships this gate and this tree does not carry it. Not taken in the #107 pass: that pass was scoped to making the registration leg itself green, and each port needs its own closure read and its own discriminating control before it can be trusted green. The absence is declared here so the population is PRINTED, because the gate's own FAIL message truncates at 90 characters and 54 undeclared absences read exactly like one.
    deferred  tests/test_score_artifact_measurable.py — PORT OWED, with the subject possibly absent. The score-artifact gates assert the shape of `evidence/score-*.md`. This factory writes no score artifact, so a verbatim port would either red or need a declared skip — the pattern the kit itself uses where TEMPLATE ships no `evidence/` (BOOTSTRAP.md creates it). Declared here rather than guessed at: the disposition is a port with a skip, or an upstream reclassification.
    deferred  tests/test_score_artifact_citations.py — PORT OWED, with the subject possibly absent. The score-artifact gates assert the shape of `evidence/score-*.md`. This factory writes no score artifact, so a verbatim port would either red or need a declared skip — the pattern the kit itself uses where TEMPLATE ships no `evidence/` (BOOTSTRAP.md creates it). Declared here rather than guessed at: the disposition is a port with a skip, or an upstream reclassification.
    deferred  tests/test_score_artifact_sections.py — PORT OWED, with the subject possibly absent. The score-artifact gates assert the shape of `evidence/score-*.md`. This factory writes no score artifact, so a verbatim port would either red or need a declared skip — the pattern the kit itself uses where TEMPLATE ships no `evidence/` (BOOTSTRAP.md creates it). Declared here rather than guessed at: the disposition is a port with a skip, or an upstream reclassification.
    deferred  tests/test_subject_form.py — PORT OWED. The kit ships this gate and this tree does not carry it. Not taken in the #107 pass: that pass was scoped to making the registration leg itself green, and each port needs its own closure read and its own discriminating control before it can be trusted green. The absence is declared here so the population is PRINTED, because the gate's own FAIL message truncates at 90 characters and 54 undeclared absences read exactly like one.
    deferred  tests/test_telemetry_reader_registry.py — PORT OWED. The kit ships this gate and this tree does not carry it. Not taken in the #107 pass: that pass was scoped to making the registration leg itself green, and each port needs its own closure read and its own discriminating control before it can be trusted green. The absence is declared here so the population is PRINTED, because the gate's own FAIL message truncates at 90 characters and 54 undeclared absences read exactly like one.
    deferred  tests/test_template_gate_pairing.py — PORT OWED. test_template_gate_pairing.py asserts the manifest grain between a gate and its TEMPLATE copy; this tree carries no TEMPLATE/ at all, so the comparison half is absent by construction and the gate would need the same declared-skip treatment as the pin's own fixture arms.
    deferred  tests/test_audit_undefined_names.py — PORT OWED, MEASURED 2026-11-05 (#107 task 7): the gate was ported from kit HEAD and RUN against this tree. 9 of its 10 probes PASS — including every undefined-name arm, so this tree's shipped tools bind every name they reference and #235's crash class has no live instance here. The single FAIL is probe_the_text_render_path_completes, and it is not a name defect: it drives `audit.py --no-gates` and requires a `Load population` line, which this tree's audit.py never renders because its run path still uses the local GATE_WALLS_SEC table and imports only TIMEOUT_EXIT_CODE from the ported gate_budget.py, instead of constructing the kit's GateBudgets manifest. That render is a REPORTING-CONTRACT port (it also owes the `Gate budgets: N run — X declared` and `Gate budget staleness` blocks beside it), the same class as #126, and it is not a gate-registration change — so the file is held OUT rather than registered, because registering it would red the daily audit on a render section nobody has written yet. The file is absent from tests/ so this entry is not STALE.
    deferred  tests/test_hygiene_placement.py — PORT OWED — PARAMETERIZE FIRST. tools/hygiene.py is carried and the leg's 18 BEHAVIOURAL arms pass; the 4 failures are the KIT's live-tree declaration data, not a missing subject. (1) this tree's top-level directories certs, grafana, landing_page, scripts, spikes, src are named by neither PLACEMENT_MAP nor PLACEMENT_UNMAPPED; (2) two live mismatches — tests/linked_channel_test.csv (a data fixture sitting at the tests root, while `tests` declares python only) and evidence/.ledger.lock (a tracked zero-byte fixture, class unknown:.lock); (3) two .gitignore arms assert the KIT's runtime artifacts — evidence/.insights.lock, evidence/.ledger.lock and evidence/opencrabs-ledger.lock are not ignored in this tree (measured: git check-ignore rc=1 for the first two). Needs this tree's map entries, a class/skip disposition for the two mismatches, and the two arms re-pointed at this tree's own artifacts. The legs themselves are already printing their populations (224 files / 11 declared directories).
    deferred  tests/test_hygiene_evidence_supersession.py — PORT OWED — PARAMETERIZE FIRST. tools/hygiene.py is carried and the leg's 12 BEHAVIOURAL arms pass; the 2 failures are live-tree facts of the KIT's tree. test_the_live_insights_TIE_is_read_as_current names evidence/instrument-census-insights-2026-09-28{,-refused}.md, which this tree does not carry, and test_the_live_tree_is_NON_VACUOUS demands len(by_convention) > 0 — i.e. that the G7 gap be LIVE here, which it is not (measured: 1 dated artifact, 1 family, 0 by_convention, 0 superseded). Needs the tie arm re-pointed at a pair this tree carries, or a STATED SKIP.
    deferred  tests/test_ruling_row_recorded.py — PORT OWED. The kit ships this gate and this tree does not carry it. Not taken in the #107 pass: that pass was scoped to making the registration leg itself green, and each port needs its own closure read and its own discriminating control before it can be trusted green. The absence is declared here so the population is PRINTED, because the gate's own FAIL message truncates at 90 characters and 54 undeclared absences read exactly like one.
    deferred  tests/test_ledger_citation_declared.py — PORT OWED. Ours is the template basis for tools/ledger.py (migrated 2026-09-27), so the closure exists; what is missing is the gate file and its registration. Not taken in the #107 pass because each of these asserts a distinct ledger invariant, and this factory carries TWO subject namespaces (a service-board item is a bare `#N`; a campaign unit is a descriptive stem), so each predicate has to be read against that split before it can be trusted green.
    deferred  tests/test_issue_routing_surfaces.py — PORT OWED. The kit ships this gate and this tree does not carry it. Not taken in the #107 pass: that pass was scoped to making the registration leg itself green, and each port needs its own closure read and its own discriminating control before it can be trusted green. The absence is declared here so the population is PRINTED, because the gate's own FAIL message truncates at 90 characters and 54 undeclared absences read exactly like one.
    deferred  tests/test_harness_bindings.py — PORT OWED. The kit ships this gate and this tree does not carry it. Not taken in the #107 pass: that pass was scoped to making the registration leg itself green, and each port needs its own closure read and its own discriminating control before it can be trusted green. The absence is declared here so the population is PRINTED, because the gate's own FAIL message truncates at 90 characters and 54 undeclared absences read exactly like one.
    deferred  tests/test_question_intake.py — DELIBERATELY NOT PORTED, measured 2026-11-05. test_question_intake.py depends on the intake-metric subsystem — a READER module plus its tools/audit.py wiring — which this factory does not carry. Porting the gate alone would red the audit against an absent dependency, so the port is owed as a SUBSYSTEM, not as a file.
    deferred  tests/test_binding_sync.py — PORT OWED. The kit ships this gate and this tree does not carry it. Not taken in the #107 pass: that pass was scoped to making the registration leg itself green, and each port needs its own closure read and its own discriminating control before it can be trusted green. The absence is declared here so the population is PRINTED, because the gate's own FAIL message truncates at 90 characters and 54 undeclared absences read exactly like one.
    deferred  tests/test_declared_event_population.py — PORT OWED. The kit ships this gate and this tree does not carry it. Not taken in the #107 pass: that pass was scoped to making the registration leg itself green, and each port needs its own closure read and its own discriminating control before it can be trusted green. The absence is declared here so the population is PRINTED, because the gate's own FAIL message truncates at 90 characters and 54 undeclared absences read exactly like one.
    deferred  tests/test_gate_direct_invocation.py — PORT OWED. The kit ships this gate and this tree does not carry it. Not taken in the #107 pass: that pass was scoped to making the registration leg itself green, and each port needs its own closure read and its own discriminating control before it can be trusted green. The absence is declared here so the population is PRINTED, because the gate's own FAIL message truncates at 90 characters and 54 undeclared absences read exactly like one.
  PASS  every required gate is PRESENT in the live tree, or DECLARED deferred with a reason
  PASS  the deferred population is PRINTED with a reason for every entry — 48 printed of 48 absent
  live tree — direction 3: 98 required, 50 present, 48 declared deferred, 0 undeclared absent
  PASS  the manifest is non-vacuous — 98 required gate(s)
  coupling leg SKIPPED: no TEMPLATE tree — this leg is meta-factory-only (n=432 Part 3)
  PASS  the manifest EQUALS the template's declared-gate inventory
  PASS  every optional gate is printed WITH a reason — 17 line(s)
  PASS  the optional set is disjoint from the required manifest — []
  PASS  the required predicate is a module constant naming the manifest — every gate in REQUIRED_GATES — the manifest derived from what the temp
    not required: tests/test_insights_audience.py — meta-factory-only (owner order 2026-09-28) — its subject tool tools/insights.py is live-only and does not ship, the same reason its author sibling above is OPTIONAL. It carries the probes for the AUDIENCE field — the reader a claim has outside this factory, which is what routes it to a publishing surface — and for the RETIREMENT of the `class` axis that field replaced (kept as history, validated against nothing). Three failure modes: an append that accepts a missing audience, an append that accepts an unknown one, and a verify that passes a stored blank while still accepting a legacy key-less row. It also guards the `set_audience` backfill's additive contract, which is what makes the 33-row migration a migration and not a rewrite. Every probe is fixture-driven, because the live register IS the artifact under measurement.
    not required: tests/test_insights_author.py — meta-factory-only (owner order 2026-09-28) — its subject tool tools/insights.py is live-only and does not ship, the same reason both synthesizer siblings above are OPTIONAL. It carries the probes for the AUTHOR field's three failure modes: an append that accepts a blank, a verify that passes one, and a derivation that defaults instead of refusing. Every probe is fixture-driven, because the live register IS the artifact under measurement.
    not required: tests/test_insights_gate_recorded.py — meta-factory-only (OWNER ORDER 2026-09-28: 'this insights tool should not be a part of the kit — it's the meta factory's subject matter only'). It was REQUIRED and shipped as the one insights surface in the kit; that is now withdrawn. The register is this factory's own subject matter, so a bootstrapped factory has no such artifact to govern — the template's own doctrine already says so (`TEMPLATE/docs/addons/domain/stories.md` classes `tools/insights.py` and `evidence/insights.jsonl` as 'the factory creates it'), and this gate joining the kit contradicted it. Its subject is `evidence/insights.jsonl`, which never shipped, so the member inherited a boundary parameter it had no artifact to apply. It stays REGISTERED and GREEN here, by the same presence-guard idiom every factory-local gate uses, and its reason for the move is the owner's — not the 'parameterize first' class the two entries above carry.
    not required: tests/test_insights_status.py — meta-factory-only (owner order 2026-09-28) — its subject tool tools/insights.py is live-only and does not ship, the same reason its author and audience siblings above are OPTIONAL. It carries the probes for the STATUS field, the WORKFLOW axis: that a new entry OPENS at `pending` and is stamped, that a blank or unknown status is refused at the append, that `status_at` travels WITH the status, that verify rejects a stored blank or an unstamped status while still accepting a legacy key-less row, that a transition rewrites the label and NOTHING else, that the routing group and the status stay orthogonal, and the two laws that bind them together — a publishing row names its `surface` and no other row does, and a publishing row is `public` while an HQ row is `process: yes`. Every probe is fixture-driven, because the live register IS the artifact under measurement.
    not required: tests/test_kit_census.py — meta-factory-only (plan 2646d31a step 9) — its POPULATION is the five MEMBER repositories, read through `registry/fleet.json`. A member does not sweep the fleet, so it has neither the tool (`tools/kit_census.py`, repo-side and unshipped) nor the population; it is this factory's instrument for publishing what every member has adopted, and the artifact is what a plan is corrected against. Its verdict is a REFUSAL when the leg reached no member, so an absent fleet fails loudly rather than publishing an empty census that reads as a clean one.
    not required: tests/test_kit_deliver.py — meta-factory-only (plan 2646d31a step 7) — its subject tool tools/kit_deliver.py is the TRANSPORT that hands a member an update, and it does not ship: a member delivers to nobody, so a member running this gate would be exercising a tool it is not supposed to carry. Same grain as test_kit_manifest.py, and declared here rather than left to a by-hand run so its twelve arms actually execute.
    not required: tests/test_kit_manifest.py — meta-factory-only (plan 2646d31a step 9) — its POPULATION is `TEMPLATE/`, the reference manifest of the shipped kit. A bootstrapped factory has no TEMPLATE/ tree, so there is no manifest for it to agree with; it is this factory's own instrument for measuring what every member has ported, and it skips with that reason rather than passing silently.
    not required: tests/test_kit_names.py — meta-factory-only (plan 2646d31a step 10) — its POPULATION is the other trees, read through `registry/fleet.json`. A member does not sweep the fleet, so it has neither the tool (`tools/kit_names.py`, repo-side and unshipped) nor the population. It is this factory's instrument for measuring naming criterion 2 (non-conflicting) against the fleet rather than against this repo alone, which is the only way a name meaning two different instruments in two trees can be seen at all.
    not required: tests/test_kit_questions_homes.py — meta-factory-only (issue #188 step 2) — its POPULATION is the EXECUTING copy of the questions register, which `/opt/questions/backend.py` resolves from the ops profile's skill tree and which sits inside NO member root. A member has no second home to compare, and this gate resolves that copy through `tools/kit_census.py`, repo-side and unshipped, so it has neither the tool nor the population. It is this factory's gate for the RETURN leg nothing owned: a fix landed on `TEMPLATE/tools/questions` alone while the executing copy kept the pre-fix refusal for two days (#183 -> #188). Its predicate is a SUBSET, so byte-identity PASSES — the gate stays green once the Toolsmith's owner-ordered adoption completes.
    not required: tests/test_kit_surfaces.py — meta-factory-only (issue #214) — its POPULATION is `TEMPLATE/tools/`, and the tool it judges (`tools/kit_surfaces.py`) is one of the five root-side executables that do not ship (docs/instruments/kit.md section 2.1): the manifest is 100% TEMPLATE-rooted, so a member has neither the tool nor the tree it sweeps. It is this factory's own gate for the surface census, and it exists because that census was run by NOTHING until #214 — an instrument whose verdict reaches no consumer is indistinguishable from one that passes, which is how an S1 predicate that could never match a hyphenated tool survived as its own headline red.
    not required: tests/test_law_coverage.py — PARAMETERIZE FIRST (n=432 Part 5) — hardcodes skills/meta-factory/SKILL.md and would RED in a bootstrapped factory.
    not required: tests/test_session_bindings.py — PARAMETERIZE FIRST (n=432 Part 5) — hardcodes skills/meta-factory/SKILL.md, and additionally requires >=4 lane rows the template's SKILL.md.tmpl does not carry.
    not required: tests/test_shipped_audit_runs.py — REPO-SIDE BY CONSTRUCTION (board #199). It runs the SHIPPED tree's own audit, so it cannot live in that tree — there is no nested TEMPLATE for it to run. It is therefore not a PAIRS entry, not in registry/kit.json, and NOT REQUIRED: a bootstrapped factory has no shipped tree, and the gate states that skip rather than reding. The class it guards is the one nothing else could see — the 65 gate registrations in TEMPLATE/tools/audit.py that nothing executed, five of which were found red by hand in a single day.
    not required: tests/test_shipped_mechanism_law.py — REPO-SIDE BY CONSTRUCTION (board #170). It asserts a property of the tree the deliver HANDS OUT — that the shipped law STATES the preconditions of the mechanisms the kit ships — so it reads TEMPLATE/SKILL.md.tmpl and TEMPLATE/tools/*, and a bootstrapped factory has neither. It is therefore not a PAIRS entry, not in registry/kit.json, and NOT REQUIRED: a member inherits the CORRECT law from the template, so the harm is created here and is caught here. The class it guards had no observer at all — the template carried the patrol's receipt legs with 0 of their five precondition clauses stated, measured 2026-09-28, against 1/1/1/2 in the live law — because the two law files are deliberately not byte-paired and test_template_sync is clean over them BY CONSTRUCTION.
    not required: tests/test_synthesize_insights.py — meta-factory-only — its subject tool tools/synthesize_insights.py is live-only and does not ship, the same reason its sibling test_synthesize_interface.py is OPTIONAL. It carries the fixture-driven non-vacuity proofs for #166 and #168, so it is DECLARED rather than left to a by-hand run: a probe the audit never executes is dead text.
    not required: tests/test_synthesize_interface.py — meta-factory-only (n=432 Part 5) — its subject tool tools/synthesize_insights.py is live-only and does not ship.
    not required: tests/test_template_sync.py — meta-factory-only (n=432 Part 5), self-documented — a copy of a pair-guard would need its own pair-guard.
  live tree — direction 4
  predicate: a tests/test_*.py that a RECORDED LAW names as an upholding mechanism must be REGISTERED. The law sources are the rework log's `Prevented by` column — section 11 makes that log a law surface — and the skill's own sentences. A mechanism named and not registered FAILS, NAMING it. A name the tree no longer carries FAILS as a dead reference UNLESS the store `docs/mechanism-splits.json` declares it SPLIT — and then EVERY successor must EXIST and be REGISTERED, because the law's claim is that the probes it cites still run; the retired file must be GONE, and a one-successor entry is a RENAME rather than a split and is refused. SCOPE BOUND: this covers mechanisms a recorded law names; the GENERAL case (a test file that should be a gate and is named nowhere) is #48, already open, and this direction must NOT swallow it. The report PRINTS the entries parsed, the mechanisms named, the split store and the files it read on every run — an unprinted population is the exempt-by-silence surface ruled at n=571.
  sources: rework log: 10 row(s) read, reason=None; skill: NOT READ; mechanism-splits store: NOT READ
  9 mechanism(s) named by law, against 53 registered tests/ path(s) — unregistered: 0, dead references: 0, nested pytest suites (out of the audit's script-gate namespace): 1, 0 problem(s)
    nested (not an audit gate): test_channel_management.py — named by rework log, Entries row 4, present at /root/ai-antispam/tests/handlers/test_channel_management.py (a pytest suite outside the audit's script-gate namespace, so registration does not apply to it)
  PASS  every mechanism a recorded law names is registered
  PASS  the rework log is present and was READ, not silently skipped — rework log: 10 row(s) read, reason=None
  (vacuity leg skipped — this tree does not carry BOTH law sources; the meta-factory does, a bootstrapped factory need not)
  live tree — direction 5
  PASS  the audit was READ, not silently skipped — audit_read=True
  PASS  the live tree has no registration that cannot run its target — []
  live tree — direction 5: 57 registration(s) read, 0 runner-form mismatch(es)
    <sys.executable> tools/ledger.py verify — tool invocation, no target shape to read
    <sys.executable> tests/test_ontology.py — runner=script, target form=script
    <sys.executable> tests/test_rework.py — runner=script, target form=script
    <sys.executable> tests/test_rework_relative_revision.py — runner=script, target form=script
    <sys.executable> tests/test_rework_declared_landed.py — runner=script, target form=script
    <sys.executable> tests/test_single_writer.py — runner=script, target form=script
    <sys.executable> tests/test_ledger_schema.py — runner=script, target form=script
    <sys.executable> tests/test_template_sync.py — runner=script, target absent (presence is direction 3's leg)
    <sys.executable> tools/hygiene.py --clean --namespace <hygiene_namespace(repo_root)> — tool invocation, no target shape to read
    <sys.executable> tools/hygiene.py --audit --namespace <hygiene_namespace(repo_root)> — tool invocation, no target shape to read
    <sys.executable> tools/roadmap.py --audit — tool invocation, no target shape to read
    <sys.executable> tests/test_audit_yield.py — runner=script, target form=dual
    <sys.executable> tests/test_audit_gate_note.py — runner=script, target form=dual
    <sys.executable> tests/test_close_board_recorded.py — runner=script, target form=dual
    <sys.executable> tests/test_close_board_recorded.py — dual-form: 1 wrapper(s) the script path does not reproduce: test_gate_is_registered_in_the_audit
    <sys.executable> tests/test_kit_pin.py — runner=script, target form=script
    <sys.executable> -m pytest tests/test_cron_thinness.py -q — runner=pytest, target form=pytest
    <sys.executable> tests/test_patrol_host_state.py — runner=script, target form=dual
    <sys.executable> tests/test_ledger.py — runner=script, target form=script
    <sys.executable> tests/test_audit_stamp_duration.py — runner=script, target form=script
    <sys.executable> tests/test_audit_cadence_record.py — runner=script, target form=script
    <sys.executable> tests/test_audit_cadence_subject.py — runner=script, target form=script
    <sys.executable> tests/test_ledger_commit_cites_no_rows.py — runner=script, target form=script
    <sys.executable> tests/test_audit_cadence_own_run.py — runner=script, target form=script
    <sys.executable> tests/test_audit_tree_condition.py — runner=script, target form=script
    <sys.executable> tests/test_board_intake_recorded.py — runner=script, target form=dual
    <sys.executable> tests/test_brain_metrics.py — runner=script, target form=script
    <sys.executable> tests/test_close_row_revision.py — runner=script, target form=dual
    <sys.executable> tests/test_close_row_revision.py — dual-form: 19 wrapper(s) the script path does not reproduce: test_probe_a_declared_retirement_with_its_naming_row_passes_and_prints, test_probe_a_fabricated_head_fails_and_a_resolvable_one_passes, test_probe_a_malformed_retirement_file_fails_rather_than_raising, test_probe_a_retirement_whose_naming_row_is_absent_is_an_error, test_probe_a_stale_retirement_excuses_nothing, test_probe_accepts_a_compliant_close_row, test_probe_accepts_a_row_that_describes_the_field_before_naming_it, test_probe_an_absent_retirement_file_is_not_a_defect, test_probe_excuses_pre_boundary_rows_without_calling_them_clean, test_probe_ignores_non_close_events, test_probe_rejects_a_close_row_with_no_revision, test_probe_rejects_a_prose_mention_without_the_field, test_probe_rejects_a_pure_digit_comment_id, test_probe_rejects_a_telemetry_trailer_match, test_probe_rejects_an_unparseable_timestamp, test_probe_rejects_an_unreadable_revision, test_probe_the_existence_leg_reads_the_canonical_run_not_the_whole_detail, test_probe_the_field_predicate_is_shared_not_reimplemented, test_probe_the_population_guard_is_proportional
    <sys.executable> tests/test_commit_close_keyword.py — runner=script, target form=dual
    <sys.executable> tests/test_commit_close_keyword.py — dual-form: 1 wrapper(s) the script path does not reproduce: test_the_hook_names_gate_files_that_exist_and_define_their_predicate
    <sys.executable> tests/test_commit_pair_hook.py — runner=script, target form=script
    <sys.executable> tests/test_commit_session_trailer.py — runner=script, target form=script
    <sys.executable> tests/test_criteria_count.py — runner=script, target form=script
    <sys.executable> tests/test_docs_sync.py — runner=script, target form=script
    <sys.executable> tests/test_duplicate_prose.py — runner=script, target form=script
    <sys.executable> tests/test_gate_fixtures_closure.py — runner=script, target form=dual
    <sys.executable> tests/test_gate_invocation_mode.py — runner=script, target form=script
    <sys.executable> tests/test_gate_registration.py — runner=script, target form=script
    <sys.executable> tests/test_ledger_identity.py — runner=script, target form=script
    <sys.executable> tests/test_ledger_index.py — runner=script, target form=script
    <sys.executable> tests/test_ledger_no_shrink.py — runner=script, target form=script
    <sys.executable> tests/test_publish.py — runner=script, target form=script
    <sys.executable> tests/test_questions.py — runner=script, target form=script
    <sys.executable> tests/test_registry.py — runner=script, target form=script
    <sys.executable> tests/test_roadmap_transition.py — runner=script, target form=script
    <sys.executable> tests/test_skill_version_contract.py — runner=script, target form=dual
    <sys.executable> tests/test_skill_version_contract.py — dual-form: 6 wrapper(s) the script path does not reproduce: test_probe_a_leading_separator_in_the_body_is_not_swallowed, test_probe_a_quoted_version_in_the_body_is_not_the_declaration, test_probe_a_section_separator_does_not_close_the_frontmatter, test_probe_an_empty_window_is_a_stated_skip, test_probe_the_predicate_is_pure_and_offline, test_probe_the_script_form_prints_its_population
    <sys.executable> tests/test_subject_anchor.py — runner=script, target form=script
    <sys.executable> tests/test_template_integrity.py — runner=script, target form=dual
    <sys.executable> tests/test_template_integrity.py — dual-form: 5 wrapper(s) the script path does not reproduce: test_a_dropped_tuple_is_impossible_to_read_past, test_a_path_with_no_readable_age_is_a_violation, test_a_reader_that_under_reads_is_refused, test_a_young_uncommitted_path_is_not_yet_a_violation, test_the_pair_guard_is_read_as_syntax_not_as_a_pattern
    <sys.executable> -m pytest tests/test_binding_mechanism_exists.py -q — runner=pytest, target form=pytest
    <sys.executable> -m pytest tests/test_close_telemetry_provenance.py -q — runner=pytest, target form=pytest
    <sys.executable> -m pytest tests/test_hygiene_inflight.py -q — runner=pytest, target form=pytest
    <sys.executable> -m pytest tests/test_hygiene_namespace.py -q — runner=pytest, target form=pytest
    <sys.executable> -m pytest tests/test_insights_gate_recorded.py -q — runner=pytest, target form=pytest
    <sys.executable> -m pytest tests/test_ledger_close_preflight.py -q — runner=pytest, target form=pytest
    <sys.executable> -m pytest tests/test_registry_attest.py -q — runner=pytest, target form=pytest
    <sys.executable> -m pytest tests/test_registry_render.py -q — runner=pytest, target form=pytest
    <sys.executable> -m pytest tests/test_score_gate_recorded.py -q — runner=pytest, target form=pytest
    <sys.executable> -m pytest tests/test_self_audit_push_gate.py -q — runner=pytest, target form=pytest
    <sys.executable> -m pytest tests/test_audit_rates.py -q — runner=pytest, target form=dual
    <sys.executable> -m pytest tests/test_audit_rates.py -q — dual-form: 4 wrapper(s) the script path does not reproduce: test_gate_is_registered_in_the_audit, test_no_SECOND_arithmetic_site_renders_the_yield, test_the_row_and_the_report_state_the_SAME_yield, test_the_run_row_duration_comes_from_the_argument_not_a_literal
  synthetic probes — the declared basis is RESOLVED (#125)
  PASS  a MOVED BLOB is stale on leg A — StaleBudget(key='tests/test_moved.py', legs=('bytes',), detail='blob fc1a363a91ca97a0add9b1434583794701b6a988 -> 1189909e5241c4574b68f2d04b784426087d5697')
  PASS  a MOVED REGISTRATION is stale on leg B with its bytes held — StaleBudget(key='tests/test_runner_moved.py', legs=('runner',), detail='registration <sys.executable> tests/test_runner_moved.py -> <sys.executable> -m pytest tests/test_runner_moved.py')
  PASS  an entry whose bytes AND registration both held is not reported — None
  PASS  the sweep reported exactly the two entries that moved — 2 stale: ['tests/test_moved.py', 'tests/test_runner_moved.py']
  PASS  the account states its count, its population AND the revision it compared against — 2 of 3 declared entries no longer describe what runs — the gate's own blob and its registered argv, compared against HEA
  PASS  an unresolvable measured_at is named WITH the count it covers — NOT EXAMINED — 1 entry carry no reachable `measured_at` here (tests/test_moved.py); measured_at resolves to nothing for 
  PASS  the entry that could not be reached is not reported as stale — []
  PASS  the entry that COULD be reached was still examined, and the account says so — 0 of 2 declared entries no longer describe what runs — the gate's own blob and its registe
  PASS  a directory with no resolvable HEAD reports NOT RUN, not clean — NOT RUN — no resolvable HEAD under /tmp/tmpqy80o9hh, so there is no revision to compare a declared basis against. 1 decl
  PASS  and it names how many declared entries it left UNEXAMINED — on to compare a declared basis against. 1 declared entry left UNEXAMINED rather than reported clean.
  PASS  an entry with no measured_at still raises, naming the key — /tmp/tmps5_18oz0/gates.json: gates['tests/test_no_basis.py'] has no `measured_at` key — a budget with no stated basis is
  PASS  a budget that is not margin_x x measured_sec still raises — /tmp/tmps056x1ww/gates.json: gates['tests/test_same.py']: budget_sec 9.0 is not margin_x x measured_sec (4.0 x 1.0 = 4.0
  synthetic probes — #588: a PRESENT path that is not a manifest is refused
  PASS  a bare directory (`.`) at the manifest path raises rather than reading as absent — .: the manifest path is present but is not a regular file (a directory) — refusing to report an absent manifest over a path that e
  PASS  the message names the path it refused — .: the manifest path is present but is not a regular file (a directory) — refusing to report an absent manifest over a path that e
  PASS  the message says it is not a regular file — .: the manifest path is present but is not a regular file (a directory) — refusing to report an absent manifest over a path that e
  PASS  the message does NOT reprint the false-absence phrase it removes — .: the manifest path is present but is not a regular file (a directory) — refusing to report an absent manifest over a path that e
  PASS  a DIRECTORY named *.json raises identically, not as a fallback — /tmp/tmpw86bi2ep/gates.json: the manifest path is present but is not a regular file (a directory) — refusing to report an absent m
  PASS  and it is identified as a directory, so the reader knows which shape it met — /tmp/tmpw86bi2ep/gates.json: the manifest path is present but is not a regular file (a directory) — refusing to report an absent m
  PASS  the fixture really is a DANGLING link — exists() cannot see it, lexists() can — is_symlink=True exists=False lexists=True
  PASS  a dangling symlink at the manifest path raises — /tmp/tmpks4kfz_4/gates.json: the manifest path is present but is not a regular file (a symlink whose target does not resolve) — re
  PASS  and it says the target does not resolve, rather than claiming absence — /tmp/tmpks4kfz_4/gates.json: the manifest path is present but is not a regular file (a symlink whose target does not resolve) — re
  PASS  a directory reaching the reader through the ENV override raises too — /tmp/tmp07occhec/gates.json: the manifest path is present but is not a regular file (a directory) — refusing to report an absent m
  PASS  and the refusal names the OVERRIDE path, so the environment was read — /tmp/tmp07occhec/gates.json: the manifest path is present but is not a regular file (a directory) — refusing to report an absent m
  PASS  and the variable was restored, so the probe leaks nothing — left as None
  PASS  an absent manifest still returns the declared fallback, never a raise — default_sec=120.0 gates={}
  PASS  and its default_source is BYTE-IDENTICAL to the shipped wording — no manifest at /tmp/tmpc5ee4bzf/nothing-here.json — declared fallback
  PASS  and its stale_note is too, so nothing else moved in the absent branch — no manifest at /tmp/tmpc5ee4bzf/nothing-here.json — nothing declared to sweep
  synthetic probes — #93: a timeout is UNKNOWN, never a 0.0s FAIL
  PASS  a timed-out gate carries the DISTINCT timeout exit code, never the generic 99 — exit_code=124, TIMEOUT_EXIT_CODE=124
  PASS  a timed-out gate is UNKNOWN, never a plain failure — unknown=True, passed=False
  PASS  the recorded duration is the MEASURED elapsed time, not the 0.0 literal — 0.05s against a 0.05s budget
  PASS  the cause is in stderr, where the FAIL line reads it — budget exhausted: the gate was killed at 0.05s after running 0.05s, and killed AGAIN on th
  PASS  a timeout is retried exactly ONCE — attempts=2
  PASS  the retry is RECORDED, with the first attempt's own elapsed time — retried=True, first_attempt_sec=0.05
  PASS  a genuine FAIL keeps its own exit code and is NEVER retried — exit_code=3, attempts=1, retried=False
  PASS  a gate that CHOOSES the kill code is UNKNOWN, never a plain failure — unknown=True, passed=False
  PASS  ... and it carries the kill code it chose, unchanged — exit_code=124
  PASS  ... and it COMPLETED, so it is never retried and its wall-clock is a measurement — attempts=1, lower_bound=False
  PASS  ... so the payload reads UNKNOWN, not DEGRADED -- the two surfaces now agree — status=UNKNOWN, unknown=1
  PASS  ARM 2 (discriminator): a genuine failure stays FAILED, never unknown — unknown=False, passed=False
  PASS  a real timed-out gate forces UNKNOWN, never DEGRADED — UNKNOWN
  PASS  the timed-out gate is counted in NO pass total — all_known_pass=True, examined=1
  PASS  the timed-out gate is not filed as a failure either — failed=0, unknown=1
  PASS  a timed-out gate is NOT healthy -- no verdict is not a green — False
  PASS  the headline renders UNKNOWN as loudly as FAIL — Status: UNKNOWN — 1 of 2 gate(s) exhausted their budget and returned NO verdict; they are counted in no pass total, and this is NO
  PASS  the headline states the population it reports over — Status: UNKNOWN — 1 of 2 gate(s) exhausted their budget and returned NO verdict; they are counted in no pass total, and this is NO
  PASS  a KILLED gate records its duration as a LOWER BOUND — duration_is_lower_bound=True, duration_sec=0.05
  PASS  a COMPLETED gate records a measurement, never a bound — duration_is_lower_bound=False, duration_sec=0.0
  PASS  the committed report renders a kill as `>=N`, never as a bare measurement — | `/bin/sleep 5` | `UNKNOWN` | `>=0.05s` | budget exhausted: the gate was killed at 0.05s after running 0.05s,
  PASS  and the completed gate's row carries NO bound marker — 3 gate row(s) rendered
  PASS  a KILLED gate under a stale basis names the staleness ON ITS OWN LINE —   [UNKNOWN] /bin/sleep 5 (>=0.05s) — budget 0.05s [STALE:bytes] blob fc1a363a91ca97a0add9b1434583794701b6a988 -> 1189909e5241c4574b68f2d04b784426087d5697 [retried once — 2 attempt(s)]
  PASS  the joined line still states the kill it is joined to —   [UNKNOWN] /bin/sleep 5 (>=0.05s) — budget 0.05s [STALE:bytes] blob fc1a363a91ca97a0add9b1434583794701b6a988 -> 1189909
  PASS  removing the join leaves the kill rendering as a bare UNKNOWN —   [UNKNOWN] /bin/sleep 5 (>=0.05s) — budget 0.05s [retried once — 2 attempt(s)]
  PASS  the killed/UNKNOWN-under-a-stale-basis population names its members — joined=['tests/test_moved.py'] without_the_join=[]
  PASS  a PASSING gate under a stale basis is marked yet stays out of the kill count —   [PASS] java -version (1.0s) [STALE:bytes] blob fc1a363a91ca97a0add9b1434583794701b6a988 -> 1189909e5241c4574b68f2d04b784426087d5697
  PASS  every gate that ran to a verdict passed -> GREEN — GREEN
  PASS  a gate that FAILED -> DEGRADED — DEGRADED
  PASS  a gate that returned NO verdict -> UNKNOWN — UNKNOWN
  PASS  the three states render as three DISTINCT headlines — Status: HEALTHY (GREEN) — all 1 ga / Status: DEGRADED (FAIL) — 1 gate(s / Status: UNKNOWN — 1 of 2 gate(s) e
  PASS  GREEN is the only state that is healthy
  PASS  a measured FAIL outranks an UNKNOWN -- a statement beats its absence — DEGRADED
  PASS  the UNKNOWN gates are still NAMED beside the failure, never dropped — Status: DEGRADED (FAIL) — 1 gate(s) failed, and 1 more exhausted their budget (UNKNOWN, no verdict taken)
  PASS  a skipped suite reports no status at all — None
  PASS  a skipped suite is not healthy — None
  PASS  a skipped suite keeps its own headline — Status: GATES SKIPPED (metrics only)
  PASS  the cause is the LAST non-blank line, not the first — 'the cause'
  PASS  the cause is collapsed onto one line — 'a b'
  PASS  an output-less gate yields NO cause rather than a blank one
  PASS  the cause is BOUNDED, so a chatty gate cannot flood the line — 200
  PASS  a script-shaped failure records its COUNT, not only a tail specimen — 'ledger schema: 6 problem(s) in /repo/evidence/ledger.jsonl — line 95: n=95 (#101) — malformed telemetry'
  PASS  the count is kept AND a specimen follows it — 'ledger schema: 6 problem(s) in /repo/evidence/ledger.jsonl — line 95: n=95 (#101) — malformed telemetry'
  PASS  the OLD predicate loses the count on this shape — the defect, reproduced — 'line 95: n=95 (#101) — malformed telemetry'
  PASS  a pytest-shaped failure is UNCHANGED — its reason is already last — '=========== 1 failed, 31 passed in 1.42s ==========='
  PASS  the cause is BOUNDED on both shapes — 103
  PASS  an output-less gate yields NO cause rather than a blank one — ''
  PASS  a count line that IS the last line is not duplicated — 'only 2 problem(s) here'
  PASS  the DECLARED count is carried when the noun is one the heuristic misses — 'oc-cause-count: 7 — hygiene declaration: 0 path(s) declared live, 0 declared scratch'
  PASS  ...and the count leads, so an informational tail cannot displace it — 'oc-cause-count: 7 — hygiene declaration: 0 path(s) declared live, 0 declared scratch'
  PASS  the ADVISORY block is not read as the cause — the ordering hazard the ruling names — 'oc-cause-count: 7 — hygiene declaration: 0 path(s) declared live, 0 declared scratch'
  PASS  the OLD predicate records the informational line here — the defect, reproduced — 'hygiene declaration: 0 path(s) declared live, 0 declared scratch' | 'oc-cause-count: 7 — hygiene declaration: 0 path(s) declared live, 0 declared scratch'
  PASS  with NO marker the noun heuristic still works, as before — "ledger schema: 6 problem(s) in /repo/evidence/ledger.jsonl — line 15: n=15 (#41) — actor 'worker' is not authorized"
  PASS  with NO marker a pytest-shaped failure is still UNCHANGED — '=========== 1 failed, 31 passed in 1.42s ==========='
  PASS  the marker is read ANCHOR-FIRST — a mid-sentence mention is not a declaration — None
  PASS  a marker line declares its integer — 0
  PASS  an output with no marker declares nothing rather than zero — None
  PASS  hygiene.py EMITS the marker on its failure path — the adoption, not merely the predicate — 1
  PASS  the audit's recorded cause for hygiene LEADS with that declared count — 'oc-cause-count: 1 — - required committed, but absent: /nonexistent/oc-205-adoption-probe'
  PASS  the OLD predicate would send the reader to a line that cannot clear the gate — the defect, reproduced — 'oc-cause-count: 1 — - required committed, but absent: /nonexistent/oc-205-adoption-probe'
  PASS  a PASSING gate carries no note — None
  PASS  a failing gate carries the note, with its COUNT — 'ledger schema: 6 problem(s) in /repo/x — line 15: n=15 (#41)'
  PASS  the note is BOUNDED, so it cannot grow the table cell — 60
  PASS  a silent failing gate carries an EMPTY note, never an absent key — ''
  PASS  prose on BOTH streams still keeps stdout — the additive arm, unchanged — 'last stdout line'
  PASS  a SILENT stdout falls through to a declaring stderr — already worked, must not regress — 'oc-cause-count: 4 — - a specimen'
  PASS  a PROSE stdout no longer buries a marker declared on stderr — #533 itself — 'oc-cause-count: 1'
  PASS  and the advisory that used to win is NOT what the reader is shown — 'oc-cause-count: 1'
  PASS  the count line outranks prose but not the marker — the rank order, not just the fix — '3 problem(s) in /repo/x'
  PASS  BOTH streams declaring DISAGREEING counts names BOTH -- never a silent stdout win — 'counts DISAGREE -- stdout 2, stderr 5: oc-cause-count: 2 — - a specimen | oc-cause-count: 5 — - b specimen'
  PASS  and the disagreement is STATED as one, so the reader sees the two halves conflict — 'counts DISAGREE -- stdout 2, stderr 5: oc-cause-count: 2 — - a specimen | oc-cause-count: 5 — - b specimen'
  PASS  counts that AGREE are not a disagreement -- the additive arm, stdout as before — 'oc-cause-count: 3 — - a specimen'
  PASS  reported_cause itself takes ONE text and is unchanged, so the fix lives in the join — ['text', 'limit']
  live manifest — the declared revisions, swept
  live manifest — declared fallback in use, nothing declared to sweep ( manifest at /root/ai-antispam/registry/gates.json — declared fallback)
  synthetic probes — #226: the load a measurement was taken at
  PASS  a manifest with no `load_at_measure` still loads — the field is OPTIONAL — loaded
  PASS  and the budget it declares is read unchanged — {'tests/test_same.py': 4.0}
  PASS  its note states the POPULATION that carries no load — load_at_measure: 0 of 2 declared bases carry it (neither `default` nor any of the 1 entries), so a kill on this manifest cannot be
  PASS  and names the harm, so the gap is readable rather than silent — load_at_measure: 0 of 2 declared bases carry it (neither `default` nor any of the 1 entries), so a kill on this manifest cannot be told from a busy box on any g
  PASS  the state the load was measured at is carried, by key — {'tests/test_same.py': 0.31}
  PASS  the account counts the declarers against the population, entries AND `default` — load_at_measure: 1 of 3 declared bases carry it (none on `default`, 1 of 2 declared entries); the other 2 state none, so for those a kill cannot be to
  PASS  and the note still names the population that declares NONE — load_at_measure: 1 of 3 declared bases carry it (none on `default`, 1 of 2 declared entries); the other 2 state none, so for those a kill cannot be told from a busy box
  PASS  a non-numeric `load_at_measure` on an entry is refused, naming the key — /tmp/tmpz321kz_4/gates.json: gates['tests/test_same.py'].load_at_measure must be a positive number, not 'busy' -- the field states
  PASS  and a non-positive one on `default` is refused too — /tmp/tmpkpzxvjtf/gates.json: default.load_at_measure must be a positive number, not -1.0 -- the field states the load the measurem
  PASS  the live manifest LOADS — a load reading is never a precondition — loaded
  live manifest — none declared (the kit's bare default is in use)
  synthetic probes — #208: the default's stated derivation is upheld
  PASS  a default that cannot contain its stated population is REFUSED — /tmp/tmp8n4habwc/gates.json: the default's stated derivation does not hold — default.measured_sec 1.0 is not the largest recorded 
  PASS  a default BELOW margin_x x measured_sec is refused — /tmp/tmpei1ooisr/gates.json: the default's stated derivation does not hold — default.budget_sec 1.0 is BELOW its own stated deriva
  PASS  a stated largest that is not the population's max is refused — /tmp/tmplehcm2g3/gates.json: the default's stated derivation does not hold — default.measured_sec 1.0 is not the largest recorded 
  PASS  a PARTIAL derivation is refused, naming the absent keys — /tmp/tmp1dzdohj8/gates.json: the default's stated derivation does not hold — default states a PARTIAL derivation -- no ['margin_x'
  PASS  a BARE default — the shipped example's shape — still loads — default_sec=True
  PASS  and the intrinsic half is silent on it, so no relation is invented — []
  PASS  a gate falling through but ABSENT from the stated population is reported — ["1 gate(s) fall through to the default and are NOT in the stated population: ['tests/test_unrecorded.py'] -- the default is re-de
  PASS  a recorded name that no longer falls through is reported too — nothing reported for an empty live population
  PASS  margin_for(v) x v increases with v over the measured span — [0.95, 1.75, 4.75, 16.75, 68.75, 280.15, 1008.75]
  PASS  and it expands to 4v + 0.75, the closed form the predicate's comment names — [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
  live manifest — the default's derivation, and its population
  live default — this tree carries no registry/gates.json (a bootstrapped factory ships gates.example.json and reads the declared fallback); nothing to check
  synthetic probes — #269: the re-derivation leg bites, and its population is printed
  SKIP  no tools/gate_budget_rederive.py in this tree — the re-derivation leg is this factory's standing duty, not yet part of the shipped kit
  SKIP  no tools/gate_budget_rederive.py in this tree — the re-derivation leg is this factory's standing duty, not yet part of the shipped kit
  SKIP  no tools/gate_budget_rederive.py in this tree — the re-derivation leg is this factory's standing duty, not yet part of the shipped kit
  SKIP  no registry/gates.json in this tree — factory data, nothing to re-derive
  synthetic probes — #442: the not-examined population is rendered once
  SKIP  no tools/gate_budget_rederive.py in this tree — the re-derivation leg is this factory's standing duty, not yet part of the shipped kit
  synthetic probes — #307: a budget-exhausted gate reaches the trigger
  SKIP  no tools/gate_budget_rederive.py in this tree — the re-derivation leg is this factory's standing duty, not yet part of the shipped kit
  synthetic probes — #564: growth is a third source, reported first, derived on trend
  SKIP  no tools/gate_budget_rederive.py in this tree — the re-derivation leg is this factory's standing duty, not yet part of the shipped kit
  synthetic probes — #578: the containment breakdown sums, or the run refuses
  SKIP  no tools/gate_budget_rederive.py in this tree — the re-derivation leg is this factory's standing duty, not yet part of the shipped kit
  synthetic probes — #240: a gate's own cap vs its audit budget
  PASS  a budget BELOW its gate's own cap is a PROBLEM — ['tests/test_synthetic_cap.py: budget_sec 50.0 <= its own cap SYNTHETIC_TIMEOUT_SEC=100 -- the audit kills the gate before it can report its own timeout (#240)']
  PASS  and the inverted pair is NOT counted among the compared — []
  PASS  a budget ABOVE its gate's own cap is clean — []
  PASS  and the pair is PRINTED among the compared — ['tests/test_synthetic_cap.py: SYNTHETIC_TIMEOUT_SEC=100 < budget_sec=150.0']
  PASS  a gate with no declared cap is NAMED, not silently passing — no_cap=['tests/test_nocap.py'] problems=[] compared=[]
  PASS  the probe's own _BUDGET_SEC constant is not read as a gate cap — {}
  live manifest — #240: the cap/budget ordering, and its population
  SKIP  no registry/gates.json in this tree -- factory data, nothing to compare
  synthetic probes — #295: every invoked gate DECLARES its exit
  PASS  direction 6 — an UNDECLARED invoked gate is REFUSED by name — ['tests/test_synthetic.py is invoked by the audit and declares NO exit — a gate whose only exits are barred is
  PASS  direction 6 — and the report counts it as missing — {'invoked': 1, 'declared': 0, 'classes': {}, 'missing': ['tests/test_synthetic.py'], 'stale': [], 'unresolved'
  PASS  direction 6 — the SAME tree, declared, is CLEAN — []
  PASS  direction 6 — a declaration for a gate that does NOT run is STALE — ['tests/test_ghost.py declares an exit but is NOT invoked by the audit — a declaration for a gate that does no
  PASS  direction 6 — a class outside the vocabulary is REFUSED — ["tests/test_synthetic.py declares the class 'vibes', which is outside the vocabulary (write-path-refusal, exe
  PASS  direction 6 — a table that does NOT resolve is REFUSED — ["tests/test_synthetic.py is `exemption-table` and its table 'docs/typo-exemptions.json' does not resolve — th
  PASS  direction 6 — a table present only as its `.example.json` skeleton RESOLVES — []
  PASS  direction 6 — a declared table the gate's OWN SOURCE never names is DRIFT — ['tests/test_synthetic.py declares the exemption table docs/synthetic-exemptions.json but its own source never
  PASS  direction 6 — and the gate NAMING its declared table is not DRIFT — []
  PASS  direction 6 — an exemption table NO declaration names is REPORTED — ['docs/orphan-exemptions.json is a factory exemption table that NO declaration names — an exit whose reader is
  PASS  direction 6 — and a `.example.json` SKELETON is never counted as an orphan — ['docs/orphan-exemptions.json']
  PASS  direction 6 — `exit-owed` {'class': 'exit-owed'} is REFUSED for naming no board item — ['tests/test_synthetic.py is `exit-owed` and declares board=None — the debt must name the 
  PASS  direction 6 — `exit-owed` {'class': 'exit-owed', 'board': 0} is REFUSED for naming no board item — ['tests/test_synthetic.py is `exit-owed` and declares board=0 — the debt must name the boa
  PASS  direction 6 — `exit-owed` {'class': 'exit-owed', 'board': '317'} is REFUSED for naming no board item — ["tests/test_synthetic.py is `exit-owed` and declares board='317' — the debt must name the
  PASS  direction 6 — and the same entry WITH its board item is CLEAN — []
  PASS  direction 6 — a gate file that is NOT invoked is outside the population — ['tests/test_synthetic.py']
  #604 — the wall-kill streak, the probe, and the licence
  SKIP  no tools/gate_budget_rederive.py in this tree — the re-derivation leg is factory-local, so its #604 probes cannot run here
  SKIP  no tools/gate_budget_rederive.py in this tree — the re-derivation leg is factory-local, so its #604 probes cannot run here
  SKIP  no tools/gate_budget_rederive.py in this tree — the re-derivation leg is factory-local, so its #604 probes cannot run here
  SKIP  no tools/gate_budget_rederive.py in this tree — the re-derivation leg is factory-local, so its #604 probes cannot run here
  SKIP  no tools/gate_budget_rederive.py in this tree — the re-derivation leg is factory-local, so its #604 probes cannot run here
  SKIP  no tools/gate_budget_rederive.py in this tree — the re-derivation leg is factory-local, so its #604 probes cannot run here
  SKIP  no tools/gate_budget_rederive.py in this tree — the re-derivation leg is factory-local, so its #604 probes cannot run here
  SKIP  no tools/gate_budget_rederive.py in this tree — the re-derivation leg is factory-local, so its #604 probes cannot run here
  live manifest — #295: the exit declaration, its classes and its coverage
  PASS  direction 6 — the kit ships `gates.example.json` and no live manifest; the live leg SKIPS with its reason (never a silent pass)

gate registry FAILED: 1 check(s)
  - every declared gate is registered, and every registered gate declares itself
```


---

## 3. Calibration Spot-Check (Sampled Task)

- **Sampled Subject:** `#89`
- **Intake Recorded:** `NO`
- **Claim Lock Recorded:** `NO`
- **Close Verified:** `YES`
- **Sequence Integrity:** `BROKEN`
