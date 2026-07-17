from __future__ import annotations

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage

from app.bot.routers import accounts, auth, categories, chain_manage, chains_wizard, menu, security, shops
from app.config import settings
from app.core.redis import redis as redis_client


def create_dispatcher() -> Dispatcher:
    if settings.use_redis and redis_client is not None:
        storage = RedisStorage(redis_client)
    else:
        storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    dp.include_router(auth.router)
    dp.include_router(menu.router)
    dp.include_router(security.router)
    dp.include_router(shops.router)
    dp.include_router(categories.router)
    dp.include_router(chains_wizard.router)
    dp.include_router(chain_manage.router)
    dp.include_router(accounts.router)
    return dp


__all__ = ["create_dispatcher"]
