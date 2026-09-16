#!/usr/bin/env python3
"""
Live test script to extract channel content for @kotnikova_yana (ID: -1003388711152)
"""

import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from dotenv import load_dotenv

# Load environment variables first
load_dotenv()

# Disable SSL verification for this test
os.environ["MTPROTO_HTTP_DISABLE_SSL_VERIFY"] = "1"

# Add src to path for mtproto_client import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from app.common.mtproto_client import (
    MtprotoHttpClient,
    MtprotoHttpError,
    get_mtproto_client,
)


# Copy the necessary classes and functions directly
@dataclass(slots=True)
class LinkedChannelSummary:
    subscribers: int | None
    total_posts: int | None
    post_age_delta: int | None
    recent_posts_content: list[str] | None = None

    def to_prompt_fragment(self) -> str:
        if self.post_age_delta is None or self.post_age_delta < 0:
            post_age_str = "unknown"
        else:
            post_age_str = f"{self.post_age_delta}mo"

        parts = [
            f"subscribers={self.subscribers if self.subscribers is not None else 'unknown'}",
            f"total_posts={self.total_posts if self.total_posts is not None else 'unknown'}",
            f"age_delta={post_age_str}",
        ]

        # Include recent posts content if available
        if self.recent_posts_content:
            # Limit to first 3 posts and truncate each to 200 chars to avoid token bloat
            content_snippets = []
            for i, content in enumerate(self.recent_posts_content[:3]):
                if content.strip():
                    truncated = content[:200].strip()
                    if len(content) > 200:
                        truncated += "..."
                    content_snippets.append(f"post_{i + 1}: {truncated}")

            if content_snippets:
                parts.append(f"recent_posts=[{'; '.join(content_snippets)}]")

        return "; ".join(parts)


def _extract_date(timestamp: Any) -> datetime | None:
    if not timestamp:
        return None
    if isinstance(timestamp, int):
        return datetime.fromtimestamp(timestamp, tz=UTC)
    if isinstance(timestamp, str):
        try:
            # Strings returned by the bridge are ISO8601 with timezone
            return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            print(f"Failed to parse date {timestamp}")
            return None
    return None


def _extract_message_date(message: dict[str, Any] | None) -> datetime | None:
    if not message:
        return None
    return _extract_date(message.get("date"))


def _extract_message_text(message: dict[str, Any]) -> str:
    """Extract text content from a Telegram message."""
    if not message:
        return ""

    # Direct message text
    message_text = message.get("message", "")

    # Caption from media messages
    if not message_text:
        media = message.get("media")
        if media and isinstance(media, dict):
            message_text = media.get("caption", "")

    return message_text


async def _fetch_channel_edge_message(
    client: MtprotoHttpClient,
    peer_reference: int | str,
    *,
    limit_offset: int | None,
) -> tuple[dict[str, Any] | None, int | None]:
    params: dict[str, Any] = {
        "peer": peer_reference,
        "offset_id": 0,
        "offset_date": 0,
        "add_offset": max(limit_offset or 0, 0),
        "limit": 1,
        "max_id": 0,
        "min_id": 0,
        "hash": 0,
    }

    try:
        history = await client.call("messages.getHistory", params=params, resolve=True)
    except MtprotoHttpError as exc:
        print(f"Failed to fetch channel history: {exc}")
        return None, None

    messages = history.get("messages", [])
    message = messages[0] if messages else None
    total = history.get("count")
    if total is None and messages:
        total = len(messages)
    return message, total


async def _fetch_recent_posts_content(
    client: MtprotoHttpClient,
    peer_reference: int | str,
    limit: int = 5,
) -> list[str]:
    """
    Fetch content from recent posts in a channel to analyze for spam indicators.
    Returns list of text content from recent posts (excluding media-only posts).
    """
    params: dict[str, Any] = {
        "peer": peer_reference,
        "offset_id": 0,
        "offset_date": 0,
        "add_offset": 0,
        "limit": limit,
        "max_id": 0,
        "min_id": 0,
        "hash": 0,
    }

    try:
        history = await client.call("messages.getHistory", params=params, resolve=True)
    except MtprotoHttpError as exc:
        print(f"Failed to fetch recent posts content: {exc}")
        return []

    messages = history.get("messages", [])
    content_list = []

    for message in messages:
        # Extract text content from message
        text_content = _extract_message_text(message)
        if text_content and text_content.strip():
            content_list.append(text_content.strip())

    return content_list


async def collect_channel_summary_by_id(
    channel_id: int,
    user_reference: str | int = "unknown",
    username: str | None = None,
) -> LinkedChannelSummary | None:
    """
    Collects summary stats for a specific channel ID.
    """
    client = get_mtproto_client()

    identifiers = []

    # Prefer username for channel resolution if available
    if username:
        identifiers.append(username)
    else:
        # Convert Bot API ID (negative -100...) to MTProto ID (positive, without -100)
        mtproto_id = channel_id
        if channel_id < 0:
            str_id = str(channel_id)
            if str_id.startswith("-100"):
                mtproto_id = int(str_id[4:])
            elif str_id.startswith("-"):
                mtproto_id = int(str_id[1:])
        identifiers.append(mtproto_id)

    try:
        full_channel, successful_identifier = await client.call_with_fallback(
            "channels.getFullChannel",
            identifiers=identifiers,
            identifier_param="channel",
        )
    except MtprotoHttpError as e:
        print(f"Failed to load full channel: {e}")
        return None

    subscribers = full_channel.get("full_chat", {}).get("participants_count")
    print(f"MTProto channel stats: subscribers={subscribers}")

    # Use the identifier that worked for the history call to ensure consistency
    peer_to_use = successful_identifier

    newest_message, total_posts = await _fetch_channel_edge_message(
        client, peer_to_use, limit_offset=None
    )

    newest_post_date = _extract_message_date(newest_message)
    oldest_post_date = None
    if total_posts and total_posts > 1:
        oldest_message, _ = await _fetch_channel_edge_message(
            client, peer_to_use, limit_offset=total_posts - 1
        )
        oldest_post_date = _extract_message_date(oldest_message)

    oldest_post_date = oldest_post_date or newest_post_date
    post_age_delta = None
    if newest_post_date and oldest_post_date:
        delta_days = (newest_post_date - oldest_post_date).days
        post_age_delta = max(delta_days // 30, 0)

    # Fetch recent posts content for spam analysis
    recent_posts_content = await _fetch_recent_posts_content(
        client, peer_to_use, limit=5
    )

    summary = LinkedChannelSummary(
        subscribers=subscribers,
        total_posts=total_posts,
        post_age_delta=post_age_delta,
        recent_posts_content=recent_posts_content if recent_posts_content else None,
    )

    print("Channel summary collected successfully")
    return summary


@pytest.mark.integration
@pytest.mark.integration
async def test_channel_extraction():
    """Test channel content extraction for the problematic channel."""
    channel_id = -1003388711152  # @kotnikova_yana

    print(f"Testing channel extraction for channel ID: {channel_id}")
    print("=" * 60)

    try:
        # Call the channel extraction function
        result = await collect_channel_summary_by_id(channel_id, user_reference="test")

        if result is None:
            print("❌ Channel extraction failed - returned None")
            return

        print("✅ Channel extraction successful!")
        print(f"📊 Subscribers: {result.subscribers}")
        print(f"📝 Total posts: {result.total_posts}")
        print(f"📅 Post age delta: {result.post_age_delta} months")

        print("\n📄 Recent posts content:")
        if result.recent_posts_content:
            for i, content in enumerate(result.recent_posts_content, 1):
                print(f"  {i}. {content[:200]}{'...' if len(content) > 200 else ''}")
        else:
            print("  No text content found in recent posts")

        print("\n🔗 Prompt fragment:")
        print(f"  {result.to_prompt_fragment()}")

    except Exception as e:
        print(f"❌ Error during channel extraction: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    # Load environment variables
    from dotenv import load_dotenv

    load_dotenv()

    # Run the test
    asyncio.run(test_channel_extraction())
