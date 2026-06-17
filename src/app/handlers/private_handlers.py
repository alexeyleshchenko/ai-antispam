import asyncio
import contextlib
import logging
import pathlib
from collections.abc import Coroutine
from datetime import timedelta
from typing import Any, Dict, List, Optional, cast

from aiogram import F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import or_f

from ..common.bot import bot
from ..common.telegram_errors import is_message_not_found_error
from ..spam.account_signals import build_account_signals_body
from ..spam.user_profile import collect_user_context
from pydantic_ai import ModelSettings

from ..agents import (
    get_chat_agent,
    get_openrouter_chat_agent,
    _get_openrouter_chat_agents,
    _next_openrouter_chat_agent,
)
from ..common.utils import get_llm_route_timeout, sanitize_llm_html
from ..database import (
    add_spam_example,
    find_message_by_text_and_user,
    get_admin,
    get_message_history,
    get_spam_examples,
    initialize_new_admin,
    remove_member_from_group,
    save_message,
    update_admin_username_if_needed,
)
from ..database.group_operations import get_admin_groups
from ..i18n import normalize_lang, t
from ..types import SpamClassificationContext
from .dp import dp

logger = logging.getLogger(__name__)


class OriginalMessageExtractionError(Exception):
    """Raised when original message information cannot be extracted"""


def _resolve_admin_lang(admin: Any) -> str:
    """Resolve admin language with fallback to English."""
    return (
        normalize_lang(admin.language_code) if admin and admin.language_code else "en"
    )


def _premium_from_forward(msg: types.Message) -> Optional[bool]:
    if msg.forward_from:
        return getattr(msg.forward_from, "is_premium", None)
    origin = msg.forward_origin
    if isinstance(origin, types.MessageOriginUser):
        return getattr(origin.sender_user, "is_premium", None)
    return None


# ---------------------------------------------------------------------------
# Helper functions for handle_private_message
# ---------------------------------------------------------------------------


def _read_prd() -> str:
    """Read PRD.md content for system prompt."""
    try:
        return pathlib.Path("PRD.md").read_text()
    except Exception:
        return ""


def _format_spam_examples(spam_examples: list[dict]) -> list[str]:
    """Format spam examples as XML-like strings for the LLM prompt."""
    formatted = []
    for example in spam_examples:
        example_str = f"<пример>\n<запрос>\n<текст сообщения>\n{example['text']}\n</текст сообщения>"
        if "name" in example:
            example_str += f"\n<имя>{example['name']}</имя>"
        if "bio" in example:
            example_str += f"\n<биография>{example['bio']}</биография>"
        if example.get("linked_channel_fragment"):
            example_str += f"\n<канал>{example['linked_channel_fragment']}</канал>"
        example_str += "\n</запрос>\n<ответ>\n"
        # DB convention: score > 0 = spam, score < 0 = legitimate
        example_str += f"{'да' if example['score'] > 0 else 'нет'} {abs(example['score'])}%\n</ответ>"
        example_str += "\n</пример>"
        formatted.append(example_str)
    return formatted


def _build_system_prompt(prd_text: str, formatted_examples: list[str]) -> str:
    """Build the system prompt with PRD description, examples, and HTML rules."""
    return f"""
Ты - нейромодератор, киберсущность, защищающая пользователя от спама.

<функционал и стиль ответа>
{prd_text}
</функционал и стиль ответа>

А вот примеры того, что ты считаешь спамом, а что нет
(is_spam=true — спам, is_spam=false — не спам):
<примеры>
{"\n".join(formatted_examples)}
</примеры>

Отвечай от имени бота и используй указанный стиль ответа.

Учитывай предыдущий контекст разговора при ответе.

Разбивай текст на короткие абзацы. Умеренно используй эмодзи.
Используй выделение жирным.

<требования к форматированию>
ВНИМАНИЕ! Используй только следующий синтаксис форматирования (Telegram HTML):

<b>Жирный</b>: выделяй жирное тегами <b> и </b>: <b>пример жирного текста</b>
<i>Курсив</i>: выделяй курсив тегами <i> и </i>: <i>пример курсива</i>
Не используй Markdown символы (*, _, `, [, ], etc.)
Не используй другие виды форматирования.

Примеры:
• Это <b>жирный текст</b>
• Это <i>курсив</i>
• Это обычный текст

Неправильно:
• *жирный* (не будет работать)
• _курсив_ (не будет работать)
• **жирный** (не будет работать)

ВСЕГДА следуй этим правилам форматирования!
</требования к форматированию>
"""


def _build_conversation_text(message_history: list[dict], admin_message: str) -> str:
    """Build the full conversation text from history for the LLM context."""
    conversation_parts = []
    for msg in message_history:
        role = msg["role"]
        content = msg["content"]
        conversation_parts.append(f"{role.upper()}: {content}")
    return "\n".join(conversation_parts) + f"\n\nUSER: {admin_message}"


def _is_html_error(send_error: TelegramBadRequest) -> bool:
    """Check whether a TelegramBadRequest is caused by HTML formatting."""
    error_msg = str(send_error).lower()
    return any(
        error_text in error_msg
        for error_text in (
            "can't parse entities",
            "can't find end tag",
            "unclosed tag",
        )
    )


def _build_html_correction_prompt(original_message: str, broken_response: str) -> str:
    """Build a prompt that asks the LLM to fix HTML formatting of its last response."""
    return (
        f"{original_message}\n\n"
        "ПРЕДЫДУЩИЙ ОТВЕТ СОДЕРЖАЛ ОШИБКУ ФОРМАТИРОВАНИЯ HTML: "
        f"{broken_response}\n\n"
        "Пожалуйста, повтори ответ, строго следуя правилам HTML: "
        "используй только <b> для жирного и <i> для курсива, "
        "обязательно закрывай все теги."
    )


async def _try_provider_with_retries(
    chat_agent,
    *,
    user_message_text: str,
    system_prompt: str,
    model_settings: ModelSettings,
    admin_message: str,
    admin_id: int,
    message: types.Message,
    max_retries: int = 3,
) -> str:
    """Run a single language model provider with HTML correction retries.

    Tries *max_retries* times to get a reply from *chat_agent*.  On each
    attempt the response is saved and sent to the user; if sending fails
    with an HTML formatting error the next attempt receives the HTML
    correction prompt (built from *admin_message* and the broken response).

    Returns ``"private_message_replied"`` on success.
    Raises the underlying exception when all retries are exhausted.
    """
    current_text = user_message_text
    for retry_count in range(max_retries):
        result = await chat_agent.run(
            current_text,
            instructions=system_prompt,
            model_settings=model_settings,
        )
        response = result.output

        await save_message(admin_id, "assistant", response)

        try:
            sanitized_response = sanitize_llm_html(response)
            await message.reply(sanitized_response, parse_mode="HTML")
            return "private_message_replied"
        except TelegramBadRequest as send_error:
            if not _is_html_error(send_error) or retry_count >= max_retries - 1:
                raise
            current_text = _build_html_correction_prompt(admin_message, response)

    raise RuntimeError("Unreachable: retry loop exhausted without return or raise")


# ===================================================================


@dp.message(
    F.chat.type == "private",
    ~F.text.startswith("/"),
    ~F.forward_from,
    ~F.forward_origin,
)
async def handle_private_message(message: types.Message) -> str:
    """Reply to user in private chat using LLM and message history context."""
    if not message.from_user:
        return "private_no_user_info"

    user = cast("types.User", message.from_user)
    admin_id = user.id
    admin_message = message.text

    if not admin_message:
        return "private_no_message_text"

    await initialize_new_admin(admin_id)
    await update_admin_username_if_needed(admin_id, user.username)

    # Save user message to history
    await save_message(admin_id, "user", admin_message)

    # Get conversation history
    message_history = await get_message_history(admin_id)

    # Build prompt components
    prd_text = _read_prd()
    spam_examples = await get_spam_examples()
    formatted_examples = _format_spam_examples(spam_examples)
    system_prompt = _build_system_prompt(prd_text, formatted_examples)
    user_message_text = _build_conversation_text(message_history, admin_message)

    llm_timeout = get_llm_route_timeout()
    model_settings = ModelSettings(timeout=llm_timeout)
    last_error = None

    # Try gateway first
    try:
        chat_agent = get_chat_agent()
        return await _try_provider_with_retries(
            chat_agent,
            user_message_text=user_message_text,
            system_prompt=system_prompt,
            model_settings=model_settings,
            admin_message=admin_message,
            admin_id=admin_id,
            message=message,
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        last_error = e
        logger.warning(f"Gateway chat failed: {e}")

    # OpenRouter pool with rotation
    num_openrouter = len(_get_openrouter_chat_agents())

    for i in range(num_openrouter):
        chat_agent = get_openrouter_chat_agent()
        provider_label = (
            f"openrouter-{chat_agent.name}"
            if hasattr(chat_agent, "name")
            else f"openrouter-{i}"
        )

        try:
            return await _try_provider_with_retries(
                chat_agent,
                user_message_text=user_message_text,
                system_prompt=system_prompt,
                model_settings=model_settings,
                admin_message=admin_message,
                admin_id=admin_id,
                message=message,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            last_error = e
            logger.warning(f"{provider_label} chat agent failed: {e}")
            _next_openrouter_chat_agent()

    raise RuntimeError(f"All chat providers failed. Last error: {last_error}")


@dp.message(F.chat.type == "private", or_f(F.forward_from, F.forward_origin))
async def handle_forwarded_message(message: types.Message) -> str:
    """Prompt admin to confirm spam/not_spam for forwarded message."""
    if not message.from_user:
        return "private_forward_no_user_info"

    admin_id = cast("types.User", message.from_user).id
    await initialize_new_admin(admin_id)
    admin = await get_admin(admin_id)
    lang = _resolve_admin_lang(admin)

    row = [
        types.InlineKeyboardButton(
            text=t(lang, "private.spam_button"),
            callback_data="spam_example:spam",
            style="danger",
        ),
        types.InlineKeyboardButton(
            text=t(lang, "private.not_spam_button"),
            callback_data="spam_example:not_spam",
            style="success",
        ),
    ]
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[row])

    await message.reply(
        t(lang, "private.add_example_confirm"),
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    return "private_forward_prompt_sent"


async def _load_channel_fragment(info: Dict[str, Any]) -> Optional[str]:
    """Best-effort linked channel fragment extraction for forwarded user."""
    user_id = info.get("user_id")
    if not user_id:
        return None

    username = info.get("username")
    try:
        user_context = await collect_user_context(user_id, username=username)
    except Exception as exc:  # noqa: BLE001 - log and continue
        logger.info(
            "Failed to load user context for forwarded user",
            extra={"user_id": user_id, "username": username, "error": str(exc)},
        )
        return None

    linked = user_context.linked_channel if user_context else None
    return linked.get_fragment() if linked else None


def _resolve_example_texts(lang: str, is_spam: bool) -> tuple[str, str]:
    """Resolve callback answer and edited message label texts."""
    answer_text = (
        t(lang, "private.example_spam_removed")
        if is_spam
        else t(lang, "private.example_not_spam_added")
    )
    edit_type = (
        ("spam" if is_spam else "valuable")
        if lang == "en"
        else ("спама" if is_spam else "ценного сообщения")
    )
    return answer_text, edit_type


async def _delete_message_safely(chat_id: int, message_id: int) -> None:
    """Delete a message, silently ignoring 'message not found' errors."""
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramBadRequest as e:
        if is_message_not_found_error(e):
            logger.debug(
                "Message %s already deleted in chat %s",
                message_id,
                chat_id,
                extra={"message_deletion": "already_deleted"},
            )
            return
        raise


def _append_spam_cleanup_tasks(
    tasks: List[Coroutine[Any, Any, Any]],
    info: Dict[str, Any],
) -> None:
    """Add best-effort cleanup tasks for spam confirmations."""
    if user_id := info.get("user_id"):
        tasks.append(remove_member_from_group(member_id=user_id))
    else:
        logger.warning("User ID not found in info, skipping removal from group")

    group_chat_id = info.get("group_chat_id")
    group_message_id = info.get("group_message_id")
    if group_chat_id and group_message_id:
        logger.info(
            f"Adding message deletion task: chat_id={group_chat_id}, message_id={group_message_id}",
            extra={"message_deletion": "task_added"},
        )
        tasks.append(
            _delete_message_safely(
                chat_id=group_chat_id,
                message_id=group_message_id,
            )
        )
        return

    logger.warning(
        "Group chat ID or message ID not found in info, skipping message deletion",
        extra={
            "message_deletion": "skipped",
            "group_chat_id": group_chat_id,
            "group_message_id": group_message_id,
        },
    )


@dp.callback_query(F.data.startswith("spam_example:"))
async def process_spam_example_callback(callback: types.CallbackQuery) -> str:
    """Handle spam/not_spam button press for forwarded message."""
    if not callback.from_user or not callback.data or not callback.message:
        return "spam_example_invalid_callback"

    user = cast("types.User", callback.from_user)
    admin_id = user.id
    _, action = callback.data.split(":", maxsplit=1)
    is_spam = action == "spam"

    try:
        if not isinstance(callback.message, types.Message):
            return "spam_example_invalid_message_type"

        info = await extract_original_message_info(callback.message, admin_id)
        channel_fragment = await _load_channel_fragment(info)

        admin = await get_admin(admin_id)
        lang = _resolve_admin_lang(admin)
        answer_text, edit_type = _resolve_example_texts(lang, is_spam)
        with contextlib.suppress(Exception):
            await callback.answer(answer_text)

        tasks: List[Coroutine[Any, Any, Any]] = [
            add_spam_example(
                info["text"],
                name=info["name"],
                bio=info["bio"],
                score=100 if is_spam else -100,
                admin_id=admin_id,
                linked_channel_fragment=channel_fragment,
                stories_context=info.get("stories_context"),
                reply_context=info.get("reply_context"),
                account_signals_context=info.get("account_signals_context"),
            ),
            bot.edit_message_text(
                chat_id=callback.message.chat.id,
                message_id=callback.message.message_id,
                text=t(lang, "private.example_added", type=edit_type),
                parse_mode="HTML",
                reply_markup=None,
            ),
        ]

        if is_spam:
            _append_spam_cleanup_tasks(tasks, info)

        await asyncio.gather(*tasks)

        if is_spam:
            logger.info(
                f"Spam example processing completed: user_removed={bool(info.get('user_id'))}, message_deleted={bool(info.get('group_chat_id') and info.get('group_message_id'))}",
                extra={"spam_example_processing": "completed"},
            )

        return "spam_example_processed"

    except OriginalMessageExtractionError as e:
        logger.error(f"Failed to extract original message info: {e}")
        admin = await get_admin(admin_id)
        lang = _resolve_admin_lang(admin)
        await callback.answer(t(lang, "private.error_forward_info"), show_alert=True)
        return "spam_example_extraction_error"
    except Exception as e:
        logger.error(f"Error processing spam example: {e}", exc_info=True)
        admin = await get_admin(admin_id)
        lang = _resolve_admin_lang(admin)
        await callback.answer(t(lang, "private.error_generic"), show_alert=True)
        return "spam_example_error"


async def extract_original_message_info(
    callback_message: types.Message,
    admin_id: int,
) -> Dict[str, Any]:
    """Extract original message info from forwarded message for spam example creation."""
    if not callback_message.reply_to_message:
        raise OriginalMessageExtractionError("No reply_to_message found")

    original_message = callback_message.reply_to_message
    if not (original_message.forward_from or original_message.forward_origin):
        raise OriginalMessageExtractionError("No forward information found")

    info = _build_base_forwarded_info(original_message)
    await _enrich_with_forward_metadata(info, original_message)
    _log_missing_forwarded_user_id(info, original_message)
    await _fill_lookup_context_if_needed(info, original_message, admin_id)
    return info


def _build_base_forwarded_info(original_message: types.Message) -> Dict[str, Any]:
    return {
        "text": original_message.text or original_message.caption or "[MEDIA_MESSAGE]",
        "name": None,
        "bio": None,
        "user_id": None,
        "username": None,
        "group_chat_id": None,
        "group_message_id": None,
        "stories_context": None,
        "reply_context": None,
        "account_signals_context": None,
    }


async def _safe_get_chat_bio(user_id: int) -> Optional[str]:
    user_info = await bot.get_chat(user_id)
    return user_info.bio if user_info else None


async def _enrich_with_forward_metadata(
    info: Dict[str, Any],
    original_message: types.Message,
) -> None:
    origin = original_message.forward_origin

    if original_message.forward_from:
        user = original_message.forward_from
        info["name"] = user.full_name
        info["user_id"] = user.id
        info["username"] = user.username
        info["bio"] = await _safe_get_chat_bio(user.id)

    if isinstance(origin, types.MessageOriginUser):
        sender_user = origin.sender_user
        info["name"] = info["name"] or sender_user.full_name
        info["user_id"] = sender_user.id
        info["username"] = info["username"] or sender_user.username
        if not info["bio"]:
            info["bio"] = await _safe_get_chat_bio(sender_user.id)
    elif isinstance(origin, types.MessageOriginChannel):
        info["name"] = info["name"] or origin.chat.title
        info["group_chat_id"] = origin.chat.id
        info["group_message_id"] = origin.message_id


def _log_missing_forwarded_user_id(
    info: Dict[str, Any],
    original_message: types.Message,
) -> None:
    if info["user_id"]:
        return
    origin = original_message.forward_origin
    logger.info(
        "Cannot determine forwarded user id for spam example",
        extra={
            "forward_from": bool(original_message.forward_from),
            "forward_origin_type": getattr(origin, "type", None) if origin else None,
        },
    )


async def _fill_lookup_context_if_needed(
    info: Dict[str, Any],
    original_message: types.Message,
    admin_id: int,
) -> None:
    if info["group_chat_id"] and info["group_message_id"]:
        return

    admin_groups = await get_admin_groups(admin_id)
    admin_group_ids = [group["id"] for group in admin_groups]
    if not admin_group_ids:
        logger.info(
            "Admin has no managed groups, skipping message lookup",
            extra={"message_lookup": "skip", "candidate_chats": 0},
        )
        return

    lookup_result = await _find_message_lookup_result(
        info=info,
        original_message=original_message,
        admin_group_ids=admin_group_ids,
    )
    if not lookup_result:
        logger.info(
            "No matching message found in message_lookup_cache",
            extra={
                "message_lookup": "miss",
                "candidate_chats": len(admin_group_ids),
                "user_id_provided": info["user_id"] is not None,
            },
        )
        return

    _merge_lookup_result_into_info(info, lookup_result)
    await _backfill_account_signals_if_missing(info, original_message)
    logger.info(
        "Message lookup succeeded",
        extra={
            "message_lookup": "success",
            "candidate_chats": len(admin_group_ids),
            "user_id_provided": info["user_id"] is not None,
        },
    )


async def _find_message_lookup_result(
    info: Dict[str, Any],
    original_message: types.Message,
    admin_group_ids: List[int],
) -> Optional[Dict[str, Any]]:
    forward_date = original_message.forward_date or original_message.date
    from_date = forward_date - timedelta(days=3)
    to_date = forward_date + timedelta(days=1)
    return await find_message_by_text_and_user(
        message_text=info["text"],
        admin_chat_ids=admin_group_ids,
        from_date=from_date,
        to_date=to_date,
        user_id=info["user_id"],
    )


def _merge_lookup_result_into_info(
    info: Dict[str, Any],
    lookup_result: Dict[str, Any],
) -> None:
    info["group_chat_id"] = lookup_result["chat_id"]
    info["group_message_id"] = lookup_result["message_id"]
    if not info["user_id"] and lookup_result.get("user_id"):
        info["user_id"] = lookup_result["user_id"]
        logger.info(
            f"Recovered user_id {info['user_id']} from message_lookup_cache",
            extra={"message_lookup": "user_recovered"},
        )
    info["reply_context"] = lookup_result.get("reply_to_text")
    info["stories_context"] = lookup_result.get("stories_context")
    info["account_signals_context"] = lookup_result.get("account_signals_context")


async def _backfill_account_signals_if_missing(
    info: Dict[str, Any],
    original_message: types.Message,
) -> None:
    if not info["user_id"]:
        return
    if info.get("stories_context") and info.get("account_signals_context"):
        return

    try:
        user_context = await collect_user_context(
            info["user_id"], username=info.get("username")
        )
        if info.get("account_signals_context"):
            return
        merge_ctx = SpamClassificationContext(
            profile_photo_age=user_context.profile_photo_age,
            is_premium=_premium_from_forward(original_message),
        )
        if body := build_account_signals_body(merge_ctx):
            info["account_signals_context"] = body
    except Exception as e:
        logger.debug(
            "On-demand context collection failed",
            extra={"user_id": info["user_id"], "error": str(e)},
        )
