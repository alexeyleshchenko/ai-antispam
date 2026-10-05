"""Guard: the webhook's `allowed_updates` covers every registered handler.

Risk R-a (#108). Passing `allowed_updates` to `set_webhook` REPLACES Telegram's
default set instead of extending it, so the list has two failure modes and both
are silent:

* a type the bot handles but the list omits simply never arrives — the handler
  is registered, looks correct, and is never called;
* Telegram's DEFAULT set already excludes `chat_member`, which is the very
  update the join store needs, so omitting the list entirely is equally fatal.

Nothing at runtime reports either: no exception, no log line, just a route that
is never taken. So the invariant is asserted against the source instead — every
`@dp.<type>` registration in the handlers tree must appear in ALLOWED_UPDATES.
"""

import re
from pathlib import Path

from src.app.handlers.dp import ALLOWED_UPDATES

# aiogram dispatcher attribute -> Telegram update type. `errors` is absent on
# purpose: it is the error observer, not an update type, so Telegram has
# nothing to send for it.
_ATTR_TO_UPDATE_TYPE = {
    "message": "message",
    "edited_message": "edited_message",
    "channel_post": "channel_post",
    "edited_channel_post": "edited_channel_post",
    "callback_query": "callback_query",
    "inline_query": "inline_query",
    "chosen_inline_result": "chosen_inline_result",
    "shipping_query": "shipping_query",
    "pre_checkout_query": "pre_checkout_query",
    "poll": "poll",
    "poll_answer": "poll_answer",
    "my_chat_member": "my_chat_member",
    "chat_member": "chat_member",
    "chat_join_request": "chat_join_request",
}

# Line-anchored so a decorator mentioned in prose (a docstring, a comment)
# cannot masquerade as a registration.
_DECORATOR = re.compile(r"^\s*@dp\.([a-z_]+)\(", re.MULTILINE)
_HANDLERS_DIR = Path(__file__).resolve().parents[2] / "src" / "app" / "handlers"

def _registered_dispatcher_attrs() -> set[str]:
    found: set[str] = set()
    for path in _HANDLERS_DIR.rglob("*.py"):
        found.update(_DECORATOR.findall(path.read_text(encoding="utf-8")))
    return found

def test_the_scan_actually_finds_registrations():
    """Positive control: a scan that finds nothing would pass every other leg."""
    attrs = _registered_dispatcher_attrs()

    assert "message" in attrs
    assert "chat_member" in attrs

def test_every_registered_handler_type_is_allowed():
    attrs = _registered_dispatcher_attrs()
    unmapped = attrs - set(_ATTR_TO_UPDATE_TYPE) - {"errors"}

    assert not unmapped, (
        f"dispatcher attributes with no update-type mapping: {sorted(unmapped)} — "
        "add them to _ATTR_TO_UPDATE_TYPE and to ALLOWED_UPDATES"
    )

    handled = {_ATTR_TO_UPDATE_TYPE[a] for a in attrs if a in _ATTR_TO_UPDATE_TYPE}
    missing = handled - set(ALLOWED_UPDATES)

    assert not missing, (
        f"handled update types absent from ALLOWED_UPDATES: {sorted(missing)} — "
        "a type left out of the webhook registration goes silently dead"
    )

def test_chat_member_is_allowed():
    """The join store's reliable observer must be explicitly allowed.

    Telegram's default set excludes `chat_member`, so this cannot be left to
    the default: without the explicit entry the observer is never fed.
    """
    assert "chat_member" in ALLOWED_UPDATES

def test_allowed_updates_has_no_duplicates():
    assert len(ALLOWED_UPDATES) == len(set(ALLOWED_UPDATES))
