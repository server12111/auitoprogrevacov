"""Раздел настроек: числовые/текстовые параметры и переключатели. Только для админов."""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

from app import settings
from app.db import repo
from app.handlers.ui import esc, require_admin, show
from app.keyboards import back
from app.services import scheduler
from app.states import EditSetting

router = Router()

# key -> человекочитаемая подпись (для редактируемых текстом/числом)
EDITABLE = {
    "delay_min": "Мин. пауза между действиями (сек)",
    "delay_max": "Макс. пауза между действиями (сек)",
    "post_delay_min": "Мин. пауза после поста (сек)",
    "post_delay_max": "Макс. пауза после поста (сек)",
    "daily_limit": "Лимит новых каналов в сутки",
    "warmup_hours": "Прогрев (часов)",
    "post_window_start": "Начало окна постинга (час)",
    "post_window_end": "Конец окна постинга (час)",
    "report_hour": "Час ежедневного отчёта",
    "flood_extra_min": "Доп. пауза после флудвейта (мин)",
    "reserve_hours": "Срок резерва при передаче (часов)",
    "invite_ttl_hours": "Срок жизни инвайт-ссылки (часов)",
    "twofa_shared_password": "Общий пароль 2FA",
    "twofa_hint": "Подсказка 2FA",
    "channel_titles": "Шаблоны названий (по строке)",
    "channel_descriptions": "Шаблоны описаний (по строке)",
    "post_captions": "Подписи к постам (по строке)",
    "img_font_size": "Размер текста на картинке",
    "img_text_color": "Цвет текста (hex)",
}

SECRET_KEYS = {"twofa_shared_password"}
MULTILINE_KEYS = {"channel_titles", "channel_descriptions", "post_captions"}


def _display(key: str, value) -> str:
    if key in SECRET_KEYS:
        return "••••••" if value else "(не задан — случайный)"
    return str(value)


def _menu_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="⏱ Задержки", callback_data="set:grp:delays"),
         InlineKeyboardButton(text="📊 Лимиты/прогрев", callback_data="set:grp:limits")],
        [InlineKeyboardButton(text="🕐 Окно/отчёт", callback_data="set:grp:time"),
         InlineKeyboardButton(text="🔐 2FA", callback_data="set:grp:twofa")],
        [InlineKeyboardButton(text="📤 Передача", callback_data="set:grp:transfer"),
         InlineKeyboardButton(text="📝 Шаблоны", callback_data="set:grp:templates")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


GROUPS = {
    "delays": ["delay_min", "delay_max", "post_delay_min", "post_delay_max"],
    "limits": ["daily_limit", "warmup_hours"],
    "time": ["post_window_start", "post_window_end", "report_hour", "flood_extra_min"],
    "twofa": ["twofa_shared_password", "twofa_hint"],
    "transfer": ["reserve_hours", "invite_ttl_hours"],
    "templates": ["channel_titles", "channel_descriptions", "post_captions"],
}


@router.callback_query(F.data == "set:menu")
async def set_menu(cq: CallbackQuery, state: FSMContext, role: str) -> None:
    if not await require_admin(cq, role):
        return
    await state.clear()  # «Назад» из редактирования значения
    text = (
        "⚙️ <b>Настройки</b>\n\n"
        f"🛑 Экстренный стоп: {'ВКЛ' if settings.get('emergency_stop') else 'выкл'}\n"
        "Выбери группу настроек:"
    )
    await show(cq, text, _menu_kb())


async def _render_group(cq: CallbackQuery, grp: str, toast: str | None = None) -> None:
    rows = []
    for k in GROUPS.get(grp, []):
        val_str = _display(k, settings.get(k))
        if len(val_str) > 24:
            val_str = val_str[:21] + "…"
        rows.append([InlineKeyboardButton(
            text=f"{EDITABLE.get(k, k)}: {val_str}",
            callback_data=f"set:edit:{k}")])

    # Переключатели для transfer и twofa
    if grp == "transfer":
        cur = settings.get("leave_after_transfer")
        rows.append([InlineKeyboardButton(
            text=f"Выходить из канала после передачи: {'✅' if cur else '❌'}",
            callback_data="set:toggle:leave_after_transfer")])
    if grp == "twofa":
        mode = settings.get("twofa_mode")
        rows.append([InlineKeyboardButton(
            text=f"Режим пароля: {'общий' if mode=='shared' else 'для каждого'}",
            callback_data="set:toggle:twofa_mode")])

    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="set:menu")])
    await show(cq, "⚙️ Нажми параметр, чтобы изменить:",
               InlineKeyboardMarkup(inline_keyboard=rows), toast)


@router.callback_query(F.data.startswith("set:grp:"))
async def set_group(cq: CallbackQuery, role: str) -> None:
    if not await require_admin(cq, role):
        return
    await _render_group(cq, cq.data.split(":")[2])


@router.callback_query(F.data.startswith("set:toggle:"))
async def set_toggle(cq: CallbackQuery, role: str) -> None:
    if not await require_admin(cq, role):
        return
    key = cq.data.split(":")[2]
    if key == "leave_after_transfer":
        await settings.set(key, not settings.get(key))
        await _render_group(cq, "transfer")
    elif key == "twofa_mode":
        new = "per_account" if settings.get("twofa_mode") == "shared" else "shared"
        await settings.set("twofa_mode", new)
        await _render_group(cq, "twofa")
    else:
        await cq.answer()


@router.callback_query(F.data.startswith("set:edit:"))
async def set_edit(cq: CallbackQuery, state: FSMContext, role: str) -> None:
    if not await require_admin(cq, role):
        return
    key = cq.data.split(":", 2)[2]
    if key not in EDITABLE:
        await cq.answer("Неизвестная настройка", show_alert=True)
        return
    await state.set_state(EditSetting.value)
    await state.update_data(key=key)
    cur = settings.get(key)
    hint = "\n\nМожно несколько строк — каждая станет отдельным вариантом." if key in MULTILINE_KEYS else ""
    if key in settings.RANGES:
        lo, hi = settings.RANGES[key]
        hint += f"\n\nДопустимо: от {lo} до {hi}."
    if key in SECRET_KEYS:
        hint += "\n\nСообщение с паролем бот удалит из чата."
    await show(cq,
               f"✏️ <b>{EDITABLE[key]}</b>\n\n"
               f"Текущее значение:\n<code>{esc(_display(key, cur))}</code>\n\n"
               f"Отправь новое значение.{hint}",
               back("set:menu"))


@router.message(EditSetting.value, F.text)
async def set_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    key = data.get("key")
    if not key or key not in EDITABLE:
        await state.clear()
        return
    raw = message.text
    if key in SECRET_KEYS:
        try:
            await message.delete()  # не оставляем пароль в переписке
        except Exception:  # noqa: BLE001
            pass
    default = settings.DEFAULTS.get(key)
    # Приводим к типу по умолчанию
    if isinstance(default, bool):
        value = raw.strip().lower() in ("1", "да", "true", "on", "вкл")
    elif isinstance(default, int):
        try:
            value = int(raw.strip())
        except ValueError:
            await message.answer("Нужно целое число. Отправь ещё раз:", reply_markup=back("set:menu"))
            return
    elif key in MULTILINE_KEYS:
        value = raw
    else:
        value = raw.strip()

    error = settings.validate(key, value)
    if error:
        await message.answer(f"❌ {esc(error)} Отправь ещё раз:", reply_markup=back("set:menu"))
        return

    await state.clear()
    await settings.set(key, value)
    await repo.log_action(message.from_user.id, "setting",
                          f"{key}=***" if key in SECRET_KEYS else f"{key}={str(value)[:50]}")
    if key == "report_hour":
        scheduler.reschedule_report()
    await message.answer(f"✅ Сохранено: {EDITABLE[key]} = {esc(_display(key, value))}",
                         reply_markup=back("set:menu"))
