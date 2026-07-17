from __future__ import annotations

import asyncio
from typing import Iterable, List

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from structlog.stdlib import BoundLogger
from telethon.events import MessageDeleted
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.limiter import deletion_limiters
from app.core.logging import get_logger
from app.domain import models
from app.domain.repositories import MessageMapRepository


class DeletionSyncService:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self.logger: BoundLogger = get_logger(__name__)

    async def handle_event(self, event: MessageDeleted.Event) -> None:
        channel_id = getattr(event, "chat_id", None)
        if channel_id is None:
            return
        message_ids = list(event.deleted_ids or [])
        if not message_ids:
            return

        async with get_session() as session:
            chains = await self._chains_for_source(session, channel_id)
            for chain in chains:
                await self._process_chain_deletion(chain, message_ids, session)

    async def _chains_for_source(self, session: AsyncSession, channel_id: int) -> List[models.Chain]:
        stmt = select(models.Chain).where(
            models.Chain.source_chat_id == channel_id,
            models.Chain.status != models.ChainStatus.STOPPED,
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def _process_chain_deletion(
        self,
        chain: models.Chain,
        message_ids: Iterable[int],
        session: AsyncSession,
    ) -> None:
        repo = MessageMapRepository(session)
        for source_id in message_ids:
            mapping = await repo.get_by_source(chain.id, source_id)
            if mapping is None:
                continue
            try:
                async with deletion_limiters.throttle(chain.sink_chat_id):
                    await self.bot.delete_message(chain.sink_chat_id, mapping.sink_msg_id)
                self.logger.info(
                    "sink_message_deleted",
                    chain_id=chain.id,
                    source_msg_id=source_id,
                    sink_msg_id=mapping.sink_msg_id,
                )
            except TelegramRetryAfter as exc:
                self.logger.warning(
                    "delete_retry_after",
                    chain_id=chain.id,
                    sink_msg_id=mapping.sink_msg_id,
                    retry_after=exc.retry_after,
                )
                await asyncio.sleep(exc.retry_after)
                await self.bot.delete_message(chain.sink_chat_id, mapping.sink_msg_id)
            except TelegramBadRequest as exc:
                self.logger.error(
                    "delete_failed",
                    chain_id=chain.id,
                    sink_msg_id=mapping.sink_msg_id,
                    error=str(exc),
                )


__all__ = ["DeletionSyncService"]
