"""Tests for the chat-topic scan service (src/app/spam/chat_topics.py).

Phase 1: manual /scan only. Covers peer selection (channel vs plain group),
message filtering, trimming, derivation fallbacks, and DB writes.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.common.mtproto_client import MtprotoHttpError
from app.spam.chat_topics import (
    _fetch_chat_bio,
    _is_bot_own_message,
    _is_command_message,
    _resolve_linked_channel,
    _trim_sample,
    scan_chat_topics,
)


def _last_history_peer(mock_client) -> int:
    """Peer of the last messages.getHistory call (skips bio lookups)."""
    for c in reversed(mock_client.call.call_args_list):
        if c.args and c.args[0] == "messages.getHistory":
            return c.kwargs["params"]["peer"]
    raise AssertionError("no messages.getHistory call recorded")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestMessageFilters:
    def test_bot_own_message(self):
        assert _is_bot_own_message({"out": True, "message": "hi"}) is True
        assert _is_bot_own_message({"out": False, "message": "hi"}) is False
        assert _is_bot_own_message({"message": "hi"}) is False

    def test_command_message(self):
        assert _is_command_message({"message": "/start"}) is True
        assert _is_command_message({"message": "not a command"}) is False
        assert _is_command_message({"message": ""}) is False
        # Media caption commands also count
        assert _is_command_message({"media": {"caption": "/ban user"}}) is True


class TestTrimSample:
    def test_truncates_per_message_to_cap(self):
        long = {"message": "X" * 1000}
        parts = _trim_sample([long], max_msg=100, max_total=100_000)
        assert parts == ["X" * 100]

    def test_respects_total_budget(self):
        big = {"message": "Y" * 100}
        parts = _trim_sample([big, big], max_msg=100, max_total=150)
        assert len(parts) == 1
        assert parts == ["Y" * 100]

    def test_dedupes_identical_messages(self):
        m = {"message": "same text"}
        parts = _trim_sample([m, m, m], max_msg=100, max_total=100_000)
        assert parts == ["same text"]

    def test_skips_media_only(self):
        parts = _trim_sample(
            [{"media": {"type": "photo"}}, {"message": "real"}],
            max_msg=100,
            max_total=100_000,
        )
        assert parts == ["real"]

    def test_empty_input(self):
        assert _trim_sample([], max_msg=100, max_total=100_000) == []


# ---------------------------------------------------------------------------
# scan_chat_topics — real DB via patched pool; MTProto client + LLM mocked.
# Fixtures: patched_db_conn + clean_db wire get_pool() to the in-memory DB.
# ---------------------------------------------------------------------------


class TestScanChatTopics:
    async def _seed_group(self, clean_db, group_id, **fields):
        async with clean_db.acquire() as conn:
            cols = ", ".join(fields.keys())
            vals = ", ".join(f"${i+1}" for i in range(len(fields)))
            await conn.execute(
                f"INSERT INTO groups (group_id, {cols}) VALUES ($1, {vals})",
                group_id,
                *fields.values(),
            )

    @pytest.mark.asyncio
    async def test_plain_group_scan_success(self, patched_db_conn, clean_db):
        """Plain group: all messages minus bot-own + commands; stored."""
        await self._seed_group(
            clean_db, -100123, title="PHP Jobs", username=None
        )

        mock_client = MagicMock()
        mock_client.call = AsyncMock(
            return_value={
                "messages": [
                    {"out": True, "message": "bot's own message"},
                    {"message": "/admin only"},
                    {"message": "What's the best PHP framework?"},
                    {"message": "Laravel is great for freelancing"},
                ],
                "count": 4,
            }
        )

        summary = MagicMock()
        summary.description = "PHP jobs discussion."
        summary.short_description = "PHP jobs"

        def fake_derive(text):
            assert "/admin only" not in text
            assert "bot's own message" not in text
            return summary

        with patch(
            "app.spam.chat_topics.get_mtproto_client",
            return_value=mock_client,
        ), patch(
            "app.spam.chat_topics.derive_topic_summary",
            side_effect=fake_derive,
        ):
            result = await scan_chat_topics(-100123)

        assert result.status == "ok"
        assert result.detail == "PHP jobs"

        # Verify DB write landed
        async with clean_db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT topic_description, topic_description_short, "
                "topic_updated_at FROM groups WHERE group_id = $1",
                -100123,
            )
        assert row["topic_description"] == "PHP jobs discussion."
        assert row["topic_description_short"] == "PHP jobs"
        assert row["topic_updated_at"] is not None

    @pytest.mark.asyncio
    async def test_linked_channel_scans_channel_peer(self, patched_db_conn, clean_db):
        """Linked-discussion: fetch the channel peer (owner posts), not the group."""
        await self._seed_group(
            clean_db, -100123, title="Discussion", linked_channel_id=-100777
        )

        mock_client = MagicMock()
        mock_client.call = AsyncMock(
            return_value={
                "messages": [
                    {"message": "Channel post one about hosting"},
                    {"message": "Channel post two about VPS deals"},
                ],
                "count": 2,
            }
        )

        summary = MagicMock()
        summary.description = "VPS/hosting channel."
        summary.short_description = "VPS deals"

        with patch(
            "app.spam.chat_topics.get_mtproto_client",
            return_value=mock_client,
        ), patch(
            "app.spam.chat_topics.derive_topic_summary",
            return_value=summary,
        ):
            result = await scan_chat_topics(-100123)

        assert result.status == "ok"
        assert _last_history_peer(mock_client) == 777  # -100777 -> 777

    @pytest.mark.asyncio
    async def test_linked_channel_fallback_to_discussion(
        self, patched_db_conn, clean_db
    ):
        """Channel peer unreadable -> fall back to the discussion group's messages."""
        await self._seed_group(
            clean_db, -100123, title="Discussion", linked_channel_id=-100777
        )

        mock_client = MagicMock()

        async def fake_call(method, **kwargs):
            if method == "messages.getHistory" and kwargs["params"]["peer"] == 777:
                raise MtprotoHttpError("channel not accessible")
            if method == "messages.getHistory":
                return {
                    "messages": [
                        {"message": "discussion comment about PHP"},
                    ],
                    "count": 1,
                }
            # Bio lookups (getFullChannel / getFullChat) — no bio set here.
            return {"_": "chatFull"}

        mock_client.call = AsyncMock(side_effect=fake_call)

        summary = MagicMock()
        summary.description = "PHP discussion."
        summary.short_description = "PHP chat"

        with patch(
            "app.spam.chat_topics.get_mtproto_client",
            return_value=mock_client,
        ), patch(
            "app.spam.chat_topics.derive_topic_summary",
            return_value=summary,
        ):
            result = await scan_chat_topics(-100123)

        assert result.status == "ok"
        # Channel getHistory failed -> discussion getHistory succeeded.
        history_peers = [
            c.kwargs["params"]["peer"]
            for c in mock_client.call.call_args_list
            if c.args and c.args[0] == "messages.getHistory"
        ]
        assert history_peers == [777, 123]

    @pytest.mark.asyncio
    async def test_first_scan_derivation_failure_uses_title(
        self, patched_db_conn, clean_db
    ):
        """First scan + LLM failure -> chat title fallback, row written."""
        await self._seed_group(
            clean_db, -100123, title="PHP Freelance Hub", username=None
        )

        mock_client = MagicMock()
        mock_client.call = AsyncMock(
            return_value={
                "messages": [{"message": "some real content"}],
                "count": 1,
            }
        )

        with patch(
            "app.spam.chat_topics.get_mtproto_client",
            return_value=mock_client,
        ), patch(
            "app.spam.chat_topics.derive_topic_summary",
            return_value=None,
        ):
            result = await scan_chat_topics(-100123)

        assert result.status == "title_fallback"
        assert "PHP Freelance Hub" in result.detail

        async with clean_db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT topic_description, topic_updated_at "
                "FROM groups WHERE group_id = $1",
                -100123,
            )
        assert "PHP Freelance Hub" in row["topic_description"]
        assert row["topic_updated_at"] is not None

    @pytest.mark.asyncio
    async def test_rescan_derivation_failure_keeps_existing(
        self, patched_db_conn, clean_db
    ):
        """Re-scan + LLM failure -> keep existing description, no write."""
        await self._seed_group(
            clean_db,
            -100123,
            title="PHP Freelance Hub",
            username=None,
            topic_description="Existing rich description",
            topic_description_short="Existing",
        )

        mock_client = MagicMock()
        mock_client.call = AsyncMock(
            return_value={
                "messages": [{"message": "some real content"}],
                "count": 1,
            }
        )

        with patch(
            "app.spam.chat_topics.get_mtproto_client",
            return_value=mock_client,
        ), patch(
            "app.spam.chat_topics.derive_topic_summary",
            return_value=None,
        ):
            result = await scan_chat_topics(-100123)

        assert result.status == "kept_existing"

        async with clean_db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT topic_description FROM groups WHERE group_id = $1",
                -100123,
            )
        assert row["topic_description"] == "Existing rich description"

    @pytest.mark.asyncio
    async def test_empty_sample_first_scan_skips_llm(
        self, patched_db_conn, clean_db
    ):
        """Media-only chat, no prior scan -> skip LLM, chat title fallback."""
        await self._seed_group(
            clean_db, -100123, title="Photo Channel", username=None
        )

        mock_client = MagicMock()
        mock_client.call = AsyncMock(
            return_value={"messages": [{"media": {"type": "photo"}}], "count": 1}
        )

        with patch(
            "app.spam.chat_topics.get_mtproto_client",
            return_value=mock_client,
        ), patch(
            "app.spam.chat_topics.derive_topic_summary",
        ) as mock_derive:
            result = await scan_chat_topics(-100123)

        assert result.status == "empty_first_scan"
        mock_derive.assert_not_called()  # never spend an LLM call on empty corpus

        async with clean_db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT topic_description FROM groups WHERE group_id = $1",
                -100123,
            )
        assert "Photo Channel" in row["topic_description"]

    @pytest.mark.asyncio
    async def test_empty_sample_rescan_keeps_existing(
        self, patched_db_conn, clean_db
    ):
        """Media-only chat with prior scan -> no LLM call, no write."""
        await self._seed_group(
            clean_db,
            -100123,
            title="Photo Channel",
            username=None,
            topic_description="Old topic",
            topic_description_short="Old",
        )

        mock_client = MagicMock()
        mock_client.call = AsyncMock(
            return_value={"messages": [{"media": {"type": "photo"}}], "count": 1}
        )

        with patch(
            "app.spam.chat_topics.get_mtproto_client",
            return_value=mock_client,
        ), patch(
            "app.spam.chat_topics.derive_topic_summary",
        ) as mock_derive:
            result = await scan_chat_topics(-100123)

        assert result.status == "empty_kept_existing"
        mock_derive.assert_not_called()

        async with clean_db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT topic_description FROM groups WHERE group_id = $1",
                -100123,
            )
        assert row["topic_description"] == "Old topic"

    @pytest.mark.asyncio
    async def test_group_not_found(self, patched_db_conn, clean_db):
        """Unknown group id -> failed, nothing happens."""
        result = await scan_chat_topics(-999)
        assert result.status == "failed"
        assert result.detail == "group_not_found"

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_failed(self, patched_db_conn, clean_db):
        """Plain group fetch error -> FAILED, no write, no LLM."""
        await self._seed_group(
            clean_db, -100123, title="T", username=None
        )

        mock_client = MagicMock()
        mock_client.call = AsyncMock(side_effect=MtprotoHttpError("bridge down"))

        with patch(
            "app.spam.chat_topics.get_mtproto_client",
            return_value=mock_client,
        ), patch(
            "app.spam.chat_topics.derive_topic_summary",
        ) as mock_derive:
            result = await scan_chat_topics(-100123)

        assert result.status == "failed"
        mock_derive.assert_not_called()

        async with clean_db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT topic_description FROM groups WHERE group_id = $1",
                -100123,
            )
        assert row["topic_description"] is None


# ---------------------------------------------------------------------------
# Issue #5: live-resolve of linked channels when the DB row lacks the link.
# ---------------------------------------------------------------------------


class TestLiveResolveLinkedChannel:
    @pytest.mark.asyncio
    async def test_get_chat_failure_returns_none(self):
        """The resolve helper never raises — get_chat errors yield None."""
        with patch(
            "app.common.bot.bot.get_chat",
            new=AsyncMock(side_effect=RuntimeError("no token")),
        ):
            result = await _resolve_linked_channel(-100123)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_linked_chat_returns_metadata_only(self):
        chat = MagicMock()
        chat.linked_chat_id = None
        chat.title = "Some Group"
        chat.username = "some_group"
        with patch(
            "app.common.bot.bot.get_chat", new=AsyncMock(return_value=chat)
        ):
            result = await _resolve_linked_channel(-100123)
        assert result == {
            "linked_channel_id": None,
            "title": "Some Group",
            "username": "some_group",
        }


class TestScanLiveResolve:
    async def _seed_group(self, clean_db, group_id, **fields):
        async with clean_db.acquire() as conn:
            cols = ", ".join(fields.keys())
            vals = ", ".join(f"${i+1}" for i in range(len(fields)))
            await conn.execute(
                f"INSERT INTO groups (group_id, {cols}) VALUES ($1, {vals})",
                group_id,
                *fields.values(),
            )

    @pytest.mark.asyncio
    async def test_live_resolve_scans_channel_peer_and_heals_row(
        self, patched_db_conn, clean_db
    ):
        """Issue #5: NULL linked_channel_id + live-resolved link ->
        scan the channel peer (owner posts) and persist the discovered metadata."""
        await self._seed_group(clean_db, -100123, title=None, username=None)

        mock_client = MagicMock()
        mock_client.call = AsyncMock(
            return_value={
                "messages": [
                    {"message": "Channel post one about real estate"},
                    {"message": "Channel post two about land deals"},
                ],
                "count": 2,
            }
        )

        summary = MagicMock()
        summary.description = "Real-estate channel."
        summary.short_description = "Real estate"

        with patch(
            "app.spam.chat_topics.get_mtproto_client",
            return_value=mock_client,
        ), patch(
            "app.spam.chat_topics.derive_topic_summary",
            return_value=summary,
        ), patch(
            "app.spam.chat_topics._resolve_linked_channel",
            return_value={
                "linked_channel_id": -100777,
                "title": "Invest Channel",
                "username": "flipping_invest",
            },
        ):
            result = await scan_chat_topics(-100123)

        assert result.status == "ok"
        # Channel peer fetched (not the discussion's own messages)
        assert _last_history_peer(mock_client) == 777  # -100777 -> 777

        # Metadata healed on the row
        async with clean_db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT title, username, linked_channel_id "
                "FROM groups WHERE group_id = $1",
                -100123,
            )
        assert row["linked_channel_id"] == -100777
        assert row["title"] == "Invest Channel"
        assert row["username"] == "flipping_invest"

    @pytest.mark.asyncio
    async def test_live_resolve_unavailable_uses_plain_group(
        self, patched_db_conn, clean_db
    ):
        """Issue #5: live resolve returns None -> plain-group path unchanged."""
        await self._seed_group(clean_db, -100123, title="PHP Jobs", username=None)

        mock_client = MagicMock()
        mock_client.call = AsyncMock(
            return_value={
                "messages": [{"message": "What's the best PHP framework?"}],
                "count": 1,
            }
        )

        summary = MagicMock()
        summary.description = "PHP jobs."
        summary.short_description = "PHP jobs"

        with patch(
            "app.spam.chat_topics.get_mtproto_client",
            return_value=mock_client,
        ), patch(
            "app.spam.chat_topics.derive_topic_summary",
            return_value=summary,
        ), patch(
            "app.spam.chat_topics._resolve_linked_channel",
            return_value=None,
        ):
            result = await scan_chat_topics(-100123)

        assert result.status == "ok"
        # Plain-group peer, not a channel
        assert _last_history_peer(mock_client) == 123  # -100123 -> 123

    @pytest.mark.asyncio
    async def test_live_resolve_heals_metadata_without_link(
        self, patched_db_conn, clean_db
    ):
        """Issue #5: title/username found but no linked channel ->
        metadata healed, plain-group scan proceeds."""
        await self._seed_group(clean_db, -100123, title=None, username=None)

        mock_client = MagicMock()
        mock_client.call = AsyncMock(
            return_value={
                "messages": [{"message": "ordinary group chatter"}],
                "count": 1,
            }
        )

        summary = MagicMock()
        summary.description = "Group."
        summary.short_description = "Group"

        with patch(
            "app.spam.chat_topics.get_mtproto_client",
            return_value=mock_client,
        ), patch(
            "app.spam.chat_topics.derive_topic_summary",
            return_value=summary,
        ), patch(
            "app.spam.chat_topics._resolve_linked_channel",
            return_value={
                "linked_channel_id": None,
                "title": "Real Group Title",
                "username": "real_group",
            },
        ):
            result = await scan_chat_topics(-100123)

        assert result.status == "ok"
        assert _last_history_peer(mock_client) == 123

        async with clean_db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT title, username, linked_channel_id "
                "FROM groups WHERE group_id = $1",
                -100123,
            )
        assert row["linked_channel_id"] is None
        assert row["title"] == "Real Group Title"
        assert row["username"] == "real_group"

# ---------------------------------------------------------------------------
# Bio signal (issue #15): _fetch_chat_bio + scan wiring
# ---------------------------------------------------------------------------


class TestFetchChatBio:
    @pytest.mark.asyncio
    async def test_channel_about_returned_stripped(self):
        client = MagicMock()
        client.call = AsyncMock(
            return_value={"full_chat": {"about": "  Real estate fund insights  "}}
        )
        assert await _fetch_chat_bio(client, 777) == "Real estate fund insights"
        client.call.assert_awaited_once_with(
            "channels.getFullChannel", params={"channel": 777}, resolve=True
        )

    @pytest.mark.asyncio
    async def test_falls_back_to_full_chat_for_plain_groups(self):
        client = MagicMock()

        async def fake(method, **kwargs):
            if method == "channels.getFullChannel":
                return {"full_chat": {"about": ""}}
            return {"full_chat": {"about": "plain group bio"}}

        client.call = AsyncMock(side_effect=fake)
        assert await _fetch_chat_bio(client, 123) == "plain group bio"

    @pytest.mark.asyncio
    async def test_empty_abouts_return_none_after_both_attempts(self):
        client = MagicMock()
        client.call = AsyncMock(return_value={"_": "chatFull"})
        assert await _fetch_chat_bio(client, 123) is None
        assert client.call.await_count == 2  # getFullChannel + getFullChat

    @pytest.mark.asyncio
    async def test_bridge_failures_return_none(self):
        client = MagicMock()
        client.call = AsyncMock(side_effect=MtprotoHttpError("nope"))
        assert await _fetch_chat_bio(client, 123) is None
        assert client.call.await_count == 2

    @pytest.mark.asyncio
    async def test_none_peer_no_calls(self):
        client = MagicMock()
        assert await _fetch_chat_bio(client, None) is None
        client.call.assert_not_called()


class TestScanBioSignal:
    async def _seed_group(self, clean_db, group_id, **fields):
        async with clean_db.acquire() as conn:
            cols = ", ".join(fields.keys())
            vals = ", ".join(f"${i+1}" for i in range(len(fields)))
            await conn.execute(
                f"INSERT INTO groups (group_id, {cols}) VALUES ($1, {vals})",
                group_id,
                *fields.values(),
            )

    @pytest.mark.asyncio
    async def test_plain_group_bio_prepended_to_corpus(
        self, patched_db_conn, clean_db
    ):
        """Group (megagroup reality: bio via getFullChannel) leads the corpus."""
        await self._seed_group(clean_db, -100123, title="PHP Jobs", username=None)

        async def fake_call(method, **kwargs):
            if method == "channels.getFullChannel":
                return {
                    "full_chat": {
                        "about": "PHP jobs and Laravel freelancing"
                    }
                }
            if method == "messages.getFullChat":
                return {"full_chat": {"about": ""}}
            return {"messages": [{"message": "What's the best framework?"}],
                    "count": 1}

        mock_client = MagicMock()
        mock_client.call = AsyncMock(side_effect=fake_call)

        derive_inputs: dict[str, str] = {}

        def fake_derive(text):
            derive_inputs["text"] = text
            s = MagicMock()
            s.description = "PHP jobs."
            s.short_description = "PHP jobs"
            return s

        with patch(
            "app.spam.chat_topics.get_mtproto_client",
            return_value=mock_client,
        ), patch(
            "app.spam.chat_topics.derive_topic_summary",
            side_effect=fake_derive,
        ):
            result = await scan_chat_topics(-100123)

        assert result.status == "ok"
        assert derive_inputs["text"].startswith(
            "[CHAT BIO] PHP jobs and Laravel freelancing"
        )
        assert "What's the best framework?" in derive_inputs["text"]

    @pytest.mark.asyncio
    async def test_bio_only_corpus_derives_via_llm(
        self, patched_db_conn, clean_db
    ):
        """Media-only chat with a bio -> derive from bio, no title fallback."""
        await self._seed_group(clean_db, -100123, title="Photo Dump", username=None)

        async def fake_call(method, **kwargs):
            if method == "channels.getFullChannel":
                return {"full_chat": {"about": "Curated drone photography"}}
            return {"messages": [{"media": {"type": "photo"}}], "count": 1}

        mock_client = MagicMock()
        mock_client.call = AsyncMock(side_effect=fake_call)

        summary = MagicMock()
        summary.description = "Drone photo channel."
        summary.short_description = "drone photos"

        with patch(
            "app.spam.chat_topics.get_mtproto_client",
            return_value=mock_client,
        ), patch(
            "app.spam.chat_topics.derive_topic_summary",
            return_value=summary,
        ) as mock_derive:
            result = await scan_chat_topics(-100123)

        assert result.status == "ok"
        mock_derive.assert_awaited_once()
        assert mock_derive.await_args.args[0] == "[CHAT BIO] Curated drone photography"

    @pytest.mark.asyncio
    async def test_linked_channel_bio_used_when_channel_readable(
        self, patched_db_conn, clean_db
    ):
        """Linked discussion: bio comes from the CHANNEL, not the group."""
        await self._seed_group(
            clean_db, -100123, title="Discussion", linked_channel_id=-100777
        )

        async def fake_call(method, **kwargs):
            if method == "channels.getFullChannel":
                return {"full_chat": {"about": "VPS and hosting deals"}}
            if method == "messages.getFullChat":
                return {"full_chat": {"about": "group bio (must NOT be used)"}}
            return {"messages": [{"message": "Channel post about hosting"}],
                    "count": 1}

        mock_client = MagicMock()
        mock_client.call = AsyncMock(side_effect=fake_call)

        derive_inputs: dict[str, str] = {}

        def fake_derive(text):
            derive_inputs["text"] = text
            s = MagicMock()
            s.description = "VPS channel."
            s.short_description = "VPS deals"
            return s

        with patch(
            "app.spam.chat_topics.get_mtproto_client",
            return_value=mock_client,
        ), patch(
            "app.spam.chat_topics.derive_topic_summary",
            side_effect=fake_derive,
        ):
            result = await scan_chat_topics(-100123)

        assert result.status == "ok"
        assert derive_inputs["text"].startswith("[CHAT BIO] VPS and hosting deals")
        assert "group bio" not in derive_inputs["text"]
