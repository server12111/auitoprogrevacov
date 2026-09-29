"""Инлайн-клавиатуры. Кнопки в 2 столбца, где удобно."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def main_menu(is_admin: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("📊 Статистика", "menu:stats"), _btn("👤 Аккаунты", "acc:list:0"))
    b.row(_btn("📢 Каналы", "ch:list:0"), _btn("📅 Дни", "days:menu"))
    if is_admin:
        b.row(_btn("📤 Передать", "tr:new"), _btn("📦 История", "tr:hist:0"))
        b.row(_btn("🖼 Картинки", "img:menu"), _btn("⚙️ Настройки", "set:menu"))
        b.row(_btn("🛡 Админ-панель", "adm:menu"))
        b.row(_btn("🛑 Экстренный стоп", "sys:stop"), _btn("▶️ Запуск", "sys:start"))
    else:
        b.row(_btn("📦 История", "tr:hist:0"), _btn("🖼 Картинки", "img:menu"))
    return b.as_markup()


def back(target: str = "menu:main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("⬅️ Назад", target)]])


def confirm(yes_data: str, no_data: str = "menu:main",
            yes_text: str = "✅ Подтвердить", no_text: str = "❌ Отмена") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn(yes_text, yes_data), _btn(no_text, no_data)]])


def paginate(items: list, page: int, per_page: int, cb_prefix: str,
             label_fn, id_fn, back_target: str = "menu:main",
             columns: int = 2, extra_top: list[InlineKeyboardButton] | None = None) -> InlineKeyboardMarkup:
    """Универсальная пагинация списком кнопок."""
    b = InlineKeyboardBuilder()
    if extra_top:
        b.row(*extra_top)
    start = page * per_page
    chunk = items[start:start + per_page]
    row: list[InlineKeyboardButton] = []
    for it in chunk:
        row.append(_btn(label_fn(it), f"{cb_prefix}:view:{id_fn(it)}"))
        if len(row) == columns:
            b.row(*row)
            row = []
    if row:
        b.row(*row)

    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(_btn("◀️", f"{cb_prefix}:list:{page - 1}"))
    nav.append(_btn(f"{page + 1}/{total_pages}", "noop"))
    if start + per_page < len(items):
        nav.append(_btn("▶️", f"{cb_prefix}:list:{page + 1}"))
    if len(nav) > 1:
        b.row(*nav)
    b.row(_btn("⬅️ Назад", back_target))
    return b.as_markup()
