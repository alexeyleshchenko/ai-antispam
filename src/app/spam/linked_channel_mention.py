"""
Extract first channel/chat mention from text (e.g. bio or message).

Used for linked channel collection from bio and message when profile has no linked channel.
"""

import re
from typing import Any

# Telegram username: 5-32 characters, [a-zA-Z0-9_]
USERNAME_PATTERN = re.compile(r"[a-zA-Z0-9_]{5,32}")
MENTION_REGEX = re.compile(r"@([a-zA-Z0-9_]{5,32})")
T_ME_USERNAME_REGEX = re.compile(
    r"t\.me/([a-zA-Z0-9_]{5,32})(?:\s|$|/|\))", re.IGNORECASE
)
T_ME_URL_IN_ENTITY_REGEX = re.compile(r"t\.me/([a-zA-Z0-9_]{5,32})(?:/|$|\?)", re.IGNORECASE)


def _entity_field(entity: Any, field: str, default: Any = None) -> Any:
    """Read field from dict-like or object-like Telegram entity."""
    if isinstance(entity, dict):
        return entity.get(field, default)
    return getattr(entity, field, default)


def _extract_username_from_entity(text: str, entity: Any) -> str | None:
    """Extract username from a single Telegram entity if present."""
    if (entity_type := _entity_field(entity, "type")) == "mention":
        offset = _entity_field(entity, "offset", 0) or 0
        if length := _entity_field(entity, "length", 0) or 0:
            mention = text[offset : offset + length].lstrip("@")
            if USERNAME_PATTERN.fullmatch(mention):
                return mention
        return None

    if entity_type == "text_link":
        url = _entity_field(entity, "url", "") or ""
        if match := T_ME_URL_IN_ENTITY_REGEX.search(url):
            return match[1]
    return None


def extract_first_channel_mention(
    text: str,
    entities: list[Any] | None = None,
) -> str | None:
    """
    Extract the first candidate channel/chat username from text.

    Prefers Telegram message entities (mention, text_link) when provided.
    Falls back to regex on raw text for @username and t.me/username.

    Args:
        text: Raw text (e.g. bio, message text or caption).
        entities: Optional list of Telegram MessageEntity dicts or objects
                  (with type, offset, length, and optionally url).

    Returns:
        First candidate username without @, or None if none found.
    """
    if not text or not isinstance(text, str):
        return None

    # Prefer entities for exact boundaries
    if entities:
        for entity in entities:
            if username := _extract_username_from_entity(text, entity):
                return username

    # Fallback: regex on text
    m = MENTION_REGEX.search(text)
    if m:
        return m.group(1)
    m = T_ME_USERNAME_REGEX.search(text)
    return m.group(1) if m else None
