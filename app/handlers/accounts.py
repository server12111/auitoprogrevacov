"""Раздел аккаунтов: подключение, статусы, управление."""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telethon.errors import PhoneCodeInvalidError

from app import crypto
from app.db import repo
from app.handlers.ui import esc, require_admin, show
from app.keyboards import back, confirm, paginate
from app.middlewares import is_admin
from app.services import account_manager as am
from app.services import transfer as transfer_svc
from app.states import Login
from app.utils import fmt_dt, fmt_left, fmt_time, now

router = Router()

STATUS_EMOJI = {
    "warming": "🔥", "active": "✅", "paused": "⏸", "floodwait": "⏳",
    "full": "📦", "loggedout": "❌", "banned": "🚫",
}
STATUS_TEXT = {
    "warming": "Прогрев", "active": "Активен", "paused": "Пауза",
    "floodwait": "Флудвейт", "full": "Заполнен", "loggedout": "Разлогинен",
    "banned": "Бан",
}


def _label(acc) -> str:
    em = STATUS_EMOJI.get(acc["status"], "•")
    return f"{em} {acc['phone']}"


PER_PAGE = 8


async def _render_list(cq: CallbackQuery, page: int, toast: str | None = None) -> None:
    accounts = await repo.list_accounts()
    top = [InlineKeyboardButton(text="➕ Подключить аккаунт", callback_data="acc:add")]
    if not accounts:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [top[0]], [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")]])
        await show(cq, "👤 <b>Аккаунты</b>\n\nПока нет ни одного. Подключи первый.", kb, toast)
        return
    kb = paginate(accounts, page, PER_PAGE, "acc", _label, lambda a: a["id"],
                  back_target="menu:main", columns=2, extra_top=top)
    await show(cq, f"👤 <b>Аккаунты</b> ({len(accounts)})\n\nВыбери аккаунт:", kb, toast)


@router.callback_query(F.data.startswith("acc:list:"))
async def acc_list(cq: CallbackQuery, state: FSMContext) -> None:
    # «Назад» из подключения аккаунта ведёт сюда — прерываем незавершённый вход
    if await state.get_state() is not None:
        await state.clear()
        am.cancel_login(cq.from_user.id)
    await _render_list(cq, int(cq.data.split(":")[2]))


async def _render_view(cq: CallbackQuery, acc_id: int, role: str, toast: str | None = None) -> None:
    acc = await repo.get_account(acc_id)
    if not acc:
        await show(cq, "Аккаунт не найден.", back("acc:list:0"), toast)
        return

    channels = await repo.channels_of_account(acc_id)
    status = acc["status"]
    lines = [
        f"{STATUS_EMOJI.get(status,'•')} <b>{esc(acc['phone'])}</b>",
        f"Статус: <b>{STATUS_TEXT.get(status, status)}</b>",
    ]
    if status == "warming" and acc["warmup_until"]:
        lines.append(f"🔥 Прогрев ещё {fmt_left(acc['warmup_until'])}")
    if status == "floodwait" and acc["flood_until"]:
        lines.append(f"⏳ Пауза до {fmt_time(acc['flood_until'])}")
    lines.append(f"Каналов: <b>{len(channels)}</b>")
    lines.append(f"Подключён: {fmt_dt(acc['added_at'])}")

    # 2FA / готовность к передаче
    if acc["twofa_set_at"]:
        lines.append(f"🔐 2FA с {fmt_dt(acc['twofa_set_at'])}")
        if transfer_svc.account_transfer_ready(acc):
            lines.append("🔓 Можно передавать")
        else:
            ready_at = transfer_svc.transfer_ready_at(acc)
            lines.append(f"🔐 Передача будет доступна {fmt_dt(ready_at)}")
    else:
        lines.append("🔐 2FA ещё не установлена (ставится после прогрева)")

    if acc["last_error"]:
        lines.append(f"\n⚠️ {esc(acc['last_error'])}")

    rows = []
    if is_admin(role):
        b = []
        if status == "paused":
            b.append(InlineKeyboardButton(text="▶️ Возобновить", callback_data=f"acc:resume:{acc_id}"))
        else:
            b.append(InlineKeyboardButton(text="⏸ Пауза", callback_data=f"acc:pause:{acc_id}"))
        b.append(InlineKeyboardButton(text="🔄 Проверить", callback_data=f"acc:check:{acc_id}"))
        rows.append(b)
        if acc["twofa_set_at"]:
            rows.append([InlineKeyboardButton(text="👁 Показать пароль 2FA",
                                              callback_data=f"acc:pass:{acc_id}")])
        rows.append([InlineKeyboardButton(text="🗑 Удалить", callback_data=f"acc:del:{acc_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="acc:list:0")])
    await show(cq, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows), toast)


@router.callback_query(F.data.startswith("acc:view:"))
async def acc_view(cq: CallbackQuery, role: str) -> None:
    await _render_view(cq, int(cq.data.split(":")[2]), role)


@router.callback_query(F.data.startswith("acc:pause:"))
async def acc_pause(cq: CallbackQuery, role: str) -> None:
    if not await require_admin(cq, role):
        return
    acc_id = int(cq.data.split(":")[2])
    await repo.set_account_status(acc_id, "paused")
    await _render_view(cq, acc_id, role, toast="Аккаунт на паузе")


@router.callback_query(F.data.startswith("acc:resume:"))
async def acc_resume(cq: CallbackQuery, role: str) -> None:
    if not await require_admin(cq, role):
        return
    acc_id = int(cq.data.split(":")[2])
    acc = await repo.get_account(acc_id)
    # если прогрев ещё не закончился — возвращаем в прогрев, иначе 2FA не поставится
    new_status = "warming" if acc and acc["warmup_until"] and acc["warmup_until"] > now() else "active"
    await repo.set_account_status(acc_id, new_status)
    await _render_view(cq, acc_id, role, toast="Аккаунт возобновлён")


@router.callback_query(F.data.startswith("acc:check:"))
async def acc_check(cq: CallbackQuery, role: str) -> None:
    if not await require_admin(cq, role):
        return
    acc_id = int(cq.data.split(":")[2])
    await cq.answer("Проверяю…")
    result = await am.check_session(acc_id)
    await cq.message.answer(esc(result), reply_markup=back(f"acc:view:{acc_id}"))


@router.callback_query(F.data.startswith("acc:pass:"))
async def acc_pass(cq: CallbackQuery, role: str) -> None:
    if not await require_admin(cq, role):
        return
    acc_id = int(cq.data.split(":")[2])
    acc = await repo.get_account(acc_id)
    pwd = crypto.decrypt(acc["twofa_enc"]) if acc and acc["twofa_enc"] else None
    await repo.log_action(cq.from_user.id, "view_2fa", f"acc={acc_id}")
    await cq.answer(f"Пароль 2FA: {pwd or 'нет'}", show_alert=True)


# Подтверждение регистрируем раньше, чем "acc:del:" — иначе тот перехватит "acc:del:yes:…"
@router.callback_query(F.data.startswith("acc:del:yes:"))
async def acc_del_yes(cq: CallbackQuery, role: str) -> None:
    if not await require_admin(cq, role):
        return
    acc_id = int(cq.data.split(":")[3])
    await am.disconnect(acc_id)
    await repo.delete_account(acc_id)
    await repo.log_action(cq.from_user.id, "delete_account", f"acc={acc_id}")
    await _render_list(cq, 0, toast="Удалён")


@router.callback_query(F.data.startswith("acc:del:"))
async def acc_del(cq: CallbackQuery, role: str) -> None:
    if not await require_admin(cq, role):
        return
    acc_id = int(cq.data.split(":")[2])
    await show(cq,
               "🗑 Удалить аккаунт из бота?\n\n"
               "Каналы в БД тоже удалятся из учёта (сами каналы в Telegram останутся).",
               confirm(f"acc:del:yes:{acc_id}", f"acc:view:{acc_id}"))


# ---------- Подключение аккаунта (FSM) ----------

@router.callback_query(F.data == "acc:add")
async def acc_add(cq: CallbackQuery, state: FSMContext, role: str) -> None:
    if not await require_admin(cq, role):
        return
    await state.set_state(Login.phone)
    await show(cq,
               "➕ <b>Подключение аккаунта</b>\n\n"
               "Отправь номер телефона в международном формате, например:\n"
               "<code>+380991234567</code>",
               back("acc:list:0"))


@router.message(Login.phone, F.text)
async def login_phone(message: Message, state: FSMContext) -> None:
    phone = am.normalize_phone(message.text)
    if len(phone) < 8:
        await message.answer("Не похоже на номер. Пример: <code>+380991234567</code>",
                             reply_markup=back("acc:list:0"))
        return
    try:
        await am.start_login(message.from_user.id, phone)
    except Exception as e:  # noqa: BLE001
        await message.answer(f"❌ Не удалось отправить код: {esc(e)}", reply_markup=back("acc:list:0"))
        await state.clear()
        return
    await state.update_data(phone=phone)
    await state.set_state(Login.code)
    await message.answer(
        "📩 Код отправлен в Telegram.\n\n"
        "Введи его <b>с пробелами между цифрами</b>, чтобы Telegram не заблокировал код, "
        "например: <code>1 2 3 4 5</code>",
        reply_markup=back("acc:list:0"),
    )


@router.message(Login.code, F.text)
async def login_code(message: Message, state: FSMContext) -> None:
    code = "".join(ch for ch in message.text if ch.isdigit())
    try:
        result = await am.submit_code(message.from_user.id, code)
    except PhoneCodeInvalidError:
        await message.answer("❌ Неверный код. Введи ещё раз:", reply_markup=back("acc:list:0"))
        return
    except Exception as e:  # noqa: BLE001
        am.cancel_login(message.from_user.id)
        await state.clear()
        await message.answer(f"❌ Ошибка: {esc(e)}\nПопробуй заново через «➕ Подключить».",
                             reply_markup=back("acc:list:0"))
        return
    if result == "need_2fa":
        await state.set_state(Login.twofa)
        await message.answer("🔐 На аккаунте включена 2FA. Введи пароль:",
                             reply_markup=back("acc:list:0"))
        return
    await state.clear()
    await message.answer("✅ Аккаунт подключён! Начинается прогрев.",
                         reply_markup=back("acc:list:0"))


@router.message(Login.twofa, F.text)
async def login_twofa(message: Message, state: FSMContext) -> None:
    pwd = message.text.strip()
    try:
        await message.delete()  # не оставляем пароль в переписке
    except Exception:  # noqa: BLE001
        pass
    try:
        await am.submit_2fa(message.from_user.id, pwd)
    except Exception as e:  # noqa: BLE001
        await message.answer(f"❌ {esc(e)}\nВведи пароль 2FA ещё раз:",
                             reply_markup=back("acc:list:0"))
        return
    await state.clear()
    await message.answer("✅ Аккаунт подключён! Начинается прогрев.",
                         reply_markup=back("acc:list:0"))
