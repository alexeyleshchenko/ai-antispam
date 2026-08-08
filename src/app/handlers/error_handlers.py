"""Centralized dispatcher error handler (aiogram ErrorEvent)."""

import contextlib
import logging

from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import ErrorEvent
from pydantic_ai.exceptions import ModelAPIError

from ..common.telegram_errors import (
    is_group_inaccessible_error,
    is_permission_error,
    is_webhook_retryable,
)
from ..i18n import resolve_lang, t
from .dp import dp

logger = logging.getLogger(__name__)


@dp.errors()
async def handle_dispatcher_error(event: ErrorEvent) -> None:
    exc = event.exception

    if isinstance(exc, ModelAPIError):
        raise exc

    if is_group_inaccessible_error(exc):
        logger.info(
            "Expected inaccessible group during update handling: %s",
            exc,
        )
        return

    if is_permission_error(exc):
        logger.warning(
            "Permission error reached dispatcher (call site should handle): %s",
            exc,
            exc_info=exc,
        )
        return

    if (
        isinstance(exc, TelegramForbiddenError)
        and "bot was blocked" in str(exc).lower()
    ):
        logger.info("Ignoring blocked user interaction: %s", exc)
        return

    logger.error("Unhandled exception in dispatcher: %s", exc, exc_info=exc)

    if is_webhook_retryable(exc):
        raise exc

    await _try_user_feedback(event)


async def _try_user_feedback(event: ErrorEvent) -> None:
    update = event.update
    if update is None:
        return

    callback = update.callback_query
    if callback is not None:
        lang = resolve_lang(callback.from_user)
        with contextlib.suppress(Exception):
            await callback.answer(
                t(lang, "callback.error_generic"),
                show_alert=True,
            )
        return

    message = update.message
    if message is not None and message.chat.type == "private":
        lang = resolve_lang(message)
        with contextlib.suppress(Exception):
            await message.answer(t(lang, "private.error_generic"))
