from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security as security_utils
from app.domain.repositories import SecurityRepository, LoginAttemptRepository
from app.config import settings


class SecurityService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = SecurityRepository(session)
        self.login_repo = LoginAttemptRepository(session)

    async def ensure_initialized(self) -> None:
        await self.repo.get_singleton()

    async def verify(
        self, 
        password: str, 
        max_attempts: int = 10,
        user_tg_id: Optional[int] = None,
        username: Optional[str] = None
    ) -> tuple[bool, Optional[datetime], int]:
        record = await self.repo.get_singleton()

        if record.locked_until:
            locked_until_aware = record.locked_until if record.locked_until.tzinfo else record.locked_until.replace(tzinfo=timezone.utc)
            if locked_until_aware > datetime.now(timezone.utc):
                return False, record.locked_until, record.failed_attempts

        password_correct = False
        
        if record.password_hash and record.password_hash.strip():
            password_correct = security_utils.verify_password(password, record.password_hash)
        else:
            correct_password = settings.admin_password
            if password == correct_password:
                password_correct = True
                hashed = security_utils.hash_password(password)
                await self.repo.update(password_hash=hashed)
        
        if user_tg_id is not None:
            try:
                await self.login_repo.log_attempt(
                    user_tg_id=user_tg_id,
                    username=username,
                    success=password_correct,
                    ip_address=None
                )
            except Exception:
                pass
        
        if password_correct:
            await self.repo.update(failed_attempts=0, last_failed_at=None, locked_until=None)
            return True, None, 0

        attempts, locked_until = security_utils.next_lockout_state(
            failed_attempts=record.failed_attempts, max_attempts=max_attempts
        )
        await self.repo.update(
            failed_attempts=attempts,
            last_failed_at=datetime.now(timezone.utc),
            locked_until=locked_until,
        )
        return False, locked_until, attempts

    async def update_password(self, new_password: str) -> None:
        hashed = security_utils.hash_password(new_password)
        await self.repo.update(password_hash=hashed, failed_attempts=0, locked_until=None)

    async def reset_attempts(self) -> None:
        await self.repo.update(failed_attempts=0, last_failed_at=None, locked_until=None)

    async def get_pickup_delay_minutes(self) -> int:
        record = await self.repo.get_singleton()
        return record.global_pickup_delay_minutes

    async def set_pickup_delay_minutes(self, minutes: int) -> None:
        await self.repo.update(global_pickup_delay_minutes=minutes)


__all__ = ["SecurityService"]
