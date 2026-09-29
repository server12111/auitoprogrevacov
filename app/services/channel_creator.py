"""Создание приватных каналов от имени аккаунта (обработчик задачи create_channel)."""
import random

from telethon import functions
from telethon.errors import ChannelsTooMuchError

from app import settings
from app.db import repo
from app.logger import log
from app.services import task_queue
from app.utils import now


def _pick(lines: list[str], fallback: str) -> str:
    return random.choice(lines) if lines else fallback


async def _create_channel(task: dict, client) -> None:
    acc_id = task["account_id"]

    # Лимит проверяем в момент выполнения: задачи могли остаться со вчера или
    # задвоиться, и без этой проверки аккаунт создал бы больше каналов, чем задано.
    if await repo.created_today(acc_id) >= int(settings.get("daily_limit")):
        log.info("Аккаунт %s: суточный лимит каналов уже выбран, задача пропущена", acc_id)
        return

    title = _pick(settings.lines("channel_titles"), "Дневник")
    # Небольшая вариация, чтобы названия не были идентичными
    about = _pick(settings.lines("channel_descriptions"), "")

    try:
        result = await client(functions.channels.CreateChannelRequest(
            title=title,
            about=about,
            broadcast=True,   # broadcast-канал (не мегагруппа)
            megagroup=False,
        ))
    except ChannelsTooMuchError:
        await repo.set_account_status(acc_id, "full", last_error="Достигнут лимит каналов")
        log.info("Аккаунт %s помечен как 'full'", acc_id)
        return

    channel = result.chats[0]
    ch_id = await repo.add_channel(
        account_id=acc_id,
        tg_channel_id=channel.id,
        access_hash=getattr(channel, "access_hash", None),
        title=title,
        created_at=now(),
    )
    await repo.inc_created_today(acc_id)
    # Первый пост (День 1) — через обычную задержку очереди
    await repo.enqueue(acc_id, "post", {"channel_id": ch_id}, run_after=now() + 60)
    log.info("Аккаунт %s создал канал '%s' (id=%s)", acc_id, title, channel.id)


def setup() -> None:
    task_queue.register("create_channel", _create_channel)
