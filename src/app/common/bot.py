import asyncio
import logging
import os
import sys

from aiogram import Bot
from aiogram.client.session.middlewares.base import (
    NextRequestMiddlewareType,
)
from aiogram.methods.base import TelegramMethod, TelegramType

logger = logging.getLogger(__name__)

bot_token = os.getenv("BOT_TOKEN")
if not bot_token:
    raise ValueError("BOT_TOKEN environment variable is required")
bot = Bot(token=bot_token)


def setup_session_retry(bot: Bot) -> None:
    """Register retry middleware on the bot session.

    Applies to ALL `bot.*` calls (send_message, delete_message, ban_chat_member, …).
    Uses the same retryability check as @retry_on_network_error.
    Total budget ≤45 s — within WEBHOOK_TIMEOUT (55 s).
    """

    async def retry_middleware(
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ):
        from .utils import _compute_retry_delay, _is_retryable_network_error

        for attempt in range(1, 5):
            try:
                result = await make_request(bot, method)
                return result
            except Exception as e:
                if not _is_retryable_network_error(e):
                    raise
                if attempt == 4:
                    logger.warning(
                        "All retries failed for %s: %s",
                        method.__class__.__name__,
                        e,
                    )
                    raise
                delay = _compute_retry_delay(e, attempt)
                logger.info(
                    "Retry %s/4 for %s, sleeping %.1fs",
                    attempt,
                    method.__class__.__name__,
                    delay,
                )
                await asyncio.sleep(delay)

    bot.session.middleware(retry_middleware)


# Admin chat ID is now loaded from config.yaml

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

try:
    import yaml

    with open("config.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    system = config.get("system", {})
    LESHCHENKO_CHAT_ID = system.get("admin_chat_id", 133526395)

except Exception:  # noqa: BLE001
    # Fallback value if config loading fails
    LESHCHENKO_CHAT_ID = 133526395
