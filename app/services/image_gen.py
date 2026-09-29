"""Генерация картинки поста: 'День N' + дата поверх (случайного) фона.

Работа с Pillow выполняется в отдельном потоке, чтобы не блокировать цикл бота.
"""
import asyncio
import io
import random
from datetime import datetime

from PIL import Image, ImageColor, ImageDraw, ImageFont

from app import config, settings
from app.logger import log

W, H = 1080, 1080
_DEFAULT_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _pick_background() -> Image.Image:
    files = [p for p in config.BACKGROUNDS_DIR.glob("*")
             if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]
    if files:
        try:
            bg = Image.open(random.choice(files)).convert("RGB")
            return _cover(bg, W, H)
        except Exception as e:  # noqa: BLE001
            log.warning("Не удалось открыть фон: %s", e)
    # Фолбэк: градиент из случайного тёплого/холодного цвета
    return _gradient()


def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    ratio = max(w / img.width, h / img.height)
    img = img.resize((int(img.width * ratio), int(img.height * ratio)))
    left = (img.width - w) // 2
    top = (img.height - h) // 2
    return img.crop((left, top, left + w, top + h))


def _gradient() -> Image.Image:
    top = tuple(random.randint(20, 90) for _ in range(3))
    bottom = tuple(random.randint(90, 200) for _ in range(3))
    # быстрый вертикальный градиент: столбец 1xH, растянутый до WxH
    grad = Image.new("RGB", (1, H))
    for y in range(H):
        t = y / H
        grad.putpixel((0, y), tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return grad.resize((W, H))


def _font(size: int) -> ImageFont.FreeTypeFont:
    # Пользовательский шрифт из DATA_DIR/fonts, иначе системный DejaVu
    custom = [p for p in config.FONTS_DIR.glob("*.ttf")]
    path = str(custom[0]) if custom else _DEFAULT_FONT
    try:
        return ImageFont.truetype(path, size)
    except Exception:  # noqa: BLE001
        return ImageFont.load_default()


def _draw_text_center(draw: ImageDraw.ImageDraw, text: str, y: int,
                      font: ImageFont.FreeTypeFont, color: str) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    x = (W - tw) // 2
    # Тень/обводка для читаемости на любом фоне
    for dx, dy in ((-3, -3), (3, -3), (-3, 3), (3, 3), (0, 0)):
        fill = "#000000" if (dx or dy) else color
        draw.text((x + dx, y + dy), text, font=font, fill=fill)


def _render(day: int) -> bytes:
    img = _pick_background()
    # Затемняющий слой для контраста текста
    overlay = Image.new("RGB", (W, H), "#000000")
    img = Image.blend(img, overlay, 0.25)

    draw = ImageDraw.Draw(img)
    size = int(settings.get("img_font_size"))
    color = str(settings.get("img_text_color") or "#FFFFFF").strip()
    try:
        ImageColor.getrgb(color)
    except ValueError:
        color = "#FFFFFF"  # битый цвет в настройках не должен ронять все посты
    pos = settings.get("img_text_pos")

    big = _font(size)
    small = _font(max(40, size // 3))

    day_text = f"День {day}"
    date_text = datetime.now(config.TZ).strftime("%d.%m.%Y")

    if pos == "top":
        y_day = int(H * 0.18)
    elif pos == "bottom":
        y_day = int(H * 0.62)
    else:
        y_day = int(H * 0.40)

    _draw_text_center(draw, day_text, y_day, big, color)
    _draw_text_center(draw, date_text, y_day + size + 20, small, color)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


async def render_day_image(day: int) -> bytes:
    """Асинхронная обёртка — рендер в пуле потоков."""
    return await asyncio.to_thread(_render, day)


async def preview(day: int = 1) -> bytes:
    return await render_day_image(day)
