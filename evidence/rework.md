# Rework log

**Owns:** every defect, regression and process rollback this factory has produced, with its
root cause and the thing that now prevents it.

The rubric's Stability criterion (O2) asks for two rates: **change fail rate** and
**rework rate**. Neither can be computed from memory, and neither means anything without a
denominator. This file is the numerator; the ledger is the denominator.

---

## Schema

| Column | What goes in it |
|---|---|
| **Date** | When the defect was found, `YYYY-MM-DD`. Not when it was introduced |
| **Source** | Who or what surfaced it: an owner instruction, a lane report, a gate, or a review |
| **Defect** | What was wrong, stated so a reader can tell whether it is fixed |
| **Root cause** | Why it happened — the mechanism, not the symptom |
| **Resolution** | The commit or action that fixed it |
| **Prevented by** | The rule, test or gate that stops the recurrence |

---

## Entries

| Date | Source | Defect | Root cause | Resolution | Prevented by |
|---|---|---|---|---|---|
| 2026-09-16 | Incident log review | Bot left channel silently after 6 admins rejected Bot API DM with TelegramForbiddenError | notify_channel_admins caught DM exceptions internally without raising, bypassing outer userbot fallback | Decoupled userbot fallback to check notified_admins == 0 and post in-channel notice before leaving | test_channel_management.py regression suite and live deployment verification |
| 2026-09-17 | Meta-factory survey score (38/76) | Factory dropped from Scalable to Operational band on missing self-audit and measurement substrate | Methodology core and self-audit tools (ledger, audit, rework, ontology) were not ported | Ported tools/ledger.py, tools/audit.py, ONTOLOGY.md, evidence/rework.md, and test gates | tests/test_ontology.py, tests/test_ledger.py, tests/test_rework.py, tests/test_single_writer.py |
| 2026-09-19 | Meta-factory score run (fleet 344/456, advisory 1) | Process 3's daily self-audit cadence was codified but no cron fired it — a law with no upholding mechanism, so the factory's own audit read Cadence MISSED and the newest scorecard went stale | docs/processes.md declared a daily cadence and tools/audit.py enforced it, but no pacemaker job existed in cron_jobs, so the ledger's last run row aged past the 30h threshold and nothing woke the runner | Created cron ai-antispam-self-audit-daily (50 8 * * * Europe/Moscow) delivering to the HQ lane, and named the mechanism in docs/processes.md Process 3 | audit.py's cadence check (ledger run row within 30h) plus tests/test_rework.py |
| 2026-10-05 | Triage lane (second falsification of the same claim in one day) | The ops router's "Renumbering caveat" claimed the 2026-10-04 board migration "moved #3–#48 to #78–#110 and kept #49–#77" — both halves false | The caveat was written from an assumed arithmetic pattern instead of the map's own rows; the map records 62 entries, 0 preserved, old 3–77 → new 49–110 | Corrected the caveat in the ops router SKILL.md: all 62 renumbered, 0 preserved, resolve through the map never by arithmetic, and named the 8 non-uniform items | docs/board-migration/number-map.json is the single source for any pre-2026-10-04 #N; the caveat now names 3,5,6,10,11,15,17,19 so a blanket "+33" cannot pass unnoticed |

---

## What this log is not

- **Not a blame record.** It records defects of the system and process.
- **Not a duplicate of the ledger.** The ledger records state transitions as they happen.
