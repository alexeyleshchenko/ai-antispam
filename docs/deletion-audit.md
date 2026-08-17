# Deletion Audit — what gets deleted, when, and by which path

Audited against `main` (2026-08-17). Scope: `groups`, `administrators` (admins/users),
`spam_examples`, plus the cascades and TTL cleanups that touch related tables.

Schema reference (FKs from `database_schema.py`):

| Table | FK / cascade |
|---|---|
| `group_administrators` | `group_id → groups ON DELETE CASCADE`; `admin_id → administrators ON DELETE CASCADE` |
| `approved_members` | `group_id → groups ON DELETE CASCADE` |
| `message_history` | `admin_id → administrators ON DELETE CASCADE` |
| `spam_examples` | `admin_id → administrators ON DELETE CASCADE`; **`chat_id` is a bare BIGINT — no FK** |
| `transactions` | `admin_id → administrators ON DELETE CASCADE` |
| `message_lookup_cache` | `chat_id` bare BIGINT — no FK |

---

## 1. Groups — lifecycle transition (E+C), no hard delete

**Function:** `cleanup_group_data(group_id, status, reason)` (`group_operations.py:181`)
Implements the E+C deletion policy (see `deletion-policy-options.md`): the `groups` row
is **never deleted**. It transitions `status` to `paused`/`left`, snapshots the old row,
and writes an append-only `entity_events` audit row. Only the re-creatable mappings are
hard-deleted, transactionally:
`group_administrators`, `approved_members`.

**Note:** `cleanup_group_data` does **not** delete `spam_examples`, `message_lookup_cache`,
`message_history`, or `transactions` — those survive a group removal. Audit events go to
`entity_events` (append-only; a batch event per TTL cleanup run).

### Triggers (4 independent paths → 2 functions)

| Trigger | Code | When |
|---|---|---|
| **Admin listing discovers inaccessible chat** | `get_admin_groups` (`group_operations.py:326`) | Any `/stats`, `/scan`, admin-groups listing → `bot.get_chat` raises Forbidden → flagged inaccessible → `cleanup_group_data(status=left)`. This is the PR #4 marker-fix path. |
| **Bot lacks rights past grace** | `leave_no_rights_groups` → `perform_complete_group_cleanup(status=paused)` (`no_rights.py:49`) | **Daily job.** Bot missing `delete_messages`/`restrict_members` for > `no_rights_grace_days` (7) → re-verify via `get_chat_member` → `bot.leave_chat` + transition. |
| **No payment, sole payer, day 7** | `leave_sole_payer_groups` → `perform_complete_group_cleanup(status=paused)` (`low_balance.py:170`) | **Daily job.** Depletion warning (day 1), final warning (day 6), at `grace_days` (7) the bot leaves every group where the admin is the only payer. Admin DMed `low_balance.left_groups` — **"training examples are preserved"** is promised to the user here. |
| **Notification total failure** | `notify_admins_with_fallback_and_cleanup` → `perform_complete_group_cleanup(status=left)` (`notifications.py:278`) | Live event. No admin reachable in private **and** group message send fails → leave + transition. |

`perform_complete_group_cleanup` (`notifications.py:27`): `bot.leave_chat` (Telegram side) then `cleanup_group_data` (DB side).

**Rollback:** re-adding the bot to a paused/left group reactivates it — `update_group_admins`
flips `status → active` and clears stale `no_rights_detected_at`, logging a `group_reactivated`
event (`reason: re_add`).

### Telegram-side side-effects of group removal
- `bot.leave_chat` (always, on cleanup)
- `bot.delete_message` — per-spam-message deletion during moderation (`handle_spam.py:432`), not on removal
- `bot.ban_chat_member` / `ban_chat_sender_chat` — per-spamster ban (`handle_spam.py:488`), not on removal

---

## 2. Admins & users (administrators)

**Function:** `remove_admin(admin_id)` (`admin_operations.py:502`)
Deletes `administrators` row → FK **CASCADE** wipes `group_administrators`, `message_history`,
`spam_examples`, `transactions` for that admin.

**But: `remove_admin` has ZERO callers in the entire source** — dead code. Admin accounts are
**never deleted** by any live path. Confirmed examples survive group removal because the
admin row they're attributed to is never removed (the `ON DELETE CASCADE` never fires).

### approved_members (the "users" dimension)
Two live deletion paths:

| Path | Code | When |
|---|---|---|
| **Spam kick** | `remove_member_from_group(user_id, chat_id)` (`handle_spam.py:521`) | On high-confidence spam: after ban + message delete, the member's `approved_members` row is removed (specific group). |
| **Spam-confirmation cleanup** | `remove_member_from_group(member_id)` — group=None (`private_handlers.py:400`) | In the best-effort cleanup tasks after an admin **confirms a spam example** by user — removes the user from `approved_members` in **all** groups. |

`remove_member_from_group` only touches `approved_members` (and bumps `last_active`). It does
not delete a user entity anywhere — users exist only as `approved_members` rows and
`message_lookup_cache`/`spam_examples` provenance.

---

## 3. Spam examples

| Path | Code | When |
|---|---|---|
| **Pending TTL** | `cleanup_pending_spam_examples` (`spam_examples.py:81`) | **Daily job.** `DELETE … confirmed = false AND created_at < NOW() - TTL`. TTL = `pending_spam_ttl_days: 3`. Silent, no user-facing warning. |
| **Replace-on-confirm (dedupe)** | `add_spam_example` (`spam_examples.py:284`) | Not retention: before inserting a confirmed example, deletes any prior confirmed row with the same `(text, name, admin_id)` — edit/re-confirm replaces, never accumulates duplicates. |

**Confirmed examples are never TTL-deleted.** They live forever, keyed to the admin who
confirmed them. On group removal they survive (no FK on `chat_id`); they go dormant when the
admin has no live group links and resurface via `get_spam_examples(admin_ids)` once the admin
re-joins or re-adds the bot.

---

## 4. Related caches (TTL, daily job)

| Table | Function | TTL (config) |
|---|---|---|
| `message_lookup_cache` (forwarded-message resolution) | `cleanup_old_lookup_entries` (`message_lookup.py:142`) | `message_lookup_ttl_days: 7` |
| `message_history` (admin DM conversation) | `cleanup_old_message_history` (`message_operations.py:54`) | `message_history_ttl_days: 1` |

Plus `message_history` has a **live ring buffer**: `save_message` keeps the newest 30 rows per
admin, dropping the oldest on overflow (`message_operations.py:36`).

---

## 5. Dead / inert deletion code

| Code | Status | Effect |
|---|---|---|
| `remove_admin` (`admin_operations.py:502`) | Zero callers | Admin cascade deletions never fire |
| `clear_message_history` (`message_operations.py:80`) | Zero callers | — |
| `process_registration` DB procedure (`database_schema.py:264`, `DELETE FROM group_administrators`) | Zero references | Legacy registration flow; superseded by `update_group_admins` |

---

## 6. The asymmetry (the interesting part)

- `group_administrators` / `approved_members` / `message_history`/`transactions`/`spam_examples` all **cascade from admin deletion** — but admin deletion never happens.
- `group_administrators` / `approved_members` **cascade from group deletion** — the only clean chain actually exercised.
- `spam_examples.chat_id` and `message_lookup_cache.chat_id` are **bare columns — no referential integrity from group deletion**. They survive group removal by design (provenance rots silently).
- The daily job's only example-path deletion is the **pending (unconfirmed)** TTL. Confirmed corpus is effectively immortal, by design, matching the user-facing promise ("training examples are preserved" on payment-driven leave).

## 7. Net summary

| Entity | Deleted? | When |
|---|---|---|
| `groups` row | ✅ yes | inaccessible-discovery, no-rights grace (7d daily), unpaid sole-payer day 7, notification total failure |
| `group_administrators` | ✅ yes | same (cascade + explicit) |
| `approved_members` | ✅ yes | same (cascade) + per-spam kick + per-spam-confirmation (all groups) |
| `administrators` (accounts) | ❌ never (dead path) | — |
| `spam_examples` — pending | ✅ yes | daily, 3-day TTL (silent) |
| `spam_examples` — confirmed | ❌ never | immortal, keyed to admin, dormant when admin has no groups |
| `message_history` | ✅ yes | daily TTL 1d + ring buffer (30/admin) |
| `message_lookup_cache` | ✅ yes | daily TTL 7d |
| `transactions` | ❌ never | only cascade from dead admin-deletion path |