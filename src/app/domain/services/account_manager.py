from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.ext.asyncio import AsyncSession
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession
from telethon.tl.custom import Message

from app.config import settings
from app.core.db import get_session
from app.core.logging import get_logger
from app.domain import models
from app.domain.repositories import TelethonAccountRepository
from app.domain.services.telethon_pool import TelethonPool, telethon_pool

logger = get_logger(__name__)


class AccountManager:
    
    def __init__(self) -> None:
        self.pool = telethon_pool
        self._legacy_client: Optional[TelegramClient] = None
        self._use_pool = False
        self._started = False
        self._event_handlers: list = []
    
    @property
    def is_pool_mode(self) -> bool:
        return self._use_pool
    
    @property
    def client(self) -> TelegramClient:
        if self._use_pool and self.pool.accounts:
            for account in self.pool.accounts.values():
                if account.is_connected:
                    return account.client
        if self._legacy_client:
            return self._legacy_client
        raise RuntimeError("No Telethon client available")
    
    async def start(self) -> None:
        if self._started:
            return
        
        async with get_session() as session:
            repo = TelethonAccountRepository(session)
            accounts = await repo.list_active()
        
        if accounts:
            self._use_pool = True
            
            self.pool.set_callbacks(
                on_flood_wait=self._on_flood_wait,
                on_request=self._on_request,
            )
            
            connected = await self.pool.start(accounts)
            
            if connected == 0:
                logger.warning("pool_no_connections_fallback_to_env")
                self._use_pool = False
                await self._start_legacy_client()
            else:
                logger.info(
                    "account_manager_started_pool_mode",
                    total_accounts=len(accounts),
                    connected=connected,
                )
        else:
            logger.info("no_accounts_in_db_using_legacy_mode")
            await self._start_legacy_client()
        
        self._started = True
    
    async def _start_legacy_client(self) -> None:
        if not settings.telethon_session_string:
            logger.warning("no_telethon_session_string_in_env")
            return
        
        session = StringSession(settings.telethon_session_string)
        self._legacy_client = TelegramClient(session, settings.api_id, settings.api_hash)
        
        await self._legacy_client.connect()
        
        if not await self._legacy_client.is_user_authorized():
            logger.error("legacy_client_not_authorized")
            await self._legacy_client.disconnect()
            self._legacy_client = None
            return

        try:
            await self._legacy_client.get_dialogs(limit=None)
        except Exception as e:
            logger.warning("warm_dialogs_failed_legacy", error=str(e))

        logger.info("legacy_client_started")
    
    async def stop(self) -> None:
        if self._use_pool:
            await self.pool.stop()
        
        if self._legacy_client:
            await self._legacy_client.disconnect()
            self._legacy_client = None
        
        self._started = False
        logger.info("account_manager_stopped")

    def add_event_handler(self, callback, event) -> None:
        self._event_handlers.append((callback, event))
        self._attach_event_handlers()

    def _attach_event_handlers(self) -> None:
        try:
            client = self.client
        except RuntimeError:
            logger.warning("event_handlers_deferred_no_client")
            return
        for callback, event in self._event_handlers:
            try:
                client.remove_event_handler(callback, event)
            except Exception:
                pass
            client.add_event_handler(callback, event)
        logger.info("event_handlers_attached", count=len(self._event_handlers))
    
    async def _on_flood_wait(self, account_id: int, seconds: int, until: datetime) -> None:
        async with get_session() as session:
            repo = TelethonAccountRepository(session)
            await repo.update_flood_wait(account_id, seconds, until)
            await session.commit()
    
    async def _on_request(self, account_id: int) -> None:
        async with get_session() as session:
            repo = TelethonAccountRepository(session)
            await repo.increment_requests(account_id)
            await session.commit()
    
    async def iter_messages(
        self,
        chat_id: int,
        limit: Optional[int] = None,
        min_id: int = 0,
        reverse: bool = False,
        search: Optional[str] = None,
    ):
        if self._use_pool:
            async for msg in self.pool.iter_messages_with_rotation(
                chat_id,
                limit=limit,
                min_id=min_id,
                reverse=reverse,
                search=search,
            ):
                yield msg
        else:
            if not self._legacy_client:
                raise RuntimeError("No Telethon client available")
            
            async for msg in self._legacy_client.iter_messages(
                chat_id,
                limit=limit,
                min_id=min_id,
                reverse=reverse,
                search=search,
            ):
                yield msg
    
    async def execute(self, operation):
        if self._use_pool:
            return await self.pool.execute_with_rotation(operation)
        else:
            if not self._legacy_client:
                raise RuntimeError("No Telethon client available")
            return await operation(self._legacy_client)
    
    def get_status(self) -> Dict[str, Any]:
        if self._use_pool:
            pool_status = self.pool.get_status()
            return {
                "mode": "pool",
                "pool": pool_status,
            }
        else:
            return {
                "mode": "legacy",
                "connected": self._legacy_client is not None and self._legacy_client.is_connected(),
            }
    
    async def add_account(
        self,
        name: str,
        phone: str,
        session_string: str,
        api_id: int,
        api_hash: str,
        is_primary: bool = False,
        priority: int = 0,
    ) -> models.TelethonAccount:
        async with get_session() as session:
            repo = TelethonAccountRepository(session)
            
            existing = await repo.get_by_phone(phone)
            if existing:
                raise ValueError(f"Account with phone {phone} already exists")
            
            account = await repo.create(
                name=name,
                phone=phone,
                session_string=session_string,
                api_id=api_id,
                api_hash=api_hash,
                is_primary=is_primary,
                priority=priority,
            )
            await session.commit()

            if self._started:
                accounts = await repo.list_active()
                if self._use_pool:
                    await self.pool.stop()
                elif self._legacy_client is not None:
                    await self._legacy_client.disconnect()
                    self._legacy_client = None
                self._use_pool = True
                self.pool.set_callbacks(
                    on_flood_wait=self._on_flood_wait,
                    on_request=self._on_request,
                )
                await self.pool.start(accounts)
                self._attach_event_handlers()

            logger.info(
                "account_added",
                account_id=account.id,
                name=name,
                phone=phone[-4:],
            )
            
            return account
    
    async def remove_account(self, account_id: int) -> bool:
        async with get_session() as session:
            repo = TelethonAccountRepository(session)
            
            deleted = await repo.delete(account_id)
            if deleted:
                await session.commit()
                
                if self._use_pool and self._started:
                    accounts = await repo.list_active()
                    await self.pool.stop()
                    if accounts:
                        await self.pool.start(accounts)
                    else:
                        self._use_pool = False
                        await self._start_legacy_client()
                    self._attach_event_handlers()

                logger.info("account_removed", account_id=account_id)
            
            return deleted
    
    async def toggle_account(self, account_id: int, is_active: bool) -> bool:
        async with get_session() as session:
            repo = TelethonAccountRepository(session)
            
            await repo.set_active(account_id, is_active)
            await session.commit()
            
            if self._use_pool and self._started:
                accounts = await repo.list_active()
                await self.pool.stop()
                await self.pool.start(accounts)
                self._attach_event_handlers()

            logger.info("account_toggled", account_id=account_id, is_active=is_active)
            return True
    
    async def list_accounts(self) -> Sequence[models.TelethonAccount]:
        async with get_session() as session:
            repo = TelethonAccountRepository(session)
            return await repo.list_all()


account_manager = AccountManager()

__all__ = ["AccountManager", "account_manager"]
