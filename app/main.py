"""Точка входа: инициализация БД, сервисов, планировщика и polling бота."""
import asyncio
import html

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent

from app import config, notify, settings
from app.db import database
from app.db import repo
from app.handlers import build_router
from app.logger import log
from app.middlewares import AccessMiddleware
from app.services import (backup, channel_creator, poster, scheduler)
from app.services import account_manager as am
from app.utils import now

_heartbeat_task: asyncio.Task | None = None


async def _heartbeat() -> None:
    """Периодически обновляет heartbeat-файл для healthcheck."""
    while True:
        try:
            config.HEARTBEAT_FILE.write_text(str(now()))
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(60)


async def on_startup(bot: Bot) -> None:
    config.ensure_dirs()
    await database.init()
    await settings.load_all()

    # Гарантируем запись владельца
    if not await repo.get_user(config.OWNER_ID):
        me_username = None
        try:
            chat = await bot.get_chat(config.OWNER_ID)
            me_username = chat.username
        except Exception:  # noqa: BLE001
            pass
        await repo.add_user(config.OWNER_ID, "owner", me_username, None)

    notify.set_bot(bot)

    # Регистрируем обработчики задач
    channel_creator.setup()
    poster.setup()

    # Планировщик
    scheduler.setup()

    # Первичный бэкап и планирование на сегодня
    await asyncio.to_thread(backup.make_backup)
    await scheduler.plan_daily()  # без дублей: учитывает уже стоящие задачи

    global _heartbeat_task
    _heartbeat_task = asyncio.create_task(_heartbeat())

    await notify.notify_owner("🤖 Бот запущен и готов к работе.")
    log.info("Старт завершён")


async def on_shutdown(bot: Bot) -> None:
    log.info("Остановка…")
    if _heartbeat_task:
        _heartbeat_task.cancel()
    try:
        scheduler.scheduler.shutdown(wait=False)
    except Exception:  # noqa: BLE001
        pass
    await am.disconnect_all()
    await database.close()
    log.info("Остановка завершена")


async def main() -> None:
    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # Мидлварь доступа на сообщения и колбэки
    dp.message.middleware(AccessMiddleware())
    dp.callback_query.middleware(AccessMiddleware())

    dp.include_router(build_router())

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Глобальный перехват ошибок в хендлерах
    @dp.error()
    async def on_error(event: ErrorEvent) -> bool:
        log.exception("Ошибка в обработчике: %s", event.exception)
        try:
            await notify.notify_owner(f"⚠️ Ошибка: {html.escape(str(event.exception)[:300])}")
        except Exception:  # noqa: BLE001
            pass
        return True

    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Запуск polling")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Завершение по сигналу")
