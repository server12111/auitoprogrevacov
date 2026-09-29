"""Автобэкап БД раз в сутки, хранение последних 7."""
import sqlite3
from datetime import datetime

from app import config
from app.logger import log


def make_backup() -> None:
    """Синхронная функция: вызывать в отдельном потоке (asyncio.to_thread / executor планировщика).

    Используем backup API SQLite, а не копирование файла: в режиме WAL свежие изменения
    лежат в bot.db-wal, и простая копия bot.db была бы неполной.
    """
    config.ensure_dirs()
    if not config.DB_PATH.exists():
        return
    stamp = datetime.now(config.TZ).strftime("%Y%m%d_%H%M%S")
    dst = config.BACKUPS_DIR / f"bot_{stamp}.db"
    try:
        src = sqlite3.connect(config.DB_PATH)
        out = sqlite3.connect(dst)
        try:
            src.backup(out)
        finally:
            out.close()
            src.close()
        log.info("Бэкап БД: %s", dst.name)
    except Exception as e:  # noqa: BLE001
        log.error("Ошибка бэкапа: %s", e)
        try:
            dst.unlink(missing_ok=True)
        except OSError:
            pass
        return
    # Чистим старые
    backups = sorted(config.BACKUPS_DIR.glob("bot_*.db"))
    for old in backups[:-7]:
        try:
            old.unlink()
        except OSError:
            pass
