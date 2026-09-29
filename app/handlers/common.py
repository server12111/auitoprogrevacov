"""Стартовый экран, главное меню, системные кнопки."""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import settings
from app.db import repo
from app.handlers.ui import require_admin, show
from app.keyboards import back, confirm, main_menu
from app.middlewares import is_admin
from app.services import account_manager as am
from app.services import scheduler

router = Router()

WELCOME = (
    "🤖 <b>Панель управления каналами</b>\n\n"
    "Всё управление — кнопками ниже. Выбирай раздел."
)


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, role: str) -> None:
    await state.clear()
    am.cancel_login(message.from_user.id)  # незавершённый вход аккаунта — закрываем
    await message.answer(WELCOME, reply_markup=main_menu(is_admin(role)))


@router.callback_query(F.data == "menu:main")
async def menu_main(cq: CallbackQuery, state: FSMContext, role: str) -> None:
    await state.clear()
    am.cancel_login(cq.from_user.id)
    await show(cq, WELCOME, main_menu(is_admin(role)))


@router.callback_query(F.data == "noop")
async def noop(cq: CallbackQuery) -> None:
    await cq.answer()


@router.callback_query(F.data == "sys:stop")
async def sys_stop(cq: CallbackQuery, role: str) -> None:
    if not await require_admin(cq, role):
        return
    await show(cq,
               "🛑 <b>Экстренный стоп</b>\n\nОстановить все действия аккаунтов "
               "(создание, постинг, передачи)?",
               confirm("sys:stop:yes"))


@router.callback_query(F.data == "sys:stop:yes")
async def sys_stop_yes(cq: CallbackQuery, role: str) -> None:
    if not await require_admin(cq, role):
        return
    await settings.set("emergency_stop", True)
    await repo.log_action(cq.from_user.id, "emergency_stop")
    await show(cq, "🛑 Все действия остановлены. Нажми «▶️ Запуск», чтобы возобновить.",
               back())


@router.callback_query(F.data == "sys:start")
async def sys_start(cq: CallbackQuery, role: str) -> None:
    if not await require_admin(cq, role):
        return
    await settings.set("emergency_stop", False)
    await repo.log_action(cq.from_user.id, "emergency_start")
    # Если стоп был включён в момент ночного планирования, план на сегодня не создался —
    # досоздаём (plan_daily не создаёт дублей).
    await scheduler.plan_daily()
    await show(cq, "▶️ Работа возобновлена.", back())
