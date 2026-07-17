from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from telethon.errors import RPCError
from telethon.tl.functions.channels import GetParticipantRequest
from telethon.tl.types import Channel, ChannelParticipantAdmin, ChannelParticipantCreator, InputPeerChannel

from app.core.logging import get_logger
from app.domain.services.account_manager import account_manager

logger = get_logger(__name__)


@dataclass
class PermissionStatus:
    can_read: bool
    can_post: bool
    can_delete: bool
    raw: dict


@dataclass
class PermissionsReport:
    source: PermissionStatus
    sink: PermissionStatus


class PermissionsService:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def _inspect_source_channel(self, chat_id: int) -> PermissionStatus:
        try:
            client = account_manager.client
            try:
                await client.get_messages(chat_id, limit=1)
            except (ValueError, TypeError):
                await client.get_dialogs(limit=None)
                await client.get_messages(chat_id, limit=1)
            return PermissionStatus(can_read=True, can_post=False, can_delete=False, raw={"read_ok": True})
        except Exception as exc:
            logger.error("permission_check_failed", chat_id=chat_id, error=str(exc))
            return PermissionStatus(False, False, False, {"error": str(exc)})

    async def _inspect_sink_channel(self, chat_id: int) -> PermissionStatus:
        try:
            chat_member = await asyncio.wait_for(
                self.bot.get_chat_member(chat_id, self.bot.id),
                timeout=10.0
            )
            
            is_admin = chat_member.status in ["administrator", "creator"]
            
            can_read = True
            can_post = False
            can_delete = False
            
            if is_admin and hasattr(chat_member, "can_post_messages"):
                can_post = chat_member.can_post_messages or False
            if is_admin and hasattr(chat_member, "can_delete_messages"):
                can_delete = chat_member.can_delete_messages or False
            
            return PermissionStatus(
                can_read=can_read,
                can_post=can_post,
                can_delete=can_delete,
                raw={
                    "status": chat_member.status,
                    "can_post_messages": getattr(chat_member, "can_post_messages", None),
                    "can_delete_messages": getattr(chat_member, "can_delete_messages", None),
                },
            )
        except asyncio.TimeoutError:
            logger.error("bot_permission_check_timeout", chat_id=chat_id)
            return PermissionStatus(False, False, False, {"error": "Timeout"})
        except TelegramBadRequest as exc:
            logger.error("bot_permission_check_failed", chat_id=chat_id, error=str(exc))
            return PermissionStatus(False, False, False, {"error": str(exc)})
        except Exception as exc:
            logger.error("bot_permission_check_error", chat_id=chat_id, error=str(exc))
            return PermissionStatus(False, False, False, {"error": str(exc)})

    async def check(self, *, source_chat_id: int, sink_chat_id: int) -> PermissionsReport:
        source_status = await self._inspect_source_channel(source_chat_id)
        sink_status = await self._inspect_sink_channel(sink_chat_id)
        return PermissionsReport(source=source_status, sink=sink_status)


__all__ = ["PermissionsService", "PermissionsReport", "PermissionStatus"]
