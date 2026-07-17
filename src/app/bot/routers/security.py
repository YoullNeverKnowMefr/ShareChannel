from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import main_menu_keyboard, security_menu_keyboard
from app.bot.middlewares import authorized_filter
from app.core.db import get_session
from app.domain.repositories import SecurityRepository, LoginAttemptRepository, AuthorizedUserRepository, BlockedUserRepository
from app.domain.services.security_service import SecurityService
from aiogram.utils.keyboard import InlineKeyboardBuilder

router = Router(name="security")


class SecurityStates(StatesGroup):
    waiting_old = State()
    waiting_new = State()
    waiting_confirm = State()
    waiting_pickup_delay = State()


@router.callback_query(F.data == "security:backup", authorized_filter)
async def security_backup(callback: CallbackQuery) -> None:
    import asyncio
    import os
    import sqlite3
    import tempfile
    from datetime import datetime
    from aiogram.types import FSInputFile
    from app.config import settings

    url = settings.database_url
    if not url.startswith("sqlite"):
        await callback.answer("Бэкап доступен только для SQLite-базы", show_alert=True)
        return

    raw = url.split("///", 1)[-1]
    db_path = raw if os.path.isabs(raw) else str((settings.project_root / raw).resolve())
    if not os.path.exists(db_path):
        await callback.answer("Файл базы не найден", show_alert=True)
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(tempfile.gettempdir(), f"sharechannel_backup_{ts}.sqlite3")

    def do_backup() -> None:
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(backup_path)
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
            src.close()

    await callback.answer("Готовлю резервную копию...")
    try:
        await asyncio.to_thread(do_backup)
        size_kb = os.path.getsize(backup_path) // 1024
        await callback.message.answer_document(
            FSInputFile(backup_path, filename=f"sharechannel_backup_{ts}.sqlite3"),
            caption=f"💾 Резервная копия базы ({size_kb} КБ).\nСохраните файл в надёжном месте.",
        )
    except Exception as exc:
        await callback.message.answer(f"❌ Не удалось создать бэкап: {exc}")
    finally:
        try:
            os.remove(backup_path)
        except Exception:
            pass


@router.callback_query(F.data == "security:menu", authorized_filter)
async def security_menu(callback: CallbackQuery, state: FSMContext) -> None:
    async with get_session() as session:
        service = SecurityService(session)
        repo = SecurityRepository(session)
        record = await repo.get_singleton()
        attempts = record.failed_attempts
        pickup_delay = await service.get_pickup_delay_minutes()
    
    text = (
        "🔐 Настройки безопасности\n"
        f"Неудачных попыток входа: {attempts}\n"
        f"⏱️ Задержка подхвата новых постов: {pickup_delay} мин\n"
        "Выберите действие:"
    )
    if callback.message:
        await callback.message.edit_text(
            text,
            reply_markup=security_menu_keyboard().as_markup(),
        )
    await callback.answer()


@router.callback_query(F.data == "security:change", authorized_filter)
async def security_change_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SecurityStates.waiting_old)
    await state.update_data(authorized=True, wizard_messages=[])
    if callback.message:
        msg = await callback.message.answer("Введите текущий пароль:")
        await state.update_data(wizard_messages=[msg.message_id])
    await callback.answer()


@router.message(SecurityStates.waiting_old)
async def security_change_old(message: Message, state: FSMContext) -> None:
    async with get_session() as session:
        service = SecurityService(session)
        success, _, _ = await service.verify(message.text or "", max_attempts=99)

    if not success:
        await message.answer("Текущий пароль неверен. Повторите попытку или отмените команду /menu.")
        return

    await state.update_data(security_new_password=None)
    await state.set_state(SecurityStates.waiting_new)
    msg = await message.answer("Введите новый пароль:")
    data = await state.get_data()
    wizard_messages = data.get("wizard_messages", [])
    wizard_messages.append(msg.message_id)
    await state.update_data(wizard_messages=wizard_messages)


@router.message(SecurityStates.waiting_new)
async def security_change_new(message: Message, state: FSMContext) -> None:
    await state.update_data(security_new_password=message.text or "")
    await state.set_state(SecurityStates.waiting_confirm)
    msg = await message.answer("Повторите новый пароль для подтверждения:")
    data = await state.get_data()
    wizard_messages = data.get("wizard_messages", [])
    wizard_messages.append(msg.message_id)
    await state.update_data(wizard_messages=wizard_messages)


@router.message(SecurityStates.waiting_confirm)
async def security_change_confirm(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    new_password = data.get("security_new_password") or ""
    confirm = message.text or ""

    if new_password != confirm:
        await state.set_state(SecurityStates.waiting_new)
        msg = await message.answer("Пароли не совпали. Введите новый пароль снова:")
        wizard_messages = data.get("wizard_messages", [])
        wizard_messages.append(msg.message_id)
        await state.update_data(wizard_messages=wizard_messages)
        return

    async with get_session() as session:
        service = SecurityService(session)
        await service.update_password(new_password)

    await state.update_data(security_new_password=None, authorized=True)
    await state.set_state(None)
    
    async with get_session() as session:
        repo = SecurityRepository(session)
        service = SecurityService(session)
        record = await repo.get_singleton()
        attempts = record.failed_attempts
        pickup_delay = await service.get_pickup_delay_minutes()
    
    text = (
        "🔐 Настройки безопасности\n"
        f"Неудачных попыток входа: {attempts}\n"
        f"⏱️ Задержка подхвата новых постов: {pickup_delay} мин\n\n"
        "✅ Пароль успешно обновлён.\n\n"
        "Выберите действие:"
    )
    await message.answer(
        text,
        reply_markup=security_menu_keyboard().as_markup(),
    )


@router.callback_query(F.data == "security:reset", authorized_filter)
async def security_reset(callback: CallbackQuery) -> None:
    async with get_session() as session:
        service = SecurityService(session)
        await service.reset_attempts()
        repo = SecurityRepository(session)
        record = await repo.get_singleton()
        attempts = record.failed_attempts
        pickup_delay = await service.get_pickup_delay_minutes()
    
    text = (
        "🔐 Настройки безопасности\n"
        f"Неудачных попыток входа: {attempts}\n"
        f"⏱️ Задержка подхвата новых постов: {pickup_delay} мин\n\n"
        "✅ Счётчик неудачных попыток сброшен.\n\n"
        "Выберите действие:"
    )
    if callback.message:
        await callback.message.edit_text(
            text,
            reply_markup=security_menu_keyboard().as_markup(),
        )
    await callback.answer("Счётчик сброшен")


@router.callback_query(F.data == "security:pickup_delay", authorized_filter)
async def security_pickup_delay_start(callback: CallbackQuery, state: FSMContext) -> None:
    async with get_session() as session:
        service = SecurityService(session)
        current_delay = await service.get_pickup_delay_minutes()
    
    await state.set_state(SecurityStates.waiting_pickup_delay)
    await state.update_data(authorized=True)
    
    if callback.message:
        await callback.message.answer(
            f"⏱️ Текущая задержка подхвата новых постов: {current_delay} минут\n\n"
            "📌 Эта настройка применяется ко ВСЕМ магазинам и цепочкам.\n"
            "После появления нового поста с хештегом в источнике, бот подождёт указанное время перед тем как начать его публиковать.\n\n"
            "💡 Рекомендуется:\n"
            "• 0 минут - для мгновенной публикации\n"
            "• 1-5 минут - для небольшой задержки (если нужно время на проверку/редактирование в источнике)\n\n"
            "Введите новое значение в минутах:"
        )
    await callback.answer()


@router.message(SecurityStates.waiting_pickup_delay)
async def security_pickup_delay_set(message: Message, state: FSMContext) -> None:
    try:
        minutes = int(message.text or "0")
        if minutes < 0:
            await message.answer("Значение не может быть отрицательным. Попробуйте ещё раз:")
            return
        
        async with get_session() as session:
            service = SecurityService(session)
            await service.set_pickup_delay_minutes(minutes)
        
        data = await state.get_data()
        is_authorized = data.get("authorized", False)
        await state.clear()
        if is_authorized:
            await state.update_data(authorized=True)
        
        async with get_session() as session:
            repo = SecurityRepository(session)
            record = await repo.get_singleton()
            attempts = record.failed_attempts
        
        text = (
            "🔐 Настройки безопасности\n"
            f"Неудачных попыток входа: {attempts}\n"
            f"⏱️ Задержка подхвата новых постов: {minutes} мин\n\n"
        )
        
        if minutes == 0:
            text += (
                "✅ Задержка подхвата установлена: 0 минут (мгновенный подхват)\n\n"
                "🚀 Новые посты будут публиковаться сразу после появления в источнике!\n"
                "Изменения применены моментально, перезапуск не требуется.\n\n"
                "Выберите действие:"
            )
        else:
            text += (
                f"✅ Задержка подхвата установлена: {minutes} минут\n\n"
                "⏱️ Бот будет подхватывать только те посты, которые были опубликованы в источнике более чем "
                f"{minutes} {'минуту' if minutes == 1 else 'минуты' if minutes < 5 else 'минут'} назад.\n"
                "Изменения применены моментально, перезапуск не требуется.\n\n"
                "Выберите действие:"
            )
        
        await message.answer(
            text,
            reply_markup=security_menu_keyboard().as_markup(),
        )
    except ValueError:
        await message.answer("Пожалуйста, введите число (целое количество минут):")


@router.callback_query(F.data == "security:login_history", authorized_filter)
async def security_login_history(callback: CallbackQuery) -> None:
    async with get_session() as session:
        repo = LoginAttemptRepository(session)
        attempts = await repo.get_recent_attempts(limit=50)
    
    if not attempts:
        text = "📋 История входов\n\nИстория входов пуста."
    else:
        users_data = {}
        for attempt in attempts:
            if attempt.user_tg_id not in users_data:
                users_data[attempt.user_tg_id] = {
                    'username': attempt.username or 'Unknown',
                    'success_count': 0,
                    'failed_count': 0,
                    'last_attempt': attempt.created_at
                }
            
            if attempt.success:
                users_data[attempt.user_tg_id]['success_count'] += 1
            else:
                users_data[attempt.user_tg_id]['failed_count'] += 1
            
            if attempt.created_at > users_data[attempt.user_tg_id]['last_attempt']:
                users_data[attempt.user_tg_id]['last_attempt'] = attempt.created_at
        
        text = "📋 История входов\n\n"
        text += f"Всего попыток входа: {len(attempts)}\n"
        text += f"Уникальных пользователей: {len(users_data)}\n\n"
        
        for user_id, data in users_data.items():
            username_display = f"@{data['username']}" if data['username'] != 'Unknown' else f"ID: {user_id}"
            text += f"👤 {username_display}\n"
            text += f"   ✅ Успешных: {data['success_count']}\n"
            text += f"   ❌ Неудачных: {data['failed_count']}\n"
            text += f"   🕐 Последняя: {data['last_attempt'].strftime('%d.%m.%Y %H:%M')}\n\n"
    
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад", callback_data="security:menu")
    builder.adjust(1)
    
    if callback.message:
        await callback.message.edit_text(
            text,
            reply_markup=builder.as_markup(),
        )
    await callback.answer()


@router.callback_query(F.data == "security:authorized_users", authorized_filter)
async def security_authorized_users(callback: CallbackQuery) -> None:
    async with get_session() as session:
        repo = AuthorizedUserRepository(session)
        users = await repo.get_all()
    
    if not users:
        text = "👥 Авторизованные пользователи\n\nНет авторизованных пользователей."
    else:
        text = "👥 Авторизованные пользователи\n\n"
        text += f"Всего: {len(users)}\n\n"
        
        for user in users:
            display_name = user.first_name or ""
            if user.last_name:
                display_name += f" {user.last_name}"
            if not display_name:
                display_name = "Без имени"
            
            username_display = f"@{user.username}" if user.username else ""
            
            text += f"👤 {display_name}"
            if username_display:
                text += f" ({username_display})"
            text += f"\n   ID: {user.user_tg_id}\n"
            text += f"   🕐 Первый вход: {user.first_login_at.strftime('%d.%m.%Y %H:%M')}\n"
            text += f"   🕐 Последний вход: {user.last_login_at.strftime('%d.%m.%Y %H:%M')}\n\n"
    
    builder = InlineKeyboardBuilder()
    
    for user in users:
        display_name = user.first_name or user.username or f"ID {user.user_tg_id}"
        builder.button(text=f"🚫 Заблокировать {display_name}", callback_data=f"security:block:{user.user_tg_id}")
    
    builder.button(text="⬅️ Назад", callback_data="security:menu")
    builder.adjust(1)

    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=builder.as_markup())
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc):
                raise
    await callback.answer()


@router.callback_query(F.data == "security:blocked_users", authorized_filter)
async def security_blocked_users(callback: CallbackQuery) -> None:
    async with get_session() as session:
        repo = BlockedUserRepository(session)
        users = await repo.get_all()
    
    if not users:
        text = "🚫 Заблокированные пользователи\n\nНет заблокированных пользователей."
    else:
        text = "🚫 Заблокированные пользователи\n\n"
        text += f"Всего: {len(users)}\n\n"
        
        for user in users:
            username_display = f"@{user.username}" if user.username else f"ID: {user.user_tg_id}"
            text += f"🚫 {username_display}\n"
            if user.reason:
                text += f"   Причина: {user.reason}\n"
            text += f"   🕐 Заблокирован: {user.blocked_at.strftime('%d.%m.%Y %H:%M')}\n\n"
    
    builder = InlineKeyboardBuilder()
    
    for user in users:
        display_name = user.username or f"ID {user.user_tg_id}"
        builder.button(text=f"✅ Разблокировать @{display_name}" if user.username else f"✅ Разблокировать {display_name}", 
                      callback_data=f"security:unblock:{user.user_tg_id}")
    
    builder.button(text="⬅️ Назад", callback_data="security:menu")
    builder.adjust(1)
    
    if callback.message:
        await callback.message.edit_text(
            text,
            reply_markup=builder.as_markup(),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("security:block:"), authorized_filter)
async def security_block_user(callback: CallbackQuery) -> None:
    user_tg_id = int(callback.data.split(":")[-1])
    admin_tg_id = callback.from_user.id if callback.from_user else None
    
    async with get_session() as session:
        blocked_repo = BlockedUserRepository(session)
        auth_repo = AuthorizedUserRepository(session)
        
        user = await auth_repo.get_by_tg_id(user_tg_id)
        
        await blocked_repo.block_user(
            user_tg_id=user_tg_id,
            username=user.username if user else None,
            reason="Заблокирован администратором",
            blocked_by_tg_id=admin_tg_id,
        )
        
        await auth_repo.remove(user_tg_id)
        
        await session.commit()
    
    await callback.answer("✅ Пользователь заблокирован")
    
    await security_authorized_users(callback)


@router.callback_query(F.data.startswith("security:unblock:"), authorized_filter)
async def security_unblock_user(callback: CallbackQuery) -> None:
    user_tg_id = int(callback.data.split(":")[-1])
    
    async with get_session() as session:
        blocked_repo = BlockedUserRepository(session)
        await blocked_repo.unblock_user(user_tg_id)
        await session.commit()
    
    await callback.answer("✅ Пользователь разблокирован")
    
    await security_blocked_users(callback)



@router.message(Command("lockout"), authorized_filter)
async def cmd_lockout(message: Message) -> None:
    if not message.text:
        await message.answer(
            "❌ Неверный формат команды\n\n"
            "Использование:\n"
            "/lockout <user_id> [причина]\n\n"
            "Пример:\n"
            "/lockout 123456789 Взлом аккаунта"
        )
        return
    
    parts = message.text.split(maxsplit=2)
    if len(parts) < 2:
        await message.answer(
            "❌ Неверный формат команды\n\n"
            "Использование:\n"
            "/lockout <user_id> [причина]\n\n"
            "Пример:\n"
            "/lockout 123456789 Взлом аккаунта"
        )
        return
    
    try:
        user_tg_id = int(parts[1])
    except ValueError:
        await message.answer("❌ ID пользователя должен быть числом")
        return
    
    reason = parts[2] if len(parts) > 2 else "Администратор"
    
    if message.from_user and user_tg_id == message.from_user.id:
        await message.answer("❌ Нельзя заблокировать самого себя")
        return
    
    async with get_session() as session:
        blocked_repo = BlockedUserRepository(session)
        auth_repo = AuthorizedUserRepository(session)
        
        is_blocked = await blocked_repo.is_blocked(user_tg_id)
        if is_blocked:
            await message.answer(f"⚠️ Пользователь {user_tg_id} уже заблокирован")
            return
        
        await blocked_repo.block_user(
            user_tg_id=user_tg_id,
            username=None,
            reason=reason
        )
        
        await auth_repo.remove(user_tg_id)
        
        await session.commit()
    
    await message.answer(
        f"🔒 Пользователь {user_tg_id} заблокирован\n"
        f"Причина: {reason}\n\n"
        "Для разблокировки используйте команду:\n"
        f"/unlock {user_tg_id}"
    )


@router.message(Command("unlock"), authorized_filter)
async def cmd_unlock(message: Message) -> None:
    if not message.text:
        await message.answer(
            "❌ Неверный формат команды\n\n"
            "Использование:\n"
            "/unlock <user_id>\n\n"
            "Пример:\n"
            "/unlock 123456789"
        )
        return
    
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer(
            "❌ Неверный формат команды\n\n"
            "Использование:\n"
            "/unlock <user_id>\n\n"
            "Пример:\n"
            "/unlock 123456789"
        )
        return
    
    try:
        user_tg_id = int(parts[1])
    except ValueError:
        await message.answer("❌ ID пользователя должен быть числом")
        return
    
    async with get_session() as session:
        blocked_repo = BlockedUserRepository(session)
        
        is_blocked = await blocked_repo.is_blocked(user_tg_id)
        if not is_blocked:
            await message.answer(f"⚠️ Пользователь {user_tg_id} не заблокирован")
            return
        
        await blocked_repo.unblock_user(user_tg_id)
        await session.commit()
    
    await message.answer(f"🔓 Пользователь {user_tg_id} разблокирован")
