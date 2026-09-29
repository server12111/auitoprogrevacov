"""Админ-панель: управление доступом и роли."""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

from app import config
from app.db import database as db
from app.db import repo
from app.handlers.ui import esc, show
from app.keyboards import back, confirm
from app.middlewares import is_admin
from app.states import GrantAccess
from app.utils import fmt_dt

router = Router()

ROLE_EMOJI = {"owner": "👑", "admin": "🛡", "user": "👤"}


def _guard(role: str) -> bool:
    return is_admin(role)


@router.callback_query(F.data == "adm:menu")
async def adm_menu(cq: CallbackQuery, role: str) -> None:
    if not _guard(role):
        await cq.answer("Только для админов", show_alert=True)
        return
    await _render_menu(cq)


async def _render_menu(cq: CallbackQuery, toast: str | None = None) -> None:
    users = await repo.list_users()
    lines = ["🛡 <b>Админ-панель</b>\n", f"Пользователей с доступом: {len(users)}\n"]
    for u in users:
        lines.append(f"{ROLE_EMOJI.get(u['role'],'•')} <code>{u['user_id']}</code> "
                     f"@{esc(u['username'] or '—')} · {u['role']}")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Выдать доступ", callback_data="adm:grant"),
         InlineKeyboardButton(text="👥 Управление", callback_data="adm:users:0")],
        [InlineKeyboardButton(text="📜 Журнал действий", callback_data="adm:log")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")],
    ])
    await show(cq, "\n".join(lines), kb, toast)


@router.callback_query(F.data.startswith("adm:users:"))
async def adm_users(cq: CallbackQuery, role: str) -> None:
    if not _guard(role):
        await cq.answer("Только для админов", show_alert=True)
        return
    users = await repo.list_users()
    rows = []
    for u in users:
        if u["user_id"] == config.OWNER_ID:
            continue
        rows.append([InlineKeyboardButton(
            text=f"{ROLE_EMOJI.get(u['role'],'•')} {u['user_id']} ({u['role']})",
            callback_data=f"adm:u:{u['user_id']}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:menu")])
    await show(cq, "👥 Выбери пользователя:", InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("adm:u:"))
async def adm_user(cq: CallbackQuery, role: str) -> None:
    if not _guard(role):
        await cq.answer("Только для админов", show_alert=True)
        return
    await _render_user(cq, int(cq.data.split(":")[2]))


async def _render_user(cq: CallbackQuery, uid: int, toast: str | None = None) -> None:
    u = await repo.get_user(uid)
    if not u:
        await show(cq, "Пользователь не найден.", back("adm:menu"), toast)
        return
    text = (f"{ROLE_EMOJI.get(u['role'],'•')} <code>{uid}</code>\n"
            f"@{esc(u['username'] or '—')}\nРоль: <b>{u['role']}</b>\n"
            f"Добавлен: {fmt_dt(u['added_at'])}")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛡 Сделать админом", callback_data=f"adm:role:{uid}:admin"),
         InlineKeyboardButton(text="👤 Сделать user", callback_data=f"adm:role:{uid}:user")],
        [InlineKeyboardButton(text="🗑 Забрать доступ", callback_data=f"adm:revoke:{uid}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:users:0")],
    ])
    await show(cq, text, kb, toast)


@router.callback_query(F.data.startswith("adm:role:"))
async def adm_role(cq: CallbackQuery, role: str) -> None:
    if role != "owner":
        await cq.answer("Менять роли может только владелец", show_alert=True)
        return
    _, _, uid_s, new_role = cq.data.split(":")
    uid = int(uid_s)
    if new_role not in ("admin", "user") or uid == config.OWNER_ID:
        await cq.answer("Недопустимо", show_alert=True)
        return
    await repo.set_role(uid, new_role)
    await repo.log_action(cq.from_user.id, "set_role", f"{uid}->{new_role}")
    await _render_user(cq, uid, toast=f"Роль изменена на {new_role}")


async def _can_revoke(cq: CallbackQuery, role: str, uid: int) -> bool:
    """Админ может забрать доступ только у обычного пользователя; владелец — у всех, кроме себя."""
    if not _guard(role):
        await cq.answer("Только для админов", show_alert=True)
        return False
    if uid == config.OWNER_ID:
        await cq.answer("Владельца удалить нельзя", show_alert=True)
        return False
    if role != "owner" and await repo.get_role(uid) in ("owner", "admin"):
        await cq.answer("Забрать доступ у админа может только владелец", show_alert=True)
        return False
    return True


# Подтверждение регистрируем раньше, чем "adm:revoke:" — иначе тот перехватит "adm:revoke:yes:…"
@router.callback_query(F.data.startswith("adm:revoke:yes:"))
async def adm_revoke_yes(cq: CallbackQuery, role: str) -> None:
    uid = int(cq.data.split(":")[3])
    if not await _can_revoke(cq, role, uid):
        return
    await repo.remove_user(uid)
    await repo.log_action(cq.from_user.id, "revoke", str(uid))
    await _render_menu(cq, toast="Доступ забран")


@router.callback_query(F.data.startswith("adm:revoke:"))
async def adm_revoke(cq: CallbackQuery, role: str) -> None:
    uid = int(cq.data.split(":")[2])
    if not await _can_revoke(cq, role, uid):
        return
    await show(cq, f"Забрать доступ у <code>{uid}</code>?",
               confirm(f"adm:revoke:yes:{uid}", "adm:menu"))


# ---------- Выдача доступа ----------

@router.callback_query(F.data == "adm:grant")
async def adm_grant(cq: CallbackQuery, state: FSMContext, role: str) -> None:
    if not _guard(role):
        await cq.answer("Только для админов", show_alert=True)
        return
    await state.set_state(GrantAccess.user)
    await show(cq,
               "➕ <b>Выдать доступ</b>\n\n"
               "Отправь ID пользователя числом, или перешли сюда любое его сообщение.",
               back("adm:menu"))


@router.message(GrantAccess.user)
async def grant_receive(message: Message, state: FSMContext) -> None:
    await state.clear()
    uid = None
    username = None
    # С Bot API 7.0 автор пересланного сообщения лежит в forward_origin
    # (поле forward_from Telegram больше не присылает)
    origin = message.forward_origin
    if origin is not None:
        if origin.type == "user":
            uid = origin.sender_user.id
            username = origin.sender_user.username
        else:
            await message.answer(
                "У пользователя скрыт профиль при пересылке — пришли его числовой ID.",
                reply_markup=back("adm:menu"))
            return
    else:
        try:
            uid = int((message.text or "").strip())
        except ValueError:
            await message.answer(
                "Не понял. Нужен числовой ID или пересланное сообщение "
                "(если у пользователя не скрыт профиль).",
                reply_markup=back("adm:menu"))
            return
    # не понижаем существующих админов/владельца до user
    if uid == config.OWNER_ID or await repo.get_role(uid) in ("owner", "admin"):
        await message.answer("У этого пользователя уже есть доступ.", reply_markup=back("adm:menu"))
        return
    await repo.add_user(uid, "user", username, message.from_user.id)
    await repo.log_action(message.from_user.id, "grant", str(uid))
    await message.answer(f"✅ Доступ выдан пользователю <code>{uid}</code> (роль user).",
                         reply_markup=back("adm:menu"))


@router.callback_query(F.data == "adm:log")
async def adm_log(cq: CallbackQuery, role: str) -> None:
    if not _guard(role):
        await cq.answer("Только для админов", show_alert=True)
        return
    rows = await db.fetchall(
        "SELECT * FROM admin_log ORDER BY created_at DESC LIMIT 20")
    if not rows:
        await show(cq, "📜 Журнал пуст.", back("adm:menu"))
        return
    lines = ["📜 <b>Журнал действий</b>\n"]
    for r in rows:
        lines.append(f"{fmt_dt(r['created_at'])} · <code>{r['admin_id']}</code>\n"
                     f"   {esc(r['action'])} {esc(r['details'])}")
    await show(cq, "\n".join(lines), back("adm:menu"))
