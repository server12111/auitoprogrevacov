"""Слой БД: одно общее подключение, режим WAL, сериализация записи.

Одно подключение aiosqlite обслуживается одним фоновым потоком, поэтому все
запросы к нему уже выполняются по очереди и ошибки "database is locked" не
возникают. Дополнительно оборачиваем запись в asyncio.Lock, чтобы держать
транзакции атомарными, и включаем WAL для быстрых параллельных чтений.
"""
import asyncio
from pathlib import Path
from typing import Any, Iterable, Sequence

import aiosqlite

from app import config
from app.logger import log

_conn: aiosqlite.Connection | None = None
_write_lock = asyncio.Lock()


async def init() -> None:
    global _conn
    config.ensure_dirs()
    _conn = await aiosqlite.connect(config.DB_PATH)
    _conn.row_factory = aiosqlite.Row

    # Настройки производительности
    await _conn.execute("PRAGMA journal_mode=WAL")
    await _conn.execute("PRAGMA synchronous=NORMAL")
    await _conn.execute("PRAGMA busy_timeout=5000")
    await _conn.execute("PRAGMA foreign_keys=ON")
    await _conn.execute("PRAGMA temp_store=MEMORY")
    await _conn.execute("PRAGMA cache_size=-8000")  # ~8 МБ кэша

    schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    await _conn.executescript(schema)
    await _conn.commit()
    log.info("БД инициализирована: %s", config.DB_PATH)


async def close() -> None:
    global _conn
    if _conn is not None:
        await _conn.commit()
        await _conn.close()
        _conn = None


def _c() -> aiosqlite.Connection:
    if _conn is None:
        raise RuntimeError("БД не инициализирована")
    return _conn


# ---------- Чтение ----------

async def fetchone(sql: str, params: Sequence[Any] = ()) -> aiosqlite.Row | None:
    async with _c().execute(sql, params) as cur:
        return await cur.fetchone()


async def fetchall(sql: str, params: Sequence[Any] = ()) -> list[aiosqlite.Row]:
    async with _c().execute(sql, params) as cur:
        return list(await cur.fetchall())


async def fetchval(sql: str, params: Sequence[Any] = (), default: Any = None) -> Any:
    row = await fetchone(sql, params)
    if row is None:
        return default
    return row[0]


# ---------- Запись (сериализована) ----------

async def execute(sql: str, params: Sequence[Any] = ()) -> int:
    """Выполнить один запрос записи. Возвращает lastrowid."""
    async with _write_lock:
        cur = await _c().execute(sql, params)
        await _c().commit()
        return cur.lastrowid


async def executemany(sql: str, seq: Iterable[Sequence[Any]]) -> None:
    """Массовая запись одной транзакцией."""
    async with _write_lock:
        await _c().executemany(sql, seq)
        await _c().commit()


async def execute_script(statements: list[tuple[str, Sequence[Any]]]) -> None:
    """Несколько запросов в одной транзакции (атомарно)."""
    async with _write_lock:
        try:
            for sql, params in statements:
                await _c().execute(sql, params)
            await _c().commit()
        except Exception:
            await _c().rollback()
            raise
