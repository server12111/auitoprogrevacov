"""Мидлварь доступа: пускает только пользователей из таблицы users (или владельца)."""
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app import config
from app.db import repo


class AccessMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        uid = user.id
        # Владелец всегда есть; гарантируем запись
        if uid == config.OWNER_ID:
            if not await repo.get_user(uid):
                await repo.add_user(uid, "owner", user.username, None)
            data["role"] = "owner"
            return await handler(event, data)

        role = await repo.get_role(uid)
        if role is None:
            deny = (
                "⛔ <b>Нет доступа</b>\n\n"
                f"Твой ID: <code>{uid}</code>\n"
                "Передай его владельцу бота, чтобы получить доступ."
            )
            if isinstance(event, Message):
                await event.answer(deny)
            elif isinstance(event, CallbackQuery):
                await event.answer("Нет доступа", show_alert=True)
            return None

        data["role"] = role
        return await handler(event, data)


def is_admin(role: str | None) -> bool:
    return role in ("owner", "admin")
