import contextlib
import html
import logging
from datetime import UTC, datetime
from typing import cast

from aiogram import F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputRichMessage,
)

from ..common.bot import bot
from ..common.utils import (
    format_chat_or_channel_display,
    get_add_to_group_url,
    get_affiliate_url,
    get_setup_guide_url,
)
from ..database import (
    cycle_moderation_mode,
    get_admin,
    get_admin_credits,
    get_admin_groups,
    get_admin_stats,
    get_spent_credits_all_time,
    get_spent_credits_last_week,
    initialize_new_admin,
    update_admin_username_if_needed,
)
from ..database.models import ModerationMode
from ..i18n import normalize_lang, resolve_lang, t
from ..spam.linked_channel_mention import extract_first_channel_mention
from ..spam.user_profile import collect_channel_summary_by_id, collect_user_context
from ..types import ContextResult, ContextStatus
from .dp import dp

logger = logging.getLogger(__name__)


@dp.message(F.text.startswith("/"), F.chat.type != "private")
async def delete_and_redirect_to_pm(message: types.Message) -> str:
    """Удаляет команду в группе и отправляет сообщение о переходе в ЛС. Использует answer(), т.к. reply() падает после delete()."""
    with contextlib.suppress(Exception):
        await message.delete()
    lang = resolve_lang(message, None)

    # When /start has a param (e.g. from startgroup=landing deep link), show brief ready message
    text = message.text or ""
    if text.startswith("/start ") and len(text) > 7:
        param = text[7:].strip()
        if param in ("landing",) or param.startswith("setup_"):
            brief = t(lang, "group_redirect.start_param_brief")
            await message.answer(brief)
            return "command_group_start_param_brief"

    group_help_text = t(lang, "group_redirect.title") + t(
        lang, "group_redirect.body", add_to_group_url=get_add_to_group_url()
    )
    await message.answer(
        group_help_text,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    return "command_group_redirect_to_pm"


async def _try_send_linked_channel_offer(
    message: types.Message, user_id: int, username: str | None
) -> bool:
    """
    Collect linked channel (Bot API first, MTProto fallback) and send protect offer.
    Returns True if offer was sent, False otherwise.
    Never queries MTProto by ID—always uses username when available.
    """
    linked, display_chat = await _collect_linked_channel_for_offer(user_id, username)
    channel_id = _get_found_channel_id(linked)
    if channel_id is None:
        return False

    chat = await _resolve_offer_display_chat(
        user_id=user_id,
        channel_id=channel_id,
        display_chat=display_chat,
    )
    if chat is None:
        return False

    channel_display = format_chat_or_channel_display(
        chat.title, getattr(chat, "username", None), t("ru", "common.channel")
    )
    user_id = message.from_user.id if message.from_user else 0
    admin = await get_admin(user_id)
    lang = resolve_lang(message, admin)
    offer_text = t(lang, "start.linked_channel_offer", channel_display=channel_display)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(lang, "offer.protect_channel"),
                    url=get_setup_guide_url(),
                )
            ]
        ]
    )
    await message.answer(
        offer_text,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=keyboard,
    )
    logger.info(
        "Sent second /start message with linked channel offer",
        extra={"user_id": user_id, "offer_message": offer_text},
    )
    return True


def _get_found_channel_id(linked: ContextResult | None) -> int | None:
    """Return linked channel id only when context status is FOUND."""
    if linked is None or linked.status != ContextStatus.FOUND or linked.content is None:
        return None
    return linked.content.channel_id


async def _resolve_offer_display_chat(
    user_id: int,
    channel_id: int,
    display_chat: Chat | None,
) -> Chat | None:
    """Use cached Bot API chat when possible, fallback to channel id lookup."""
    if display_chat is not None:
        return display_chat

    # Linked from collect_user_context (MTProto); bot.get_chat by ID may fail.
    try:
        return await bot.get_chat(channel_id)
    except TelegramBadRequest as e:
        logger.warning(
            "Linked channel not accessible for offer display: %s",
            e,
            extra={"user_id": user_id, "channel_id": channel_id},
        )
        return None


async def _collect_linked_channel_for_offer(
    user_id: int,
    username: str | None,
) -> tuple[ContextResult, Chat | None]:
    """Collect linked channel with Bot API first and MTProto fallback."""
    linked = ContextResult(status=ContextStatus.EMPTY)
    display_chat: Chat | None = None

    try:
        chat_full = await bot.get_chat(user_id)
        linked, display_chat = await _collect_linked_channel_via_bot_api(
            chat_full=chat_full,
            user_id=user_id,
            linked=linked,
            display_chat=display_chat,
        )

        if linked.status != ContextStatus.FOUND and username:
            user_context = await collect_user_context(user_id, username=username)
            linked = user_context.linked_channel or linked
    except Exception as e:
        logger.warning(
            "Failed to collect context via Bot API for /start offer: %s",
            e,
            exc_info=True,
        )

    return linked, display_chat


async def _collect_linked_channel_via_bot_api(
    chat_full: Chat,
    user_id: int,
    linked: ContextResult,
    display_chat: Chat | None,
) -> tuple[ContextResult, Chat | None]:
    """Try personal_chat and bio mentions for linked channel discovery."""
    if chat_full.personal_chat and chat_full.personal_chat.type == "channel":
        personal_chat = chat_full.personal_chat
        personal_username = getattr(personal_chat, "username", None)
        linked = await collect_channel_summary_by_id(
            personal_chat.id,
            user_id,
            username=personal_username,
            channel_source="linked",
        )
        if linked.status == ContextStatus.FOUND:
            return linked, personal_chat

    if linked.status == ContextStatus.FOUND or not chat_full.bio:
        return linked, display_chat

    candidate_username = extract_first_channel_mention(chat_full.bio)
    if not candidate_username:
        return linked, display_chat

    with contextlib.suppress(Exception):
        channel_chat = await bot.get_chat(f"@{candidate_username}")
        if channel_chat.type in ("channel", "supergroup"):
            linked = await collect_channel_summary_by_id(
                channel_chat.id,
                user_id,
                username=candidate_username,
                channel_source="bio",
            )
            if linked.status == ContextStatus.FOUND:
                return linked, channel_chat

    return linked, display_chat


@dp.message(Command("start", "help"))
async def handle_help_command(message: types.Message) -> str:
    """
    Обработчик команд /start и /help
    Отправляет пользователю справочную информацию и начисляет начальные звезды новым пользователям
    """
    if not message.from_user:
        return "command_no_user_info"

    if not message.text:
        return "command_no_text"

    user = cast("types.User", message.from_user)  # Cast to ensure proper type hints
    user_id = user.id

    command = message.text.split()[0]
    admin = await get_admin(user_id)
    lang = resolve_lang(message, admin)

    if command == "/start":
        lang_for_new = normalize_lang(getattr(user, "language_code", None))
        is_new = await initialize_new_admin(user_id, language_code=lang_for_new)
        await update_admin_username_if_needed(user_id, user.username)
        if is_new:
            welcome_text = t(lang, "start.welcome")
            await message.reply(
                welcome_text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            logger.info(
                "Sent /start welcome to new user",
                extra={"user_id": user_id, "welcome_message": welcome_text},
            )
            await _try_send_linked_channel_offer(message, user_id, user.username)
            return "command_start_new_user_sent"
        # Для существующих пользователей покажем приветствие с быстрым доступом к функциям
        existing_user_text = t(lang, "start.existing_user")
        await message.reply(
            existing_user_text,
            parse_mode="HTML",
        )
        return "command_start_existing_user"

    safe_text = t(lang, "help.main")
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(lang, "help.buttons.getting_started"),
                    callback_data="help.getting_started",
                ),
                InlineKeyboardButton(
                    text=t(lang, "help.buttons.training"),
                    callback_data="help.training",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t(lang, "help.buttons.moderation"),
                    callback_data="help.moderation",
                ),
                InlineKeyboardButton(
                    text=t(lang, "help.buttons.commands"),
                    callback_data="help.commands",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t(lang, "help.buttons.payment"),
                    callback_data="help.payment",
                ),
                InlineKeyboardButton(
                    text=t(lang, "help.buttons.support"),
                    callback_data="help.support",
                ),
            ],
        ]
    )

    await message.reply(
        safe_text,
        parse_mode="HTML",
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )

    return "command_help_sent"


def _format_topic_age(topic_updated_at, lang: str) -> str | None:
    """Humanized, localized topic age for /scan and /stats lines.

    Returns None when the age is unknown; otherwise a localized phrase like
    "today" / "3d ago" (en) or "сегодня" / "3 дн. назад" (ru).
    """
    if not topic_updated_at:
        return None
    from datetime import datetime

    if isinstance(topic_updated_at, str):
        try:
            topic_updated_at = datetime.fromisoformat(topic_updated_at)
        except ValueError:
            return None
    if topic_updated_at.tzinfo is None:
        topic_updated_at = topic_updated_at.replace(tzinfo=UTC)
    days = (datetime.now(UTC) - topic_updated_at).days
    if days <= 0:
        return t(lang, "topic_age.today")
    return t(lang, "topic_age.days", days=days)


def _md_escape(text: str | None) -> str:
    """Escape a string for a Rich-Markdown (GFM) table cell.

    Pipe, backslash, backticks and emphasis markers are special in GFM cells;
    escape them so group titles and topic descriptions arrive verbatim.
    Newlines are collapsed to spaces (a cell must stay on one line).
    """
    if not text:
        return ""
    out = text.replace("\\", "\\\\").replace("|", "\\|")
    for ch in "`*_[]~":
        out = out.replace(ch, "\\" + ch)
    return " ".join(out.split())


@dp.message(Command("scan"), F.chat.type == "private")
async def handle_scan_command(message: types.Message) -> str:
    """
    Обработчик команды /scan
    Запускает сканирование тематики чата и её анализ для улучшения фильтрации спама.
    """
    if not message.from_user:
        return "command_no_user_info"

    user = cast("types.User", message.from_user)
    user_id = user.id
    admin = await get_admin(user_id)
    lang = resolve_lang(message, admin)

    groups = await get_admin_groups(user_id)
    if not groups:
        await message.answer(t(lang, "scan.no_groups"), parse_mode="HTML")
        return "command_scan_no_groups"

    if len(groups) == 1:
        group = groups[0]
        await message.answer(
            t(
                lang,
                "scan.starting",
                title=html.escape(group["title"] or "", quote=True),
            ),
            parse_mode="HTML",
        )
        await _run_scan_and_report(message, group, lang)
        return "command_scan_sent"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=html.escape(
                        group["title"] or f"Group {group['id']}", quote=True
                    ),
                    callback_data=f"scan_chat:{group['id']}",
                )
            ]
            for group in groups
        ]
    )
    await message.answer(
        t(lang, "scan.select_chat"),
        reply_markup=keyboard,
    )
    return "command_scan_sent"


async def _run_scan_and_report(message: types.Message, group: dict, lang: str) -> None:
    """Run scan_chat_topics for one group and reply with the outcome."""
    from ..spam.chat_topics import scan_chat_topics

    try:
        result = await scan_chat_topics(int(group["id"]))
    except Exception as e:
        logger.warning(
            f"scan_chat_topics raised for group {group['id']}: {e}", exc_info=True
        )
        await message.answer(t(lang, "scan.error"), parse_mode="HTML")
        return

    title = html.escape(group["title"] or "", quote=True)
    if result.status == "ok":
        # The scan just wrote topic_updated_at = NOW(), so the age is always
        # "today" — the pre-scan group dict would show a stale value instead.
        age = _format_topic_age(datetime.now(UTC), lang)
        age_note = f" ({age})" if age else ""
        await message.answer(
            t(
                lang,
                "scan.success",
                title=title,
                short=html.escape(result.detail, quote=True),
                age=age_note,
            ),
            parse_mode="HTML",
        )
    elif result.status in ("title_fallback", "empty_first_scan"):
        await message.answer(
            t(
                lang,
                "scan.title_fallback",
                title=title,
                short=html.escape(result.detail, quote=True),
            ),
            parse_mode="HTML",
        )
    elif result.status in ("kept_existing", "empty_kept_existing"):
        await message.answer(
            t(
                lang,
                "scan.kept_existing",
                title=title,
            ),
            parse_mode="HTML",
        )
    else:
        await message.answer(
            t(lang, "scan.failed", title=title, reason=result.detail),
            parse_mode="HTML",
        )


@dp.message(Command("stats"), F.chat.type == "private")
async def handle_stats_command(message: types.Message) -> str:
    """
    Обработчик команды /stats
    Показывает баланс пользователя, глобальную статистику и статус модерации в его группах
    """
    if not message.from_user:
        return "command_no_user_info"

    user = cast("types.User", message.from_user)  # Cast to ensure proper type hints
    user_id = user.id
    admin = await get_admin(user_id)
    lang = resolve_lang(message, admin)

    try:
        balance = await get_admin_credits(user_id)
        spent_week = await get_spent_credits_last_week(user_id)
        spent_all = await get_spent_credits_all_time(user_id)
        admin_stats = await get_admin_stats(user_id)
        global_stats = admin_stats["global"]
        groups = admin_stats["groups"]

        # --- Rich-Markdown (GFM) rendering — native Telegram Rich Message ---

        # h1 heading: the report title (balance). Locale strings mix plain text
        # and <b> tags, which the Rich-Markdown parser (GFM + inline HTML)
        # understands too.
        md_balance = "# " + t(lang, "stats.balance", balance=balance)

        # 3x6 period table: last-7-days vs all-time across the tracked metrics.
        # "—" marks metrics that are not tracked for that period.
        period_headers = (
            t(lang, "stats.table_period"),
            t(lang, "stats.table_spent"),
            t(lang, "stats.table_checked"),
            t(lang, "stats.table_spam"),
            t(lang, "stats.table_approved"),
            t(lang, "stats.table_training"),
        )
        md_rows = ["| " + " | ".join(period_headers) + " |"]
        md_rows.append("|" + "---|" * len(period_headers))
        md_rows.append(
            "| "
            + " | ".join(
                [
                    _md_escape(t(lang, "stats.period_week")),
                    str(spent_week),
                    str(global_stats["processed"]),
                    str(global_stats["spam"]),
                    str(global_stats["approved_7d"]),
                    str(global_stats["spam_examples_7d"]),
                ]
            )
            + " |"
        )
        md_rows.append(
            "| "
            + " | ".join(
                [
                    _md_escape(t(lang, "stats.period_all")),
                    str(spent_all),
                    "—",
                    "—",
                    str(global_stats["approved"]),
                    str(global_stats["spam_examples"]),
                ]
            )
            + " |"
        )

        if groups:
            # Per-group table: group name (topic as a subscript line inside the
            # first cell) + per-period metrics.
            group_headers = (
                t(lang, "stats.table_group"),
                t(lang, "stats.table_checked_7d"),
                t(lang, "stats.table_spam_7d"),
                t(lang, "stats.table_approved_all"),
            )
            group_rows = ["| " + " | ".join(group_headers) + " |"]
            group_rows.append("|" + "---|" * len(group_headers))

            html_blocks: list[str] = []
            for group in groups:
                status_emoji = "✅" if group["is_moderation_enabled"] else "❌"
                g_stats = group["stats"]
                approved_users = group["approved_users_count"]
                safe_title = html.escape(group["title"] or "", quote=True)
                topic_short = group.get("topic_description_short")

                # Rich table row: name + subscript topic on its own line.
                md_title = f"{status_emoji} {_md_escape(group['title'])}"
                if topic_short:
                    md_title += f"<br><sub>{_md_escape(topic_short)}</sub>"
                group_rows.append(
                    "| "
                    + " | ".join(
                        [
                            md_title,
                            str(g_stats["processed"]),
                            str(g_stats["spam"]),
                            str(approved_users),
                        ]
                    )
                    + " |"
                )

                # HTML fallback card: same data as a readable text block.
                card = f"{status_emoji} <b>{safe_title}</b>"
                if topic_short:
                    card += f"\n   📋 {html.escape(topic_short, quote=True)}"
                card += (
                    f"\n   📨 {g_stats['processed']} · "
                    f"🗑 {g_stats['spam']} · "
                    f"👤 {approved_users}"
                )
                html_blocks.append(card)

            md_groups = "## " + t(lang, "stats.by_groups") + "\n\n" + "\n".join(group_rows)
            html_groups = (
                "<b>" + t(lang, "stats.by_groups") + "</b>\n\n"
                + "\n\n".join(html_blocks)
            )
        else:
            no_groups = t(lang, "stats.no_groups")
            md_groups = no_groups
            html_groups = no_groups

        # Subscript legend below everything (renders as small text).
        legend = t(lang, "stats.legend")

        md_text = (
            md_balance
            + "\n\n"
            + "## " + t(lang, "stats.summary")
            + "\n\n"
            + "\n".join(md_rows)
            + "\n\n"
            + md_groups
            + "\n\n"
            + "<sub>" + _md_escape(legend) + "</sub>"
        )

        spent_line_week = (
            f"{t(lang, 'stats.period_week')}: ⭐ {spent_week} · "
            f"📨 {global_stats['processed']} · 🗑 {global_stats['spam']} · "
            f"👤 {global_stats['approved_7d']} · 📝 {global_stats['spam_examples_7d']}"
        )
        spent_line_all = (
            f"{t(lang, 'stats.period_all')}: ⭐ {spent_all} · "
            f"👤 {global_stats['approved']} · 📝 {global_stats['spam_examples']}"
        )
        html_text = (
            t(lang, "stats.balance", balance=balance)
            + "\n\n"
            + "<b>" + t(lang, "stats.summary") + "</b>\n"
            + spent_line_week + "\n"
            + spent_line_all + "\n\n"
            + html_groups + "\n\n"
            + "<i>" + html.escape(legend, quote=True) + "</i>"
        )

        bot = message.bot
        if bot is None:
            await message.reply(html_text, parse_mode="HTML")
            return "command_stats_sent"

        try:
            # Native Rich Message needs a Bot API server with Rich Messages
            # support (Bot API 10.1+); fall back to classic HTML if the API
            # refuses.
            await bot.send_rich_message(
                chat_id=message.chat.id,
                rich_message=InputRichMessage(markdown=md_text),
            )
            return "command_stats_sent"
        except Exception as e:  # noqa: BLE001
            logger.info("Rich message rejected, falling back to HTML: %s", e)
            await message.reply(html_text, parse_mode="HTML")
            return "command_stats_sent"

    except Exception as e:
        logger.info(f"Error handling stats command: {e}", exc_info=True)
        await message.reply(t(lang, "stats.error"), parse_mode="HTML")
        return "command_stats_error"


@dp.message(Command("mode"), F.chat.type == "private")
async def handle_mode_command(message: types.Message) -> str:
    """
    Обработчик команды /mode
    Переключает режим между удалением спама и уведомлениями
    """
    if not message.from_user:
        return "command_no_user_info"

    user = cast("types.User", message.from_user)  # Cast to ensure proper type hints
    user_id = user.id
    admin = await get_admin(user_id)
    lang = resolve_lang(message, admin)

    try:
        new_mode = await cycle_moderation_mode(user_id)

        if new_mode is None:
            await message.reply(t(lang, "mode.error"), parse_mode="HTML")
            return "command_mode_error"

        mode_messages = {
            ModerationMode.NOTIFY: (
                "mode.notify_enabled",
                "command_mode_changed_to_notification",
            ),
            ModerationMode.DELETE: (
                "mode.delete_enabled",
                "command_mode_changed_to_deletion",
            ),
            ModerationMode.DELETE_SILENT: (
                "mode.delete_silent_enabled",
                "command_mode_changed_to_delete_silent",
            ),
        }
        message_key, return_value = mode_messages[new_mode]
        await message.reply(t(lang, message_key), parse_mode="HTML")
        return return_value

    except Exception:
        logger.exception("Error handling mode command")
        await message.reply(t(lang, "mode.error"), parse_mode="HTML")
        return "command_mode_error"


@dp.message(Command("ref"), F.chat.type == "private")
async def cmd_ref(message: types.Message) -> str:
    """Объясняет, как получить официальную реферальную ссылку Telegram Partner Program"""
    if not message.from_user:
        return "command_no_user_info"
    admin = await get_admin(message.from_user.id)
    lang = resolve_lang(message, admin)
    text = (
        t(lang, "ref.title")
        + t(lang, "ref.steps")
        + f"<i>Подробнее: {get_affiliate_url()}</i>"
    )
    await message.answer(text, parse_mode="HTML")
    return "command_ref_sent"


@dp.message(Command("lang"), F.chat.type == "private")
async def cmd_lang(message: types.Message) -> str:
    """Change bot language. Private chat only."""
    if not message.from_user:
        return "command_no_user_info"
    admin = await get_admin(message.from_user.id)
    lang = resolve_lang(message, admin)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Русский", callback_data="lang_set:ru"),
                InlineKeyboardButton(text="English", callback_data="lang_set:en"),
            ]
        ]
    )
    await message.answer(
        t(lang, "lang.select"),
        reply_markup=keyboard,
    )
    return "command_lang_sent"
