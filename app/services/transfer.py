"""Передача приватных каналов получателю.

Готовность аккаунта к передаче: 2FA включена >= 7 дней и сессия старше 24 часов
(требования Telegram для смены владельца). Шаги:
  1) резерв каналов + одноразовые инвайт-ссылки;
  2) проверка вступления получателя;
  3) выдача прав админа -> передача владения (с паролем 2FA) -> выход старого аккаунта.
"""
import asyncio
import random

from telethon import functions
from telethon.errors import FloodWaitError
from telethon.password import compute_check
from telethon.tl.types import ChatAdminRights, InputPeerChannel

from app import crypto, settings
from app.db import repo
from app.logger import log
from app.utils import day_number, now

WEEK = 7 * 24 * 3600
DAY = 24 * 3600


def account_transfer_ready(acc) -> bool:
    if acc["status"] in ("banned", "loggedout"):
        return False
    if not acc["twofa_set_at"]:
        return False
    if now() - acc["twofa_set_at"] < WEEK:
        return False
    if now() - acc["added_at"] < DAY:
        return False
    return True


def transfer_ready_at(acc) -> int | None:
    """Момент, когда аккаунт станет готов к передаче."""
    if not acc["twofa_set_at"]:
        return None
    return max(acc["twofa_set_at"] + WEEK, acc["added_at"] + DAY)


async def find_eligible(day: int, ready_only: bool = True,
                        min_day: int | None = None) -> list:
    """Активные, не зарезервированные каналы нужного дня (или >= min_day)."""
    rows = await repo.active_channels()
    accounts = {a["id"]: a for a in await repo.list_accounts()}
    result = []
    for ch in rows:
        if ch["status"] != "active":
            continue
        d = day_number(ch["created_at"])
        if min_day is not None:
            if d < min_day:
                continue
        elif d != day:
            continue
        acc = accounts.get(ch["account_id"])
        if not acc:
            continue
        if ready_only and not account_transfer_ready(acc):
            continue
        result.append(ch)
    # Сначала самые «молодые» (ближе всего к запрошенному дню), потом старше —
    # иначе при добор «старше» уходили бы самые старые и ценные каналы
    result.sort(key=lambda c: c["created_at"], reverse=True)
    return result


async def _throttle() -> None:
    lo = settings.get("transfer_delay_min")
    hi = settings.get("transfer_delay_max")
    await asyncio.sleep(random.uniform(lo, hi))


async def reserve_and_link(from_am, transfer_id: int, channels: list) -> list[dict]:
    """Зарезервировать каналы и создать одноразовые инвайт-ссылки."""
    reserve_until = now() + settings.get("reserve_hours") * 3600
    ttl = settings.get("invite_ttl_hours") * 3600
    out = []
    for ch in channels:
        client = await from_am.get_client(ch["account_id"])
        if client is None:
            out.append({"channel_id": ch["id"], "error": "нет клиента"})
            continue
        peer = InputPeerChannel(ch["tg_channel_id"], ch["access_hash"])
        try:
            res = await client(functions.messages.ExportChatInviteRequest(
                peer=peer,
                expire_date=now() + ttl,
                usage_limit=1,
            ))
            link = res.link
        except FloodWaitError:
            raise
        except Exception as e:  # noqa: BLE001
            out.append({"channel_id": ch["id"], "error": str(e)})
            continue

        await repo.set_channel_status(ch["id"], "reserved", reserved_until=reserve_until)
        await repo.set_channel_invite(ch["id"], link)
        await repo.add_transfer_channel(transfer_id, ch["id"], link)
        out.append({"channel_id": ch["id"], "link": link, "title": ch["title"]})
        await _throttle()
    return out


async def check_joins(from_am, transfer_id: int, recipient: str) -> tuple[int, int]:
    """Проверить, в какие каналы получатель уже вступил. Возвращает (вступил, всего)."""
    tcs = await repo.transfer_channels(transfer_id)
    joined = 0
    for tc in tcs:
        client = await from_am.get_client(tc["account_id"])
        if client is None:
            continue
        peer = InputPeerChannel(tc["tg_channel_id"], tc["access_hash"])
        try:
            user = await client.get_entity(recipient)
            await client(functions.channels.GetParticipantRequest(channel=peer, participant=user))
            await repo.set_tc_joined(transfer_id, tc["channel_id"], True)
            joined += 1
        except Exception:  # noqa: BLE001
            await repo.set_tc_joined(transfer_id, tc["channel_id"], False)
    return joined, len(tcs)


async def transfer_joined(from_am, transfer_id: int, recipient: str) -> dict:
    """Передать владение по всем каналам, куда получатель вступил."""
    tcs = await repo.transfer_channels(transfer_id)
    ok, failed = 0, 0
    leave_after = settings.get("leave_after_transfer")

    admin_rights = ChatAdminRights(
        change_info=True, post_messages=True, edit_messages=True,
        delete_messages=True, ban_users=True, invite_users=True,
        pin_messages=True, add_admins=True, manage_call=True,
    )

    for tc in tcs:
        if not tc["joined"] or tc["transferred"]:
            continue
        acc = await repo.get_account(tc["account_id"])
        if acc is None:
            await repo.set_tc_transferred(transfer_id, tc["channel_id"], False, "аккаунт удалён")
            failed += 1
            continue
        client = await from_am.get_client(tc["account_id"])
        if client is None:
            await repo.set_tc_transferred(transfer_id, tc["channel_id"], False, "нет клиента")
            failed += 1
            continue

        peer = InputPeerChannel(tc["tg_channel_id"], tc["access_hash"])
        try:
            user = await client.get_entity(recipient)

            # 1. Права админа получателю
            await client(functions.channels.EditAdminRequest(
                channel=peer, user_id=user, admin_rights=admin_rights, rank="owner",
            ))
            await asyncio.sleep(random.uniform(2, 5))

            # 2. Передача владения (нужен пароль 2FA -> SRP)
            password = crypto.decrypt(acc["twofa_enc"])
            if not password:
                raise RuntimeError("нет пароля 2FA")
            pwd = await client(functions.account.GetPasswordRequest())
            srp = compute_check(pwd, password)
            await client(functions.channels.EditCreatorRequest(
                channel=peer, user_id=user, password=srp,
            ))

            # Владение уже у получателя — фиксируем СРАЗУ, до выхода из канала.
            # Если выход упадёт, канал всё равно не вернётся в продажу.
            await repo.set_channel_status(tc["channel_id"], "transferred")
            await repo.set_tc_transferred(transfer_id, tc["channel_id"], True, None)
            ok += 1
            log.info("Канал %s передан %s", tc["channel_id"], recipient)

            # 3. Старый аккаунт покидает канал (освобождает лимит)
            if leave_after:
                await asyncio.sleep(random.uniform(2, 4))
                try:
                    await client(functions.channels.LeaveChannelRequest(peer))
                    if acc["status"] == "full":
                        # место под новые каналы освободилось
                        await repo.set_account_status(acc["id"], "active")
                except FloodWaitError:
                    raise
                except Exception as e:  # noqa: BLE001
                    log.warning("Канал %s передан, но выйти не удалось: %s", tc["channel_id"], e)

        except FloodWaitError:
            raise
        except Exception as e:  # noqa: BLE001
            await repo.set_tc_transferred(transfer_id, tc["channel_id"], False, str(e)[:200])
            failed += 1
            log.error("Канал %s: ошибка передачи: %s", tc["channel_id"], e)

        await _throttle()

    delivered = await repo.transfer_channels(transfer_id)
    total_ok = sum(1 for t in delivered if t["transferred"])
    await repo.update_transfer(
        transfer_id, delivered_qty=total_ok,
        status="done", finished_at=now(),
    )
    return {"ok": ok, "failed": failed, "total": len(tcs)}
