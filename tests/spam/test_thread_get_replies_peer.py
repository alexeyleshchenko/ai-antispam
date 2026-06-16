"""Tests for messages.getReplies peer selection in thread context establishment."""

from unittest.mock import AsyncMock, patch

import pytest

from app.common.mtproto_client import MtprotoHttpError
from app.spam.user_context_utils import (
    _resolve_grouped_album_anchor,
    establish_context_via_thread_reading,
)
from app.types import PeerResolutionContext


def _make_context(**overrides):
    """Create a PeerResolutionContext with sensible defaults for discussion thread tests."""
    defaults = (
        dict(
            chat_id=-1001660382870,
            user_id=1,
            message_id=100,
            chat_username="discussgroup",
            message_thread_id=14979,
            reply_to_message_id=None,
            main_channel_id=-100111,
            main_channel_username="publicchannel",
            original_channel_post_id=4021,
        )
        | overrides
    )
    return PeerResolutionContext(**defaults)


# ---------------------------------------------------------------------------
# Peer selection tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_replies_prefers_main_channel_and_original_post_id():
    ctx = _make_context(
        chat_username=None,
        main_channel_username="publicchannel",
        original_channel_post_id=42,
        reply_to_message_id=14978,
    )
    mock_call = AsyncMock(return_value={"messages": []})
    with patch("app.spam.user_context_utils.get_mtproto_client") as m:
        m.return_value.call = mock_call
        assert await establish_context_via_thread_reading(ctx) is True

    mock_call.assert_awaited_once()
    assert mock_call.call_args[0][0] == "messages.getReplies"
    params = mock_call.call_args[1]["params"]
    assert params["peer"] == "publicchannel"
    assert params["msg_id"] == 42


@pytest.mark.asyncio
async def test_get_replies_uses_discussion_when_no_main_channel():
    ctx = _make_context(
        chat_username="discussgroup",
        message_thread_id=5,
        main_channel_id=None,
        main_channel_username=None,
        original_channel_post_id=None,
    )
    mock_call = AsyncMock(return_value={"messages": []})
    with patch("app.spam.user_context_utils.get_mtproto_client") as m:
        m.return_value.call = mock_call
        assert await establish_context_via_thread_reading(ctx) is True

    params = mock_call.call_args[1]["params"]
    assert params["peer"] == "discussgroup"
    assert params["msg_id"] == 5


# ---------------------------------------------------------------------------
# MSG_ID_INVALID → grouped album anchor resolution tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_msg_id_invalid_resolves_grouped_album_anchor():
    """When GetReplies fails with MSG_ID_INVALID on a message that's part of a
    grouped album, the code should find the first message in the group and retry."""
    ctx = _make_context(original_channel_post_id=4021)

    msg_id_invalid_error = MtprotoHttpError(
        "MTProto HTTP bridge error 500: {'error_code': 'MSG_ID_INVALID'}"
    )
    # Channel history: messages 4014-4017 all share grouped_id 999
    channel_history = {
        "messages": [
            {"id": 4014, "grouped_id": 999, "message": "caption"},
            {"id": 4015, "grouped_id": 999, "message": ""},
            {"id": 4016, "grouped_id": 999, "message": ""},
            {"id": 4017, "grouped_id": 999, "message": ""},
            {"id": 4021, "grouped_id": 999, "message": ""},
        ]
    }
    anchor_replies = {"messages": [{"id": 5887}, {"id": 5888}]}

    calls = []

    async def mock_call(method, **kwargs):
        calls.append((method, kwargs))
        if method == "messages.getReplies":
            params = kwargs.get("params", {})
            if params.get("msg_id") == 4021:
                raise msg_id_invalid_error
            return anchor_replies
        return channel_history if method == "messages.getHistory" else {"messages": []}

    with patch("app.spam.user_context_utils.get_mtproto_client") as m:
        m.return_value.call = mock_call
        result = await establish_context_via_thread_reading(ctx)

    assert result is True
    # getReplies(4021) → getHistory(anchor) → getReplies(4014)
    assert len(calls) == 3
    assert calls[0][0] == "messages.getReplies"
    assert calls[0][1]["params"]["msg_id"] == 4021
    assert calls[1][0] == "messages.getHistory"  # anchor resolution
    assert calls[2][0] == "messages.getReplies"
    assert calls[2][1]["params"]["msg_id"] == 4014  # anchor message


@pytest.mark.asyncio
async def test_msg_id_invalid_no_grouped_album_falls_back_to_discussion_group():
    """When GetReplies fails with MSG_ID_INVALID and the message is NOT part of a
    grouped album (no grouped_id), fall back to discussion group reading."""
    ctx = _make_context(original_channel_post_id=4021)

    msg_id_invalid_error = MtprotoHttpError(
        "MTProto HTTP bridge error 500: {'error_code': 'MSG_ID_INVALID'}"
    )
    # Channel history: message 4021 exists but has no grouped_id
    channel_history = {
        "messages": [
            {"id": 4020, "message": "something else"},
            {"id": 4021, "message": "standalone post"},
        ]
    }
    discussion_history = {"messages": [{"id": 101}, {"id": 102}]}

    calls = []

    async def mock_call(method, **kwargs):
        calls.append((method, kwargs))
        if method == "messages.getReplies":
            raise msg_id_invalid_error
        if method == "messages.getHistory":
            params = kwargs.get("params", {})
            peer = params.get("peer")
            # Channel history for anchor resolution
            return channel_history if peer == "publicchannel" else discussion_history
        return {"messages": []}

    with patch("app.spam.user_context_utils.get_mtproto_client") as m:
        m.return_value.call = mock_call
        result = await establish_context_via_thread_reading(ctx)

    assert result is True
    # getReplies(4021) → getHistory(channel, anchor) → getHistory(discussion, fallback)
    assert len(calls) == 3
    assert calls[0][0] == "messages.getReplies"
    assert calls[1][0] == "messages.getHistory"  # anchor resolution (returns None)
    assert calls[2][0] == "messages.getHistory"  # discussion group fallback


@pytest.mark.asyncio
async def test_msg_id_invalid_anchor_retry_also_fails_falls_back_to_discussion():
    """When anchor resolution finds a first message but GetReplies also fails on it,
    fall back to discussion group reading."""
    ctx = _make_context(original_channel_post_id=4021)

    msg_id_invalid_error = MtprotoHttpError(
        "MTProto HTTP bridge error 500: {'error_code': 'MSG_ID_INVALID'}"
    )
    channel_history = {
        "messages": [
            {"id": 4014, "grouped_id": 999, "message": "caption"},
            {"id": 4021, "grouped_id": 999, "message": ""},
        ]
    }
    discussion_history = {"messages": [{"id": 101}]}

    calls = []

    async def mock_call(method, **kwargs):
        calls.append((method, kwargs))
        if method == "messages.getReplies":
            raise msg_id_invalid_error  # both 4021 and 4014 fail
        if method == "messages.getHistory":
            params = kwargs.get("params", {})
            if params.get("peer") == "publicchannel":
                return channel_history
            return discussion_history
        return {"messages": []}

    with patch("app.spam.user_context_utils.get_mtproto_client") as m:
        m.return_value.call = mock_call
        result = await establish_context_via_thread_reading(ctx)

    assert result is True
    # getReplies(4021) → getHistory(anchor) → getReplies(4014) → getHistory(discussion)
    assert len(calls) == 4
    assert calls[0][0] == "messages.getReplies"  # 4021 → MSG_ID_INVALID
    assert calls[1][0] == "messages.getHistory"  # anchor resolution
    assert calls[2][0] == "messages.getReplies"  # 4014 → also MSG_ID_INVALID
    assert calls[3][0] == "messages.getHistory"  # discussion group fallback


@pytest.mark.asyncio
async def test_non_msg_id_invalid_errors_still_return_false():
    """Other MtprotoHttpError types should still return False (no fallback)."""
    ctx = _make_context(original_channel_post_id=4021)

    other_error = MtprotoHttpError(
        "MTProto HTTP bridge error 500: {'error_code': 'CHANNEL_PRIVATE'}"
    )

    async def mock_call(method, **kwargs):
        raise other_error

    with patch("app.spam.user_context_utils.get_mtproto_client") as m:
        m.return_value.call = mock_call
        result = await establish_context_via_thread_reading(ctx)

    assert result is False


# ---------------------------------------------------------------------------
# _resolve_grouped_album_anchor unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_grouped_album_anchor_finds_first_message():
    """Happy path: message 4021 is in a grouped album with first message at 4014."""
    ctx = _make_context()
    logging_ctx = {}

    history = {
        "messages": [
            {"id": 4014, "grouped_id": 999, "message": "caption"},
            {"id": 4015, "grouped_id": 999, "message": ""},
            {"id": 4016, "grouped_id": None, "message": "unrelated"},
            {"id": 4021, "grouped_id": 999, "message": ""},
        ]
    }

    async def mock_call(method, **kwargs):
        return history

    with patch("app.spam.user_context_utils.get_mtproto_client") as m:
        m.return_value.call = mock_call
        result = await _resolve_grouped_album_anchor(
            -100111, "publicchannel", 4021, logging_ctx
        )

    assert result == 4014


@pytest.mark.asyncio
async def test_resolve_grouped_album_anchor_returns_none_when_no_grouped_id():
    """Message exists but is not part of a grouped album."""
    ctx = _make_context()
    logging_ctx = {}

    history = {
        "messages": [
            {"id": 4021, "grouped_id": None, "message": "standalone post"},
        ]
    }

    async def mock_call(method, **kwargs):
        return history

    with patch("app.spam.user_context_utils.get_mtproto_client") as m:
        m.return_value.call = mock_call
        result = await _resolve_grouped_album_anchor(
            -100111, "publicchannel", 4021, logging_ctx
        )

    assert result is None


@pytest.mark.asyncio
async def test_resolve_grouped_album_anchor_returns_none_when_already_first():
    """Message is the first in its group — GetReplies failed for another reason."""
    logging_ctx = {}

    history = {
        "messages": [
            {"id": 4021, "grouped_id": 999, "message": "first photo"},
            {"id": 4022, "grouped_id": 999, "message": "second photo"},
        ]
    }

    async def mock_call(method, **kwargs):
        return history

    with patch("app.spam.user_context_utils.get_mtproto_client") as m:
        m.return_value.call = mock_call
        result = await _resolve_grouped_album_anchor(
            -100111, "publicchannel", 4021, logging_ctx
        )

    assert result is None


@pytest.mark.asyncio
async def test_resolve_grouped_album_anchor_returns_none_when_empty():
    """Channel history returns no messages."""
    logging_ctx = {}

    async def mock_call(method, **kwargs):
        return {"messages": []}

    with patch("app.spam.user_context_utils.get_mtproto_client") as m:
        m.return_value.call = mock_call
        result = await _resolve_grouped_album_anchor(
            -100111, "publicchannel", 4021, logging_ctx
        )

    assert result is None


@pytest.mark.asyncio
async def test_resolve_grouped_album_anchor_returns_none_on_error():
    """MTProto call fails — graceful degradation."""
    logging_ctx = {}

    async def mock_call(method, **kwargs):
        raise MtprotoHttpError("MTProto HTTP bridge error 500: something")

    with patch("app.spam.user_context_utils.get_mtproto_client") as m:
        m.return_value.call = mock_call
        result = await _resolve_grouped_album_anchor(
            -100111, "publicchannel", 4021, logging_ctx
        )

    assert result is None


@pytest.mark.asyncio
async def test_resolve_grouped_album_anchor_target_not_in_history():
    """Target message ID not found in the returned history."""
    logging_ctx = {}

    history = {
        "messages": [
            {"id": 4019, "grouped_id": 999, "message": ""},
            {"id": 4020, "grouped_id": 999, "message": ""},
        ]
    }

    async def mock_call(method, **kwargs):
        return history

    with patch("app.spam.user_context_utils.get_mtproto_client") as m:
        m.return_value.call = mock_call
        result = await _resolve_grouped_album_anchor(
            -100111, "publicchannel", 4021, logging_ctx
        )

    assert result is None
