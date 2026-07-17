from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import models
from app.domain.repositories import MessageMapRepository


class MappingService:

    def __init__(self, session: AsyncSession) -> None:
        self.repo = MessageMapRepository(session)

    async def register(
        self,
        *,
        chain_id: int,
        source_msg_id: int,
        source_msg_date: datetime,
        sink_msg_id: int,
        sink_msg_date: datetime,
        number_tag: int,
        media_type: models.MediaType,
    ) -> models.MessageMap:
        return await self.repo.add_mapping(
            chain_id=chain_id,
            source_msg_id=source_msg_id,
            source_msg_date=source_msg_date,
            sink_msg_id=sink_msg_id,
            sink_msg_date=sink_msg_date,
            number_tag=number_tag,
            media_type=media_type,
        )

    async def find_by_source(self, *, chain_id: int, source_msg_id: int) -> Optional[models.MessageMap]:
        return await self.repo.get_by_source(chain_id, source_msg_id)

    async def find_by_sink(self, *, chain_id: int, sink_msg_id: int) -> Optional[models.MessageMap]:
        return await self.repo.get_by_sink(chain_id, sink_msg_id)


__all__ = ["MappingService"]
