"""Гостевые боты: предикат, общий для роутинга и валидации (#123).

Гостевой бот — это бот, которого позвал человек упоминанием: бот отвечает в чате
от своего имени, и Telegram доставляет этот ответ, потому что он
user-initiated. Трафик бот-в-бота не доставляется вовсе
(core.telegram.org/bots/faq, "regardless of mode"), поэтому сплошной пропуск
ботов из #122 выглядел безопасным — но до нас доходят именно вызванные боты,
и у них заполнен `guest_bot_caller_user`.

Модуль-лист: импортируется только `aiogram.types`, поэтому его можно
юнит-тестировать без BOT_TOKEN и импортировать из слоя роутинга, не затягивая
туда `message.pipeline` и БД.
"""

from aiogram import types


def guest_bot_caller(message: types.Message) -> types.User | None:
    """Человек, вызвавший бота, который отправил это сообщение, иначе None."""
    if message.from_user is None or not message.from_user.is_bot:
        return None
    return message.guest_bot_caller_user


def is_foreign_bot_message(message: types.Message) -> bool:
    """True, если сообщение от БОТА и его никто не вызывал — пропуск из #122."""
    if message.from_user is None or not message.from_user.is_bot:
        return False
    return message.guest_bot_caller_user is None
