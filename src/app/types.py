from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from .common.utils import (
    determine_effective_user_id,
    format_chat_or_channel_display,
)

if TYPE_CHECKING:
    from aiogram import types


class ContextStatus(Enum):
    FOUND = "found"
    EMPTY = "empty"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class ContextResult[T]:
    status: ContextStatus
    content: T | None = None
    error: str | None = None

    def get_fragment(self, not_found_default: str | None = None) -> str | None:
        """Extract content as prompt fragment string.

        Uses to_prompt_fragment if available, else str(content).
        Returns not_found_default when status != FOUND or content is None.
        """
        if self.status != ContextStatus.FOUND or self.content is None:
            return not_found_default
        method = getattr(self.content, "to_prompt_fragment", None)
        return str(method()) if callable(method) else str(self.content)

    @staticmethod
    def fragment_from_logfire_dict(
        obj: Any,
        *,
        empty_marker: str = "[EMPTY]",
    ) -> str | None:
        """Extract fragment string from Logfire-serialized ContextResult dict.

        Handles dict with status/content/error. Returns content if status=found
        and content is a string; empty_marker if status=empty; None otherwise.
        For dict content (e.g. profile_photo_age), returns None - caller handles separately.
        """
        if not obj or not isinstance(obj, dict):
            return None
        status = obj.get("status")
        content = obj.get("content")
        if status == "found" and content is not None:
            if isinstance(content, str):
                return content
            return None if isinstance(content, dict) else str(content)
        return empty_marker if status == "empty" else None


@dataclass(slots=True)
class LinkedChannelSummary:
    subscribers: int | None
    total_posts: int | None
    post_age_delta: int | None
    recent_posts_content: list[str] | None = None
    users: list[dict] | None = None
    channel_source: str | None = None  # "linked" | "bio" | "message"
    channel_id: int | None = None  # for display/resolve in /start etc.

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
        if self.channel_source:
            parts.append(f"channel_source={self.channel_source}")

        # Include recent posts content if available
        if self.recent_posts_content:
            # Limit to first 3 posts and truncate each to 200 chars to avoid token bloat
            content_snippets = []
            content_snippets.extend(
                f"post_{i + 1}: {content.strip()}"
                for i, content in enumerate(self.recent_posts_content[:3])
                if content.strip()
            )
            if content_snippets:
                formatted_posts = "\n\n".join(content_snippets)
                parts.append(f"recent_posts=[\n{formatted_posts}\n]")

        return "; ".join(parts)


@dataclass(slots=True)
class UserAccountInfo:
    user_id: int
    profile_photo_date: datetime | None = None

    @property
    def photo_age_months(self) -> int | None:
        if not self.profile_photo_date:
            return None
        now = datetime.now(UTC)
        return (
            (now.year - self.profile_photo_date.year) * 12
            + now.month
            - self.profile_photo_date.month
        )

    def to_prompt_fragment(self) -> str:
        parts = []

        # Only use photo age for prompt, ignore ID as per new robust strategy
        age_months = self.photo_age_months

        if age_months is not None:
            # Ensure non-negative
            age_months = max(0, age_months)
            parts.append(f"photo_age={age_months}mo")
        else:
            parts.append("photo_age=unknown")

        return "; ".join(parts)

    @classmethod
    def fragment_from_logfire_dict(cls, content: Any) -> str:
        """Convert Logfire-serialized profile photo age content dict to prompt fragment.

        Expects dict with user_id and profile_photo_date (ISO string).
        Returns 'photo_age=Xmo' or 'photo_age=unknown' per to_prompt_fragment contract.
        """
        if not content or not isinstance(content, dict):
            return "photo_age=unknown"
        date_val = content.get("profile_photo_date")
        if not date_val:
            return "photo_age=unknown"
        try:
            if isinstance(date_val, str):
                dt = datetime.fromisoformat(date_val)
            else:
                return "photo_age=unknown"
            now = datetime.now(UTC)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            age_months = max(
                0,
                (now.year - dt.year) * 12 + now.month - dt.month,
            )
            return f"photo_age={age_months}mo"
        except ValueError, TypeError:
            return "photo_age=unknown"


@dataclass(slots=True)
class PeerResolutionContext:
    """Context information for MTProto peer resolution operations."""

    # Core identifiers (always required)
    chat_id: int
    user_id: int
    message_id: int

    # Optional context parameters
    chat_username: str | None = None
    message_thread_id: int | None = None
    reply_to_message_id: int | None = None
    is_topic_message: bool = False
    main_channel_id: int | None = None
    main_channel_username: str | None = None
    original_channel_post_id: int | None = None

    @classmethod
    def from_message(
        cls, message: types.Message, user_id: int
    ) -> PeerResolutionContext:
        """Create PeerResolutionContext from a Telegram message object."""
        chat_id = int(message.chat.id)
        message_id = int(message.message_id)
        chat_username = getattr(message.chat, "username", None)
        message_thread_id = getattr(message, "message_thread_id", None)
        is_topic_message = bool(getattr(message, "is_topic_message", False))

        main_channel_id = None
        main_channel_username = None

        if (
            message_thread_id
            and not is_topic_message
            and hasattr(message, "reply_to_message")
            and message.reply_to_message
        ):
            reply_to = message.reply_to_message
            if hasattr(reply_to, "sender_chat") and reply_to.sender_chat:
                sender_chat = reply_to.sender_chat
                if getattr(sender_chat, "type", None) == "channel":
                    main_channel_id = getattr(sender_chat, "id", None)
                    main_channel_username = getattr(sender_chat, "username", None)

        reply_to_message_id = None
        original_channel_post_id = None
        if hasattr(message, "reply_to_message") and message.reply_to_message:
            reply_to_message_id = getattr(message.reply_to_message, "message_id", None)
            if (
                hasattr(message, "message_thread_id")
                and message.message_thread_id
                and not getattr(message, "is_topic_message", False)
            ):
                original_channel_post_id = getattr(
                    message.reply_to_message, "forward_from_message_id", None
                )

        return cls(
            chat_id=chat_id,
            user_id=user_id,
            message_id=message_id,
            chat_username=chat_username,
            message_thread_id=message_thread_id,
            reply_to_message_id=reply_to_message_id,
            is_topic_message=is_topic_message,
            main_channel_id=main_channel_id,
            main_channel_username=main_channel_username,
            original_channel_post_id=original_channel_post_id,
        )


@dataclass(slots=True)
class MessageContextResult:
    """Result of message context collection."""

    message_text: str
    is_story: bool
    context: SpamClassificationContext
    linked_channel_found: bool = False
    channel_users: list[dict] | None = None


@dataclass
class SpamClassificationContext:
    """Context information for spam classification."""

    # Basic user info (always available from message)
    name: str | None = None
    bio: str | None = None

    # Enriched context (may require additional API calls)
    linked_channel: ContextResult[LinkedChannelSummary] | None = None
    stories: ContextResult[str] | None = None
    reply: str | None = None
    profile_photo_age: ContextResult[UserAccountInfo] | None = None
    is_premium: bool | None = None
    is_channel_sender: bool = False
    # DB replay: raw ACCOUNT SIGNALS body from spam_examples / cache (photo + optional is_premium lines)
    account_signals_snapshot: str | None = None
    # Chat-topic signal (derived by /scan): short label of what this chat is normally about.
    chat_topics: str | None = None

    @property
    def include_chat_topics_guidance(self) -> bool:
        """Whether to include chat-topic guidance in the prompt."""
        return bool(self.chat_topics)

    @property
    def include_linked_channel_guidance(self) -> bool:
        """Whether to include linked channel guidance in the prompt."""
        return self.linked_channel is not None and self.linked_channel.status in (
            ContextStatus.FOUND,
            ContextStatus.EMPTY,
        )

    @property
    def include_stories_guidance(self) -> bool:
        """Whether to include stories guidance in the prompt."""
        return self.stories is not None and self.stories.status in (
            ContextStatus.FOUND,
            ContextStatus.EMPTY,
        )

    @property
    def include_reply_guidance(self) -> bool:
        """Whether to include reply context guidance in the prompt."""
        return self.reply is not None

    @property
    def include_account_signals_guidance(self) -> bool:
        """Whether to include account signals guidance (photo age + optional Premium)."""
        from .spam.account_signals import context_includes_account_signals

        return context_includes_account_signals(self)

    @property
    def include_ai_detection_guidance(self) -> bool:
        """Whether to include AI and emoji detection guidance in the prompt."""
        # Always include if it's a reply, or if we want to be proactive
        return (
            self.include_reply_guidance
            or self.include_stories_guidance
            or self.include_linked_channel_guidance
        )


@dataclass
class StorySummary:
    id: int
    date: int
    caption: str | None = None
    entities: list[dict[str, Any]] | None = None
    media: dict[str, Any] | None = None
    media_areas: list[dict[str, Any]] | None = None

    @staticmethod
    def contains_links(
        media: dict[str, Any] | None,
        media_areas: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Return True if media or media_areas contain links for context."""
        if media_areas:
            for area in media_areas:
                if area.get("_") == "MediaAreaUrl" and area.get("url"):
                    return True
        if media and media.get("_") == "messageMediaWebPage":
            return bool(media.get("webpage", {}).get("url"))
        return False

    @staticmethod
    def _link_strings_from_areas(
        areas: list[dict[str, Any]] | None,
    ) -> list[str]:
        """Extract 'Link: url' strings from MediaAreaUrl areas."""
        if not areas:
            return []
        return [
            f"Link: {area['url']}"
            for area in areas
            if area.get("_") == "MediaAreaUrl" and area.get("url")
        ]

    def to_string(self) -> str:
        parts = []
        if self.caption:
            parts.append(f"Caption: {self.caption}")
        if self.entities and (
            entity_links := [
                f"Link: {entity['url']}"
                for entity in self.entities
                if entity.get("_") == "messageEntityTextUrl" and entity.get("url")
            ]
        ):
            parts.append(f"Links: {', '.join(entity_links)}")
        if self.media:
            media_links = []
            if self.media.get("_") == "messageMediaWebPage" and (
                url := self.media.get("webpage", {}).get("url")
            ):
                media_links.append(f"Link: {url}")
            media_links.extend(
                self._link_strings_from_areas(self.media.get("media_areas"))
            )
            if media_links:
                parts.append(f"Links: {', '.join(media_links)}")
        if area_links := self._link_strings_from_areas(self.media_areas):
            parts.append(f"Links: {', '.join(area_links)}")
        return " | ".join(parts) if parts else "Media story"


@dataclass(slots=True)
class MessageNotificationContext:
    effective_user_id: int | None
    content_text: str
    chat_title: str
    chat_username: str | None  # raw username without @
    is_channel_sender: bool
    violator_name: str
    violator_username: str | None  # raw username without @
    forward_source: str
    message_link: str
    entity_name: str
    entity_type: str
    entity_username: str | None  # raw username without @

    @classmethod
    def from_message(cls, message: types.Message) -> MessageNotificationContext:
        """Create MessageNotificationContext from a Telegram message."""
        effective_user_id = determine_effective_user_id(message)
        content_text = message.text or message.caption or "[MEDIA_MESSAGE]"
        content_text = html.escape(content_text, quote=True)
        chat_title = message.chat.title or "Группа"
        chat_username = getattr(message.chat, "username", None)
        is_channel_sender = (
            message.sender_chat is not None
            and message.sender_chat.id != message.chat.id
        )

        if is_channel_sender and message.sender_chat is not None:
            violator_name = message.sender_chat.title or "Канал"
            violator_username = getattr(message.sender_chat, "username", None)
        elif message.from_user is not None:
            violator_name = message.from_user.full_name or "Пользователь без имени"
            violator_username = getattr(message.from_user, "username", None)
        else:
            violator_name = "Пользователь"
            violator_username = None

        forward_source = ""
        if forward_chat := getattr(message, "forward_from_chat", None):
            forward_title = getattr(forward_chat, "title", None) or "Канал"
            forward_username = getattr(forward_chat, "username", None)
            forward_source = (
                "\n\n"
                f"<b>Источник пересланного:</b> "
                f"{format_chat_or_channel_display(forward_title, forward_username, 'Канал')}"
            )

        message_link = (
            f"https://t.me/{message.chat.username}/{message.message_id}"
            if message.chat.username
            else ""
        )

        reply_sender_chat = None
        if message.reply_to_message is not None:
            reply_sender_chat = getattr(message.reply_to_message, "sender_chat", None)

        if reply_sender_chat is not None:
            entity_name = reply_sender_chat.title or "Канал"
            entity_type = "канале"
            entity_username = getattr(reply_sender_chat, "username", None)
        else:
            entity_name = chat_title
            entity_type = "группе"
            entity_username = chat_username

        return cls(
            effective_user_id=effective_user_id,
            content_text=content_text,
            chat_title=chat_title,
            chat_username=chat_username,
            is_channel_sender=is_channel_sender,
            violator_name=violator_name,
            violator_username=violator_username,
            forward_source=forward_source,
            message_link=message_link,
            entity_name=entity_name,
            entity_type=entity_type,
            entity_username=entity_username,
        )
