"""Unit tests for the MAX webhook ingress (#30).

Two layers, no network:
  * the pure primitives in ``app.max_webhook`` — auth, envelope validation and
    the log summary;
  * the aiohttp route ``POST /process-max-updates`` driven through
    ``aiohttp.test_utils.TestClient``, so the real status codes are exercised.

Nothing here calls the MAX API and nothing moderates: the route is ingress +
observability only.
"""

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src.app.main import handle_max_update
from src.app.max_webhook import (
    MAX_SECRET_HEADER,
    MaxCommentItem,
    extract_comment,
    is_valid_envelope,
    summarise,
    verify_secret,
)

SECRET = "test-secret-9f3a1c"  # noqa: S105 — fixture value, never a real secret

# The live envelope shape observed in the 2026-07-28 delivery.
REAL_ENVELOPE = {
    "update_type": "message_created",
    "timestamp": 1753718400000,
    "message": {
        "recipient": {"chat_id": -77345848199175, "user_id": 385916094},
        "sender": {"user_id": 42, "name": "Ivan"},
        "body": {
            "mid": "mid.ffffb9a7843177f9019fa83f1d383ad4",
            "text": "buy cheap pills now",
        },
    },
}

BODY_TEXT = REAL_ENVELOPE["message"]["body"]["text"]


# ── verify_secret ────────────────────────────────────────────────────────────


def test_verify_secret_accepts_matching_value():
    assert verify_secret(SECRET, SECRET) is True


def test_verify_secret_rejects_mismatch():
    assert verify_secret("wrong-secret", SECRET) is False


def test_verify_secret_fails_closed_when_expected_unset():
    """An unconfigured secret must never authenticate anything."""
    assert verify_secret(SECRET, None) is False
    assert verify_secret(SECRET, "") is False


def test_verify_secret_rejects_when_provided_missing():
    assert verify_secret(None, SECRET) is False
    assert verify_secret("", SECRET) is False


def test_verify_secret_rejects_when_both_missing():
    assert verify_secret(None, None) is False


def test_verify_secret_header_name_is_the_documented_one():
    assert MAX_SECRET_HEADER == "X-Max-Bot-Api-Secret"


# ── is_valid_envelope ────────────────────────────────────────────────────────


def test_is_valid_envelope_accepts_real_delivery_shape():
    assert is_valid_envelope(REAL_ENVELOPE) is True


def test_is_valid_envelope_accepts_minimal_envelope():
    assert is_valid_envelope({"update_type": "message_created"}) is True


@pytest.mark.parametrize("payload", ["a string", ["a", "list"], None, 42, True])
def test_is_valid_envelope_rejects_non_dict(payload):
    assert is_valid_envelope(payload) is False


def test_is_valid_envelope_rejects_missing_update_type():
    assert is_valid_envelope({"message": {"body": {}}}) is False


@pytest.mark.parametrize("bad", [123, None, {"nested": 1}, ["x"], True])
def test_is_valid_envelope_rejects_non_string_update_type(bad):
    assert is_valid_envelope({"update_type": bad}) is False


@pytest.mark.parametrize("blank", ["", "   ", "\n"])
def test_is_valid_envelope_rejects_blank_update_type(blank):
    assert is_valid_envelope({"update_type": blank}) is False


# ── summarise ────────────────────────────────────────────────────────────────


def test_summarise_reports_identity_fields():
    line = summarise(REAL_ENVELOPE)
    assert "update_type=message_created" in line
    assert "chat_id=-77345848199175" in line
    assert "mid=mid.ffffb9a7843177f9019fa83f1d383ad4" in line
    assert "sender_id=42" in line
    assert "sender_name=Ivan" in line
    assert f"text_len={len(BODY_TEXT)}" in line


def test_summarise_never_leaks_the_message_body():
    assert BODY_TEXT not in summarise(REAL_ENVELOPE)


def test_summarise_never_leaks_the_secret():
    """The summary goes to the daemon log; the secret must not ride along."""
    assert SECRET not in summarise(REAL_ENVELOPE)
    assert SECRET not in summarise({**REAL_ENVELOPE, "secret": SECRET})


def test_summarise_survives_empty_and_malformed_envelopes():
    line = summarise({"update_type": "x"})
    assert "update_type=x" in line
    assert "chat_id=None" in line
    assert "mid=None" in line
    assert "text_len=0" in line


def test_summarise_ignores_non_dict_subobjects():
    line = summarise({"update_type": "x", "message": "not-a-dict"})
    assert "update_type=x" in line
    assert "mid=None" in line


# ── route: POST /process-max-updates ─────────────────────────────────────────


@pytest_asyncio.fixture
async def max_client(monkeypatch):
    """A bare aiohttp app exposing ONLY the MAX route.

    Deliberately not the full ``app`` from main.py: that one carries startup
    hooks (DB, bot session) this ingress has no business needing.
    """
    monkeypatch.setenv("MAX_WEBHOOK_SECRET", SECRET)
    app = web.Application()
    app.router.add_post("/process-max-updates", handle_max_update)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


URL = "/process-max-updates"


async def test_route_accepts_correct_secret(max_client):
    resp = await max_client.post(
        URL, json=REAL_ENVELOPE, headers={MAX_SECRET_HEADER: SECRET}
    )
    assert resp.status == 200
    assert await resp.json() == {"ok": True}


async def test_route_header_lookup_is_case_insensitive(max_client):
    resp = await max_client.post(
        URL, json=REAL_ENVELOPE, headers={MAX_SECRET_HEADER.lower(): SECRET}
    )
    assert resp.status == 200


async def test_route_rejects_wrong_secret(max_client):
    resp = await max_client.post(
        URL, json=REAL_ENVELOPE, headers={MAX_SECRET_HEADER: "not-the-secret"}
    )
    assert resp.status == 403


async def test_route_rejects_missing_secret_header(max_client):
    resp = await max_client.post(URL, json=REAL_ENVELOPE)
    assert resp.status == 403


async def test_route_returns_503_when_secret_unconfigured(max_client, monkeypatch):
    """Fail closed: no configured secret means the route refuses to run."""
    monkeypatch.delenv("MAX_WEBHOOK_SECRET", raising=False)
    resp = await max_client.post(
        URL, json=REAL_ENVELOPE, headers={MAX_SECRET_HEADER: SECRET}
    )
    assert resp.status == 503


async def test_route_returns_503_when_secret_blank(max_client, monkeypatch):
    monkeypatch.setenv("MAX_WEBHOOK_SECRET", "")
    resp = await max_client.post(
        URL, json=REAL_ENVELOPE, headers={MAX_SECRET_HEADER: SECRET}
    )
    assert resp.status == 503


async def test_route_rejects_malformed_json(max_client):
    resp = await max_client.post(
        URL, data="{not-json", headers={MAX_SECRET_HEADER: SECRET}
    )
    assert resp.status == 400


async def test_route_rejects_json_without_update_type(max_client):
    resp = await max_client.post(
        URL, json={"message": {"body": {}}}, headers={MAX_SECRET_HEADER: SECRET}
    )
    assert resp.status == 400


async def test_route_rejects_non_dict_json(max_client):
    resp = await max_client.post(
        URL, json=["not", "an", "envelope"], headers={MAX_SECRET_HEADER: SECRET}
    )
    assert resp.status == 400


async def test_route_does_not_echo_the_payload_back(max_client):
    """The ack is a bare ok — no body, no secret, no echo."""
    resp = await max_client.post(
        URL, json=REAL_ENVELOPE, headers={MAX_SECRET_HEADER: SECRET}
    )
    raw = await resp.text()
    assert BODY_TEXT not in raw
    assert SECRET not in raw


async def test_route_authenticates_before_parsing(max_client):
    """A bad secret on a malformed body is 403, not 400 — auth comes first."""
    resp = await max_client.post(
        URL, data="{not-json", headers={MAX_SECRET_HEADER: "wrong"}
    )
    assert resp.status == 403


# ── extract_comment ────────────────────────────────────────��─────────────────


REAL_COMMENT_ENVELOPE = {
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
            "text": "3-1",
        },
    },
}


def test_extract_comment_from_real_comment_envelope():
    item = extract_comment(REAL_COMMENT_ENVELOPE)
    assert isinstance(item, MaxCommentItem)
    assert item.update_type == "comment_created"
    assert item.chat_id == -77345848199175
    assert item.post_mid == "mid.ffffb9a7843177f901a095b6ca9f7bfa"
    assert item.comment_mid == "mid.ffffb9a7843177f901a0961f0b404461"
    assert item.text == "3-1"
    assert item.sender_id is None
    assert item.sender_name is None
    assert item.timestamp == 1789225012032
    assert item.is_actionable is True


def test_extract_comment_handles_comment_edited():
    payload = {
        **REAL_COMMENT_ENVELOPE,
        "update_type": "comment_edited",
        "message": {
            **REAL_COMMENT_ENVELOPE["message"],
            "body": {
                "mid": "mid.123",
                "text": "edited spam",
            },
        },
    }
    item = extract_comment(payload)
    assert item is not None
    assert item.update_type == "comment_edited"
    assert item.text == "edited spam"
    assert item.is_actionable is True


def test_extract_comment_returns_none_for_non_comment_updates():
    assert extract_comment(REAL_ENVELOPE) is None  # message_created
    assert extract_comment({"update_type": "comment_removed"}) is None
    assert extract_comment({"update_type": "probe"}) is None
    assert extract_comment({}) is None
    assert extract_comment("not a dict") is None


def test_extract_comment_actionable_flag():
    # Missing post_mid
    item = MaxCommentItem(
        update_type="comment_created",
        chat_id=-1,
        post_mid=None,
        comment_mid="mid.1",
        text="spam",
        sender_id=None,
        sender_name=None,
        timestamp=1,
    )
    assert item.is_actionable is False

    # Empty text
    item2 = MaxCommentItem(
        update_type="comment_created",
        chat_id=-1,
        post_mid="post.1",
        comment_mid="mid.1",
        text="   ",
        sender_id=None,
        sender_name=None,
        timestamp=1,
    )
    assert item2.is_actionable is False

