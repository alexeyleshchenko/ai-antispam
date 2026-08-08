"""
Userbot messaging via MCP bridge.

Sends direct messages using the MTProto userbot (via MCP) when Bot API
cannot reach the recipient (e.g. user never started the bot, bot removed from chat).
"""

import logging

from .mcp_client import McpHttpError, get_mcp_client

logger = logging.getLogger(__name__)


async def send_userbot_dm(
    *,
    username: str,
    user_id: int | None = None,
    message: str,
) -> bool:
    """
    Send a direct message to a user via the MCP userbot.

    Uses username for chat resolution. The message is sent from the userbot
    account, not from the bot.

    Args:
        username: Username without @ (required for resolution)
        user_id: Optional user ID for logging
        message: HTML-formatted message content

    Returns:
        True if sent successfully, False otherwise
    """
    client = get_mcp_client()
    chat_identifier = f"@{username}"
    log_extra = {"username": username, "user_id": user_id}

    try:
        await client.call_tool(
            "send_message",
            arguments={
                "chat_id": chat_identifier,
                "message": message,
                "parse_mode": "html",
            },
        )
        logger.info("Sent userbot DM", extra=log_extra)
        return True
    except McpHttpError as e:
        logger.warning(
            "Failed to send userbot DM",
            extra={**log_extra, "error": str(e)},
            exc_info=True,
        )
        return False
    except Exception as e:
        logger.exception(
            "Unexpected error sending userbot DM",
            extra={**log_extra, "error": str(e)},
        )
        return False
