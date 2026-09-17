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

---

## What this log is not

- **Not a blame record.** It records defects of the system and process.
- **Not a duplicate of the ledger.** The ledger records state transitions as they happen.
