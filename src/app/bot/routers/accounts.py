from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.exceptions import TelegramBadRequest

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)

from app.bot.middlewares import authorized_filter
from app.config import settings
from app.core.db import get_session
from app.core.logging import get_logger
from app.domain.dto import TelethonAccountDTO
from app.domain.repositories import TelethonAccountRepository
from app.domain.services.account_manager import account_manager

router = Router()
logger = get_logger(__name__)


class AccountStates(StatesGroup):
    waiting_name = State()
    waiting_phone = State()
    waiting_api_id = State()
    waiting_api_hash = State()
    waiting_session = State()


class PhoneLoginStates(StatesGroup):
    waiting_name = State()
    waiting_phone = State()
    waiting_code = State()
    waiting_password = State()


_login_clients: dict[int, dict] = {}


async def _cleanup_login(user_id: int) -> None:
    entry = _login_clients.pop(user_id, None)
    if entry:
        client = entry.get("client")
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass


def accounts_keyboard(accounts=None):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows = []
    for acc in accounts or []:
        label = ("✅ " if acc.is_active else "❌ ") + acc.name + (" ⭐" if acc.is_primary else "")
        rows.append([InlineKeyboardButton(text=label, callback_data=f"accounts:view:{acc.id}")])

    rows.append([InlineKeyboardButton(text="➕ Добавить по телефону", callback_data="accounts:add_phone")])
    rows.append([InlineKeyboardButton(text="🧩 Добавить по Session String", callback_data="accounts:add")])
    rows.append([InlineKeyboardButton(text="📊 Статус пула", callback_data="accounts:status")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="security:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def account_card_keyboard(account_id: int, is_active: bool, is_primary: bool):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    
    buttons = []
    
    if is_active:
        buttons.append([InlineKeyboardButton(
            text="⏸ Деактивировать", 
            callback_data=f"accounts:toggle:{account_id}:0"
        )])
    else:
        buttons.append([InlineKeyboardButton(
            text="▶️ Активировать", 
            callback_data=f"accounts:toggle:{account_id}:1"
        )])
    
    buttons.append([InlineKeyboardButton(
        text="🗑 Удалить",
        callback_data=f"accounts:delete:{account_id}"
    )])
    
    buttons.append([InlineKeyboardButton(
        text="◀️ К списку", 
        callback_data="accounts:list"
    )])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def format_account_status(account) -> str:
    status_emoji = "✅" if account.is_active else "❌"
    primary = " ⭐" if account.is_primary else ""
    
    flood_info = ""
    if account.flood_wait_until and account.flood_wait_until > datetime.now(timezone.utc):
        remaining = int((account.flood_wait_until - datetime.now(timezone.utc)).total_seconds())
        flood_info = f"\n⏳ FloodWait: {remaining}с"
    
    return (
        f"{status_emoji} <b>{account.name}</b>{primary}\n"
        f"📱 ...{account.phone[-4:]}\n"
        f"📊 Запросов: {account.total_requests}"
        f"{flood_info}"
    )


@router.callback_query(F.data == "accounts:menu", authorized_filter)
async def accounts_menu(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id if callback.from_user else None
    if user_id is not None:
        await _cleanup_login(user_id)

    await state.clear()
    await state.update_data(authorized=True)

    async with get_session() as session:
        repo = TelethonAccountRepository(session)
        accounts = await repo.list_all()

    if not accounts:
        text = (
            "🔐 <b>Telethon аккаунты</b>\n\n"
            "Нет добавленных аккаунтов.\n\n"
            "Аккаунты используются для парсинга каналов-источников. "
            "При FloodWait система автоматически переключится на другой аккаунт.\n\n"
            "<i>Используется аккаунт из .env (legacy режим)</i>"
        )
    else:
        lines = ["🔐 <b>Telethon аккаунты</b>\n"]
        for acc in accounts:
            lines.append(format_account_status(acc))
        text = "\n".join(lines)

    try:
        await callback.message.edit_text(text, reply_markup=accounts_keyboard(accounts))
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data == "accounts:list", authorized_filter)
async def accounts_list(callback: CallbackQuery, state: FSMContext) -> None:
    await accounts_menu(callback, state)


@router.callback_query(F.data == "accounts:status", authorized_filter)
async def pool_status(callback: CallbackQuery) -> None:
    status = account_manager.get_status()
    
    if status["mode"] == "legacy":
        text = (
            "📊 <b>Статус пула</b>\n\n"
            "Режим: <b>Legacy</b> (один аккаунт из .env)\n"
            f"Подключен: {'✅' if status['connected'] else '❌'}"
        )
    else:
        pool = status["pool"]
        text = (
            "📊 <b>Статус пула</b>\n\n"
            f"Режим: <b>Pool</b>\n"
            f"Всего аккаунтов: {pool['total_accounts']}\n"
            f"Подключено: {pool['connected']}\n"
            f"Доступно: {pool['available']}\n"
            f"В FloodWait: {pool['in_flood_wait']}\n"
        )
        
        if pool["accounts"]:
            text += "\n<b>Детали:</b>\n"
            for acc in pool["accounts"]:
                status_emoji = "✅" if acc["is_available"] else "⏳"
                flood_info = f" (ждать {acc['flood_wait_remaining']}с)" if acc["flood_wait_remaining"] > 0 else ""
                text += f"{status_emoji} {acc['name']}: {acc['total_requests']} запросов{flood_info}\n"
    
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="accounts:menu")]
    ])
    
    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data == "accounts:add", authorized_filter)
async def add_account_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AccountStates.waiting_name)
    
    text = (
        "➕ <b>Добавление аккаунта</b>\n\n"
        "Введите название для аккаунта (например: Main, Reserve, Account1):"
    )
    
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="accounts:menu")]
    ])
    
    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.message(AccountStates.waiting_name, authorized_filter)
async def add_account_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()
    if len(name) > 100:
        await message.answer("❌ Название слишком длинное (максимум 100 символов)")
        return
    
    await state.update_data(name=name)
    await state.set_state(AccountStates.waiting_phone)
    
    await message.answer(
        f"✅ Название: <b>{name}</b>\n\n"
        "Теперь введите номер телефона (в международном формате, например +79991234567):"
    )


@router.message(AccountStates.waiting_phone, authorized_filter)
async def add_account_phone(message: Message, state: FSMContext) -> None:
    phone = message.text.strip()
    if not phone.startswith("+"):
        phone = "+" + phone
    
    if len(phone) < 10 or not phone[1:].isdigit():
        await message.answer("❌ Некорректный номер телефона. Введите в формате +79991234567")
        return
    
    async with get_session() as session:
        repo = TelethonAccountRepository(session)
        existing = await repo.get_by_phone(phone)
        if existing:
            await message.answer("❌ Аккаунт с таким номером уже существует")
            return
    
    await state.update_data(phone=phone)
    await state.set_state(AccountStates.waiting_api_id)
    
    await message.answer(
        f"✅ Телефон: <b>{phone}</b>\n\n"
        "Введите API ID (получить на https://my.telegram.org):"
    )


@router.message(AccountStates.waiting_api_id, authorized_filter)
async def add_account_api_id(message: Message, state: FSMContext) -> None:
    try:
        api_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ API ID должен быть числом")
        return
    
    await state.update_data(api_id=api_id)
    await state.set_state(AccountStates.waiting_api_hash)
    
    await message.answer(
        f"✅ API ID: <b>{api_id}</b>\n\n"
        "Введите API Hash:"
    )


@router.message(AccountStates.waiting_api_hash, authorized_filter)
async def add_account_api_hash(message: Message, state: FSMContext) -> None:
    api_hash = message.text.strip()
    if len(api_hash) != 32:
        await message.answer("❌ API Hash должен быть 32 символа")
        return
    
    await state.update_data(api_hash=api_hash)
    await state.set_state(AccountStates.waiting_session)
    
    data = await state.get_data()
    await message.answer(
        f"✅ API Hash: <code>{api_hash[:8]}...</code>\n\n"
        "Теперь введите Session String.\n\n"
        "Для его получения запустите скрипт:\n"
        "<code>python create_session.py</code>\n\n"
        f"Используйте телефон: <code>{data['phone']}</code>\n"
        f"API ID: <code>{data['api_id']}</code>\n"
        f"API Hash: <code>{api_hash}</code>"
    )


@router.message(AccountStates.waiting_session, authorized_filter)
async def add_account_session(message: Message, state: FSMContext) -> None:
    session_string = message.text.strip()
    
    if len(session_string) < 100:
        await message.answer("❌ Session String выглядит некорректно (слишком короткий)")
        return
    
    data = await state.get_data()
    
    try:
        account = await account_manager.add_account(
            name=data["name"],
            phone=data["phone"],
            session_string=session_string,
            api_id=data["api_id"],
            api_hash=data["api_hash"],
        )
        
        await state.clear()
        
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 К списку аккаунтов", callback_data="accounts:menu")]
        ])
        
        await message.answer(
            f"✅ <b>Аккаунт добавлен!</b>\n\n"
            f"Название: {account.name}\n"
            f"Телефон: ...{account.phone[-4:]}\n"
            f"ID: {account.id}",
            reply_markup=keyboard
        )
        
    except ValueError as e:
        await message.answer(f"❌ Ошибка: {e}")
    except Exception as e:
        logger.error("add_account_error", error=str(e))
        await message.answer(f"❌ Ошибка при добавлении аккаунта: {e}")


@router.callback_query(F.data.startswith("accounts:view:"), authorized_filter)
async def view_account(callback: CallbackQuery) -> None:
    account_id = int(callback.data.split(":")[2])
    
    async with get_session() as session:
        repo = TelethonAccountRepository(session)
        account = await repo.get_by_id(account_id)
    
    if not account:
        await callback.answer("❌ Аккаунт не найден", show_alert=True)
        return
    
    flood_info = ""
    if account.flood_wait_until and account.flood_wait_until > datetime.now(timezone.utc):
        remaining = int((account.flood_wait_until - datetime.now(timezone.utc)).total_seconds())
        flood_info = f"\n⏳ FloodWait: осталось {remaining} секунд"
    
    text = (
        f"🔐 <b>{account.name}</b>\n\n"
        f"📱 Телефон: ...{account.phone[-4:]}\n"
        f"🆔 ID: {account.id}\n"
        f"{'✅ Активен' if account.is_active else '❌ Неактивен'}\n"
        f"{'⭐ Основной аккаунт' if account.is_primary else ''}\n\n"
        f"<b>Статистика:</b>\n"
        f"📊 Запросов: {account.total_requests}\n"
        f"⚠️ FloodWait-ов: {account.total_flood_waits}"
        f"{flood_info}"
    )
    
    try:
        await callback.message.edit_text(
            text, 
            reply_markup=account_card_keyboard(account_id, account.is_active, account.is_primary)
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("accounts:toggle:"), authorized_filter)
async def toggle_account(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    account_id = int(parts[2])
    is_active = parts[3] == "1"
    
    await account_manager.toggle_account(account_id, is_active)
    
    action = "активирован" if is_active else "деактивирован"
    await callback.answer(f"✅ Аккаунт {action}")
    
    await view_account(callback)


@router.callback_query(F.data.startswith("accounts:delete:"), authorized_filter)
async def delete_account_confirm(callback: CallbackQuery) -> None:
    account_id = int(callback.data.split(":")[2])
    
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"accounts:delete_confirm:{account_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"accounts:view:{account_id}"),
        ]
    ])
    
    try:
        await callback.message.edit_text(
            "⚠️ <b>Вы уверены?</b>\n\n"
            "Аккаунт будет удалён из пула.",
            reply_markup=keyboard
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("accounts:delete_confirm:"), authorized_filter)
async def delete_account(callback: CallbackQuery, state: FSMContext) -> None:
    account_id = int(callback.data.split(":")[2])

    deleted = await account_manager.remove_account(account_id)

    if deleted:
        await callback.answer("✅ Аккаунт удалён")
    else:
        await callback.answer("❌ Не удалось удалить аккаунт", show_alert=True)

    await accounts_menu(callback, state)



def _cancel_keyboard():
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="accounts:menu")]
    ])


@router.callback_query(F.data == "accounts:add_phone", authorized_filter)
async def add_phone_start(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id if callback.from_user else None
    if user_id is not None:
        await _cleanup_login(user_id)
    await state.set_state(PhoneLoginStates.waiting_name)
    await state.update_data(authorized=True)
    text = (
        "➕ <b>Добавление аккаунта по телефону</b>\n\n"
        "Код подтверждения придёт в Telegram на добавляемый аккаунт.\n\n"
        "Введите название аккаунта (например: Main, Reserve):"
    )
    try:
        await callback.message.edit_text(text, reply_markup=_cancel_keyboard())
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.message(PhoneLoginStates.waiting_name, authorized_filter)
async def add_phone_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name or len(name) > 100:
        await message.answer("❌ Введите корректное название (до 100 символов):")
        return
    await state.update_data(name=name)
    await state.set_state(PhoneLoginStates.waiting_phone)
    await message.answer(
        f"✅ Название: <b>{name}</b>\n\n"
        "Введите номер телефона аккаунта в международном формате (например +79991234567):"
    )


@router.message(PhoneLoginStates.waiting_phone, authorized_filter)
async def add_phone_number(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    phone = (message.text or "").strip().replace(" ", "")
    if not phone.startswith("+"):
        phone = "+" + phone
    if len(phone) < 10 or not phone[1:].isdigit():
        await message.answer("❌ Некорректный номер. Введите в формате +79991234567:")
        return

    async with get_session() as session:
        repo = TelethonAccountRepository(session)
        if await repo.get_by_phone(phone):
            await message.answer("❌ Аккаунт с таким номером уже существует.")
            return

    data = await state.get_data()
    name = data.get("name", phone)

    await message.answer("⏳ Подключаюсь к Telegram и запрашиваю код...")
    client = TelegramClient(StringSession(), settings.api_id, settings.api_hash)
    try:
        await client.connect()
        sent = await client.send_code_request(phone)
    except PhoneNumberInvalidError:
        await client.disconnect()
        await message.answer("❌ Telegram отклонил номер. Введите другой номер:")
        return
    except FloodWaitError as exc:
        await client.disconnect()
        await state.clear()
        await state.update_data(authorized=True)
        await message.answer(
            f"⏳ Слишком много попыток. Telegram просит подождать {exc.seconds} сек. Повторите позже.",
            reply_markup=_cancel_keyboard(),
        )
        return
    except Exception as exc:
        await client.disconnect()
        logger.error("send_code_failed", error=str(exc))
        await message.answer(f"❌ Ошибка запроса кода: {exc}")
        return

    if user_id is not None:
        _login_clients[user_id] = {
            "client": client,
            "phone": phone,
            "name": name,
            "phone_code_hash": sent.phone_code_hash,
        }
    await state.set_state(PhoneLoginStates.waiting_code)
    await message.answer(
        "📲 Код отправлен в Telegram.\n\n"
        "Введите код подтверждения. Чтобы Telegram не «съел» код, вводите его с пробелами, "
        "например: <code>1 2 3 4 5</code>.",
        reply_markup=_cancel_keyboard(),
    )


@router.message(PhoneLoginStates.waiting_code, authorized_filter)
async def add_phone_code(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    entry = _login_clients.get(user_id) if user_id is not None else None
    if not entry:
        await state.clear()
        await state.update_data(authorized=True)
        await message.answer("❌ Сессия входа истекла. Начните заново.", reply_markup=_cancel_keyboard())
        return

    code = "".join(ch for ch in (message.text or "") if ch.isdigit())
    if not code:
        await message.answer("❌ Введите цифровой код из Telegram:")
        return

    client: TelegramClient = entry["client"]
    phone = entry["phone"]
    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=entry.get("phone_code_hash"))
    except SessionPasswordNeededError:
        await state.set_state(PhoneLoginStates.waiting_password)
        await message.answer(
            "🔒 На аккаунте включена двухфакторная защита.\nВведите облачный пароль (2FA):",
            reply_markup=_cancel_keyboard(),
        )
        return
    except (PhoneCodeInvalidError, PhoneCodeExpiredError):
        await message.answer("❌ Неверный или истёкший код. Введите код ещё раз:")
        return
    except Exception as exc:
        logger.error("sign_in_failed", error=str(exc))
        await message.answer(f"❌ Ошибка входа: {exc}")
        return

    await _finalize_phone_login(message, state, user_id)


@router.message(PhoneLoginStates.waiting_password, authorized_filter)
async def add_phone_password(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    entry = _login_clients.get(user_id) if user_id is not None else None
    if not entry:
        await state.clear()
        await state.update_data(authorized=True)
        await message.answer("❌ Сессия входа истекла. Начните заново.", reply_markup=_cancel_keyboard())
        return

    password = message.text or ""
    client: TelegramClient = entry["client"]
    try:
        await client.sign_in(password=password)
    except Exception as exc:
        logger.error("sign_in_2fa_failed", error=str(exc))
        await message.answer("❌ Неверный пароль 2FA. Попробуйте снова:")
        return

    await _finalize_phone_login(message, state, user_id)


async def _finalize_phone_login(message: Message, state: FSMContext, user_id: int | None) -> None:
    entry = _login_clients.pop(user_id, None) if user_id is not None else None
    if not entry:
        await message.answer("❌ Внутренняя ошибка: клиент входа не найден.")
        return

    client: TelegramClient = entry["client"]
    phone = entry["phone"]
    name = entry["name"]
    try:
        session_string = client.session.save()
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass

    await state.clear()
    await state.update_data(authorized=True)

    try:
        account = await account_manager.add_account(
            name=name,
            phone=phone,
            session_string=session_string,
            api_id=settings.api_id,
            api_hash=settings.api_hash,
        )
    except ValueError as exc:
        await message.answer(f"❌ {exc}")
        return
    except Exception as exc:
        logger.error("add_account_after_login_failed", error=str(exc))
        await message.answer(f"❌ Не удалось сохранить аккаунт: {exc}")
        return

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 К списку аккаунтов", callback_data="accounts:menu")]
    ])
    await message.answer(
        f"✅ <b>Аккаунт добавлен и авторизован!</b>\n\n"
        f"Название: {account.name}\n"
        f"Телефон: …{account.phone[-4:]}\n"
        f"ID: {account.id}\n\n"
        "Аккаунт уже включён в пул наблюдателей.",
        reply_markup=keyboard,
    )
