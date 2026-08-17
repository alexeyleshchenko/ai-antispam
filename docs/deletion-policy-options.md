# Deletion policy — options research

**Date:** 2026-08-17
**Status:** Research / decision input (untracked — not for commit until a direction is chosen)
**Context:** Alexey wants "never delete anything automatically", active/inactive flags, audit + rollback. Suspects better practices exist. This doc researches them and maps them onto the ai-antispam schema (from `docs/deletion-audit.md`).

## The two goals, separated

1. **Audit** — know *who* removed *what*, *when*, *why*, and what it looked like.
2. **Rollback** — undo an unwanted removal.

Soft delete (boolean flag) is the bluntest tool for both — and the research is consistent: it's a *recoverability* pattern, **not** an audit trail (a flag says "row is gone", not who removed it or what it held), and it taxes every query forever.

## Options (from research: pulse.support, thoughtbot, Umur Inan, atlas9, Michal Drozd, Marty Friedel)

| Option | Mechanism | Audit? | Rollback? | Query pollution | Fits our scale? |
|---|---|---|---|---|---|
| **A. Soft delete** (your proposal) | `is_active`/`deleted_at` on rows; every query filters | ⚠️ partial — "gone" but not who/why | ✅ instant (flip flag) | ❌ high — every SELECT/JOIN needs the filter | ✅ perf-wise (tiny DB), ❌ hygiene-wise (whole codebase touched) |
| **B. Soft delete + audit columns** | A + `deleted_by`, `deleted_reason` | ✅ better | ✅ | ❌ same as A | same as A |
| **C. Hard delete + event log** | Keep DELETE; BEFORE it, append a row snapshot to an `entity_events` table (entity, id, action, reason, old-row JSONB, ts) | ✅✅ who/when/why/what | ⚠️ manual — reconstruct from log (rare = manual is fine) | ✅ none — live queries unchanged | ✅✅ lean, one table |
| **D. Archive tables** | Trigger/app moves deleted rows to `groups_archive` etc. (or one generic JSON archive) | ✅ | ✅ (restore from archive) | ✅ none | ✅ but schema duplication; atlas9's generic-JSON-trigger variant is the lean version |
| **E. Status/lifecycle column** | `status` = `active`/`paused`/`left`/`archived` — deletion is *state*, not removal | ✅ (state + transitions) | ✅ (reactivate) | ⚠️ moderate — filters on `status`, but semantically meaningful | ✅✅ best domain fit (below) |
| **F. Temporal tables / CDC** | System-versioned rows (pg_temporal / Debezium+WAL) | ✅✅ full history | ✅ time-travel | ✅ none | ❌ overkill — infra, ops burden, zero need at 46 groups |

## What "deletion" actually means in our domain (from the audit)

This is the key insight: **almost none of our deletions are deletions.** They're lifecycle transitions with business meaning:

| Current behavior | Trigger | What it really is |
|---|---|---|
| `cleanup_group_data` — group removed | Bot kicked | Group **left** the chat |
| Group removed | No rights > 7 days | Group **paused** (no-rights) |
| Group removed | Unpaid, day 7 | Group **paused** (payment) |
| Group removed | Notification total failure | Group **left** (unreachable) |
| Pending example deleted | Daily TTL, 3 days | **Data destruction** — silent, no warning |
| `message_lookup_cache` TTL | Daily | Cache — fine to delete (re-derivable) |

Option E (status) maps 1:1 onto these. A group is never "deleted" — it's `active → paused → left`, and reactivation (re-add, payment, rights restored) flips it back. That's rollback *by design*, and the state transitions themselves are the audit.

## Recommendation — hybrid (C + E), tailored per entity

| Entity | Today | Recommended |
|---|---|---|
| **groups** (the only hard-deleted root) | hard DELETE | `status` column (`active`/`paused`/`left`) — cleanup becomes an UPDATE; re-add path flips back to `active`; keep `moderation_enabled` as-is |
| **group_administrators / approved_members** (mappings) | hard DELETE | keep hard DELETE — re-creatable on reactivation (`update_group_admins` already does it); the `groups` row is the audit anchor |
| **spam_examples — confirmed** | immortal (never deleted) | unchanged — already the desired end state |
| **spam_examples — pending** | TTL-delete after 3 days | **stop destroying** — archive instead (`archived = true` + timestamp) or extend TTL; keeps data for later analysis (Alexey's ask) |
| **admins** | never deleted | unchanged (dead `remove_admin` path) |
| **caches** (`message_lookup_cache`, `message_history` ring) | TTL / ring-drop | unchanged — re-derivable, no audit value |
| **new: `entity_events` log** | — | append-only table: any *automatic* status change / cleanup writes (entity_type, entity_id, action, reason, old_state JSONB, ts) — the who/when/why audit trail |

Why not pure soft delete everywhere: the mappings and caches would force cascade logic (soft-delete children? filter every join?) for zero rollback gain — reactivation re-creates them anyway. The group row + event log carry the full history.

## Effort estimate (order of magnitude)

- **Option A/E on `groups` only**: ~5 files (group_operations, status_handlers, admin listings, daily jobs, re-add path) + tests — small.
- **+ `entity_events`**: 1 table + 1 writer module + wiring into the 4 cleanup triggers — small.
- **Pending-example archive**: 1 column + TTL job change + tests — trivial.
- **Full soft delete everywhere**: 10+ files, every query, cascade decisions — the expensive path, least value.

## Decision needed

1. Direction: **E+C hybrid** (recommended) / pure soft delete / hard delete + event log / other?
2. Pending examples: archive (recommended) or keep TTL?
3. Scope of "never delete automatically": does it include the caches (recommended: no — they're re-derivable)?
