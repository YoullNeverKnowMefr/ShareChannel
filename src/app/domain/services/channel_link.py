from __future__ import annotations

from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Chat
from telethon.errors import RPCError

from app.core.logging import get_logger
from app.domain import models
from app.domain.repositories import ChainRepository
from app.domain.services.account_manager import account_manager

logger = get_logger(__name__)


class ChannelLinkService:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    @staticmethod
    def _username_to_link(username: Optional[str]) -> Optional[str]:
        if not username:
            return None
        return f"https://t.me/{username.lstrip('@')}"

    @staticmethod
    def _private_chat_link(chat_id: int) -> Optional[str]:
        if chat_id >= 0:
            return None
        short_id = abs(chat_id) % 10**10
        return f"https://t.me/c/{short_id}"

    def _link_from_aiogram_chat(self, chat: Optional[Chat]) -> Optional[str]:
        if not chat:
            return None
        return self._username_to_link(getattr(chat, "username", None)) or getattr(chat, "invite_link", None)

    async def _resolve_with_bot(self, chat_id: int) -> Optional[str]:
        try:
            chat = await self.bot.get_chat(chat_id)
            return self._link_from_aiogram_chat(chat)
        except TelegramBadRequest as exc:
            logger.warning("bot_chat_lookup_failed", chat_id=chat_id, error=str(exc))
        except Exception as exc:
            logger.error("bot_chat_lookup_error", chat_id=chat_id, error=str(exc))
        return None

    async def _resolve_with_telethon(self, chat_id: int) -> Optional[str]:
        try:
            entity = await account_manager.client.get_entity(chat_id)
            username = getattr(entity, "username", None)
            if not username:
                usernames = getattr(entity, "usernames", None)
                if usernames:
                    username = getattr(usernames[0], "username", None)
            link = self._username_to_link(username)
            if link:
                return link
        except RPCError as exc:
            logger.warning("telethon_chat_lookup_failed", chat_id=chat_id, error=str(exc))
        except Exception as exc:
            logger.error("telethon_chat_lookup_error", chat_id=chat_id, error=str(exc))
        return None

    async def resolve_link(
        self,
        *,
        chat_id: int,
        prefer_bot: bool = False,
        aiogram_chat: Optional[Chat] = None,
    ) -> Optional[str]:
        link = self._link_from_aiogram_chat(aiogram_chat)
        if link:
            return link

        resolvers = [self._resolve_with_bot, self._resolve_with_telethon]
        if not prefer_bot:
            resolvers.reverse()

        for resolver in resolvers:
            link = await resolver(chat_id)
            if link:
                return link

        return self._private_chat_link(chat_id)

    async def ensure_chain_links(self, chain: models.Chain, repo: ChainRepository) -> models.Chain:
        updates: dict[str, str] = {}

        if not chain.source_chat_link:
            source_link = await self.resolve_link(chat_id=chain.source_chat_id, prefer_bot=False)
            if source_link:
                updates["source_chat_link"] = source_link

        if not chain.sink_chat_link:
            sink_link = await self.resolve_link(chat_id=chain.sink_chat_id, prefer_bot=True)
            if sink_link:
                updates["sink_chat_link"] = sink_link

        if updates:
            await repo.update_chat_links(chain.id, **updates)
            chain.source_chat_link = updates.get("source_chat_link", chain.source_chat_link)
            chain.sink_chat_link = updates.get("sink_chat_link", chain.sink_chat_link)

        return chain


__all__ = ["ChannelLinkService"]
