from __future__ import annotations

from typing import Any, Dict

from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import TelegramObject, CallbackQuery, Message

from app.core.db import get_session
from app.domain.repositories import BlockedUserRepository


class AuthorizedAccessFilter(BaseFilter):

    async def __call__(self, *args: Any, **kwargs: Any) -> bool:
        state: FSMContext | None = kwargs.get("state")
        if state is None:
            return False
        
        fsm_data = await state.get_data()
        if not fsm_data.get("authorized", False):
            return False
        
        user_tg_id = None
        callback: CallbackQuery | None = kwargs.get("callback") or kwargs.get("callback_query")
        message: Message | None = kwargs.get("message")
        
        if callback and callback.from_user:
            user_tg_id = callback.from_user.id
        elif message and message.from_user:
            user_tg_id = message.from_user.id
        
        if user_tg_id:
            async with get_session() as session:
                blocked_repo = BlockedUserRepository(session)
                if await blocked_repo.is_blocked(user_tg_id):
                    await state.clear()
                    return False
        
        return True


authorized_filter = AuthorizedAccessFilter()

__all__ = ["authorized_filter", "AuthorizedAccessFilter"]
