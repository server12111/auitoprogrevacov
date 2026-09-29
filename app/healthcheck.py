"""Healthcheck: проверяет свежесть heartbeat-файла, который пишет главный цикл."""
import sys
import time

from app import config


def main() -> int:
    try:
        mtime = config.HEARTBEAT_FILE.stat().st_mtime
    except FileNotFoundError:
        return 1
    # Считаем здоровым, если heartbeat обновлялся в последние 3 минуты
    if time.time() - mtime > 180:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
