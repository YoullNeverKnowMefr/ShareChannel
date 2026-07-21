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
    waiting_db_upload = State()


@router.callback_query(F.data == "security:backup_chat", authorized_filter)
async def security_backup_to_chat(callback: CallbackQuery) -> None:
    from app.domain.services.backup_service import BackupService

    chat_id = callback.message.chat.id if callback.message else None
    if chat_id is None:
        await callback.answer("Не удалось определить чат", show_alert=True)
        return

    await callback.answer("Готовлю резервную копию...")
    try:
        backup = BackupService(callback.bot)
        filename = await backup.send_backup_to_chat(chat_id, reason="manual")
        if callback.message:
            await callback.message.answer(
                f"✅ База отправлена в этот чат.\nФайл: <code>{filename}</code>"
            )
    except Exception as exc:
        if callback.message:
            await callback.message.answer(f"❌ Не удалось создать бэкап: {exc}")


@router.callback_query(F.data == "security:backup", authorized_filter)
async def security_backup(callback: CallbackQuery) -> None:
    from app.config import settings
    from app.domain.services.backup_service import BackupService

    if settings.backup_chat_id is None:
        await callback.answer(
            "Не задан BACKUP_CHAT_ID в .env",
            show_alert=True,
        )
        return

    await callback.answer("Готовлю резервную копию...")
    try:
        backup = BackupService(callback.bot)
        filename = await backup.send_backup(reason="channel")
        if callback.message:
            await callback.message.answer(
                f"✅ Бэкап отправлен в канал <code>{settings.backup_chat_id}</code>\n"
                f"Файл: <code>{filename}</code>"
            )
    except Exception as exc:
        if callback.message:
            await callback.message.answer(f"❌ Не удалось создать бэкап: {exc}")


@router.callback_query(F.data == "security:restore", authorized_filter)
async def security_restore_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SecurityStates.waiting_db_upload)
    await state.update_data(authorized=True)
    if callback.message:
        await callback.message.answer(
            "📥 Загрузка базы данных\n\n"
            "Пришлите файл <code>.sqlite3</code> / <code>.db</code> ответом в этот чат.\n\n"
            "⚠️ Текущая база будет заменена. Перед заменой сохранится копия "
            "<code>*.before_restore_*</code> рядом с файлом БД.\n"
            "После загрузки бот переподключит аккаунты и цепочки.\n\n"
            "Отмена: /menu"
        )
    await callback.answer()


@router.message(SecurityStates.waiting_db_upload, F.document, authorized_filter)
async def security_restore_document(message: Message, state: FSMContext) -> None:
    import os
    import tempfile
    from datetime import datetime, timedelta, timezone
    import random

    from app.core.scheduler import scheduler
    from app.domain.repositories import ChainRepository
    from app.domain.services.account_manager import account_manager
    from app.domain.services.backup_service import BackupService
    from app.domain.services.forwarding import ForwardingService
    from app.core.db import get_session

    document = message.document
    if document is None:
        await message.answer("Пришлите именно файл-документ базы данных.")
        return

    name = (document.file_name or "").lower()
    if not (name.endswith(".sqlite3") or name.endswith(".sqlite") or name.endswith(".db")):
        await message.answer(
            "❌ Нужен файл с расширением <code>.sqlite3</code>, <code>.sqlite</code> или <code>.db</code>."
        )
        return

    # Telegram Bot API limit for download via bot is typically 20 MB
    if document.file_size and document.file_size > 20 * 1024 * 1024:
        await message.answer("❌ Файл больше 20 МБ — Telegram не даст скачать его боту.")
        return

    await message.answer("⏳ Скачиваю и проверяю базу...")

    tmp_path = os.path.join(
        tempfile.gettempdir(),
        f"sharechannel_upload_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sqlite3",
    )
    try:
        await message.bot.download(document, destination=tmp_path)
        backup = BackupService(message.bot)
        safety_path = await backup.restore_from_path(tmp_path)

        # Перезапуск пула аккаунтов и расписания цепочек под новую БД
        await account_manager.stop()
        await account_manager.start()

        for job in list(scheduler.scheduler.get_jobs()):
            job_id = str(job.id)
            if job_id.startswith("chain:"):
                scheduler.remove(job_id)

        forwarding = ForwardingService(message.bot)
        async with get_session() as session:
            chains = await ChainRepository(session).list_active()
        for chain in chains:
            scheduler.add_interval_job(
                forwarding.process_chain,
                job_id=f"chain:{chain.id}",
                seconds=chain.interval_seconds,
                kwargs={"chain_id": chain.id},
            )
            scheduler.add_one_off_job(
                forwarding.process_chain,
                job_id=f"chain:{chain.id}:bootstrap",
                run_date=datetime.now(timezone.utc) + timedelta(seconds=random.uniform(1, 5)),
                kwargs={"chain_id": chain.id},
            )

        data = await state.get_data()
        is_authorized = data.get("authorized", False)
        await state.clear()
        if is_authorized:
            await state.update_data(authorized=True)

        await message.answer(
            "✅ База успешно загружена и применена.\n"
            f"Копия старой БД: <code>{safety_path}</code>\n"
            f"Активных цепочек после загрузки: <b>{len(chains)}</b>\n\n"
            "Проверьте Telethon-аккаунты и цепочки в меню.",
            reply_markup=security_menu_keyboard().as_markup(),
        )
    except Exception as exc:
        await message.answer(f"❌ Не удалось загрузить базу: {exc}")
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


@router.message(SecurityStates.waiting_db_upload, authorized_filter)
async def security_restore_not_document(message: Message) -> None:
    await message.answer(
        "Пришлите файл базы (<code>.sqlite3</code>) или нажмите /menu для отмены."
    )


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
