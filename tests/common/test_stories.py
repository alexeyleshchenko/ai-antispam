from unittest.mock import AsyncMock, patch

import pytest

from src.app.spam.stories import StorySummary, collect_user_stories


@pytest.fixture
def mock_mtproto_client():
    with patch("src.app.spam.stories.get_mtproto_client") as mock:
        client = AsyncMock()
        mock.return_value = client
        yield client


@pytest.mark.asyncio
async def test_collect_user_stories_success(mock_mtproto_client):
    mock_mtproto_client.call = AsyncMock(
        return_value={
            "stories": {
                "stories": [
                    {
                        "id": 123,
                        "date": 1600000000,
                        "caption": "Check out my new project!",
                        "entities": [
                            {
                                "_": "messageEntityTextUrl",
                                "url": "http://spam.com",
                                "offset": 13,
                                "length": 11,
                            }
                        ],
                    },
                    {
                        "id": 124,
                        "date": 1600000100,
                        "caption": "Just a photo",
                        "_": "storyItem",
                    },
                ]
            }
        }
    )

    result = await collect_user_stories(123456, username="testuser")

    assert result.status.name == "FOUND"
    assert result.content is not None
    assert "Caption: Check out my new project!" in result.content
    assert "Link: http://spam.com" in result.content
    assert "Caption: Just a photo" in result.content

    # Verify call was made
    mock_mtproto_client.call.assert_called_once()
    args = mock_mtproto_client.call.call_args
    assert args[0][0] == "stories.getPeerStories"
    assert args[1]["params"]["peer"] == "testuser"


@pytest.mark.asyncio
async def test_collect_user_stories_no_stories(mock_mtproto_client):
    from src.app.types import ContextStatus

    mock_mtproto_client.call = AsyncMock(return_value={"stories": {"stories": []}})
    result = await collect_user_stories(123456, username="testuser")
    assert result.status == ContextStatus.EMPTY


@pytest.mark.asyncio
async def test_collect_user_stories_deleted(mock_mtproto_client):
    from src.app.types import ContextStatus

    mock_mtproto_client.call = AsyncMock(
        return_value={"stories": {"stories": [{"_": "storyItemDeleted", "id": 123}]}}
    )
    result = await collect_user_stories(123456, username="testuser")
    assert result.status == ContextStatus.EMPTY


@pytest.mark.asyncio
async def test_collect_user_stories_error(mock_mtproto_client):
    from src.app.common.mtproto_client import MtprotoHttpError
    from src.app.types import ContextStatus

    mock_mtproto_client.call = AsyncMock(side_effect=MtprotoHttpError("MTProto error"))
    result = await collect_user_stories(123456, username="testuser")
    assert result.status == ContextStatus.FAILED
    assert result.error == "MTProto error"


def test_story_summary_formatting():
    summary = StorySummary(
        id=1,
        date=123,
        caption="Click here",
        entities=[{"_": "messageEntityTextUrl", "url": "http://example.com"}],
    )
    s = summary.to_string()
    assert "Caption: Click here" in s
    assert "Link: http://example.com" in s


def test_story_summary_media_only():
    summary = StorySummary(id=1, date=123)
    assert summary.to_string() == "Media story"


def test_story_summary_with_media_link():
    """Test that stories with media links are properly formatted."""
    summary = StorySummary(
        id=1,
        date=123,
        media={
            "_": "messageMediaWebPage",
            "webpage": {
                "id": 123,
                "url": "https://t.me/channel_invite_link",
                "display_url": "t.me/channel_invite_link",
                "title": "Join our channel",
            },
        },
    )
    result = summary.to_string()
    assert "Link: https://t.me/channel_invite_link" in result


@pytest.mark.asyncio
async def test_collect_user_stories_media_only_with_link(mock_mtproto_client):
    """Test that stories with only media links are included in results."""
    from src.app.types import ContextStatus

    mock_mtproto_client.call = AsyncMock(
        return_value={
            "stories": {
                "stories": [
                    {
                        "id": 123,
                        "date": 1600000000,
                        "_": "storyItem",
                        "media": {
                            "_": "messageMediaWebPage",
                            "webpage": {
                                "id": 456,
                                "url": "https://t.me/channel_invite_link",
                                "display_url": "t.me/channel_invite_link",
                            },
                        },
                        # No caption or entities - should still be included
                    }
                ]
            }
        }
    )

    result = await collect_user_stories(123456, username="testuser")

    assert result.status == ContextStatus.FOUND
    assert result.content is not None
    assert "Link: https://t.me/channel_invite_link" in result.content


@pytest.mark.asyncio
async def test_collect_user_stories_with_media_area_link(mock_mtproto_client):
    """Test that stories with media area URLs (like clickable areas on videos) are included."""
    from src.app.types import ContextStatus

    mock_mtproto_client.call = AsyncMock(
        return_value={
            "stories": {
                "stories": [
                    {
                        "id": 10,
                        "date": 1600000000,
                        "_": "storyItem",
                        "media": {
                            "_": "messageMediaDocument",
                            "document": {"id": 123, "mime_type": "video/mp4"},
                        },
                        "media_areas": [
                            {
                                "_": "MediaAreaUrl",
                                "coordinates": {"x": 50, "y": 80, "w": 90, "h": 10},
                                "url": "https://t.me/+channel_invite_link",
                            }
                        ],
                        # No caption or entities - should still be included due to media area link
                    }
                ]
            }
        }
    )

    result = await collect_user_stories(123456, username="testuser")

    assert result.status == ContextStatus.FOUND
    assert result.content is not None
    assert "Link: https://t.me/+channel_invite_link" in result.content


def test_story_summary_with_media_area_link():
    """Test that stories with media area URLs are properly formatted."""
    summary = StorySummary(
        id=10,
        date=123,
        media={
            "_": "messageMediaDocument",
            "document": {"id": 123, "mime_type": "video/mp4"},
        },
        media_areas=[
            {
                "_": "MediaAreaUrl",
                "coordinates": {"x": 50, "y": 80, "w": 90, "h": 10},
                "url": "https://t.me/+channel_invite_link",
            }
        ],
    )
    result = summary.to_string()
    assert "Link: https://t.me/+channel_invite_link" in result
