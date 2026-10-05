"""The dispatcher, and the update-type set the webhook is registered for.

`ALLOWED_UPDATES` is passed to `set_webhook`. It is NOT optional and NOT
cosmetic: Telegram's default set EXCLUDES `chat_member`, so a webhook
registered without this list never receives member-join updates at all —
`@dp.chat_member()` would be registered, look correct, and stay silent
forever. Passing a list also REPLACES the default set outright, so any type
the bot handles but this list omits goes equally dead, silently.

Hence the guard test in `tests/handlers/test_allowed_updates.py`: it scans the
source for every `@dp.<type>` registration and asserts the type is listed
here. Adding a handler without adding its type fails that test rather than
shipping a dead route.
"""

from aiogram import Dispatcher

dp = Dispatcher()

# Every update type this bot handles, i.e. every `@dp.<type>` in `src/app/`.
# `dp.errors` is deliberately absent: it is an error observer, not an update
# type, so Telegram has nothing to send for it.
ALLOWED_UPDATES: list[str] = [
    "message",
    "edited_message",
    "channel_post",
    "callback_query",
    "pre_checkout_query",
    "my_chat_member",
    "chat_member",
]
