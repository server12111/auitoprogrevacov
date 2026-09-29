"""Отправка уведомлений владельцу/админам из любого места без циклических импортов."""
from aiogram import Bot

from app import config
from app.logger import log

_bot: Bot | None = None


def set_bot(bot: Bot) -> None:
    global _bot
    _bot = bot


async def notify_owner(text: str) -> None:
    if _bot is None:
        log.warning("notify_owner до инициализации бота: %s", text)
        return
    try:
        await _bot.send_message(config.OWNER_ID, text)
    except Exception as e:  # noqa: BLE001
        log.error("Не удалось уведомить владельца: %s", e)
