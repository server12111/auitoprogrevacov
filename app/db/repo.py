"""Запросы к БД, сгруппированные по сущностям."""
import json
from typing import Any

import aiosqlite

from app.db import database as db
from app.utils import now, today_str

# ============ USERS ============

async def get_user(user_id: int) -> aiosqlite.Row | None:
    return await db.fetchone("SELECT * FROM users WHERE user_id=?", (user_id,))

async def get_role(user_id: int) -> str | None:
    row = await get_user(user_id)
    return row["role"] if row else None

async def add_user(user_id: int, role: str, username: str | None, added_by: int | None) -> None:
    await db.execute(
        "INSERT INTO users(user_id, role, username, added_by, added_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET role=excluded.role, username=excluded.username",
        (user_id, role, username, added_by, now()),
    )

async def remove_user(user_id: int) -> None:
    await db.execute("DELETE FROM users WHERE user_id=?", (user_id,))

async def set_role(user_id: int, role: str) -> None:
    await db.execute("UPDATE users SET role=? WHERE user_id=?", (role, user_id))

async def list_users() -> list[aiosqlite.Row]:
    return await db.fetchall("SELECT * FROM users ORDER BY added_at")


# ============ ACCOUNTS ============

async def add_account(phone: str, session_enc: str, twofa_enc: str | None,
                      tg_user_id: int | None, username: str | None,
                      warmup_until: int) -> int:
    return await db.execute(
        "INSERT INTO accounts(phone, session_enc, twofa_enc, tg_user_id, username, "
        "status, warmup_until, added_at, counter_date) VALUES(?,?,?,?,?,?,?,?,?)",
        (phone, session_enc, twofa_enc, tg_user_id, username,
         "warming", warmup_until, now(), today_str()),
    )

async def get_account(acc_id: int) -> aiosqlite.Row | None:
    return await db.fetchone("SELECT * FROM accounts WHERE id=?", (acc_id,))

async def get_account_by_phone(phone: str) -> aiosqlite.Row | None:
    return await db.fetchone("SELECT * FROM accounts WHERE phone=?", (phone,))

async def get_account_by_tg_id(tg_user_id: int) -> aiosqlite.Row | None:
    return await db.fetchone("SELECT * FROM accounts WHERE tg_user_id=?", (tg_user_id,))

async def relogin_account(acc_id: int, phone: str, session_enc: str, tg_user_id: int,
                          username: str | None, warmup_until: int) -> None:
    """Новая сессия для уже известного аккаунта: сессия свежая, поэтому заново прогрев
    и отсчёт возраста сессии (added_at) — иначе передача упадёт на «сессия слишком новая»."""
    await db.execute(
        "UPDATE accounts SET phone=?, session_enc=?, tg_user_id=?, username=?, status='warming', "
        "warmup_until=?, added_at=?, flood_until=NULL, last_error=NULL WHERE id=?",
        (phone, session_enc, tg_user_id, username, warmup_until, now(), acc_id),
    )

async def list_accounts() -> list[aiosqlite.Row]:
    return await db.fetchall("SELECT * FROM accounts ORDER BY id")

async def accounts_by_status(status: str) -> list[aiosqlite.Row]:
    return await db.fetchall("SELECT * FROM accounts WHERE status=? ORDER BY id", (status,))

async def set_account_status(acc_id: int, status: str, flood_until: int | None = None,
                             last_error: str | None = None) -> None:
    await db.execute(
        "UPDATE accounts SET status=?, flood_until=?, last_error=? WHERE id=?",
        (status, flood_until, last_error, acc_id),
    )

async def update_account_session(acc_id: int, session_enc: str) -> None:
    await db.execute("UPDATE accounts SET session_enc=? WHERE id=?", (session_enc, acc_id))

async def set_twofa(acc_id: int, twofa_enc: str, set_at: int) -> None:
    await db.execute(
        "UPDATE accounts SET twofa_enc=?, twofa_set_at=? WHERE id=?",
        (twofa_enc, set_at, acc_id),
    )

async def delete_account(acc_id: int) -> None:
    await db.execute("DELETE FROM accounts WHERE id=?", (acc_id,))

async def inc_created_today(acc_id: int) -> None:
    td = today_str()
    await db.execute(
        "UPDATE accounts SET "
        "created_today = CASE WHEN counter_date=? THEN created_today+1 ELSE 1 END, "
        "counter_date=? WHERE id=?",
        (td, td, acc_id),
    )

async def created_today(acc_id: int) -> int:
    row = await get_account(acc_id)
    if not row:
        return 0
    if row["counter_date"] != today_str():
        return 0
    return row["created_today"]


# ============ CHANNELS ============

async def add_channel(account_id: int, tg_channel_id: int, access_hash: int | None,
                      title: str, created_at: int) -> int:
    return await db.execute(
        "INSERT INTO channels(account_id, tg_channel_id, access_hash, title, created_at, status) "
        "VALUES(?,?,?,?,?, 'active')",
        (account_id, tg_channel_id, access_hash, title, created_at),
    )

async def get_channel(ch_id: int) -> aiosqlite.Row | None:
    return await db.fetchone("SELECT * FROM channels WHERE id=?", (ch_id,))

async def channels_of_account(account_id: int) -> list[aiosqlite.Row]:
    return await db.fetchall(
        "SELECT * FROM channels WHERE account_id=? ORDER BY created_at DESC", (account_id,)
    )

async def active_channels() -> list[aiosqlite.Row]:
    return await db.fetchall(
        "SELECT * FROM channels WHERE status IN ('active','reserved') ORDER BY created_at"
    )

async def set_channel_status(ch_id: int, status: str, reserved_until: int | None = None) -> None:
    await db.execute(
        "UPDATE channels SET status=?, reserved_until=? WHERE id=?",
        (status, reserved_until, ch_id),
    )

async def set_channel_invite(ch_id: int, link: str | None) -> None:
    await db.execute("UPDATE channels SET invite_link=? WHERE id=?", (link, ch_id))

async def mark_posted(ch_id: int, date_str: str) -> None:
    await db.execute("UPDATE channels SET last_post_date=? WHERE id=?", (date_str, ch_id))

async def count_channels(status: str | None = None) -> int:
    if status:
        return await db.fetchval("SELECT COUNT(*) FROM channels WHERE status=?", (status,), 0)
    return await db.fetchval("SELECT COUNT(*) FROM channels", (), 0)

async def count_created_since(ts: int) -> int:
    return await db.fetchval("SELECT COUNT(*) FROM channels WHERE created_at>=?", (ts,), 0)

async def expire_reservations() -> list[aiosqlite.Row]:
    """Вернуть просроченные резервы в active. Возвращает снятые каналы (с их инвайт-ссылками).

    Каналы, куда получатель уже вступил, но владение ещё не передано, не снимаются —
    они закреплены за покупателем, иначе их можно было бы продать второй раз.
    """
    rows = await db.fetchall(
        "SELECT * FROM channels c WHERE c.status='reserved' AND c.reserved_until IS NOT NULL "
        "AND c.reserved_until < ? AND NOT EXISTS ("
        "  SELECT 1 FROM transfer_channels tc WHERE tc.channel_id=c.id "
        "  AND tc.joined=1 AND tc.transferred=0)", (now(),)
    )
    ids = [r["id"] for r in rows]
    if ids:
        await db.execute(
            f"UPDATE channels SET status='active', reserved_until=NULL, invite_link=NULL "
            f"WHERE id IN ({','.join('?' * len(ids))})", ids
        )
    return rows


# ============ TASKS ============

async def enqueue(account_id: int, kind: str, payload: dict | None = None,
                  run_after: int = 0) -> int:
    return await db.execute(
        "INSERT INTO tasks(account_id, kind, payload, run_after, created_at) VALUES(?,?,?,?,?)",
        (account_id, kind, json.dumps(payload or {}), run_after, now()),
    )

async def enqueue_many(items: list[tuple[int, str, dict, int]]) -> None:
    """Массовая постановка задач одной транзакцией: (account_id, kind, payload, run_after)."""
    ts = now()
    await db.executemany(
        "INSERT INTO tasks(account_id, kind, payload, run_after, created_at) VALUES(?,?,?,?,?)",
        [(a, k, json.dumps(p or {}), r, ts) for a, k, p, r in items],
    )

async def pending_post_channel_ids() -> set[int]:
    """id каналов, для которых уже стоит pending-задача поста."""
    rows = await db.fetchall(
        "SELECT json_extract(payload, '$.channel_id') AS ch FROM tasks "
        "WHERE status='pending' AND kind='post'"
    )
    return {r["ch"] for r in rows if r["ch"] is not None}

async def pending_count(account_id: int, kind: str) -> int:
    return await db.fetchval(
        "SELECT COUNT(*) FROM tasks WHERE account_id=? AND status='pending' AND kind=?",
        (account_id, kind), 0,
    )

async def cleanup_tasks(older_than: int) -> None:
    """Удалить завершённые/проваленные задачи старше older_than (unix) — иначе таблица растёт вечно."""
    await db.execute(
        "DELETE FROM tasks WHERE status IN ('done','failed') AND created_at < ?", (older_than,)
    )

async def pending_task_for_account(account_id: int) -> aiosqlite.Row | None:
    return await db.fetchone(
        "SELECT * FROM tasks WHERE account_id=? AND status='pending' AND run_after<=? "
        "ORDER BY run_after LIMIT 1",
        (account_id, now()),
    )

async def mark_task(task_id: int, status: str, error: str | None = None) -> None:
    await db.execute(
        "UPDATE tasks SET status=?, last_error=? WHERE id=?", (status, error, task_id)
    )

async def retry_task(task_id: int, run_after: int, error: str | None) -> None:
    await db.execute(
        "UPDATE tasks SET attempts=attempts+1, run_after=?, last_error=?, status='pending' WHERE id=?",
        (run_after, error, task_id),
    )

async def defer_account_tasks(account_id: int, run_after: int, kind: str | None = None,
                              exclude_kind: str | None = None) -> None:
    """Отложить pending-задачи аккаунта (например после флудвейта).

    kind — только задачи этого типа; exclude_kind — все, кроме этого типа.
    """
    sql = "UPDATE tasks SET run_after=? WHERE account_id=? AND status='pending' AND run_after<?"
    params: list = [run_after, account_id, run_after]
    if kind is not None:
        sql += " AND kind=?"
        params.append(kind)
    if exclude_kind is not None:
        sql += " AND kind<>?"
        params.append(exclude_kind)
    await db.execute(sql, params)

async def count_pending_tasks() -> int:
    return await db.fetchval("SELECT COUNT(*) FROM tasks WHERE status='pending'", (), 0)


# ============ TRANSFERS ============

async def create_transfer(recipient: str, day: int, qty: int, admin_id: int) -> int:
    return await db.execute(
        "INSERT INTO transfers(recipient, day_number, requested_qty, admin_id, status, created_at) "
        "VALUES(?,?,?,?, 'reserved', ?)",
        (recipient, day, qty, admin_id, now()),
    )

async def add_transfer_channel(transfer_id: int, channel_id: int, invite_link: str) -> None:
    await db.execute(
        "INSERT OR REPLACE INTO transfer_channels(transfer_id, channel_id, invite_link) VALUES(?,?,?)",
        (transfer_id, channel_id, invite_link),
    )

async def transfer_channels(transfer_id: int) -> list[aiosqlite.Row]:
    return await db.fetchall(
        "SELECT tc.*, c.title, c.account_id, c.tg_channel_id, c.access_hash "
        "FROM transfer_channels tc JOIN channels c ON c.id=tc.channel_id "
        "WHERE tc.transfer_id=?", (transfer_id,)
    )

async def set_tc_joined(transfer_id: int, channel_id: int, joined: bool) -> None:
    await db.execute(
        "UPDATE transfer_channels SET joined=? WHERE transfer_id=? AND channel_id=?",
        (1 if joined else 0, transfer_id, channel_id),
    )

async def set_tc_transferred(transfer_id: int, channel_id: int, ok: bool, error: str | None) -> None:
    await db.execute(
        "UPDATE transfer_channels SET transferred=?, error=? WHERE transfer_id=? AND channel_id=?",
        (1 if ok else 0, error, transfer_id, channel_id),
    )

async def get_transfer(transfer_id: int) -> aiosqlite.Row | None:
    return await db.fetchone("SELECT * FROM transfers WHERE id=?", (transfer_id,))

async def update_transfer(transfer_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    await db.execute(f"UPDATE transfers SET {cols} WHERE id=?", (*fields.values(), transfer_id))

async def list_transfers(limit: int = 20) -> list[aiosqlite.Row]:
    return await db.fetchall(
        "SELECT * FROM transfers ORDER BY created_at DESC LIMIT ?", (limit,)
    )


# ============ ADMIN LOG ============

async def log_action(admin_id: int, action: str, details: str = "") -> None:
    await db.execute(
        "INSERT INTO admin_log(admin_id, action, details, created_at) VALUES(?,?,?,?)",
        (admin_id, action, details, now()),
    )


# ============ HISTORY / STATS ============

async def snapshot_channels(date_str: str, total: int, created: int) -> None:
    await db.execute(
        "INSERT INTO channel_history(date, total, created) VALUES(?,?,?) "
        "ON CONFLICT(date) DO UPDATE SET total=excluded.total, created=excluded.created",
        (date_str, total, created),
    )

async def history_rows(days: int = 30) -> list[aiosqlite.Row]:
    return await db.fetchall(
        "SELECT date, total, created FROM channel_history ORDER BY date DESC LIMIT ?", (days,)
    )

async def day_distribution() -> list[aiosqlite.Row]:
    """Все активные каналы с датой создания (для расчёта распределения по дням)."""
    return await db.fetchall(
        "SELECT c.id, c.created_at, c.account_id, c.status, a.status AS acc_status, "
        "a.twofa_set_at, a.added_at AS acc_added "
        "FROM channels c JOIN accounts a ON a.id=c.account_id "
        "WHERE c.status='active'"
    )
