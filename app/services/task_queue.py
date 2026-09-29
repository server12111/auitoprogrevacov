"""Очередь задач с защитой от флудвейтов.

Принципы:
- у каждого аккаунта задачи выполняются строго по одной, с паузой delay_min..delay_max
  между ними (пауза задаётся через run_after следующей задачи аккаунта);
- аккаунты обрабатываются со сдвигом, а не одновременно;
- FloodWaitError переводит аккаунт в паузу на (flood + доп.время) и откладывает
  все его задачи;
- глобальный "экстренный стоп" мгновенно замораживает выполнение.
"""
import asyncio
import json
import random
from typing import Awaitable, Callable

from telethon.errors import FloodWaitError, PeerFloodError, UserBannedInChannelError

from app import settings
from app.db import repo
from app.logger import log
from app.notify import notify_owner
from app.services import account_manager as am
from app.utils import now, fmt_time

# kind -> обработчик(task_row, client) -> None (бросает исключение при ошибке)
_handlers: dict[str, Callable[[dict, object], Awaitable[None]]] = {}

MAX_ATTEMPTS = 3
_stagger = 0  # сдвиг между аккаунтами в одном тике


def register(kind: str, handler: Callable) -> None:
    _handlers[kind] = handler


async def _account_ready(acc) -> bool:
    status = acc["status"]
    if status in ("paused", "banned", "loggedout"):
        return False
    if status == "warming":
        if acc["warmup_until"] and now() < acc["warmup_until"]:
            return False
        # прогрев закончился — активируем и ставим 2FA
        await repo.set_account_status(acc["id"], "active")
        await am.ensure_2fa(acc["id"])
        return True
    if status == "floodwait":
        if acc["flood_until"] and now() < acc["flood_until"]:
            return False
        await repo.set_account_status(acc["id"], "active")
        return True
    if status in ("active", "full"):
        # 2FA могла не встать с первого раза (сеть/флуд) — повторяем (с паузой внутри ensure_2fa)
        if not acc["twofa_set_at"]:
            await am.ensure_2fa(acc["id"])
        return True
    return False


async def _defer_account(acc_id: int, seconds: int) -> None:
    await repo.defer_account_tasks(acc_id, now() + seconds)


async def _handle_flood(acc_id: int, seconds: int, task_id: int) -> None:
    extra = settings.get("flood_extra_min") * 60
    jitter = random.randint(0, settings.get("flood_jitter_min") * 60)
    until = now() + seconds + extra + jitter
    await repo.set_account_status(acc_id, "floodwait", flood_until=until,
                                  last_error=f"FloodWait {seconds}s")
    await repo.retry_task(task_id, run_after=until, error=f"FloodWait {seconds}s")
    await _defer_account(acc_id, seconds + extra + jitter)
    log.warning("Аккаунт %s: флудвейт %s сек, пауза до %s", acc_id, seconds, fmt_time(until))
    await notify_owner(f"⏳ Аккаунт #{acc_id}: флудвейт {seconds} сек. Пауза до {fmt_time(until)}.")


async def _run_task(task, client) -> None:
    handler = _handlers.get(task["kind"])
    if handler is None:
        await repo.mark_task(task["id"], "failed", "Неизвестный тип задачи")
        return
    payload = json.loads(task["payload"] or "{}")
    task_dict = {**dict(task), "payload": payload}
    await handler(task_dict, client)


async def process_once() -> None:
    """Один проход диспетчера. Вызывается планировщиком каждые ~20 сек."""
    if settings.get("emergency_stop"):
        return

    global _stagger
    accounts = await repo.list_accounts()
    random.shuffle(accounts)
    _stagger = 0

    for acc in accounts:
        if not await _account_ready(acc):
            continue
        task = await repo.pending_task_for_account(acc["id"])
        if task is None:
            continue

        # Сдвиг между аккаунтами, чтобы не действовать одновременно
        if _stagger:
            await asyncio.sleep(random.uniform(1.0, 3.0))
        _stagger += 1

        # get_client сам ставит loggedout/banned, если Telegram так ответил;
        # при сетевом сбое просто пробуем в следующем тике
        client = await am.get_client(acc["id"])
        if client is None:
            continue

        try:
            await _run_task(task, client)

        except FloodWaitError as e:
            await _handle_flood(acc["id"], e.seconds, task["id"])

        except (PeerFloodError, UserBannedInChannelError) as e:
            # floodwait + flood_until, чтобы аккаунт сам вернулся в работу через сутки
            until = now() + 24 * 3600
            await repo.set_account_status(acc["id"], "floodwait", flood_until=until,
                                          last_error=f"Спамблок: {type(e).__name__}")
            await _defer_account(acc["id"], 24 * 3600)
            await notify_owner(
                f"🚫 Аккаунт #{acc['id']}: похоже на спамблок ({type(e).__name__}). "
                f"Пауза на 24 часа."
            )

        except Exception as e:  # noqa: BLE001
            attempts = task["attempts"] + 1
            if attempts >= MAX_ATTEMPTS:
                await repo.mark_task(task["id"], "failed", str(e)[:300])
                log.error("Задача %s провалена окончательно: %s", task["id"], e)
            else:
                backoff = 60 * (2 ** attempts) + random.randint(0, 60)
                await repo.retry_task(task["id"], run_after=now() + backoff, error=str(e)[:300])
                log.warning("Задача %s: повтор через %s сек (%s)", task["id"], backoff, e)

        else:
            # Задача выполнена. Отметку делаем вне try выше: ошибка здесь не должна
            # вернуть уже выполненную задачу в очередь (иначе — дубли каналов).
            await repo.mark_task(task["id"], "done")
            # Пауза до следующего действия этого аккаунта. Следующий пост можно раньше
            # (post_delay), но всё остальное (создание каналов и т.п.) — не раньше обычной паузы.
            lo, hi = settings.get_range("delay_min", "delay_max")
            normal = random.randint(lo, hi)
            if task["kind"] == "post":
                p_lo, p_hi = settings.get_range("post_delay_min", "post_delay_max")
                await repo.defer_account_tasks(acc["id"], now() + random.randint(p_lo, p_hi),
                                               kind="post")
                await repo.defer_account_tasks(acc["id"], now() + normal, exclude_kind="post")
            else:
                await _defer_account(acc["id"], normal)
