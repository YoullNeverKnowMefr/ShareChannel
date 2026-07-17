from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from structlog.stdlib import BoundLogger
from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError
from telethon.sessions import StringSession
from telethon.tl.custom import Message

from app.config import settings
from app.core.logging import get_logger

RetryCallback = Callable[[Message], Awaitable[None]]


class TelethonClientManager:

    def __init__(self) -> None:
        session = StringSession(settings.telethon_session_string) if settings.telethon_session_string else "sharechannel-session"
        self.client = TelegramClient(session, settings.api_id, settings.api_hash)
        self.logger: BoundLogger = get_logger(__name__)

    async def start(self) -> None:
        await self.client.connect()
        if not await self.client.is_user_authorized():
            if not settings.telethon_session_string:
                await self.client.start(bot_token=settings.bot_token)
            else:
                self.logger.error("telethon_not_authorized", message="Session string invalid or expired. Run create_session.py")
                raise RuntimeError("Telethon session not authorized. Please run create_session.py")
        self.logger.info("telethon_client_started")

    async def stop(self) -> None:
        await self.client.disconnect()
        self.logger.info("telethon_client_stopped")

    async def __aenter__(self) -> "TelethonClientManager":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop()

    async def iter_numbered_messages(
        self, chat_id: int, limit: Optional[int] = None
    ) -> AsyncIterator[Message]:
        async for message in self.client.iter_messages(chat_id, limit=limit):
            if message.message:
                yield message

    async def add_update_handler(self, handler: Callable[[Any], Awaitable[None]]) -> None:
        self.client.add_event_handler(handler)

    async def remove_update_handler(self, handler: Callable[[Any], Awaitable[None]]) -> None:
        self.client.remove_event_handler(handler)

    async def with_retry(
        self,
        coro: Callable[[], Awaitable[Any]],
        *,
        scope: str,
    ) -> None:
        try:
            await coro()
        except FloodWaitError as exc:
            self.logger.warning("telethon_flood_wait", scope=scope, wait=exc.seconds)
            await asyncio.sleep(exc.seconds)
            await coro()
        except RPCError as exc:
            self.logger.error("telethon_rpc_error", scope=scope, error=str(exc))
            raise


telethon_manager = TelethonClientManager()

__all__ = ["telethon_manager", "TelethonClientManager"]
