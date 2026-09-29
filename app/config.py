"""Конфигурация из переменных окружения и пути внутри DATA_DIR."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Не задана обязательная переменная окружения: {name}")
    return val


BOT_TOKEN: str = _require("BOT_TOKEN")
API_ID: int = int(_require("API_ID"))
API_HASH: str = _require("API_HASH")
OWNER_ID: int = int(_require("OWNER_ID"))
ENCRYPTION_KEY: str = _require("ENCRYPTION_KEY")

TZ_NAME: str = os.getenv("TZ", "Europe/Kiev")
TZ = ZoneInfo(TZ_NAME)

# Все данные (БД, сессии, бэкапы) — в /app/data; локально можно переопределить через DATA_DIR
DATA_DIR = Path(os.getenv("DATA_DIR") or "/app/data")

# --- Пути внутри DATA_DIR (единственное место хранения данных) ---
DB_PATH = DATA_DIR / "bot.db"
SESSIONS_DIR = DATA_DIR / "sessions"
BACKGROUNDS_DIR = DATA_DIR / "backgrounds"
FONTS_DIR = DATA_DIR / "fonts"
LOGS_DIR = DATA_DIR / "logs"
BACKUPS_DIR = DATA_DIR / "backups"
HEARTBEAT_FILE = DATA_DIR / "heartbeat"


def ensure_dirs() -> None:
    for d in (DATA_DIR, SESSIONS_DIR, BACKGROUNDS_DIR, FONTS_DIR, LOGS_DIR, BACKUPS_DIR):
        d.mkdir(parents=True, exist_ok=True)
