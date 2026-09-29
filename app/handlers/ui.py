"""Хелперы для единообразного показа экранов (edit / answer) и проверки прав."""
from html import escape

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.middlewares import is_admin


def esc(value) -> str:
    """Экранировать произвольное значение для вставки в HTML-сообщение."""
    return escape(str(value), quote=False)


async def show(event: Message | CallbackQuery, text: str,
               kb: InlineKeyboardMarkup | None = None, toast: str | None = None) -> None:
    """Показать экран: редактируем сообщение (callback) или отправляем новое (message).

    toast — короткое всплывающее уведомление для callback (на callback можно ответить только один раз).
    """
    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=kb)
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                await event.message.answer(text, reply_markup=kb)
        try:
            await event.answer(toast)
        except TelegramBadRequest:
            pass  # уже отвечен или устарел
    else:
        await event.answer(text, reply_markup=kb)


async def require_admin(cq: CallbackQuery, role: str | None) -> bool:
    """True, если роль админская; иначе показывает алерт и возвращает False."""
    if is_admin(role):
        return True
    await cq.answer("Только для админов", show_alert=True)
    return False
