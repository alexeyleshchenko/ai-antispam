# Logging Rules

Conventions for logging in ai-antispam. These rules exist because real incidents
were hard or impossible to investigate without them (see the examples at the end).

## The one rule: every side-effect logs its outcome

Every action that changes external state — sending a message, deleting a message,
leaving a chat, disabling moderation, deducting credits, notifying an admin —
MUST log its outcome: **success or failure**, with the chat/admin context.

No silent branches. If a code path ends with nothing logged, that is a bug.

```python
# ❌ silent success — the 18:16 incident
sent = await bot.send_message(...)
# ...nothing...

# ✅ outcome logged, success
sent = await bot.send_message(...)
logger.info(f"Deactivation message sent to {format_chat_log(chat_id, title, username)} (message_id={sent.message_id})")

# ✅ outcome logged, failure with context
except Exception as e:
    logger.warning(f"Failed to send group deactivation message to {format_chat_log(chat_id, title, username)}: {e}", exc_info=True)
```

## Correlation IDs in plain-text logs

The docker container log is the first stop for every investigation. **Every log
line must be self-contained** — carry the ids needed to correlate it, even if the
same ids are also passed to logfire via `extra={...}`. Logfire `extra` is NOT a
substitute for plain-text ids.

Minimum correlation ids, depending on context:

- `chat_id` — every chat-scoped log (with `format_chat_log(chat_id, title, username)`; bare ids were the pre-2026-08 state, now deprecated)
- `admin_id` / `user_id` — every admin/user-scoped log
- `pending_id` — every pending-record operation (the 07:00 duplicate-callback incident was uninvestigable from the WARNING line alone because it carried no id)
- `message_id` — every send/delete outcome

Use `format_chat_log(...)` / `format_user_log(...)` from `common/utils.py` so
titles/usernames ride along with the ids.

## Instrument key flows with logfire

Functions that constitute a flow boundary (or are suspected of being one) get:

```python
@logfire.instrument(extract_args=True, record_return=True)
async def handle_deactivation(chat_id: int) -> None: ...
```

`extract_args=True` makes chat/admin ids queryable; `record_return=True` captures
the outcome. Follow the existing pattern (e.g. `notify_admins_with_fallback_and_cleanup`).
Prefer this over hand-rolling spans inside the function.

## Absence of a span/line ≠ absence of execution

**Never conclude "X didn't happen" from the absence of a log line or span.**
Logs are evidence of what DID happen, not a complete record of what didn't.
A function that isn't instrumented leaves no span; a silent success leaves no line.
If an investigation needs to prove a negative, the fix is to ADD the missing
logging (see rule 1), not to conclude from silence.

This exact mistake produced a false "the deactivation post never went out" finding
(2026-08-10) — the post had gone out; the function just logged nothing on success.

## Log levels

| Level | Use for | Examples |
|---|---|---|
| DEBUG | Successful routine operations, per-item detail | "Deducted 5★ from 1735638881 in -1002195337276", min-credits admin choice |
| INFO | Notable state transitions / outcomes | "Moderation disabled for ...", "Deactivation message sent ... (message_id=...)", "N admins notified, M unreachable" |
| WARNING | Genuine anomalies needing attention | genuine missing pending record, failed deactivation post |
| INFO (not WARNING) | Benign, expected noise | duplicate callback for already-confirmed record, admin never started the bot (Forbidden DM) |

Discriminate before you warn. If a "failure" has a benign cause that can be
proven (e.g. a second callback for an already-confirmed record), log it at INFO
with the reason — and keep WARNING for the genuinely anomalous case. The
`mark_as_not_spam` duplicate-callback fix (2026-08-10) is the canonical example:
genuine not-found stays WARNING, proven duplicate drops to INFO with
`pending {id} already confirmed (by admin {id})`.

## Guarded enrichment

Fetching title/username for a log line must never crash the flow:

```python
title = username = None
try:
    chat = await bot.get_chat(chat_id)
    title = getattr(chat, "title", None)
    username = getattr(chat, "username", None)
except Exception:  # noqa: BLE001
    title = username = None
```

Never fetch inside an `except` block for the very error being handled (the fetch
will fail again) — log the bare id instead and note the limitation.

## Examples from real incidents

1. **2026-08-10 18:16 — deactivation post investigation (rule 1 & 4).**
   `send_group_deactivation_message` logged only on failure, and without chat
   context. "No log line" was misread as "post never sent". Fix: success now logs
   `(message_id=...)`, failure logs chat context.

2. **2026-08-10 07:00 — duplicate callback (rule 2 & 5).**
   `mark_as_not_spam` WARNING carried no pending_id in the docker log (only in
   logfire `extra`). Investigation required correlating a preceding INFO line.
   Fix: classification + ids now in plain text; benign duplicate at INFO, genuine
   missing at WARNING.

3. **2026-08-08 09:28 — channel-add race (rule 3).**
   The add-flow race was only reconstructable because the webhook entry line
   carried the update payload; the handler flow itself was opaque. Key handlers
   are now instrumented with logfire spans.
