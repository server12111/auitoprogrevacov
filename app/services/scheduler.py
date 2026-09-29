"""Планировщик: диспетчер очереди, ежедневное планирование, проверки, отчёты."""
import asyncio
import random
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telethon import functions
from telethon.errors import ChannelInvalidError, ChannelPrivateError, FloodWaitError
from telethon.tl.types import InputPeerChannel

from app import config, settings
from app.db import repo
from app.logger import log
from app.notify import notify_owner
from app.services import backup, stats, task_queue
from app.services import account_manager as am
from app.utils import now, today_str

scheduler = AsyncIOScheduler(timezone=config.TZ)

# Выполненные задачи храним неделю, потом удаляем (иначе БД растёт ~0.7 ГБ/год на 20k каналов)
TASKS_KEEP_DAYS = 7


def _window_bounds_today() -> tuple[int, int]:
    base = datetime.now(config.TZ).replace(minute=0, second=0, microsecond=0)
    start = base.replace(hour=settings.get_hour("post_window_start"))
    end = base.replace(hour=settings.get_hour("post_window_end"))
    return int(start.timestamp()), int(end.timestamp())


def _rand_in_window() -> int:
    start, end = _window_bounds_today()
    if end <= start:
        end = start + 3600
    ts = random.randint(start, end)
    # если окно уже прошло — постим в ближайшие пару часов
    if ts < now():
        ts = now() + random.randint(60, 2 * 3600)
    return ts


def _end_of_today() -> int:
    tomorrow = (datetime.now(config.TZ) + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return int(tomorrow.timestamp())


# ---------- Ежедневное планирование ----------

async def plan_daily() -> None:
    """Ставит задачи на день: посты во все каналы + создание новых каналов.

    Идемпотентна: повторный вызов (рестарт бота, «▶️ Запуск») не создаёт дублей —
    учитываются уже стоящие в очереди задачи.
    """
    if settings.get("emergency_stop"):
        return

    items: list[tuple[int, str, dict, int]] = []

    channels = await repo.active_channels()
    already_queued = await repo.pending_post_channel_ids()
    today = today_str()
    for ch in channels:
        if ch["last_post_date"] == today or ch["id"] in already_queued:
            continue
        items.append((ch["account_id"], "post", {"channel_id": ch["id"]}, _rand_in_window()))

    limit = int(settings.get("daily_limit"))
    day_start = int(datetime.now(config.TZ).replace(
        hour=settings.get_hour("post_window_start"),
        minute=0, second=0, microsecond=0).timestamp())
    # создание растягиваем по дню, но так, чтобы не переползти за полночь
    # (иначе завтрашний лимит посчитается заново и каналов станет больше лимита)
    begin = max(now(), day_start)
    finish = min(begin + 8 * 3600, _end_of_today() - 15 * 60)
    accounts = await repo.list_accounts()
    for acc in accounts:
        if acc["status"] != "active" or finish <= begin:
            continue
        already = await repo.created_today(acc["id"])
        queued = await repo.pending_count(acc["id"], "create_channel")
        for _ in range(max(0, limit - already - queued)):
            items.append((acc["id"], "create_channel", {}, random.randint(begin, finish)))

    if items:
        await repo.enqueue_many(items)
    stats.invalidate()
    log.info("Ежедневное планирование выполнено (каналов: %s, новых задач: %s)",
             len(channels), len(items))


# ---------- Проверка каналов ----------

async def health_check_channels() -> None:
    """Помечает недоступные каналы как dead.

    dead ставится только когда Telegram прямо говорит, что канала нет / доступа нет.
    Флудвейт или сетевой сбой — не повод хоронить канал: прекращаем проверку аккаунта.
    """
    if settings.get("emergency_stop"):
        return
    by_account: dict[int, list] = {}
    for ch in await repo.active_channels():
        if ch["status"] == "active":
            by_account.setdefault(ch["account_id"], []).append(ch)
    accounts = {a["id"]: a for a in await repo.list_accounts()}

    async def check_account(acc_id: int, chans: list) -> int:
        """Каналы одного аккаунта — по очереди с паузой; разные аккаунты — параллельно."""
        acc = accounts.get(acc_id)
        if not acc or acc["status"] not in ("active", "full"):
            return 0
        client = await am.get_client(acc_id)
        if client is None:
            return 0
        found = 0
        for ch in chans:
            if settings.get("emergency_stop"):
                break
            peer = InputPeerChannel(ch["tg_channel_id"], ch["access_hash"])
            try:
                await client(functions.channels.GetFullChannelRequest(peer))
            except (ChannelPrivateError, ChannelInvalidError):
                await repo.set_channel_status(ch["id"], "dead")
                found += 1
            except FloodWaitError as e:
                log.warning("Проверка каналов: флудвейт %s сек на аккаунте %s — пропускаю его",
                            e.seconds, acc_id)
                break
            except Exception as e:  # noqa: BLE001
                log.warning("Проверка канала %s: %s", ch["id"], e)
            await asyncio.sleep(random.uniform(1.0, 3.0))
        return found

    # не больше 10 аккаунтов одновременно, чтобы не открывать сотни соединений разом
    sem = asyncio.Semaphore(10)

    async def limited(acc_id: int, chans: list) -> int:
        async with sem:
            return await check_account(acc_id, chans)

    results = await asyncio.gather(*(limited(a, c) for a, c in by_account.items()),
                                   return_exceptions=True)
    dead = sum(r for r in results if isinstance(r, int))
    if dead:
        await notify_owner(f"⚠️ Проверка каналов: {dead} недоступны, помечены как dead.")
    stats.invalidate()


# ---------- Резервы ----------

async def expire_reservations() -> None:
    freed = await repo.expire_reservations()
    if not freed:
        return
    log.info("Снят резерв с %s каналов (истёк срок)", len(freed))
    stats.invalidate()
    # Отзываем выданные ссылки: иначе по старой ссылке в канал сможет зайти
    # прежний покупатель, когда канал уже будет продан другому
    for ch in freed:
        if not ch["invite_link"]:
            continue
        client = await am.get_client(ch["account_id"])
        if client is None:
            continue
        try:
            await client(functions.messages.EditExportedChatInviteRequest(
                peer=InputPeerChannel(ch["tg_channel_id"], ch["access_hash"]),
                link=ch["invite_link"], revoked=True,
            ))
        except Exception as e:  # noqa: BLE001
            log.warning("Не удалось отозвать ссылку канала %s: %s", ch["id"], e)
        await asyncio.sleep(random.uniform(1.0, 3.0))


# ---------- Снимок + отчёт ----------

async def daily_snapshot() -> None:
    total = await repo.count_channels("active")
    created = await repo.count_created_since(now() - 24 * 3600)
    await repo.snapshot_channels(today_str(), total, created)


async def cleanup_tasks() -> None:
    await repo.cleanup_tasks(now() - TASKS_KEEP_DAYS * 24 * 3600)


async def daily_report() -> None:
    ov = await stats.overview()
    acc_lines = "\n".join(f"  • {k}: {v}" for k, v in ov["accounts_by_status"].items())
    text = (
        f"📊 <b>Ежедневный отчёт</b>\n\n"
        f"Каналов всего: <b>{ov['total']}</b> (активных {ov['active']})\n"
        f"Создано за сутки: <b>{ov['created_day']}</b>\n"
        f"За неделю: {ov['created_week']} · за месяц: {ov['created_month']}\n"
        f"Передано: {ov['transferred']}\n\n"
        f"Аккаунты ({ov['accounts_total']}):\n{acc_lines or '  —'}\n\n"
        f"Задач в очереди: {ov['pending_tasks']}"
    )
    await notify_owner(text)


# ---------- Регистрация задач ----------

def setup() -> None:
    scheduler.add_job(task_queue.process_once, "interval", seconds=20, id="dispatcher",
                      max_instances=1, coalesce=True)
    scheduler.add_job(expire_reservations, "interval", minutes=10, id="reservations",
                      max_instances=1, coalesce=True)
    scheduler.add_job(plan_daily, "cron", hour=0, minute=5, id="plan_daily")
    scheduler.add_job(health_check_channels, "cron", hour=4, minute=0, id="health",
                      max_instances=1, coalesce=True)
    scheduler.add_job(daily_snapshot, "cron", hour=23, minute=55, id="snapshot")
    scheduler.add_job(cleanup_tasks, "cron", hour=3, minute=15, id="cleanup")
    # sync-функция: APScheduler выполнит её в пуле потоков, не блокируя бота
    scheduler.add_job(backup.make_backup, "cron", hour=3, minute=30, id="backup")
    scheduler.add_job(
        daily_report, "cron",
        hour=settings.get_hour("report_hour"), minute=0, id="report",
    )
    scheduler.start()
    log.info("Планировщик запущен")


def reschedule_report() -> None:
    try:
        scheduler.reschedule_job("report", trigger="cron",
                                 hour=settings.get_hour("report_hour"), minute=0)
    except Exception as e:  # noqa: BLE001
        log.error("Не удалось перепланировать отчёт: %s", e)
