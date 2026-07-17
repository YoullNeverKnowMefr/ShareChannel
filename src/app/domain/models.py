from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")

class ChainStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


class MediaType(str, enum.Enum):
    PHOTO = "photo"
    VIDEO = "video"
    TEXT = "text"
    ALBUM = "album"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Security(Base):
    __tablename__ = "security"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_failed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    global_pickup_delay_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Shop(Base):
    __tablename__ = "shops"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    owner_tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    categories: Mapped[list["Category"]] = relationship("Category", back_populates="shop", cascade="all,delete")
    chains: Mapped[list["Chain"]] = relationship("Chain", back_populates="shop", cascade="all,delete")


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    shop: Mapped["Shop"] = relationship("Shop", back_populates="categories")
    parent: Mapped[Optional["Category"]] = relationship("Category", remote_side=[id], back_populates="children")
    children: Mapped[list["Category"]] = relationship("Category", back_populates="parent", cascade="all,delete")
    chains: Mapped[list["Chain"]] = relationship("Chain", back_populates="category", cascade="all,delete")


class Chain(Base):
    __tablename__ = "chains"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    category_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("categories.id", ondelete="CASCADE"), nullable=True, index=True)
    status: Mapped[ChainStatus] = mapped_column(
        Enum(ChainStatus, native_enum=False, length=20), default=ChainStatus.PAUSED, nullable=False
    )
    source_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    source_chat_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source_chat_link: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    sink_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sink_chat_link: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    start_number: Mapped[int] = mapped_column(Integer, nullable=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    next_expected_number: Mapped[int] = mapped_column(Integer, nullable=False)
    last_sent_number: Mapped[Optional[int]] = mapped_column(Integer)
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    shop: Mapped["Shop"] = relationship("Shop", back_populates="chains")
    category: Mapped[Optional["Category"]] = relationship("Category", back_populates="chains")
    messages: Mapped[list["MessageMap"]] = relationship(
        "MessageMap", back_populates="chain", cascade="all,delete-orphan"
    )


class MessageMap(Base):
    __tablename__ = "message_map"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    chain_id: Mapped[int] = mapped_column(ForeignKey("chains.id", ondelete="CASCADE"), nullable=False, index=True)
    source_msg_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    source_msg_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sink_msg_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    sink_msg_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    number_tag: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    media_type: Mapped[MediaType] = mapped_column(
        Enum(MediaType, native_enum=False, length=20),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    chain: Mapped["Chain"] = relationship("Chain", back_populates="messages")


class RateLimitEvent(Base):
    __tablename__ = "rate_limit_events"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(100), nullable=False)
    until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    meta: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    actor_tg_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    user_tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ip_address: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)


class AuthorizedUser(Base):
    __tablename__ = "authorized_users"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    user_tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_login_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_login_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class BlockedUser(Base):
    __tablename__ = "blocked_users"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    user_tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    blocked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    blocked_by_tg_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class TelethonAccount(Base):
    __tablename__ = "telethon_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    session_string: Mapped[str] = mapped_column(String, nullable=False)
    api_id: Mapped[int] = mapped_column(Integer, nullable=False)
    api_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    flood_wait_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    total_flood_waits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_flood_wait_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_requests: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
