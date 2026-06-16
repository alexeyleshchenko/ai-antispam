"""Tests for messages.getReplies peer selection in thread context establishment."""

from unittest.mock import AsyncMock, patch

import pytest

from app.spam.user_context_utils import establish_context_via_thread_reading
from app.types import PeerResolutionContext


@pytest.mark.asyncio
async def test_get_replies_prefers_main_channel_and_original_post_id():
    ctx = PeerResolutionContext(
        chat_id=-1001660382870,
        user_id=1,
        message_id=100,
        chat_username=None,
        message_thread_id=14979,
        reply_to_message_id=14978,
        main_channel_id=-100111,
        main_channel_username="publicchannel",
        original_channel_post_id=42,
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
    ctx = PeerResolutionContext(
        chat_id=-1001660382870,
        user_id=1,
        message_id=100,
        chat_username="discussgroup",
        message_thread_id=5,
        reply_to_message_id=None,
        main_channel_id=None,
        main_channel_username=None,
    )
    mock_call = AsyncMock(return_value={"messages": []})
    with patch("app.spam.user_context_utils.get_mtproto_client") as m:
        m.return_value.call = mock_call
        assert await establish_context_via_thread_reading(ctx) is True

    params = mock_call.call_args[1]["params"]
    assert params["peer"] == "discussgroup"
    assert params["msg_id"] == 5


@pytest.mark.asyncio
async def test_msg_id_invalid_falls_back_to_discussion_group_reading():
    """When GetReplies returns MSG_ID_INVALID (no discussion thread on channel post),
    fall back to reading the discussion group via messages.getHistory."""
    from app.common.mtproto_client import MtprotoHttpError

    ctx = PeerResolutionContext(
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

    msg_id_invalid_error = MtprotoHttpError(
        "MTProto HTTP bridge error 500: {'error_code': 'MSG_ID_INVALID'}"
    )
    fallback_result = {"messages": [{"id": 101}, {"id": 102}]}

    call_count = 0

    async def mock_call(method, **kwargs):
        nonlocal call_count
        call_count += 1
        if method == "messages.getReplies":
            raise msg_id_invalid_error
        return fallback_result

    with patch("app.spam.user_context_utils.get_mtproto_client") as m:
        m.return_value.call = mock_call
        result = await establish_context_via_thread_reading(ctx)

    assert result is True
    assert call_count == 2  # getReplies failed, then getHistory succeeded


@pytest.mark.asyncio
async def test_non_msg_id_invalid_errors_still_return_false():
    """Other MtprotoHttpError types should still return False (no fallback)."""
    from app.common.mtproto_client import MtprotoHttpError

    ctx = PeerResolutionContext(
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

    other_error = MtprotoHttpError(
        "MTProto HTTP bridge error 500: {'error_code': 'CHANNEL_PRIVATE'}"
    )

    async def mock_call(method, **kwargs):
        raise other_error

    with patch("app.spam.user_context_utils.get_mtproto_client") as m:
        m.return_value.call = mock_call
        result = await establish_context_via_thread_reading(ctx)

    assert result is False
