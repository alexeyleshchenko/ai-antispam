# Telegram ephemeral messages — what they are and where this bot can use them

**Date:** 2026-10-20
**Status:** descriptive — survey only, nothing implemented
**Trigger:** owner request, after the group-command incident (#122)

## What the feature is

An **ephemeral message** is a message a bot sends *into a group* that is visible
only to **one named user** and the bot. Everyone else in the chat — members and
other bots — sees nothing. Ephemeral messages may disappear on their own after a
while, or when the app restarts; Telegram documents no fixed TTL.

An **ephemeral command** is the inbound half: a bot can declare a command with
`is_ephemeral = True` in `BotCommand`, and when a user sends it, **the command
message itself is invisible to every other member and every other bot**.

Sources read first-hand on 2026-10-20:
`core.telegram.org/bots/api` §*Ephemeral Messages and Commands*,
`core.telegram.org/bots/features` §*Ephemeral Messages*,
`core.telegram.org/bots/api-changelog`.

| API version | Date | What landed |
|---|---|---|
| **10.2** | 2026-07-14 | Introduced ephemeral messages: `is_ephemeral` on `BotCommand`, `receiver_user_id` / `callback_query_id` on `send*`, `ephemeral_message_id` on `Message`, `editEphemeralMessage*`, `deleteEphemeralMessage` |
| **10.3** | 2026-08-24 | `EphemeralMessageParameters` replaces the two loose params; adds `replace_callback_query_message`; `can_send_welcome_messages` admin right |

## The delivery rules — these decide everything

1. **Any bot**, within **15 seconds** of an incoming eligible action, may send an
   ephemeral message to that user, but must supply either the
   `callback_query_id` of a received callback query, or
   `reply_parameters.ephemeral_message_id` from an incoming ephemeral message.
2. **A chat administrator** may send an ephemeral message to any **non-bot**
   member of that chat **at any time**, with neither of the above.
3. **Delivery is not guaranteed**, especially when the user is offline. Telegram
   says so twice.
4. Group and supergroup chats only — **not channels**.
5. The ephemeral id may be **reused** after the message is deleted or expires, so
   it is not a durable key.

## Why rule 2 is the one that matters here

This bot is **always an administrator** in every group it sits in: the add-to-group
deep link requests `delete_messages + restrict_members`
(`src/app/common/utils.py:517`), and `src/app/background_jobs/no_rights.py`
**leaves** any group where those rights are missing. So the 15-second /
callback-id restriction does not bind this bot: it can send an ephemeral message
to any member of any of its groups at any time.

That is what makes the feature usable for the thing the owner actually complained
about, rather than a curiosity.

## Feasibility: aiogram does not ship it, but does not block it

| | |
|---|---|
| installed aiogram | **3.29.0** |
| latest release | **3.31.0** — still no `ephemeral_message_parameters` on `SendMessage` |
| aiogram `dev-3.x` (unreleased) | has `EphemeralMessageParameters` and the field |
| this tree's escape hatch | `TelegramMethod.model_config` sets `extra="allow"`, so an unknown kwarg **survives** `model_dump` |

Verified locally on 2026-10-20:

```python
SendMessage(chat_id=-100123, text="hi",
            ephemeral_message_parameters={"receiver_user_id": 42}).model_dump(exclude_none=True)
# -> {..., 'ephemeral_message_parameters': {'receiver_user_id': 42}}
```

So a thin helper is enough — **no aiogram upgrade, no fork, no vendored patch**.
A live smoke test against a real group is still owed before anything relies on it.

## Candidate sites in this repo

Ordered by how well the feature fits, not by effort.

### 1. Group command redirect — `src/app/handlers/command_handlers.py:106`

Today a group command DMs the help text to the sender and, if the DM fails
(403, user never opened the bot), returns `command_group_dm_failed_silent` —
a **silent no-op with no way to reach the user at all**.

Ephemeral fixes both halves: the guidance appears in the group, in context, only
to the person who typed the command, and works for users who never started the
bot. This is the single strongest fit in the codebase and it directly answers
the invasiveness complaint that produced #122.

### 2. Ephemeral commands — `src/app/bot_commands.py`

Declaring the command list with `is_ephemeral = True` makes a user's `/start`
in a group **invisible to everyone else**, so the interaction never appears in
the chat at all. This is the "silent mode" the owner expected to already exist.

Open question to settle before shipping: whether privacy mode
(`can_read_all_group_messages: False` on this bot) delivers ephemeral commands
the same way it delivers addressed ones. Not answered by the docs I read —
needs a smoke test.

### 3. Discussion-group notice — `src/app/handlers/message/channel_management.py:543`

When the owner DM fails, the bot posts the "discussion group added" notice
**into the discussion group itself** — public clutter in someone else's chat.
An ephemeral message to the owner keeps the information and drops the clutter.

### 4. Admin notifications — `src/app/common/notifications.py`

Where an admin notification is triggered by an event in a group, an ephemeral
copy **in that group** gives the admin the context (the deleted message, the
verdict) without a DM per event. Keep the DM for anything actionable or durable:
delivery is not guaranteed.

### 5. Callback overlays — `replace_callback_query_message`

A button tap can render an ephemeral message **in place of** the message holding
the button, for that user only, without editing what everyone else sees. Applies
to the existing callback flows (`spam_example:`, `buy_stars:`).

### 6. Welcome messages — new capability, not a retrofit

Bot API 10.3 added `can_send_welcome_messages` in the same release family, and
the features page names welcome messages as the archetypal ephemeral use case.
This bot has no welcome feature today, so this is a new surface, not a migration.

### Not applicable

`src/app/handlers/message/channel_management.py:648` posts a wrong-place notice
into a **channel** — ephemeral does not cover channels.

## What this does NOT solve

- **The bot blocklist ask (`opencrabs#789`)** is unrelated. Ephemeral controls
  where a bot's *own* messages are visible; it does nothing about another bot's
  messages starting a turn in an `open` group.
- **Not a reliable channel.** Not guaranteed, no fixed TTL, no persistence.
  Anything a user may need later (payment instructions, settings) stays in DM or
  in the bot's own menu.

## Recommendation

Ship **site 1** first — it is a small, reversible change inside a handler that
already exists, and it removes a real blind spot (the 403 silent path) while
making the bot strictly less visible. Sites 2 and 3 next, each with its own
smoke test. Site 5 is a polish item. Site 6 is a product decision, not a fix.
