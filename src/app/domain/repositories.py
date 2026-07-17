from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import models


class ShopRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, shop_id: int) -> models.Shop | None:
        result = await self.session.execute(select(models.Shop).where(models.Shop.id == shop_id))
        return result.scalar_one_or_none()

    async def list_by_owner(self, owner_tg_id: int) -> Sequence[models.Shop]:
        result = await self.session.execute(
            select(models.Shop).order_by(models.Shop.created_at)
        )
        return result.scalars().all()
    
    async def list_all(self) -> Sequence[models.Shop]:
        result = await self.session.execute(
            select(models.Shop).order_by(models.Shop.created_at)
        )
        return result.scalars().all()

    async def get_by_name(self, owner_tg_id: int, name: str) -> models.Shop | None:
        result = await self.session.execute(
            select(models.Shop).where(models.Shop.name == name)
        )
        return result.scalar_one_or_none()

    async def create(self, owner_tg_id: int, name: str) -> models.Shop:
        shop = models.Shop(owner_tg_id=owner_tg_id, name=name)
        self.session.add(shop)
        await self.session.flush()
        return shop


class CategoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, category_id: int) -> models.Category | None:
        result = await self.session.execute(
            select(models.Category).where(models.Category.id == category_id)
        )
        return result.scalar_one_or_none()

    async def list_by_shop(self, shop_id: int, parent_id: Optional[int] = None) -> Sequence[models.Category]:
        if parent_id is None:
            result = await self.session.execute(
                select(models.Category)
                .where(models.Category.shop_id == shop_id, models.Category.parent_id.is_(None))
                .order_by(models.Category.created_at)
            )
        else:
            result = await self.session.execute(
                select(models.Category)
                .where(models.Category.shop_id == shop_id, models.Category.parent_id == parent_id)
                .order_by(models.Category.created_at)
            )
        return result.scalars().all()

    async def list_by_parent(self, parent_id: int) -> Sequence[models.Category]:
        result = await self.session.execute(
            select(models.Category)
            .where(models.Category.parent_id == parent_id)
            .order_by(models.Category.created_at)
        )
        return result.scalars().all()

    async def get_by_name(self, shop_id: int, name: str, parent_id: Optional[int] = None) -> models.Category | None:
        if parent_id is None:
            result = await self.session.execute(
                select(models.Category).where(
                    models.Category.shop_id == shop_id,
                    models.Category.name == name,
                    models.Category.parent_id.is_(None)
                )
            )
        else:
            result = await self.session.execute(
                select(models.Category).where(
                    models.Category.shop_id == shop_id,
                    models.Category.name == name,
                    models.Category.parent_id == parent_id
                )
            )
        return result.scalar_one_or_none()

    async def create(self, shop_id: int, name: str, parent_id: Optional[int] = None) -> models.Category:
        category = models.Category(shop_id=shop_id, name=name, parent_id=parent_id)
        self.session.add(category)
        await self.session.flush()
        return category


class ChainRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, chain_id: int) -> models.Chain | None:
        result = await self.session.execute(select(models.Chain).where(models.Chain.id == chain_id))
        return result.scalar_one_or_none()

    async def list_by_shop(self, shop_id: int) -> Sequence[models.Chain]:
        result = await self.session.execute(
            select(models.Chain).where(models.Chain.shop_id == shop_id).order_by(models.Chain.created_at)
        )
        return result.scalars().all()

    async def list_by_category(self, category_id: int) -> Sequence[models.Chain]:
        result = await self.session.execute(
            select(models.Chain)
            .where(models.Chain.category_id == category_id)
            .order_by(models.Chain.created_at)
        )
        return result.scalars().all()

    async def list_by_source(self, source_chat_id: int) -> Sequence[models.Chain]:
        result = await self.session.execute(
            select(models.Chain).where(
                models.Chain.source_chat_id == source_chat_id,
                models.Chain.status != models.ChainStatus.STOPPED,
            )
        )
        return result.scalars().all()

    async def list_active(self) -> Sequence[models.Chain]:
        result = await self.session.execute(
            select(models.Chain).where(models.Chain.status == models.ChainStatus.ACTIVE)
        )
        return result.scalars().all()
    
    async def list_all(self) -> Sequence[models.Chain]:
        result = await self.session.execute(select(models.Chain).order_by(models.Chain.created_at))
        return result.scalars().all()
    
    async def find_duplicate(
        self, shop_id: int, source_chat_id: int, sink_chat_id: int
    ) -> models.Chain | None:
        result = await self.session.execute(
            select(models.Chain).where(
                models.Chain.shop_id == shop_id,
                models.Chain.source_chat_id == source_chat_id,
                models.Chain.sink_chat_id == sink_chat_id,
                models.Chain.status != models.ChainStatus.STOPPED,
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        shop_id: int,
        source_chat_id: int,
        source_chat_title: Optional[str] = None,
        source_chat_link: Optional[str] = None,
        sink_chat_id: int,
        sink_chat_link: Optional[str] = None,
        start_number: int,
        interval_seconds: int,
        category_id: Optional[int] = None,
    ) -> models.Chain:
        chain = models.Chain(
            shop_id=shop_id,
            category_id=category_id,
            source_chat_id=source_chat_id,
            source_chat_title=source_chat_title,
            source_chat_link=source_chat_link,
            sink_chat_id=sink_chat_id,
            sink_chat_link=sink_chat_link,
            start_number=start_number,
            interval_seconds=interval_seconds,
            next_expected_number=start_number,
            status=models.ChainStatus.ACTIVE,
        )
        self.session.add(chain)
        await self.session.flush()
        return chain

    async def update_chat_links(
        self,
        chain_id: int,
        *,
        source_chat_link: Optional[str] = None,
        sink_chat_link: Optional[str] = None,
    ) -> None:
        values = {k: v for k, v in {
            "source_chat_link": source_chat_link,
            "sink_chat_link": sink_chat_link,
        }.items() if v is not None}

        if not values:
            return

        values["updated_at"] = datetime.now(timezone.utc)

        await self.session.execute(
            update(models.Chain)
            .where(models.Chain.id == chain_id)
            .values(**values)
        )

    async def update_status(self, chain_id: int, status: models.ChainStatus) -> None:
        await self.session.execute(
            update(models.Chain)
            .where(models.Chain.id == chain_id)
            .values(status=status, updated_at=datetime.now(timezone.utc))
        )

    async def update_interval(self, chain_id: int, interval_seconds: int) -> None:
        await self.session.execute(
            update(models.Chain)
            .where(models.Chain.id == chain_id)
            .values(interval_seconds=interval_seconds, updated_at=datetime.now(timezone.utc))
        )

    async def advance_pointer(
        self,
        chain_id: int,
        *,
        next_expected_number: int,
        last_sent_number: Optional[int],
    ) -> None:
        await self.session.execute(
            update(models.Chain)
            .where(models.Chain.id == chain_id)
            .values(
                next_expected_number=next_expected_number,
                last_sent_number=last_sent_number,
                last_activity_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )

    async def delete(self, chain_id: int) -> None:
        chain = await self.get_by_id(chain_id)
        if chain:
            await self.session.delete(chain)


class MessageMapRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_mapping(
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
        mapping = models.MessageMap(
            chain_id=chain_id,
            source_msg_id=source_msg_id,
            source_msg_date=source_msg_date,
            sink_msg_id=sink_msg_id,
            sink_msg_date=sink_msg_date,
            number_tag=number_tag,
            media_type=media_type,
        )
        self.session.add(mapping)
        await self.session.flush()
        return mapping

    async def get_by_source(self, chain_id: int, source_msg_id: int) -> models.MessageMap | None:
        result = await self.session.execute(
            select(models.MessageMap).where(
                models.MessageMap.chain_id == chain_id, models.MessageMap.source_msg_id == source_msg_id
            )
        )
        return result.scalar_one_or_none()

    async def get_by_sink(self, chain_id: int, sink_msg_id: int) -> models.MessageMap | None:
        result = await self.session.execute(
            select(models.MessageMap).where(
                models.MessageMap.chain_id == chain_id, models.MessageMap.sink_msg_id == sink_msg_id
            )
        )
        return result.scalar_one_or_none()

    async def list_by_numbers(self, chain_id: int, numbers: Iterable[int]) -> Sequence[models.MessageMap]:
        stmt = select(models.MessageMap).where(
            models.MessageMap.chain_id == chain_id,
            models.MessageMap.number_tag.in_(list(numbers)),
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def get_last_by_number(self, chain_id: int, number_tag: int) -> models.MessageMap | None:
        stmt = (
            select(models.MessageMap)
            .where(
                models.MessageMap.chain_id == chain_id,
                models.MessageMap.number_tag == number_tag,
            )
            .order_by(models.MessageMap.source_msg_date.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_last_for_chain(self, chain_id: int) -> models.MessageMap | None:
        stmt = (
            select(models.MessageMap)
            .where(models.MessageMap.chain_id == chain_id)
            .order_by(models.MessageMap.source_msg_id.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()


class SecurityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_singleton(self) -> models.Security:
        result = await self.session.execute(select(models.Security).limit(1))
        security = result.scalar_one_or_none()
        if security is None:
            security = models.Security(password_hash="")
            self.session.add(security)
            await self.session.flush()
        return security

    async def update(
        self,
        *,
        password_hash: Optional[str] = None,
        failed_attempts: Optional[int] = None,
        last_failed_at: Optional[datetime] = None,
        locked_until: Optional[datetime] = None,
        global_pickup_delay_minutes: Optional[int] = None,
    ) -> None:
        security = await self.get_singleton()

        if password_hash is not None:
            security.password_hash = password_hash
        if failed_attempts is not None:
            security.failed_attempts = failed_attempts
        if last_failed_at is not None:
            security.last_failed_at = last_failed_at
        if locked_until is not None:
            security.locked_until = locked_until
        if global_pickup_delay_minutes is not None:
            security.global_pickup_delay_minutes = global_pickup_delay_minutes
        security.updated_at = datetime.now(timezone.utc)
        await self.session.flush()


class AuditLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, *, actor_tg_id: Optional[int], action: str, payload: Optional[dict] = None) -> None:
        log = models.AuditLog(actor_tg_id=actor_tg_id, action=action, payload=payload or {})
        self.session.add(log)
        await self.session.flush()


class RateLimitRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(self, *, scope: str, until: datetime, meta: Optional[dict] = None) -> None:
        event = models.RateLimitEvent(scope=scope, until=until, meta=meta)
        self.session.add(event)
        await self.session.flush()

    async def get_active(self, scope: str, now: datetime | None = None) -> models.RateLimitEvent | None:
        now = now or datetime.now(timezone.utc)
        result = await self.session.execute(
            select(models.RateLimitEvent)
            .where(models.RateLimitEvent.scope == scope, models.RateLimitEvent.until > now)
            .order_by(models.RateLimitEvent.until.desc())
        )
        return result.scalar_one_or_none()


class LoginAttemptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def log_attempt(
        self,
        *,
        user_tg_id: int,
        username: Optional[str],
        success: bool,
        ip_address: Optional[str] = None
    ) -> models.LoginAttempt:
        attempt = models.LoginAttempt(
            user_tg_id=user_tg_id,
            username=username,
            success=success,
            ip_address=ip_address
        )
        self.session.add(attempt)
        await self.session.flush()
        return attempt

    async def get_recent_attempts(self, limit: int = 100) -> Sequence[models.LoginAttempt]:
        result = await self.session.execute(
            select(models.LoginAttempt)
            .order_by(models.LoginAttempt.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def get_attempts_by_user(self, user_tg_id: int, limit: int = 50) -> Sequence[models.LoginAttempt]:
        result = await self.session.execute(
            select(models.LoginAttempt)
            .where(models.LoginAttempt.user_tg_id == user_tg_id)
            .order_by(models.LoginAttempt.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def get_failed_attempts_count(self, user_tg_id: int) -> int:
        from sqlalchemy import func
        result = await self.session.execute(
            select(func.count())
            .select_from(models.LoginAttempt)
            .where(
                models.LoginAttempt.user_tg_id == user_tg_id,
                models.LoginAttempt.success == False
            )
        )
        return result.scalar_one() or 0


class AuthorizedUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_tg_id(self, user_tg_id: int) -> models.AuthorizedUser | None:
        result = await self.session.execute(
            select(models.AuthorizedUser).where(models.AuthorizedUser.user_tg_id == user_tg_id)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> Sequence[models.AuthorizedUser]:
        result = await self.session.execute(
            select(models.AuthorizedUser).order_by(models.AuthorizedUser.last_login_at.desc())
        )
        return result.scalars().all()

    async def create_or_update(
        self,
        *,
        user_tg_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None
    ) -> models.AuthorizedUser:
        user = await self.get_by_tg_id(user_tg_id)
        
        if user:
            user.username = username
            user.first_name = first_name
            user.last_name = last_name
            user.last_login_at = datetime.now(timezone.utc)
            await self.session.flush()
            return user
        else:
            user = models.AuthorizedUser(
                user_tg_id=user_tg_id,
                username=username,
                first_name=first_name,
                last_name=last_name
            )
            self.session.add(user)
            await self.session.flush()
            return user

    async def delete_by_tg_id(self, user_tg_id: int) -> bool:
        user = await self.get_by_tg_id(user_tg_id)
        if user:
            await self.session.delete(user)
            await self.session.flush()
            return True
        return False

    async def get_all(self) -> Sequence[models.AuthorizedUser]:
        return await self.list_all()

    async def remove(self, user_tg_id: int) -> bool:
        return await self.delete_by_tg_id(user_tg_id)


class BlockedUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_tg_id(self, user_tg_id: int) -> models.BlockedUser | None:
        result = await self.session.execute(
            select(models.BlockedUser).where(models.BlockedUser.user_tg_id == user_tg_id)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> Sequence[models.BlockedUser]:
        result = await self.session.execute(
            select(models.BlockedUser).order_by(models.BlockedUser.blocked_at.desc())
        )
        return result.scalars().all()

    async def create(
        self,
        *,
        user_tg_id: int,
        username: Optional[str] = None,
        reason: Optional[str] = None,
        blocked_by_tg_id: Optional[int] = None
    ) -> models.BlockedUser:
        blocked_user = models.BlockedUser(
            user_tg_id=user_tg_id,
            username=username,
            reason=reason,
            blocked_by_tg_id=blocked_by_tg_id
        )
        self.session.add(blocked_user)
        await self.session.flush()
        return blocked_user

    async def delete_by_tg_id(self, user_tg_id: int) -> bool:
        user = await self.get_by_tg_id(user_tg_id)
        if user:
            await self.session.delete(user)
            await self.session.flush()
            return True
        return False

    async def is_blocked(self, user_tg_id: int) -> bool:
        return await self.get_by_tg_id(user_tg_id) is not None

    async def get_all(self) -> Sequence[models.BlockedUser]:
        return await self.list_all()

    async def block_user(
        self,
        *,
        user_tg_id: int,
        username: Optional[str] = None,
        reason: Optional[str] = None,
        blocked_by_tg_id: Optional[int] = None,
    ) -> models.BlockedUser:
        existing = await self.get_by_tg_id(user_tg_id)
        if existing:
            return existing
        return await self.create(
            user_tg_id=user_tg_id,
            username=username,
            reason=reason,
            blocked_by_tg_id=blocked_by_tg_id,
        )

    async def unblock_user(self, user_tg_id: int) -> bool:
        return await self.delete_by_tg_id(user_tg_id)


class TelethonAccountRepository:
    
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, account_id: int) -> models.TelethonAccount | None:
        result = await self.session.execute(
            select(models.TelethonAccount).where(models.TelethonAccount.id == account_id)
        )
        return result.scalar_one_or_none()

    async def get_by_phone(self, phone: str) -> models.TelethonAccount | None:
        result = await self.session.execute(
            select(models.TelethonAccount).where(models.TelethonAccount.phone == phone)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> Sequence[models.TelethonAccount]:
        result = await self.session.execute(
            select(models.TelethonAccount)
            .order_by(models.TelethonAccount.priority.desc(), models.TelethonAccount.id)
        )
        return result.scalars().all()

    async def list_active(self) -> Sequence[models.TelethonAccount]:
        result = await self.session.execute(
            select(models.TelethonAccount)
            .where(models.TelethonAccount.is_active == True)
            .order_by(models.TelethonAccount.priority.desc(), models.TelethonAccount.id)
        )
        return result.scalars().all()

    async def get_primary(self) -> models.TelethonAccount | None:
        result = await self.session.execute(
            select(models.TelethonAccount)
            .where(models.TelethonAccount.is_primary == True, models.TelethonAccount.is_active == True)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        name: str,
        phone: str,
        session_string: str,
        api_id: int,
        api_hash: str,
        is_primary: bool = False,
        priority: int = 0,
    ) -> models.TelethonAccount:
        account = models.TelethonAccount(
            name=name,
            phone=phone,
            session_string=session_string,
            api_id=api_id,
            api_hash=api_hash,
            is_primary=is_primary,
            priority=priority,
        )
        self.session.add(account)
        await self.session.flush()
        return account

    async def update(
        self,
        account_id: int,
        **kwargs,
    ) -> models.TelethonAccount | None:
        account = await self.get_by_id(account_id)
        if not account:
            return None
        
        for key, value in kwargs.items():
            if hasattr(account, key):
                setattr(account, key, value)
        
        account.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return account

    async def update_flood_wait(
        self,
        account_id: int,
        seconds: int,
        until: datetime,
    ) -> None:
        await self.session.execute(
            update(models.TelethonAccount)
            .where(models.TelethonAccount.id == account_id)
            .values(
                flood_wait_until=until,
                total_flood_waits=models.TelethonAccount.total_flood_waits + 1,
                last_flood_wait_seconds=seconds,
                updated_at=datetime.now(timezone.utc),
            )
        )

    async def increment_requests(self, account_id: int) -> None:
        await self.session.execute(
            update(models.TelethonAccount)
            .where(models.TelethonAccount.id == account_id)
            .values(
                total_requests=models.TelethonAccount.total_requests + 1,
                last_used_at=datetime.now(timezone.utc),
            )
        )

    async def set_active(self, account_id: int, is_active: bool) -> None:
        await self.session.execute(
            update(models.TelethonAccount)
            .where(models.TelethonAccount.id == account_id)
            .values(
                is_active=is_active,
                updated_at=datetime.now(timezone.utc),
            )
        )

    async def set_primary(self, account_id: int) -> None:
        await self.session.execute(
            update(models.TelethonAccount).values(is_primary=False)
        )
        await self.session.execute(
            update(models.TelethonAccount)
            .where(models.TelethonAccount.id == account_id)
            .values(is_primary=True)
        )

    async def delete(self, account_id: int) -> bool:
        account = await self.get_by_id(account_id)
        if account:
            await self.session.delete(account)
            await self.session.flush()
            return True
        return False
