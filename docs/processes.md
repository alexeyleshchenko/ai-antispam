# ai-antispam — Processes

**Version:** 0.1.0  
**Owns:** the registry of operational processes running across this factory.

Every recurring activity in the factory maps to a defined process with an explicit owner, declared cadence, client, implementer, and quality criteria.

---

## Process Registry

### Process 1: Inbound Triage & Moderation
- **Process Owner:** HQ Lane
- **Process Client:** Channel Operators and Subscribers
- **Process Implementer:** `ai-antispam` runtime container (`apps`), LLM Classifier, Regex Rule Evaluator
- **Cadence:** Continuous event-driven (Telegram webhook `/process-tg-updates`, MAX webhook)
- **Product:** Spam-free comments, message deletion receipts, user restrictions/bans
- **Quality Criteria:** Zero false positives on legitimate user comments, sub-second latency on deletion, zero unhandled webhook crashes

### Process 2: Outreach Campaign & Lead Triage
- **Process Owner:** Outreach Lane
- **Process Client:** Product Owner (Alexey)
- **Process Implementer:** `outreach` scripts, `watch_poll.py`, cron pacemakers
- **Cadence:** Scheduled pacemaker ticks (every 3h for watch-poll, daily sync at 06:00 UTC)
- **Product:** Qualified channel owner candidates, verified pitch dispatches, reply triage alerts
- **Quality Criteria:** Strict OWNER_HANDLED adherence (never message threads owner answered), 0 duplicate outreach sends, verified DB-backed export

### Process 3: Internal Self-Audit & Conformance
- **Process Owner:** Triage & Self-Audit Runner
- **Process Client:** Factory Owner & Fleet Meta-Auditor
- **Process Implementer:** `tools/audit.py`, `tools/ledger.py`, pytest test suites
- **Cadence:** Daily and on release
- **Pacemaker (upholding mechanism):** cron `ai-antispam-self-audit-daily` (`50 8 * * *`, Europe/Moscow) — fires the runner, stamps `evidence/ledger.jsonl`, commits the dated scorecard. A cadence law with no pacemaker is unenforced (P29); this line is the mechanism, keep the two in step.
- **Product:** Event ledger (`evidence/ledger.jsonl`), dated scorecards (`evidence/scores/<date>.md`), clean invariant checks
- **Quality Criteria:** 100% single-writer ledger integrity, zero unexempted vocabulary drift, complete rework root-cause accounting
