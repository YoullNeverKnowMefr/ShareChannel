from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Optional

from redis.asyncio import Redis

from app.config import settings


redis: Optional[Redis]
if settings.use_redis:
    redis = Redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
else:
    redis = None

_local_locks: Dict[str, asyncio.Lock] = {}


@asynccontextmanager
async def redis_lock(key: str, ttl: int = 60) -> AsyncIterator[bool]:
    if redis is None:
        lock = _local_locks.setdefault(key, asyncio.Lock())
        acquired = False
        if not lock.locked():
            await lock.acquire()
            acquired = True
        try:
            yield acquired
        finally:
            if acquired:
                lock.release()
    else:
        lock = redis.lock(key, timeout=ttl)
        acquired = await lock.acquire(blocking=False)
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    await lock.release()
                except Exception:
                    pass


__all__ = ["redis", "redis_lock"]
