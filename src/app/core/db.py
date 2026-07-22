from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import settings


class Base(DeclarativeBase):
    pass


def _create_engine() -> AsyncEngine:
    is_sqlite = settings.database_url.startswith("sqlite")
    connect_args = {}
    engine_kwargs: dict = {
        "echo": False,
        "pool_pre_ping": True,
    }

    if is_sqlite:
        # Один writer + timeout: снижает риск повреждения SQLite при гонках.
        connect_args = {
            "timeout": 60.0,
            "check_same_thread": False,
        }
        engine_kwargs["poolclass"] = NullPool
        engine_kwargs["connect_args"] = connect_args
    else:
        engine_kwargs["connect_args"] = connect_args

    engine = create_async_engine(settings.database_url, **engine_kwargs)

    if is_sqlite:
        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=60000")
            # FULL безопаснее при внезапном kill/ребуте VPS, чем NORMAL.
            cur.execute("PRAGMA synchronous=FULL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


engine: AsyncEngine = _create_engine()
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def dispose_engine() -> None:
    """Закрыть все соединения перед подменой файла SQLite на диске."""
    global engine
    await engine.dispose()


async def recreate_engine() -> None:
    """Пересоздать engine после замены файла SQLite на диске."""
    global engine, SessionLocal
    await engine.dispose()
    engine = _create_engine()
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:

    session = SessionLocal()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


__all__ = [
    "Base",
    "engine",
    "get_session",
    "SessionLocal",
    "dispose_engine",
    "recreate_engine",
]
