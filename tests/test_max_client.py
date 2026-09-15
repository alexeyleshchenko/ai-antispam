"""Unit tests for MaxClient."""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src.app.max_client import MaxApiError, MaxClient


@pytest.fixture
async def mock_max_server():
    recorded_requests = []

    async def handle_delete_comment(request: web.Request):
        recorded_requests.append(
            {
                "method": request.method,
                "path": request.path,
                "query": dict(request.query),
                "headers": dict(request.headers),
            }
        )
        post_mid = request.match_info.get("post_mid")
        comment_id = request.query.get("comment_id")

        auth = request.headers.get("Authorization")
        if not auth or auth == "invalid":
            return web.json_response({"error": "unauthorized"}, status=401)

        if comment_id == "already_deleted":
            return web.json_response({"error": "access.denied"}, status=403)

        if comment_id == "error_500":
            return web.json_response({"error": "server error"}, status=500)

        return web.json_response({"success": True}, status=200)

    app = web.Application()
    app.router.add_delete("/messages/{post_mid}/comments", handle_delete_comment)

    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        yield client, recorded_requests, server
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_max_client_delete_comment_success(mock_max_server):
    client, recorded_requests, server = mock_max_server
    base_url = f"http://127.0.0.1:{server.port}"

    max_client = MaxClient(token="test_tok_123", base_url=base_url, session=client.session)

    res = await max_client.delete_comment("post.100", "comm.200")
    assert res is True
    assert len(recorded_requests) == 1
    req = recorded_requests[0]
    assert req["method"] == "DELETE"
    assert req["path"] == "/messages/post.100/comments"
    assert req["query"] == {"comment_id": "comm.200"}
    assert req["headers"]["Authorization"] == "test_tok_123"


@pytest.mark.asyncio
async def test_max_client_delete_comment_handles_403_as_already_gone(mock_max_server):
    client, recorded_requests, server = mock_max_server
    base_url = f"http://127.0.0.1:{server.port}"

    max_client = MaxClient(token="test_tok_123", base_url=base_url, session=client.session)

    res = await max_client.delete_comment("post.100", "already_deleted")
    assert res is True


@pytest.mark.asyncio
async def test_max_client_delete_comment_raises_on_500(mock_max_server):
    client, recorded_requests, server = mock_max_server
    base_url = f"http://127.0.0.1:{server.port}"

    max_client = MaxClient(token="test_tok_123", base_url=base_url, session=client.session)

    with pytest.raises(MaxApiError) as exc_info:
        await max_client.delete_comment("post.100", "error_500")
    assert exc_info.value.status == 500


@pytest.mark.asyncio
async def test_max_client_missing_token():
    max_client = MaxClient(token="")
    with pytest.raises(ValueError, match="MAX_BOT_TOKEN is not configured"):
        await max_client.delete_comment("post.100", "comm.200")
