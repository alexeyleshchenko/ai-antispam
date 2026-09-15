"""Unit tests for MAX comment moderator orchestration."""

from unittest.mock import AsyncMock, patch

import pytest

from src.app.max_moderator import MAX_OWNER_WHITELIST_IDS, process_max_comment
from src.app.max_webhook import MaxCommentItem


@pytest.fixture
def sample_comment():
    return MaxCommentItem(
        update_type="comment_created",
        chat_id=-77345848199175,
        post_mid="mid.post123",
        comment_mid="mid.comm456",
        text="Buy crypto now!",
        sender_id=None,
        sender_name=None,
        timestamp=1789225012032,
    )


@pytest.mark.asyncio
async def test_process_max_comment_skips_non_actionable():
    comment = MaxCommentItem(
        update_type="comment_created",
        chat_id=-77345848199175,
        post_mid=None,
        comment_mid="mid.comm456",
        text="Hello",
        sender_id=None,
        sender_name=None,
        timestamp=1,
    )
    is_spam, confidence, reason = await process_max_comment(comment)
    assert is_spam is False
    assert reason == "not_actionable"


@pytest.mark.asyncio
async def test_process_max_comment_whitelists_owner():
    owner_id = next(iter(MAX_OWNER_WHITELIST_IDS))
    comment = MaxCommentItem(
        update_type="comment_created",
        chat_id=-77345848199175,
        post_mid="mid.post123",
        comment_mid="mid.comm456",
        text="Buy crypto now!",
        sender_id=owner_id,
        sender_name="Alexey",
        timestamp=1789225012032,
    )
    is_spam, confidence, reason = await process_max_comment(comment)
    assert is_spam is False
    assert reason == "whitelisted_owner"


@pytest.mark.asyncio
async def test_process_max_comment_invokes_classifier_and_deletes_on_spam(sample_comment):
    mock_client = AsyncMock()
    mock_client.delete_comment = AsyncMock(return_value=True)

    with patch("src.app.spam.spam_classifier.is_spam", new_callable=AsyncMock) as mock_is_spam:
        mock_is_spam.return_value = (True, 95, "crypto spam")

        is_spam, conf, reason = await process_max_comment(sample_comment, max_client=mock_client)

        assert is_spam is True
        assert conf == 95
        assert reason == "crypto spam"
        mock_is_spam.assert_awaited_once()
        mock_client.delete_comment.assert_awaited_once_with(
            post_mid="mid.post123",
            comment_mid="mid.comm456",
        )


@pytest.mark.asyncio
async def test_process_max_comment_does_not_delete_on_legitimate(sample_comment):
    mock_client = AsyncMock()
    mock_client.delete_comment = AsyncMock()

    with patch("src.app.spam.spam_classifier.is_spam", new_callable=AsyncMock) as mock_is_spam:
        mock_is_spam.return_value = (False, 10, "legit discussion")

        is_spam, conf, reason = await process_max_comment(sample_comment, max_client=mock_client)

        assert is_spam is False
        assert conf == 10
        mock_is_spam.assert_awaited_once()
        mock_client.delete_comment.assert_not_awaited()
