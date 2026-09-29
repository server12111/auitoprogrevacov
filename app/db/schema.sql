-- Схема БД. Все таблицы создаются при старте (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS users (
    user_id      INTEGER PRIMARY KEY,
    role         TEXT NOT NULL DEFAULT 'user',   -- owner / admin / user
    username     TEXT,
    added_by     INTEGER,
    added_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    phone         TEXT UNIQUE NOT NULL,
    tg_user_id    INTEGER,
    username      TEXT,
    session_enc   TEXT,                 -- зашифрованная StringSession
    twofa_enc     TEXT,                 -- зашифрованный пароль 2FA
    twofa_set_at  INTEGER,              -- когда 2FA была установлена/подтверждена
    status        TEXT NOT NULL DEFAULT 'warming', -- warming/active/paused/floodwait/full/loggedout/banned
    flood_until   INTEGER,              -- до какого времени аккаунт в паузе (unix)
    warmup_until  INTEGER,              -- до какого времени длится прогрев
    created_today INTEGER NOT NULL DEFAULT 0,      -- счётчик созданных сегодня каналов
    counter_date  TEXT,                 -- дата счётчика (YYYY-MM-DD)
    added_at      INTEGER NOT NULL,
    last_error    TEXT
);
CREATE INDEX IF NOT EXISTS idx_accounts_status ON accounts(status);

CREATE TABLE IF NOT EXISTS channels (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id    INTEGER NOT NULL,
    tg_channel_id INTEGER,              -- id канала в телеграме
    access_hash   INTEGER,
    title         TEXT,
    created_at    INTEGER NOT NULL,     -- дата создания (unix); из неё считается "День N"
    status        TEXT NOT NULL DEFAULT 'active', -- active/reserved/transferred/dead
    reserved_until INTEGER,             -- до какого времени зарезервирован под передачу
    last_post_date TEXT,                -- дата последнего успешного поста (YYYY-MM-DD)
    invite_link   TEXT,                 -- текущая инвайт-ссылка (для передачи)
    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_channels_account ON channels(account_id);
CREATE INDEX IF NOT EXISTS idx_channels_status ON channels(status);
CREATE INDEX IF NOT EXISTS idx_channels_created ON channels(created_at);

-- Очередь задач для каждого аккаунта (переживает перезапуск).
CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id    INTEGER NOT NULL,
    kind          TEXT NOT NULL,        -- create_channel / post / set_2fa / transfer_step ...
    payload       TEXT,                 -- JSON с параметрами
    run_after     INTEGER NOT NULL DEFAULT 0,  -- не запускать раньше этого времени
    attempts      INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'pending', -- pending/done/failed
    created_at    INTEGER NOT NULL,
    last_error    TEXT,
    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tasks_pending ON tasks(status, run_after);
CREATE INDEX IF NOT EXISTS idx_tasks_account ON tasks(account_id, status);

-- История передач каналов.
CREATE TABLE IF NOT EXISTS transfers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient     TEXT NOT NULL,        -- @username получателя
    day_number    INTEGER,
    requested_qty INTEGER,
    delivered_qty INTEGER DEFAULT 0,
    admin_id      INTEGER,
    status        TEXT NOT NULL DEFAULT 'reserved', -- reserved/awaiting_join/done/cancelled
    created_at    INTEGER NOT NULL,
    finished_at   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_transfers_status ON transfers(status);

-- Связка каналов с передачей.
CREATE TABLE IF NOT EXISTS transfer_channels (
    transfer_id   INTEGER NOT NULL,
    channel_id    INTEGER NOT NULL,
    invite_link   TEXT,
    joined        INTEGER NOT NULL DEFAULT 0, -- получатель вступил
    transferred   INTEGER NOT NULL DEFAULT 0, -- владение передано
    error         TEXT,
    PRIMARY KEY(transfer_id, channel_id)
);

-- Пары ключ/значение для настроек.
CREATE TABLE IF NOT EXISTS settings (
    key           TEXT PRIMARY KEY,
    value         TEXT
);

-- Журнал действий админов.
CREATE TABLE IF NOT EXISTS admin_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id      INTEGER,
    action        TEXT,
    details       TEXT,
    created_at    INTEGER NOT NULL
);

-- Ежедневный снимок количества каналов (для графика роста).
CREATE TABLE IF NOT EXISTS channel_history (
    date          TEXT PRIMARY KEY,     -- YYYY-MM-DD
    total         INTEGER NOT NULL,
    created       INTEGER NOT NULL DEFAULT 0
);
