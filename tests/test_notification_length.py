"""Guards for the silently dropped admin notification (2026-09-24).

Telegram refuses a message longer than 4096 characters rather than trimming it,
so an over-long notification is not delivered in truncated form - it is never
delivered at all, and the admin never learns that spam was caught.

Measured live: an admin notification failed with "Bad Request: message is too
long". The user-controlled body was embedded unbounded and live spam text
reaches 3665 characters, and the LLM-written reason is bounded nowhere either -
3000 chars of content plus a long reason still assembled to 4339 characters.

Bounding the content alone is therefore not sufficient: the assembled body is
what has to fit, so the guarantee lives at the send.
"""

import html
from types import SimpleNamespace

import pytest

from app.common.notifications import notify_admins_with_fallback_and_cleanup
from app.common.utils import truncate_escaped_html
from app.handlers.handle_spam import format_admin_notification_message
from app.types import ADMIN_CONTENT_MAX_CHARS, MessageNotificationContext

TELEGRAM_MESSAGE_LIMIT = 4096


def _context(content_text: str) -> MessageNotificationContext:
    return MessageNotificationContext(
        effective_user_id=12345,
        content_text=content_text,
        chat_title="Test Group",
        chat_username="testgroup",
        is_channel_sender=False,
        violator_name="Spammer",
        violator_username="spammer",
        forward_source="",
        message_link="https://t.me/c/123/456",
        entity_name="Test Group",
        entity_type="group",
        entity_username="testgroup",
    )


# --- defect 1: the fallback must be given a viable per-attempt budget ---------


def test_truncate_escaped_html_bounds_length():
    text = "a" * 5000
    out = truncate_escaped_html(text, 3000)
    assert len(out) <= 3000
    assert out.endswith("...")
    assert out.startswith("a" * 100)


def test_truncate_escaped_html_never_splits_an_entity():
    """A cut entity makes Telegram reject the message, so the bound must be safe.

    Worst case is text that escaping expands hardest: "&" -> "&amp;" (5x) and
    a quote -> "&quot;" (6x), so the escaped form is far longer than the input.
    """
    escaped = html.escape('&"' * 2000, quote=True)
    out = truncate_escaped_html(escaped, 3000)

    assert len(out) <= 3000
    body = out[: -len("...")]
    # every "&" must still be the start of a complete entity
    for i, ch in enumerate(body):
        if ch == "&":
            assert ";" in body[i : i + 8], f"entity split at offset {i}"


def test_truncate_escaped_html_leaves_short_text_alone():
    assert truncate_escaped_html("hello", 3000) == "hello"


async def test_live_shaped_notification_is_sent_within_the_limit():
    """The live shape: content at the measured 3665 chars plus a long reason.

    Bounding the content alone is NOT sufficient - 3000 chars of content plus a
    long LLM reason still assembled to 4339 chars - which is why the guarantee
    lives at the send, and this asserts on what Telegram actually receives.
    """
    bot = _StubBot()
    content = truncate_escaped_html(
        html.escape("x" * 3665, quote=True), ADMIN_CONTENT_MAX_CHARS
    )
    body = format_admin_notification_message(
        _context(content),
        all_admins_delete=False,
        reason="promotional spam with an inline keyboard link " * 20,
        lang="en",
    )
    await notify_admins_with_fallback_and_cleanup(
        bot,
        [111],
        -100123,
        private_message=body,
        assume_human_admins=True,
    )

    assert bot.sent
    for text in bot.sent:
        assert len(text) <= TELEGRAM_MESSAGE_LIMIT, (
            f"sent {len(text)} chars against the {TELEGRAM_MESSAGE_LIMIT} limit"
        )


class _StubBot:
    """Records what the notification path actually hands to Telegram."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def get_chat(self, admin_id: int):
        return SimpleNamespace(type="private", is_bot=False)

    async def send_message(self, chat_id: int, text: str, **kwargs):
        self.sent.append(text)
        return SimpleNamespace(message_id=1)


async def test_send_path_bounds_an_over_long_body():
    """The end-to-end guard: whatever is handed in, Telegram gets <= 4096.

    This is the regression that actually bit - the assembled body, not any one
    field, is what has to fit, because the LLM-written reason is bounded
    nowhere else. Against the pre-fix code a 20 000-char body is sent whole and
    Telegram answers "message is too long", so the admin never hears about the
    spam.
    """
    bot = _StubBot()
    await notify_admins_with_fallback_and_cleanup(
        bot,
        [111],
        -100123,
        private_message="x" * 20000,
        assume_human_admins=True,
    )

    assert bot.sent, "the notification path sent nothing"
    for text in bot.sent:
        assert len(text) <= TELEGRAM_MESSAGE_LIMIT, (
            f"sent {len(text)} chars against the {TELEGRAM_MESSAGE_LIMIT} limit"
        )


async def test_send_path_does_not_leave_a_dangling_tag():
    """Truncation must cut outside a tag, or Telegram rejects the message."""
    bot = _StubBot()
    body = "y" * 4000 + '<a href="https://example.com/spam-guide">guide</a>'
    await notify_admins_with_fallback_and_cleanup(
        bot,
        [111],
        -100123,
        private_message=body,
        assume_human_admins=True,
    )

    assert bot.sent
    sent = bot.sent[0]
    assert len(sent) <= TELEGRAM_MESSAGE_LIMIT
    assert sent.count("<") == sent.count(">"), "a tag was split by the bound"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
