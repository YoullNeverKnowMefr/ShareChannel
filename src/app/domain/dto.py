from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.domain.models import ChainStatus, MediaType


class ShopDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_tg_id: int
    name: str
    created_at: datetime


class CategoryDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    shop_id: int
    parent_id: Optional[int] = None
    name: str
    created_at: datetime


class ChainDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    shop_id: int
    category_id: Optional[int] = None
    status: ChainStatus
    source_chat_id: int
    source_chat_title: Optional[str] = None
    source_chat_link: Optional[str] = None
    sink_chat_id: int
    sink_chat_link: Optional[str] = None
    start_number: int
    interval_seconds: int
    next_expected_number: int
    last_sent_number: Optional[int]
    last_activity_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class MessageMapDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    chain_id: int
    source_msg_id: int
    source_msg_date: datetime
    sink_msg_id: int
    sink_msg_date: datetime
    number_tag: int
    media_type: MediaType
    created_at: datetime


class RateLimitEventDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    scope: str
    until: datetime
    created_at: datetime
    meta: dict | None = None


class TelethonAccountDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    phone: str
    api_id: int
    is_active: bool
    is_primary: bool
    priority: int
    flood_wait_until: Optional[datetime] = None
    total_flood_waits: int
    last_flood_wait_seconds: Optional[int] = None
    total_requests: int
    last_used_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
