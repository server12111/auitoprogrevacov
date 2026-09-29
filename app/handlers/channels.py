"""Разделы: Каналы, Дни, Поиск по дню, экспорт CSV."""
import csv
import io

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (BufferedInputFile, CallbackQuery,
                           InlineKeyboardButton, InlineKeyboardMarkup, Message)

from app.db import repo
from app.handlers.ui import esc, show
from app.keyboards import back, paginate
from app.services import stats
from app.states import Search
from app.utils import day_number, fmt_dt

router = Router()
PER_PAGE = 8

CH_STATUS = {"active": "✅", "reserved": "🔒", "transferred": "📤", "dead": "❌"}


# ================= КАНАЛЫ =================

@router.callback_query(F.data.startswith("ch:list:"))
async def ch_list(cq: CallbackQuery) -> None:
    page = int(cq.data.split(":")[2])
    channels = await repo.active_channels()
    all_ch = channels
    if not all_ch:
        await show(cq, "📢 <b>Каналы</b>\n\nПока нет каналов.", back("menu:main"))
        return

    def label(ch):
        return f"{CH_STATUS.get(ch['status'],'•')} День {day_number(ch['created_at'])}"

    top = [InlineKeyboardButton(text="📄 Экспорт CSV", callback_data="ch:export")]
    kb = paginate(all_ch, page, PER_PAGE, "ch", label, lambda c: c["id"],
                  back_target="menu:main", columns=2, extra_top=top)
    await show(cq, f"📢 <b>Каналы</b> (активных {len(all_ch)}):", kb)


@router.callback_query(F.data.startswith("ch:view:"))
async def ch_view(cq: CallbackQuery) -> None:
    ch_id = int(cq.data.split(":")[2])
    ch = await repo.get_channel(ch_id)
    if not ch:
        await show(cq, "Канал не найден.", back("ch:list:0"))
        return
    acc = await repo.get_account(ch["account_id"])
    lines = [
        f"{CH_STATUS.get(ch['status'],'•')} <b>{esc(ch['title'] or 'Без названия')}</b>",
        f"День: <b>{day_number(ch['created_at'])}</b>",
        f"Статус: {ch['status']}",
        f"Создан: {fmt_dt(ch['created_at'])}",
        f"Аккаунт: {esc(acc['phone']) if acc else '—'}",
        f"Последний пост: {ch['last_post_date'] or '—'}",
    ]
    if ch["invite_link"]:
        lines.append(f"Ссылка: {esc(ch['invite_link'])}")
    await show(cq, "\n".join(lines), back("ch:list:0"))


@router.callback_query(F.data == "ch:export")
async def ch_export(cq: CallbackQuery) -> None:
    await cq.answer("Готовлю файл…")
    channels = await repo.active_channels()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "title", "day", "status", "created", "account_id", "invite_link"])
    for ch in channels:
        w.writerow([ch["id"], ch["title"], day_number(ch["created_at"]),
                    ch["status"], fmt_dt(ch["created_at"]), ch["account_id"],
                    ch["invite_link"] or ""])
    data = buf.getvalue().encode("utf-8-sig")
    await cq.message.answer_document(
        BufferedInputFile(data, "channels.csv"),
        caption="📄 Список каналов",
        reply_markup=back("ch:list:0"),
    )


# ================= ДНИ =================

@router.callback_query(F.data == "days:menu")
async def days_menu(cq: CallbackQuery) -> None:
    dist = await stats.day_distribution()
    groups = [(1, 30), (31, 90), (91, 180), (181, 365), (366, 10**9)]
    labels = ["1–30", "31–90", "91–180", "181–365", "365+"]
    lines = ["📅 <b>Распределение каналов по дням</b>\n"]
    for (lo, hi), lab in zip(groups, labels):
        total = sum(v["total"] for d, v in dist.items() if lo <= d <= hi)
        ready = sum(v["ready"] for d, v in dist.items() if lo <= d <= hi)
        lines.append(f"• Дни {lab}: <b>{total}</b> (🔓 к передаче: {ready})")

    total_all = sum(v["total"] for v in dist.values())
    ready_all = sum(v["ready"] for v in dist.values())
    lines.append(f"\nВсего активных: <b>{total_all}</b> · готовых к передаче: <b>{ready_all}</b>")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Поиск по дню", callback_data="days:search")],
        [InlineKeyboardButton(text="📋 Список дней", callback_data="days:list")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")],
    ])
    await show(cq, "\n".join(lines), kb)


@router.callback_query(F.data == "days:list")
async def days_list(cq: CallbackQuery) -> None:
    dist = await stats.day_distribution()
    if not dist:
        await show(cq, "Пока нет активных каналов.", back("days:menu"))
        return
    # У Telegram лимит ~100 кнопок на клавиатуру: показываем не больше 98 дней
    items = list(dist.items())
    shown = items[:98]
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for day, v in shown:
        row.append(InlineKeyboardButton(
            text=f"День {day} — {v['total']}", callback_data="noop"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="days:menu")])
    extra = (f"\n\nПоказаны первые {len(shown)} из {len(items)} дней — остальные ищи через «🔍 Поиск»."
             if len(items) > len(shown) else "")
    await show(cq, "📋 <b>Дни и количество каналов</b>\n(🔓 — сколько готово к передаче видно в поиске)"
               + extra, InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data == "days:search")
async def days_search(cq: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Search.day)
    await show(cq, "🔍 Введи номер дня (например <code>200</code>):", back("days:menu"))


@router.message(Search.day)
async def do_search(message: Message, state: FSMContext) -> None:
    await state.clear()
    try:
        day = int((message.text or "").strip())
        if day < 1:
            raise ValueError
    except ValueError:
        await message.answer("Нужно число от 1. Попробуй ещё раз через «🔍 Поиск».",
                             reply_markup=back("days:menu"))
        return
    r = await stats.search_day(day)
    text = (
        f"🔍 <b>День {day}</b>\n\n"
        f"Ровно на дне {day}: <b>{r['exact_total']}</b> "
        f"(🔓 к передаче: {r['exact_ready']})\n"
        f"На дне {day} и старше: <b>{r['ge_total']}</b> "
        f"(🔓 к передаче: {r['ge_ready']})"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Передать с этого дня", callback_data=f"tr:from:{day}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="days:menu")],
    ])
    await message.answer(text, reply_markup=kb)
