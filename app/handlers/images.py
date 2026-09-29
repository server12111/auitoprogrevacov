"""Раздел «Картинки»: предпросмотр, загрузка фонов, параметры текста."""
from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (BufferedInputFile, CallbackQuery,
                           InlineKeyboardButton, InlineKeyboardMarkup, Message)

from app import config, settings
from app.handlers.ui import esc, require_admin, show
from app.keyboards import back
from app.middlewares import is_admin
from app.services import image_gen
from app.states import Upload
from app.utils import now

router = Router()


def _menu_kb(admin: bool) -> InlineKeyboardMarkup:
    if not admin:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👁 Предпросмотр", callback_data="img:preview")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")],
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁 Предпросмотр", callback_data="img:preview"),
         InlineKeyboardButton(text="🖼 Загрузить фон", callback_data="img:bg")],
        [InlineKeyboardButton(text="🔤 Размер текста", callback_data="set:edit:img_font_size"),
         InlineKeyboardButton(text="🎨 Цвет текста", callback_data="set:edit:img_text_color")],
        [InlineKeyboardButton(text="📍 Положение", callback_data="img:pos"),
         InlineKeyboardButton(text="🗑 Очистить фоны", callback_data="img:clearbg")],
        [InlineKeyboardButton(text="📝 Подписи к постам", callback_data="set:edit:post_captions")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")],
    ])


@router.callback_query(F.data == "img:menu")
async def img_menu(cq: CallbackQuery, state: FSMContext, role: str) -> None:
    await state.clear()  # выход из режима загрузки фонов
    await _render_menu(cq, role)


async def _render_menu(cq: CallbackQuery, role: str, toast: str | None = None) -> None:
    bg_count = len([p for p in config.BACKGROUNDS_DIR.glob("*")
                    if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")])
    text = (
        "🖼 <b>Картинки</b>\n\n"
        f"Фонов загружено: <b>{bg_count}</b>\n"
        f"Размер текста: {settings.get('img_font_size')}\n"
        f"Цвет: {esc(settings.get('img_text_color'))}\n"
        f"Положение: {settings.get('img_text_pos')}\n\n"
        "На каждой картинке пишется «День N» и дата поверх случайного фона."
    )
    await show(cq, text, _menu_kb(is_admin(role)), toast)


@router.callback_query(F.data == "img:preview")
async def img_preview(cq: CallbackQuery) -> None:
    await cq.answer("Генерирую…")
    png = await image_gen.preview(day=137)
    await cq.message.answer_photo(
        BufferedInputFile(png, "preview.jpg"),
        caption="👁 Пример (День 137)",
        reply_markup=back("img:menu"),
    )


@router.callback_query(F.data == "img:bg")
async def img_bg(cq: CallbackQuery, state: FSMContext, role: str) -> None:
    if not await require_admin(cq, role):
        return
    await state.set_state(Upload.background)
    await show(cq,
               "🖼 Отправь одно или несколько изображений (как фото) — они станут фонами.\n"
               "Когда закончишь, нажми «Назад».",
               back("img:menu"))


@router.message(Upload.background, F.photo)
async def receive_bg(message: Message, state: FSMContext, bot: Bot) -> None:
    photo = message.photo[-1]
    dst = config.BACKGROUNDS_DIR / f"bg_{now()}_{photo.file_unique_id}.jpg"
    await bot.download(photo, destination=dst)
    await message.answer("✅ Фон добавлен. Можешь прислать ещё или нажать «Назад».",
                         reply_markup=back("img:menu"))


@router.callback_query(F.data == "img:pos")
async def img_pos(cq: CallbackQuery, role: str) -> None:
    if not await require_admin(cq, role):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Сверху", callback_data="img:pos:top"),
         InlineKeyboardButton(text="Центр", callback_data="img:pos:center"),
         InlineKeyboardButton(text="Снизу", callback_data="img:pos:bottom")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="img:menu")],
    ])
    await show(cq, "📍 Где размещать текст?", kb)


@router.callback_query(F.data.startswith("img:pos:"))
async def img_pos_set(cq: CallbackQuery, role: str) -> None:
    if not await require_admin(cq, role):
        return
    pos = cq.data.split(":")[2]
    if settings.validate("img_text_pos", pos):
        await cq.answer("Неизвестное положение", show_alert=True)
        return
    await settings.set("img_text_pos", pos)
    await _render_menu(cq, role, toast=f"Положение: {pos}")


@router.callback_query(F.data == "img:clearbg")
async def img_clearbg(cq: CallbackQuery, role: str) -> None:
    if not await require_admin(cq, role):
        return
    for p in config.BACKGROUNDS_DIR.glob("*"):
        try:
            p.unlink()
        except OSError:
            pass
    await _render_menu(cq, role, toast="Фоны удалены")
