from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict

from aiolimiter import AsyncLimiter

from app.config import settings


class RateLimiterSet:

    def __init__(self) -> None:
        self.global_limiter = AsyncLimiter(settings.global_rate_limit_per_sec, time_period=1)
        self.chat_limiters: Dict[int, AsyncLimiter] = {}

    def _get_chat_limiter(self, chat_id: int) -> AsyncLimiter:
        limiter = self.chat_limiters.get(chat_id)
        if limiter is None:
            limiter = AsyncLimiter(settings.chat_rate_limit_per_sec, time_period=1)
            self.chat_limiters[chat_id] = limiter
        return limiter

    @asynccontextmanager
    async def throttle(self, chat_id: int) -> AsyncIterator[None]:
        async with self.global_limiter:
            async with self._get_chat_limiter(chat_id):
                yield


rate_limiters = RateLimiterSet()

deletion_limiters = RateLimiterSet()


class BotPostCooldownSet:
    """КД 45с между постами в один и тот же канал-приёмник (разные каналы независимо)."""

    def __init__(self) -> None:
        self._period = max(1, settings.new_post_delay_seconds)
        self._chat_limiters: Dict[int, AsyncLimiter] = {}

    def _get(self, chat_id: int) -> AsyncLimiter:
        limiter = self._chat_limiters.get(chat_id)
        if limiter is None:
            limiter = AsyncLimiter(1, time_period=self._period)
            self._chat_limiters[chat_id] = limiter
        return limiter

    @asynccontextmanager
    async def throttle(self, chat_id: int) -> AsyncIterator[None]:
        async with self._get(chat_id):
            yield


bot_post_cooldowns = BotPostCooldownSet()

__all__ = ["RateLimiterSet", "rate_limiters", "deletion_limiters", "bot_post_cooldowns"]
