"""Раздел статистики."""
from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery

from app.handlers.ui import show
from app.keyboards import back
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from app.services import stats

router = Router()


def _kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 График роста", callback_data="stats:chart")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")],
    ])


@router.callback_query(F.data == "menu:stats")
async def show_stats(cq: CallbackQuery) -> None:
    ov = await stats.overview()
    acc_lines = "\n".join(f"   {k}: <b>{v}</b>" for k, v in ov["accounts_by_status"].items())
    text = (
        "📊 <b>Статистика</b>\n\n"
        f"📢 Каналов всего: <b>{ov['total']}</b>\n"
        f"   активных: {ov['active']} · зарезерв.: {ov['reserved']}\n"
        f"   передано: {ov['transferred']} · мёртвых: {ov['dead']}\n\n"
        f"🆕 Создано:\n"
        f"   за сутки: <b>{ov['created_day']}</b>\n"
        f"   за неделю: {ov['created_week']}\n"
        f"   за месяц: {ov['created_month']}\n\n"
        f"👤 Аккаунтов: <b>{ov['accounts_total']}</b>\n{acc_lines or '   —'}\n\n"
        f"📋 Задач в очереди: {ov['pending_tasks']}"
    )
    await show(cq, text, _kb())


@router.callback_query(F.data == "stats:chart")
async def show_chart(cq: CallbackQuery) -> None:
    await cq.answer("Строю график…")
    png = await stats.growth_chart(30)
    if png is None:
        await cq.message.answer("Пока нет данных для графика — появится после первого дня работы.")
        return
    await cq.message.answer_photo(
        BufferedInputFile(png, "growth.png"),
        caption="📈 Рост количества каналов за последние 30 дней",
        reply_markup=back("menu:stats"),
    )
