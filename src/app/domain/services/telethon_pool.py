from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Sequence, TypeVar

from structlog.stdlib import BoundLogger
from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError, SessionPasswordNeededError
from telethon.sessions import StringSession
from telethon.tl.custom import Message

from app.core.logging import get_logger
from app.domain import models
from app.domain.services.notifications import notify_admins

T = TypeVar("T")


@dataclass
class AccountState:
    account_id: int
    name: str
    phone: str
    client: TelegramClient
    is_connected: bool = False
    flood_wait_until: Optional[datetime] = None
    total_requests: int = 0
    last_used_at: Optional[datetime] = None
    consecutive_errors: int = 0
    
    def is_available(self) -> bool:
        if not self.is_connected:
            return False
        if self.flood_wait_until and self.flood_wait_until > datetime.now(timezone.utc):
            return False
        return True
    
    def remaining_flood_wait(self) -> int:
        if not self.flood_wait_until:
            return 0
        remaining = (self.flood_wait_until - datetime.now(timezone.utc)).total_seconds()
        return max(0, int(remaining))


class TelethonPool:
    
    def __init__(self) -> None:
        self.accounts: Dict[int, AccountState] = {}
        self.logger: BoundLogger = get_logger(__name__)
        self._lock = asyncio.Lock()
        self._round_robin_index = 0
        self._started = False
        self._on_flood_wait_callback: Optional[Callable] = None
        self._on_request_callback: Optional[Callable] = None
    
    def set_callbacks(
        self,
        on_flood_wait: Optional[Callable] = None,
        on_request: Optional[Callable] = None,
    ) -> None:
        self._on_flood_wait_callback = on_flood_wait
        self._on_request_callback = on_request
    
    async def start(self, accounts: Sequence[models.TelethonAccount]) -> int:
        if self._started:
            self.logger.warning("pool_already_started")
            return len([a for a in self.accounts.values() if a.is_connected])
        
        connected = 0
        
        for account in accounts:
            if not account.is_active:
                continue
                
            try:
                session = StringSession(account.session_string)
                client = TelegramClient(
                    session,
                    account.api_id,
                    account.api_hash,
                )
                
                await client.connect()
                
                if not await client.is_user_authorized():
                    self.logger.error(
                        "account_not_authorized",
                        account_id=account.id,
                        name=account.name,
                        phone=account.phone,
                    )
                    await client.disconnect()
                    await notify_admins(
                        f"⚠️ Аккаунт-наблюдатель «{account.name}» (…{account.phone[-4:]}) не авторизован "
                        f"(сессия истекла или отозвана) и был пропущен. Добавьте его заново через меню.",
                        dedup_key=f"acc_unauth:{account.id}",
                        cooldown_seconds=3600,
                    )
                    continue

                try:
                    await client.get_dialogs(limit=None)
                except Exception as e:
                    self.logger.warning("warm_dialogs_failed", account_id=account.id, error=str(e))

                state = AccountState(
                    account_id=account.id,
                    name=account.name,
                    phone=account.phone,
                    client=client,
                    is_connected=True,
                    flood_wait_until=account.flood_wait_until,
                    total_requests=account.total_requests,
                    last_used_at=account.last_used_at,
                )
                
                self.accounts[account.id] = state
                connected += 1
                
                self.logger.info(
                    "account_connected",
                    account_id=account.id,
                    name=account.name,
                    phone=account.phone[-4:],
                )
                
            except Exception as e:
                self.logger.error(
                    "account_connection_failed",
                    account_id=account.id,
                    name=account.name,
                    error=str(e),
                )
                await notify_admins(
                    f"⚠️ Не удалось подключить аккаунт-наблюдатель «{account.name}»: {e}",
                    dedup_key=f"acc_conn_fail:{account.id}",
                    cooldown_seconds=3600,
                )

        self._started = True
        self.logger.info("pool_started", total_accounts=len(accounts), connected=connected)
        return connected
    
    async def stop(self) -> None:
        for state in self.accounts.values():
            if state.is_connected:
                try:
                    await state.client.disconnect()
                    state.is_connected = False
                    self.logger.info("account_disconnected", account_id=state.account_id)
                except Exception as e:
                    self.logger.error("disconnect_error", account_id=state.account_id, error=str(e))
        
        self.accounts.clear()
        self._started = False
        self.logger.info("pool_stopped")
    
    def _get_available_accounts(self) -> List[AccountState]:
        available = [a for a in self.accounts.values() if a.is_available()]
        return sorted(available, key=lambda a: a.total_requests)
    
    async def _select_account(self) -> Optional[AccountState]:
        async with self._lock:
            available = self._get_available_accounts()
            if not available:
                return None
            
            self._round_robin_index = self._round_robin_index % len(available)
            account = available[self._round_robin_index]
            self._round_robin_index += 1
            
            return account
    
    async def _mark_flood_wait(self, account: AccountState, seconds: int) -> None:
        until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        account.flood_wait_until = until
        account.consecutive_errors += 1
        
        self.logger.warning(
            "account_flood_wait",
            account_id=account.account_id,
            name=account.name,
            seconds=seconds,
            until=until.isoformat(),
        )
        
        if self._on_flood_wait_callback:
            await self._on_flood_wait_callback(account.account_id, seconds, until)

        await notify_admins(
            f"⏳ Аккаунт-наблюдатель «{account.name}» (…{account.phone[-4:]}) ушёл в FloodWait на {seconds}с. "
            f"Его задачи автоматически переключены на другой доступный аккаунт.",
            dedup_key=f"acc_flood:{account.account_id}",
            cooldown_seconds=120,
        )
    
    async def _record_request(self, account: AccountState) -> None:
        account.total_requests += 1
        account.last_used_at = datetime.now(timezone.utc)
        account.consecutive_errors = 0
        
        if self._on_request_callback:
            await self._on_request_callback(account.account_id)
    
    @asynccontextmanager
    async def get_client(self) -> AsyncIterator[TelegramClient]:
        account = await self._select_account()
        
        if account is None:
            min_wait = self._get_min_flood_wait_time()
            if min_wait > 0:
                self.logger.warning("all_accounts_flood_wait", waiting_seconds=min_wait)
                await asyncio.sleep(min_wait)
                account = await self._select_account()
            
            if account is None:
                raise RuntimeError("No available Telethon accounts in pool")
        
        yield account.client
        await self._record_request(account)
    
    def _get_min_flood_wait_time(self) -> int:
        times = [a.remaining_flood_wait() for a in self.accounts.values() if a.is_connected]
        return min(times) if times else 0
    
    async def execute_with_rotation(
        self,
        operation: Callable[[TelegramClient], Any],
        max_retries: int = 3,
    ) -> Any:
        last_error: Optional[Exception] = None
        tried_accounts: set = set()
        
        for attempt in range(max_retries):
            account = await self._select_account()
            
            if account is None:
                min_wait = self._get_min_flood_wait_time()
                if min_wait > 0:
                    self.logger.info(
                        "waiting_flood_wait",
                        seconds=min_wait,
                        attempt=attempt + 1,
                        max_retries=max_retries,
                    )
                    await asyncio.sleep(min_wait + 1)
                    continue
                break
            
            if account.account_id in tried_accounts:
                continue
            
            tried_accounts.add(account.account_id)
            
            try:
                result = await operation(account.client)
                await self._record_request(account)
                return result
                
            except FloodWaitError as e:
                last_error = e
                await self._mark_flood_wait(account, e.seconds)
                
                self.logger.info(
                    "rotating_account",
                    from_account=account.name,
                    flood_wait_seconds=e.seconds,
                    attempt=attempt + 1,
                )
                continue
                
            except RPCError as e:
                last_error = e
                account.consecutive_errors += 1
                self.logger.error(
                    "rpc_error",
                    account_id=account.account_id,
                    error=str(e),
                )
                raise
        
        if last_error:
            raise last_error
        raise RuntimeError("No available accounts for operation")
    
    async def iter_messages_with_rotation(
        self,
        chat_id: int,
        limit: Optional[int] = None,
        min_id: int = 0,
        reverse: bool = False,
        search: Optional[str] = None,
    ) -> AsyncIterator[Message]:
        offset_id = 0 if reverse else 0
        fetched = 0
        
        while limit is None or fetched < limit:
            batch_limit = min(100, limit - fetched) if limit else 100
            
            try:
                account = await self._select_account()
                if account is None:
                    min_wait = self._get_min_flood_wait_time()
                    if min_wait > 0:
                        self.logger.info("waiting_for_account", seconds=min_wait)
                        await notify_admins(
                            "🚦 Все аккаунты-наблюдатели достигли лимитов Telegram (FloodWait). "
                            "Парсинг временно замедлен. Рекомендуется добавить ещё один аккаунт.",
                            dedup_key="all_accounts_busy",
                            cooldown_seconds=300,
                        )
                        await asyncio.sleep(min_wait + 1)
                        continue
                    break
                
                messages = []
                async for msg in account.client.iter_messages(
                    chat_id,
                    limit=batch_limit,
                    min_id=min_id,
                    offset_id=offset_id,
                    reverse=reverse,
                    search=search,
                ):
                    messages.append(msg)
                
                await self._record_request(account)
                
                if not messages:
                    break
                
                for msg in messages:
                    yield msg
                    fetched += 1
                    if limit and fetched >= limit:
                        return
                
                if reverse:
                    offset_id = messages[-1].id + 1
                else:
                    offset_id = messages[-1].id
                    
            except FloodWaitError as e:
                if account:
                    await self._mark_flood_wait(account, e.seconds)
                continue
    
    def get_status(self) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        return {
            "total_accounts": len(self.accounts),
            "connected": len([a for a in self.accounts.values() if a.is_connected]),
            "available": len(self._get_available_accounts()),
            "in_flood_wait": len([
                a for a in self.accounts.values() 
                if a.flood_wait_until and a.flood_wait_until > now
            ]),
            "accounts": [
                {
                    "id": a.account_id,
                    "name": a.name,
                    "phone_last4": a.phone[-4:],
                    "is_available": a.is_available(),
                    "flood_wait_remaining": a.remaining_flood_wait(),
                    "total_requests": a.total_requests,
                    "last_used": a.last_used_at.isoformat() if a.last_used_at else None,
                }
                for a in self.accounts.values()
            ],
        }


telethon_pool = TelethonPool()

__all__ = ["TelethonPool", "telethon_pool", "AccountState"]
