from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from aiogram import Bot
from aiogram.enums import ParseMode

from app.core.db import get_session
from app.core.logging import get_logger
from app.domain.repositories import AuthorizedUserRepository

logger = get_logger(__name__)


class NotificationService:

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._last_sent: Dict[str, datetime] = {}

    async def broadcast(
        self,
        text: str,
        *,
        dedup_key: Optional[str] = None,
        cooldown_seconds: int = 60,
    ) -> None:
        if dedup_key is not None:
            now = datetime.now(timezone.utc)
            last = self._last_sent.get(dedup_key)
            if last is not None and (now - last) < timedelta(seconds=cooldown_seconds):
                return
            self._last_sent[dedup_key] = now

        recipients: List[int] = []
        try:
            async with get_session() as session:
                users = await AuthorizedUserRepository(session).list_all()
                recipients = [u.user_tg_id for u in users]
        except Exception as exc:
            logger.error("notify_recipients_failed", error=str(exc))
            return

        for tg_id in recipients:
            try:
                await self.bot.send_message(tg_id, text, parse_mode=ParseMode.HTML)
            except Exception as exc:
                logger.warning("notify_send_failed", user_id=tg_id, error=str(exc))



_notifier: Optional[NotificationService] = None


def set_notifier(notifier: NotificationService) -> None:
    global _notifier
    _notifier = notifier


def get_notifier() -> Optional[NotificationService]:
    return _notifier


async def notify_admins(
    text: str,
    *,
    dedup_key: Optional[str] = None,
    cooldown_seconds: int = 60,
) -> None:
    notifier = _notifier
    if notifier is None:
        return
    try:
        await notifier.broadcast(text, dedup_key=dedup_key, cooldown_seconds=cooldown_seconds)
    except Exception as exc:
        logger.error("notify_admins_failed", error=str(exc))


__all__ = ["NotificationService", "set_notifier", "get_notifier", "notify_admins"]
