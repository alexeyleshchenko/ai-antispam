"""Bot command menu setup for Telegram. Commands are localized via language_code."""

import logging

from aiogram.types import BotCommand

from .i18n import load_locales, t

logger = logging.getLogger(__name__)

_COMMAND_IDS = ["start", "help", "buy", "stats", "mode", "ref", "lang", "scan"]


def _build_commands(lang: str) -> list[BotCommand]:
    """Build BotCommand list from locale files."""
    load_locales()
    return [
        BotCommand(command=cmd, description=t(lang, f"bot_commands.{cmd}"))
        for cmd in _COMMAND_IDS
    ]


async def setup_bot_commands(bot) -> None:
    """Register bot command menus for EN and RU. Called on server startup."""
    try:
        # English first (fallback for users with unsupported locale)
        commands_en = _build_commands("en")
        await bot.set_my_commands(commands=commands_en, language_code="en")
        logger.info("Bot commands set for language_code=en")
        # Russian
        commands_ru = _build_commands("ru")
        await bot.set_my_commands(commands=commands_ru, language_code="ru")
        logger.info("Bot commands set for language_code=ru")
    except Exception as e:
        logger.error("Failed to set bot commands: %s", e)
        raise
