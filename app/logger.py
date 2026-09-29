"""Логирование с ротацией в DATA_DIR/logs."""
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler

from app import config


def setup_logging() -> logging.Logger:
    config.ensure_dirs()
    logger = logging.getLogger("bot")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Время в логе — по часовому поясу бота (config.TZ), а не системному:
    # на Windows переменная TZ=Europe/Kiev сбивает time.localtime и лог уходит в UTC
    fmt.converter = lambda ts: datetime.fromtimestamp(ts, config.TZ).timetuple()

    file_handler = RotatingFileHandler(
        config.LOGS_DIR / "bot.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)

    logger.addHandler(file_handler)
    logger.addHandler(stream)

    # Приглушаем шумные библиотеки
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.INFO)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    return logger


log = setup_logging()
