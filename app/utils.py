"""Мелкие утилиты: время, форматирование."""
import time
from datetime import datetime, timedelta

from app import config


def now() -> int:
    return int(time.time())


def today_str() -> str:
    return datetime.now(config.TZ).strftime("%Y-%m-%d")


def dt_local(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, config.TZ)


def fmt_dt(ts: int | None) -> str:
    if not ts:
        return "—"
    return dt_local(ts).strftime("%d.%m.%Y %H:%M")


def fmt_time(ts: int | None) -> str:
    if not ts:
        return "—"
    return dt_local(ts).strftime("%H:%M")


def day_number(created_at: int) -> int:
    """День N: 1 в день создания, +1 каждые сутки."""
    created = dt_local(created_at).date()
    today = datetime.now(config.TZ).date()
    return (today - created).days + 1


def fmt_left(until: int | None) -> str:
    """Сколько осталось до момента until (человекочитаемо)."""
    if not until:
        return "—"
    left = until - now()
    if left <= 0:
        return "скоро"
    h, rem = divmod(left, 3600)
    m = rem // 60
    if h:
        return f"{h}ч {m}м"
    return f"{m}м"


def human_td(seconds: int) -> str:
    td = timedelta(seconds=seconds)
    return str(td)
