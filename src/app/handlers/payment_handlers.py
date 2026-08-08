import logging

from aiogram import F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..common.bot import bot
from ..common.utils import retry_on_network_error
from ..database import get_admin, record_successful_payment
from ..i18n import resolve_lang, t
from .dp import dp

logger = logging.getLogger(__name__)


@dp.message(Command("buy"), F.chat.type == "private")
async def handle_buy_command(message: types.Message) -> str:
    """
    Обрабатывает команду покупки звезд
    Показывает меню с разными пакетами звезд
    """
    if not message.from_user:
        return "command_no_user_info"
    admin = await get_admin(message.from_user.id)
    lang = resolve_lang(message, admin)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(lang, "payment.stars_100"), callback_data="buy_stars:100"
                ),
                InlineKeyboardButton(
                    text=t(lang, "payment.stars_500"), callback_data="buy_stars:500"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t(lang, "payment.stars_1000"), callback_data="buy_stars:1000"
                ),
                InlineKeyboardButton(
                    text=t(lang, "payment.stars_5000"), callback_data="buy_stars:5000"
                ),
            ],
        ]
    )

    buy_text = (
        t(lang, "payment.choose_stars")
        + t(lang, "payment.buy_menu")
        + t(lang, "payment.max_protection")
        + t(lang, "payment.buy_tip")
        + t(lang, "payment.channel_link")
    )

    await message.reply(
        buy_text,
        reply_markup=keyboard,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    return "command_buy_menu_shown"


@dp.callback_query(F.data.startswith("buy_stars:"))
async def handle_buy_stars_callback(callback: types.CallbackQuery) -> str:
    """
    Обрабатывает выбор количества звезд для покупки
    """
    await callback.answer()

    admin = await get_admin(callback.from_user.id) if callback.from_user else None
    lang = resolve_lang(callback.from_user, admin)

    if not callback.data or ":" not in callback.data:
        if callback.message:
            await callback.message.reply(t(lang, "payment.invalid_data"))
        return "invalid_callback_data"

    stars_amount = int(callback.data.split(":")[1])

    if (
        not callback.message
        or not hasattr(callback.message, "chat")
        or not callback.message.chat
    ):
        if callback.message:
            await callback.message.reply(t(lang, "payment.send_failed"))
        return "invalid_message"

    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title=t(lang, "payment.invoice_title"),
        description=t(lang, "payment.invoice_description", amount=stars_amount),
        payload=f"Stars purchase:{stars_amount}",
        provider_token="",
        currency="XTR",
        prices=[
            types.LabeledPrice(
                label=t(lang, "payment.stars_label", amount=stars_amount),
                amount=stars_amount,
            )
        ],
    )
    return "callback_buy_stars_selected"


@dp.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: types.PreCheckoutQuery) -> str:
    """Обработчик предварительной проверки платежа"""
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)
    return "pre_checkout_processed"


@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message) -> str:
    """Обработчик успешного платежа"""
    if not message.from_user or not message.successful_payment:
        logger.warning(
            "Received successful payment message with missing user or payment data"
        )
        return "payment_processing_skipped"

    admin_id = message.from_user.id
    stars_amount = message.successful_payment.total_amount
    admin = await get_admin(admin_id)
    lang = resolve_lang(message, admin)

    try:
        await record_successful_payment(admin_id, stars_amount)

        success_text = t(lang, "payment.success_full", amount=stars_amount)

        @retry_on_network_error
        async def send_payment_confirmation():
            return await bot.send_message(
                admin_id,
                success_text,
                parse_mode="HTML",
            )

        await send_payment_confirmation()
        return "payment_successful_processed"

    except Exception:
        logger.exception("Error processing payment")
        raise
