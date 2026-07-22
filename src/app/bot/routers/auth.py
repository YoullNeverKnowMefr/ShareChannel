from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from app.config import settings
from app.core.db import get_session
from app.core.redis import redis
from app.domain.services.security_service import SecurityService
from app.domain.repositories import AuthorizedUserRepository
from app.bot.keyboards import main_menu_keyboard, reply_back_keyboard

router = Router(name="auth")


class AuthStates(StatesGroup):
    waiting_password = State()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    from app.core.logging import get_logger
    logger = get_logger(__name__)
    
    user_id = message.from_user.id if message.from_user else None
    username = message.from_user.username if message.from_user else None
    
    logger.info("start_command_received", user_id=user_id, username=username)
    
    if user_id is None:
        return
    
    await state.clear()
    logger.info("state_cleared", user_id=user_id)
    
    is_authorized = False
    redis_key = f"authorized:{user_id}"
    
    try:
        if redis is not None:
            redis_value = await redis.get(redis_key)
            if redis_value:
                is_authorized = True
                logger.info("auth_check_redis", user_id=user_id, redis_value=redis_value)
    except Exception as e:
        logger.warning("redis_check_failed", user_id=user_id, error=str(e))
    
    if not is_authorized:
        try:
            async with get_session() as session:
                auth_repo = AuthorizedUserRepository(session)
                db_user = await auth_repo.get_by_tg_id(user_id)
                if db_user:
                    is_authorized = True
                    logger.info("auth_check_db", user_id=user_id, found=True)
                    try:
                        if redis is not None:
                            await redis.set(redis_key, "1", ex=30 * 24 * 60 * 60)
                    except Exception:
                        pass
        except Exception as e:
            logger.warning("db_check_failed", user_id=user_id, error=str(e))

    logger.info("auth_check_final", user_id=user_id, is_authorized=is_authorized)

    if is_authorized:
        await state.update_data(authorized=True)
        await message.answer(
            "♻️ Бот перезапущен.\nГлавное меню:",
            reply_markup=reply_back_keyboard(),
        )
        await message.answer("Выберите раздел:", reply_markup=main_menu_keyboard().as_markup())
        logger.info("main_menu_sent", user_id=user_id)
        return

    logger.info("requesting_password", user_id=user_id)
    await state.set_state(AuthStates.waiting_password)
    await message.answer("Введите пароль для доступа к боту:")
    
    async with get_session() as session:
        service = SecurityService(session)
        await service.ensure_initialized()


@router.message(AuthStates.waiting_password)
async def process_password(message: Message, state: FSMContext) -> None:
    from app.core.logging import get_logger
    logger = get_logger(__name__)
    
    password = message.text or ""
    user_tg_id = message.from_user.id if message.from_user else None
    username = message.from_user.username if message.from_user else None
    
    logger.info("password_attempt", user_id=user_tg_id, username=username)
    
    try:
        async with get_session() as session:
            service = SecurityService(session)
            success, locked_until, failed_attempts = await service.verify(
                password, 
                user_tg_id=user_tg_id,
                username=username
            )
            
        logger.info("password_verified", user_id=user_tg_id, success=success, failed_attempts=failed_attempts)
    except Exception as e:
        logger.error("password_verification_error", user_id=user_tg_id, error=str(e), exc_info=True)
        await message.answer("Произошла ошибка при проверке пароля. Попробуйте еще раз.")
        return

    if success:
        await state.update_data(authorized=True)
        await state.set_state(None)
        
        logger.info("password_accepted", user_id=user_tg_id)
        
        user_id = message.from_user.id if message.from_user else None
        if user_id is not None:
            try:
                async with get_session() as session:
                    auth_repo = AuthorizedUserRepository(session)
                    await auth_repo.create_or_update(
                        user_tg_id=user_id,
                        username=message.from_user.username if message.from_user else None,
                        first_name=message.from_user.first_name if message.from_user else None,
                        last_name=message.from_user.last_name if message.from_user else None,
                    )
                    await session.commit()
                
                logger.info("user_saved_to_db", user_id=user_id)
                
                redis_key = f"authorized:{user_id}"
                try:
                    if redis is not None:
                        await redis.set(redis_key, "1", ex=30 * 24 * 60 * 60)
                        logger.info("user_saved_to_redis", user_id=user_id)
                except Exception as e:
                    logger.warning("redis_save_failed", user_id=user_id, error=str(e))
            except Exception as e:
                logger.error("user_save_error", user_id=user_id, error=str(e))
        
        await message.answer("Главное меню:", reply_markup=reply_back_keyboard())
        await message.answer("Выберите раздел:", reply_markup=main_menu_keyboard().as_markup())
        logger.info("main_menu_shown", user_id=user_tg_id)
        return

    if locked_until:
        locked_until_aware = locked_until if locked_until.tzinfo else locked_until.replace(tzinfo=timezone.utc)
        if locked_until_aware > datetime.now(timezone.utc):
            logger.warning("user_locked", user_id=user_tg_id, locked_until=locked_until_aware.isoformat())
            await state.clear()
            return

    logger.info("password_rejected", user_id=user_tg_id, failed_attempts=failed_attempts)
