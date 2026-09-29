"""Статистика: сводка, распределение по дням, график роста. С кэшем."""
import asyncio
import io
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from app.db import repo
from app.services import transfer as transfer_svc
from app.utils import day_number, now

_CACHE_TTL = 180  # сек
_cache: dict[str, tuple[int, object]] = {}


def _cached(key: str):
    entry = _cache.get(key)
    if entry and now() - entry[0] < _CACHE_TTL:
        return entry[1]
    return None


def _store(key: str, value):
    _cache[key] = (now(), value)


def invalidate() -> None:
    _cache.clear()


async def overview() -> dict:
    cached = _cached("overview")
    if cached is not None:
        return cached

    total = await repo.count_channels()
    active = await repo.count_channels("active")
    reserved = await repo.count_channels("reserved")
    transferred = await repo.count_channels("transferred")
    dead = await repo.count_channels("dead")

    day_ts = now() - 24 * 3600
    week_ts = now() - 7 * 24 * 3600
    month_ts = now() - 30 * 24 * 3600
    created_day = await repo.count_created_since(day_ts)
    created_week = await repo.count_created_since(week_ts)
    created_month = await repo.count_created_since(month_ts)

    accounts = await repo.list_accounts()
    by_status = defaultdict(int)
    for a in accounts:
        by_status[a["status"]] += 1

    data = {
        "total": total, "active": active, "reserved": reserved,
        "transferred": transferred, "dead": dead,
        "created_day": created_day, "created_week": created_week,
        "created_month": created_month,
        "accounts_total": len(accounts), "accounts_by_status": dict(by_status),
        "pending_tasks": await repo.count_pending_tasks(),
    }
    _store("overview", data)
    return data


async def day_distribution() -> dict:
    """day -> {'total': n, 'ready': m}. ready = каналы с аккаунта, готового к передаче."""
    cached = _cached("days")
    if cached is not None:
        return cached

    rows = await repo.day_distribution()
    accounts = {a["id"]: a for a in await repo.list_accounts()}
    dist: dict[int, dict[str, int]] = defaultdict(lambda: {"total": 0, "ready": 0})
    for r in rows:
        d = day_number(r["created_at"])
        dist[d]["total"] += 1
        acc = accounts.get(r["account_id"])
        if acc and transfer_svc.account_transfer_ready(acc):
            dist[d]["ready"] += 1
    result = dict(sorted(dist.items()))
    _store("days", result)
    return result


async def search_day(day: int) -> dict:
    dist = await day_distribution()
    exact = dist.get(day, {"total": 0, "ready": 0})
    total_ge = sum(v["total"] for d, v in dist.items() if d >= day)
    ready_ge = sum(v["ready"] for d, v in dist.items() if d >= day)
    return {
        "day": day,
        "exact_total": exact["total"], "exact_ready": exact["ready"],
        "ge_total": total_ge, "ge_ready": ready_ge,
    }


def _render_growth(dates: list[str], totals: list[int]) -> bytes:
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.plot(dates, totals, marker="o", linewidth=2, color="#2b8a3e")
    ax.fill_between(range(len(totals)), totals, alpha=0.15, color="#2b8a3e")
    ax.set_title("Рост количества каналов")
    ax.set_ylabel("Каналов всего")
    ax.grid(True, alpha=0.3)
    step = max(1, len(dates) // 10)
    ax.set_xticks(range(0, len(dates), step))
    ax.set_xticklabels([dates[i] for i in range(0, len(dates), step)], rotation=45, ha="right")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return buf.getvalue()


async def growth_chart(days: int = 30) -> bytes | None:
    rows = await repo.history_rows(days)
    if not rows:
        return None
    rows = list(reversed(rows))
    dates = [r["date"][5:] for r in rows]  # MM-DD
    totals = [r["total"] for r in rows]
    return await asyncio.to_thread(_render_growth, dates, totals)
