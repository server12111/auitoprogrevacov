"""Раздел передачи каналов и история передач. Только для админов."""
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (BufferedInputFile, CallbackQuery,
                           InlineKeyboardButton, InlineKeyboardMarkup, Message)

from app import settings
from app.db import repo
from app.handlers.ui import esc, require_admin, show
from app.keyboards import back, confirm
from app.services import account_manager as am
from app.services import transfer as transfer_svc
from app.states import Transfer
from app.utils import fmt_dt

router = Router()

MAX_QTY = 500
_USERNAME_RE = re.compile(r"^@[A-Za-z0-9_]{4,32}$")

# Защита от повторного нажатия, пока идёт долгая операция:
# ключи "confirm:<user_id>" и "do:<transfer_id>"
_running: set[str] = set()

STALE = "Данные передачи устарели (бот перезапускался или кнопка уже нажата). Начни заново: «📤 Передать»."


async def _stopped(cq: CallbackQuery) -> bool:
    if settings.get("emergency_stop"):
        await cq.answer("Включён экстренный стоп — сначала «▶️ Запуск».", show_alert=True)
        return True
    return False


@router.callback_query(F.data == "tr:new")
async def tr_new(cq: CallbackQuery, state: FSMContext, role: str) -> None:
    if not await require_admin(cq, role):
        return
    await state.set_state(Transfer.input)
    await show(cq,
               "📤 <b>Передача каналов</b>\n\n"
               "Отправь одной строкой: <code>ДЕНЬ КОЛИЧЕСТВО @username</code>\n"
               "Например: <code>300 10 @buyer</code>\n\n"
               "Бот подберёт каналы этого дня с аккаунтов, готовых к передаче.",
               back("menu:main"))


@router.callback_query(F.data.startswith("tr:from:"))
async def tr_from(cq: CallbackQuery, state: FSMContext, role: str) -> None:
    if not await require_admin(cq, role):
        return
    day = int(cq.data.split(":")[2])
    await state.set_state(Transfer.input)
    await state.update_data(suggest_day=day)
    await show(cq,
               f"📤 <b>Передача (день {day})</b>\n\n"
               f"Отправь: <code>{day} КОЛИЧЕСТВО @username</code>\n"
               f"Например: <code>{day} 5 @buyer</code>",
               back("menu:main"))


@router.message(Transfer.input, F.text)
async def transfer_input(message: Message, state: FSMContext) -> None:
    await _parse_and_prepare(message, state)


async def _parse_and_prepare(message: Message, state: FSMContext) -> None:
    parts = message.text.strip().split()
    if len(parts) != 3:
        await message.answer("Формат: <code>ДЕНЬ КОЛИЧЕСТВО @username</code>")
        return
    try:
        day = int(parts[0])
        qty = int(parts[1])
    except ValueError:
        await message.answer("День и количество — числа. Пример: <code>300 10 @buyer</code>")
        return
    if day < 1 or not 1 <= qty <= MAX_QTY:
        await message.answer(f"День — от 1, количество — от 1 до {MAX_QTY}.")
        return
    recipient = parts[2]
    if not recipient.startswith("@"):
        recipient = "@" + recipient
    if not _USERNAME_RE.match(recipient):
        await message.answer("Неверный username получателя. Пример: <code>@buyer</code>")
        return

    eligible = await transfer_svc.find_eligible(day, ready_only=True)
    await state.update_data(day=day, qty=qty, recipient=recipient,
                            channel_ids=[c["id"] for c in eligible])

    if len(eligible) >= qty:
        chosen = eligible[:qty]
        await state.update_data(channel_ids=[c["id"] for c in chosen])
        await _show_confirm(message, day, len(chosen), recipient)
        return

    # Не хватает — предлагаем варианты
    have = len(eligible)
    text = (
        f"⚠️ Готовых к передаче каналов дня {day}: <b>{have}</b>, запрошено {qty}.\n\n"
        f"Что делать?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬆️ Добрать каналами старше",
                              callback_data="tr:older")],
        [InlineKeyboardButton(text=f"✅ Передать сколько есть ({have})",
                              callback_data="tr:asis")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="menu:main")],
    ])
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "tr:older")
async def tr_older(cq: CallbackQuery, state: FSMContext, role: str) -> None:
    if not await require_admin(cq, role):
        return
    data = await state.get_data()
    if "day" not in data:
        await show(cq, STALE, back("menu:main"))
        return
    day, qty, recipient = data["day"], data["qty"], data["recipient"]
    eligible = await transfer_svc.find_eligible(day, ready_only=True, min_day=day)
    chosen = eligible[:qty]
    await state.update_data(channel_ids=[c["id"] for c in chosen])
    await _show_confirm(cq, day, len(chosen), recipient)


@router.callback_query(F.data == "tr:asis")
async def tr_asis(cq: CallbackQuery, state: FSMContext, role: str) -> None:
    if not await require_admin(cq, role):
        return
    data = await state.get_data()
    if "day" not in data:
        await show(cq, STALE, back("menu:main"))
        return
    await _show_confirm(cq, data["day"], len(data["channel_ids"]), data["recipient"])


async def _show_confirm(event, day: int, count: int, recipient: str) -> None:
    text = (
        f"📤 <b>Подтверждение передачи</b>\n\n"
        f"День: <b>{day}</b>\n"
        f"Каналов: <b>{count}</b>\n"
        f"Получатель: <b>{esc(recipient)}</b>\n\n"
        f"Шаг 1 — каналы зарезервируются и бот выдаст инвайт-ссылки. "
        f"Затем получатель вступает, и ты жмёшь «Передать вступившие»."
    )
    kb = confirm("tr:confirm", "menu:main", yes_text="✅ Создать ссылки")
    await show(event, text, kb)


@router.callback_query(F.data == "tr:confirm")
async def tr_confirm(cq: CallbackQuery, state: FSMContext, role: str) -> None:
    if not await require_admin(cq, role) or await _stopped(cq):
        return
    key = f"confirm:{cq.from_user.id}"
    if key in _running:
        await cq.answer("Уже создаю ссылки, подожди…")
        return
    data = await state.get_data()
    if "day" not in data:
        await show(cq, STALE, back("menu:main"))
        return
    day, recipient = data["day"], data["recipient"]
    channel_ids = data["channel_ids"]
    await state.clear()
    if not channel_ids:
        await show(cq, "Нет подходящих каналов.", back("menu:main"))
        return

    _running.add(key)
    try:
        await cq.answer("Создаю ссылки…")
        # Между выбором и подтверждением каналы мог забрать другой админ — берём только свободные
        channels = [await repo.get_channel(cid) for cid in channel_ids]
        channels = [c for c in channels if c and c["status"] == "active"]
        if not channels:
            await cq.message.answer("Выбранные каналы уже заняты. Начни заново.",
                                    reply_markup=back("menu:main"))
            return

        tid = await repo.create_transfer(recipient, day, len(channels), cq.from_user.id)
        await repo.log_action(cq.from_user.id, "transfer_start",
                              f"day={day} qty={len(channels)} to={recipient}")
        try:
            results = await transfer_svc.reserve_and_link(am, tid, channels)
        except Exception as e:  # noqa: BLE001
            await cq.message.answer(f"❌ Ошибка при создании ссылок: {esc(e)}",
                                    reply_markup=back("menu:main"))
            return
    finally:
        _running.discard(key)

    ok = [r for r in results if r.get("link")]
    links_text = "\n".join(f"{i+1}. {esc(r['link'])}" for i, r in enumerate(ok))
    txt_bytes = "\n".join(r["link"] for r in ok).encode("utf-8")

    header = (
        f"🔗 <b>Ссылки готовы</b> (передача #{tid})\n"
        f"День {day} · получатель {esc(recipient)} · каналов {len(ok)}\n\n"
        f"Отправь ссылки получателю. После того как он вступит, жми «Проверить вступление»."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Проверить вступление", callback_data=f"tr:check:{tid}")],
        [InlineKeyboardButton(text="📤 Передать вступившие", callback_data=f"tr:do:{tid}")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:main")],
    ])
    await cq.message.answer(header + "\n\n" + (links_text or "—"))
    if txt_bytes:
        await cq.message.answer_document(
            BufferedInputFile(txt_bytes, f"transfer_{tid}_links.txt"),
            caption="Ссылки списком", reply_markup=kb,
        )
    else:
        await cq.message.answer("Ни одной ссылки создать не удалось.", reply_markup=kb)


@router.callback_query(F.data.startswith("tr:check:"))
async def tr_check(cq: CallbackQuery, role: str) -> None:
    if not await require_admin(cq, role):
        return
    tid = int(cq.data.split(":")[2])
    tr = await repo.get_transfer(tid)
    if not tr:
        await cq.answer("Передача не найдена", show_alert=True)
        return
    await cq.answer("Проверяю вступления…")
    joined, total = await transfer_svc.check_joins(am, tid, tr["recipient"])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Проверить снова", callback_data=f"tr:check:{tid}")],
        [InlineKeyboardButton(text="📤 Передать вступившие", callback_data=f"tr:do:{tid}")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:main")],
    ])
    await cq.message.answer(f"✅ Вступил в {joined} из {total} каналов.", reply_markup=kb)


@router.callback_query(F.data.startswith("tr:do:"))
async def tr_do(cq: CallbackQuery, role: str) -> None:
    if not await require_admin(cq, role) or await _stopped(cq):
        return
    tid = int(cq.data.split(":")[2])
    tr = await repo.get_transfer(tid)
    if not tr:
        await cq.answer("Передача не найдена", show_alert=True)
        return
    key = f"do:{tid}"
    if key in _running:
        await cq.answer("Эта передача уже выполняется, подожди…", show_alert=True)
        return
    _running.add(key)
    try:
        await cq.answer("Передаю владение… это займёт время")
        try:
            res = await transfer_svc.transfer_joined(am, tid, tr["recipient"])
        except Exception as e:  # noqa: BLE001
            await cq.message.answer(f"❌ Ошибка передачи: {esc(e)}")
            return
    finally:
        _running.discard(key)
    await repo.log_action(cq.from_user.id, "transfer_done",
                          f"tid={tid} ok={res['ok']} failed={res['failed']}")
    text = (
        f"📦 <b>Передача #{tid} завершена</b>\n\n"
        f"✅ Успешно: {res['ok']}\n"
        f"❌ Ошибок: {res['failed']}\n"
        f"Всего в передаче: {res['total']}\n\n"
        f"Не переданные каналы остаются в резерве — можно повторить."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔁 Повторить непереданные", callback_data=f"tr:do:{tid}")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:main")],
    ])
    await cq.message.answer(text, reply_markup=kb)


# ================= ИСТОРИЯ =================

@router.callback_query(F.data.startswith("tr:hist:"))
async def tr_hist(cq: CallbackQuery) -> None:
    transfers = await repo.list_transfers(20)
    if not transfers:
        await show(cq, "📦 <b>История передач</b>\n\nПока пусто.", back("menu:main"))
        return
    lines = ["📦 <b>История передач</b>\n"]
    for t in transfers:
        lines.append(
            f"#{t['id']} · {fmt_dt(t['created_at'])}\n"
            f"   {esc(t['recipient'])} · день {t['day_number']} · "
            f"{t['delivered_qty']}/{t['requested_qty']} · {t['status']}"
        )
    await show(cq, "\n".join(lines), back("menu:main"))
