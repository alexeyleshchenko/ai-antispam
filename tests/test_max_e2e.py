"""End-to-end integration tests for MAX webhook moderation pipeline."""

from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src.app.main import handle_max_update
from src.app.max_webhook import MAX_SECRET_HEADER

SECRET = "test-secret-e2e"

REAL_SPAM_COMMENT_PAYLOAD = {
    "update_type": "comment_created",
    "timestamp": 1789225012032,
    "message": {
        "recipient": {
            "chat_type": "channel",
            "chat_id": -77345848199175,
            "post_id": "mid.ffffb9a7843177f901a095b6ca9f7bfa",
        },
        "timestamp": 1789225012032,
        "body": {
            "mid": "mid.ffffb9a7843177f901a0961f0b404461",
            "seq": 117258650388546657,
            "text": "Earn 5000$ per day working from home! Click here: http://scam.xyz",
        },
    },
}


@pytest.fixture
async def max_app_client(monkeypatch):
    monkeypatch.setenv("MAX_WEBHOOK_SECRET", SECRET)
    app = web.Application()
    app.router.add_post("/process-max-updates", handle_max_update)

    mock_max_client = AsyncMock()
    mock_max_client.delete_comment = AsyncMock(return_value=True)
    app.max_client = mock_max_client

    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield client, mock_max_client
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_e2e_webhook_triggers_spam_classification_and_deletion(max_app_client):
    client, mock_max_client = max_app_client

    with patch("src.app.spam.spam_classifier.is_spam", new_callable=AsyncMock) as mock_is_spam:
        mock_is_spam.return_value = (True, 98, "financial scam link")

        resp = await client.post(
            "/process-max-updates",
            json=REAL_SPAM_COMMENT_PAYLOAD,
            headers={MAX_SECRET_HEADER: SECRET},
        )
        assert resp.status == 200
        assert await resp.json() == {"ok": True}

        # Allow asyncio background task to complete
        import asyncio
        await asyncio.sleep(0.05)

        mock_is_spam.assert_awaited_once()
        mock_max_client.delete_comment.assert_awaited_once_with(
            post_mid="mid.ffffb9a7843177f901a095b6ca9f7bfa",
            comment_mid="mid.ffffb9a7843177f901a0961f0b404461",
        )


@pytest.mark.asyncio
async def test_e2e_webhook_ignores_legitimate_comment(max_app_client):
    client, mock_max_client = max_app_client

    with patch("src.app.spam.spam_classifier.is_spam", new_callable=AsyncMock) as mock_is_spam:
        mock_is_spam.return_value = (False, 5, "normal question")

        resp = await client.post(
            "/process-max-updates",
            json=REAL_SPAM_COMMENT_PAYLOAD,
            headers={MAX_SECRET_HEADER: SECRET},
        )
        assert resp.status == 200

        import asyncio
        await asyncio.sleep(0.05)

        mock_is_spam.assert_awaited_once()
        mock_max_client.delete_comment.assert_not_awaited()
