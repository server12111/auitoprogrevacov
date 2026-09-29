"""Управление Telethon-клиентами аккаунтов.

Клиенты создаются из зашифрованной StringSession, хранятся в памяти по id
аккаунта. Здесь же — вход по номеру/коду/2FA и установка 2FA после прогрева.
"""
import asyncio
import secrets
import string

from telethon import TelegramClient
from telethon.errors import (
    AuthKeyUnregisteredError,
    FloodWaitError,
    PasswordHashInvalidError,
    SessionPasswordNeededError,
    SessionRevokedError,
    UserDeactivatedBanError,
    UserDeactivatedError,
)
from telethon.sessions import StringSession

from app import config, crypto
from app.db import repo
from app.logger import log
from app.utils import now

# Кэш подключённых клиентов: acc_id -> TelegramClient
_clients: dict[int, TelegramClient] = {}
# Временные клиенты во время интерактивного входа: user_id админа -> данные
_login_sessions: dict[int, dict] = {}
# Блокировки на создание клиента, чтобы параллельные вызовы не плодили подключения
_client_locks: dict[int, asyncio.Lock] = {}


def _new_client(session: StringSession) -> TelegramClient:
    return TelegramClient(session, config.API_ID, config.API_HASH)


async def get_client(acc_id: int) -> TelegramClient | None:
    """Вернуть подключённый клиент аккаунта (создаёт и подключает при необходимости).

    None — клиента сейчас нет. Статус loggedout/banned ставится только когда Telegram
    явно сообщил, что сессия мертва; сетевые сбои статус не меняют.
    """
    client = _clients.get(acc_id)
    if client is not None and client.is_connected():
        return client

    lock = _client_locks.setdefault(acc_id, asyncio.Lock())
    async with lock:
        # пока ждали блокировку, клиент мог создать другой вызов
        client = _clients.get(acc_id)
        if client is not None and client.is_connected():
            return client
        if client is not None:  # отвалившийся клиент — закрываем, чтобы не течь
            _clients.pop(acc_id, None)
            await _safe_disconnect(client)

        acc = await repo.get_account(acc_id)
        if not acc or not acc["session_enc"]:
            return None

        session_str = crypto.decrypt(acc["session_enc"])
        if not session_str:
            await repo.set_account_status(
                acc_id, "loggedout",
                last_error="Не удалось расшифровать сессию (сменили ENCRYPTION_KEY?)")
            return None

        client = _new_client(StringSession(session_str))
        try:
            await client.connect()
            authorized = await client.is_user_authorized()
        except (AuthKeyUnregisteredError, SessionRevokedError):
            await _safe_disconnect(client)
            await repo.set_account_status(acc_id, "loggedout", last_error="Сессия отозвана")
            return None
        except (UserDeactivatedError, UserDeactivatedBanError):
            await _safe_disconnect(client)
            await repo.set_account_status(acc_id, "banned", last_error="Аккаунт удалён/забанен")
            return None
        except Exception as e:  # noqa: BLE001
            await _safe_disconnect(client)
            log.error("Аккаунт %s: ошибка подключения: %s", acc_id, e)
            return None

        if not authorized:
            await _safe_disconnect(client)
            await repo.set_account_status(acc_id, "loggedout", last_error="Не авторизован")
            return None

        _clients[acc_id] = client
        return client


async def disconnect(acc_id: int) -> None:
    client = _clients.pop(acc_id, None)
    if client:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass


async def disconnect_all() -> None:
    for acc_id in list(_clients):
        await disconnect(acc_id)


# ---------- Интерактивный вход ----------

def normalize_phone(raw: str) -> str:
    """'+38 (099) 123-45-67' -> '+380991234567'."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    return f"+{digits}" if digits else ""


async def start_login(admin_id: int, phone: str) -> str:
    """Шаг 1: отправить код. Возвращает 'code_sent'."""
    cancel_login(admin_id)  # незавершённый прошлый вход — закрываем его клиент
    client = _new_client(StringSession())
    try:
        await client.connect()
        sent = await client.send_code_request(phone)
    except Exception:
        await _safe_disconnect(client)
        raise
    _login_sessions[admin_id] = {
        "client": client,
        "phone": phone,
        "hash": sent.phone_code_hash,
    }
    return "code_sent"


async def submit_code(admin_id: int, code: str) -> str:
    """Шаг 2: подтвердить код. Возвращает 'ok' / 'need_2fa'."""
    data = _login_sessions.get(admin_id)
    if not data:
        raise RuntimeError("Сессия входа не найдена, начните заново")
    client: TelegramClient = data["client"]
    try:
        await client.sign_in(phone=data["phone"], code=code, phone_code_hash=data["hash"])
    except SessionPasswordNeededError:
        return "need_2fa"
    await _finalize_login(admin_id, twofa_password=None)
    return "ok"


async def submit_2fa(admin_id: int, password: str) -> str:
    data = _login_sessions.get(admin_id)
    if not data:
        raise RuntimeError("Сессия входа не найдена, начните заново")
    client: TelegramClient = data["client"]
    try:
        await client.sign_in(password=password)
    except PasswordHashInvalidError:
        raise RuntimeError("Неверный пароль 2FA")
    await _finalize_login(admin_id, twofa_password=password)
    return "ok"


async def _finalize_login(admin_id: int, twofa_password: str | None) -> int:
    from app import settings
    data = _login_sessions.pop(admin_id)
    client: TelegramClient = data["client"]
    phone = data["phone"]

    me = await client.get_me()
    session_str = client.session.save()

    warmup_until = now() + settings.get("warmup_hours") * 3600

    # Тот же аккаунт мог быть добавлен с другой записью номера — ищем и по tg id
    existing = await repo.get_account_by_tg_id(me.id) or await repo.get_account_by_phone(phone)
    twofa_enc = crypto.encrypt(twofa_password) if twofa_password else None
    if existing:
        acc_id = existing["id"]
        await disconnect(acc_id)  # старый клиент со старой сессией больше не нужен
        await repo.relogin_account(acc_id, phone, crypto.encrypt(session_str),
                                   me.id, me.username, warmup_until)
        if twofa_enc:
            # 2FA уже была — сохраняем прежнюю дату установки, если она известна
            await repo.set_twofa(acc_id, twofa_enc, existing["twofa_set_at"] or now())
    else:
        acc_id = await repo.add_account(
            phone=phone,
            session_enc=crypto.encrypt(session_str),
            twofa_enc=twofa_enc,
            tg_user_id=me.id,
            username=me.username,
            warmup_until=warmup_until,
        )
        if twofa_password:
            # 2FA уже была включена пользователем — считаем set_at = сейчас
            await repo.set_twofa(acc_id, twofa_enc, now())

    _clients[acc_id] = client
    log.info("Аккаунт %s (%s) подключён", acc_id, phone)
    return acc_id


def cancel_login(admin_id: int) -> None:
    data = _login_sessions.pop(admin_id, None)
    if data:
        client = data["client"]
        asyncio.create_task(_safe_disconnect(client))


async def _safe_disconnect(client: TelegramClient) -> None:
    try:
        await client.disconnect()
    except Exception:  # noqa: BLE001
        pass


# ---------- 2FA ----------

# acc_id -> когда можно снова пытаться поставить 2FA после неудачи
_twofa_retry_at: dict[int, int] = {}

def _gen_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def ensure_2fa(acc_id: int) -> bool:
    """Установить 2FA, если её нет. Возвращает True при успехе/уже установлена."""
    from app import settings
    acc = await repo.get_account(acc_id)
    if not acc:
        return False
    if acc["twofa_set_at"]:
        return True  # уже есть

    if now() < _twofa_retry_at.get(acc_id, 0):
        return False

    client = await get_client(acc_id)
    if client is None:
        return False

    if settings.get("twofa_mode") == "per_account":
        password = _gen_password()
    else:
        password = settings.get("twofa_shared_password") or _gen_password()

    hint = settings.get("twofa_hint") or None
    try:
        await client.edit_2fa(new_password=password, hint=hint)
    except Exception as e:  # noqa: BLE001
        log.error("Аккаунт %s: не удалось поставить 2FA: %s", acc_id, e)
        _twofa_retry_at[acc_id] = now() + 3600  # повторим через час
        return False

    _twofa_retry_at.pop(acc_id, None)
    await repo.set_twofa(acc_id, crypto.encrypt(password), now())
    log.info("Аккаунт %s: 2FA установлена", acc_id)
    return True


async def check_session(acc_id: int) -> str:
    """Проверить сессию, вернуть статус-строку для UI."""
    client = await get_client(acc_id)
    if client is None:
        return "❌ Сессия недоступна / разлогинен"
    try:
        me = await client.get_me()
        return f"✅ Живая сессия: {me.first_name} (@{me.username})" if me else "❌ Нет данных"
    except FloodWaitError as e:
        return f"⏳ Флудвейт: {e.seconds} сек"
    except Exception as e:  # noqa: BLE001
        return f"❌ Ошибка: {e}"
