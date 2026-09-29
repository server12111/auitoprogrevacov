"""Настройки бота: значения по умолчанию + чтение/запись в таблицу settings с кэшем."""
import json
from typing import Any

from app.db import database as db

# Значения по умолчанию. Тип значения задаёт, как парсить строку из БД.
DEFAULTS: dict[str, Any] = {
    # Задержки между действиями одного аккаунта (сек)
    "delay_min": 120,
    "delay_max": 300,
    # Паузы после публикации поста (сек) — посты в свои каналы, отдельно от создания
    "post_delay_min": 30,
    "post_delay_max": 90,
    # Задержки между шагами передачи (сек)
    "transfer_delay_min": 20,
    "transfer_delay_max": 60,
    # Суточный лимит новых каналов на аккаунт
    "daily_limit": 2,
    # Длительность прогрева (часы)
    "warmup_hours": 24,
    # Окно постинга (часы, локальное время)
    "post_window_start": 9,
    "post_window_end": 12,
    # Время ежедневного отчёта владельцу (час)
    "report_hour": 21,
    # Флудвейт: сколько добавлять к паузе после флудвейта (мин) и разброс (мин)
    "flood_extra_min": 60,
    "flood_jitter_min": 10,
    # 2FA: режим пароля — "shared" общий или "per_account" генерировать
    "twofa_mode": "shared",
    "twofa_shared_password": "",
    "twofa_hint": "recovery",
    # Передача: срок резерва (часы), срок жизни инвайт-ссылки (часы), выходить ли из канала после передачи
    "reserve_hours": 24,
    "invite_ttl_hours": 24,
    "leave_after_transfer": True,
    # Глобальный стоп
    "emergency_stop": False,
    # Шаблоны названий/описаний (по строке на вариант, разделитель — перевод строки)
    "channel_titles": "Дневник\nМой путь\nАрхив дней\nХроника\nЗаметки",
    "channel_descriptions": "Ежедневный счётчик дней.\nКаждый день — новый день.\n",
    # Подписи к посту (ротация)
    "post_captions": "Новый день ✨\nЕщё один день позади\nДень засчитан",
    # Картинка
    "img_font_size": 120,
    "img_text_color": "#FFFFFF",
    "img_text_pos": "center",  # center / top / bottom
}

_cache: dict[str, Any] = {}


async def load_all() -> None:
    """Загрузить все настройки в кэш при старте."""
    _cache.clear()
    rows = await db.fetchall("SELECT key, value FROM settings")
    stored = {r["key"]: r["value"] for r in rows}
    for key, default in DEFAULTS.items():
        if key in stored:
            _cache[key] = _parse(stored[key], default)
        else:
            _cache[key] = default


def _parse(raw: str, default: Any) -> Any:
    if isinstance(default, bool):
        return raw == "1" or raw.lower() == "true"
    if isinstance(default, int):
        try:
            return int(raw)
        except ValueError:
            return default
    if isinstance(default, float):
        try:
            return float(raw)
        except ValueError:
            return default
    return raw


def _dump(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def get(key: str) -> Any:
    return _cache.get(key, DEFAULTS.get(key))


async def set(key: str, value: Any) -> None:
    _cache[key] = value
    await db.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, _dump(value)),
    )


# Допустимые диапазоны числовых настроек: key -> (min, max)
RANGES: dict[str, tuple[int, int]] = {
    "delay_min": (5, 86400), "delay_max": (5, 86400),
    "post_delay_min": (5, 86400), "post_delay_max": (5, 86400),
    "transfer_delay_min": (1, 3600), "transfer_delay_max": (1, 3600),
    "daily_limit": (0, 50),
    "warmup_hours": (0, 720),
    "post_window_start": (0, 23), "post_window_end": (0, 23),
    "report_hour": (0, 23),
    "flood_extra_min": (0, 1440), "flood_jitter_min": (0, 1440),
    "reserve_hours": (1, 720), "invite_ttl_hours": (1, 720),
    "img_font_size": (20, 400),
}

# Пары (min-ключ, max-ключ), где min не может быть больше max
PAIRS = [("delay_min", "delay_max"), ("post_delay_min", "post_delay_max"),
         ("transfer_delay_min", "transfer_delay_max")]


def validate(key: str, value: Any) -> str | None:
    """Проверить новое значение. Возвращает текст ошибки или None, если всё ок."""
    if key in RANGES:
        lo, hi = RANGES[key]
        if not lo <= value <= hi:
            return f"Допустимо от {lo} до {hi}."
    for k_min, k_max in PAIRS:
        if key == k_min and value > get(k_max):
            return f"Минимум не может быть больше максимума ({get(k_max)})."
        if key == k_max and value < get(k_min):
            return f"Максимум не может быть меньше минимума ({get(k_min)})."
    if key == "img_text_color":
        from PIL import ImageColor
        try:
            ImageColor.getrgb(str(value).strip())
        except ValueError:
            return "Неверный цвет. Пример: #FFFFFF или white."
    if key == "img_text_pos" and value not in ("top", "center", "bottom"):
        return "Допустимо: top / center / bottom."
    return None


def get_range(key_min: str, key_max: str) -> tuple[int, int]:
    """Пара (min, max) в правильном порядке, даже если в БД лежат перепутанные значения."""
    a, b = int(get(key_min)), int(get(key_max))
    return (a, b) if a <= b else (b, a)


def get_hour(key: str) -> int:
    """Час из настроек, гарантированно в 0..23."""
    try:
        return min(23, max(0, int(get(key))))
    except (TypeError, ValueError):
        return int(DEFAULTS[key])


def lines(key: str) -> list[str]:
    """Получить непустые строки многострочной настройки."""
    raw = str(get(key) or "")
    return [ln.strip() for ln in raw.splitlines() if ln.strip()]
