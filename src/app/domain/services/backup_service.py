from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
from datetime import datetime
from typing import Optional

from aiogram import Bot
from aiogram.types import FSInputFile

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class BackupService:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    def _resolve_db_path(self) -> str:
        url = settings.database_url
        if not url.startswith("sqlite"):
            raise RuntimeError("Бэкап доступен только для SQLite-базы")
        raw = url.split("///", 1)[-1]
        db_path = raw if os.path.isabs(raw) else str((settings.project_root / raw).resolve())
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"Файл базы не найден: {db_path}")
        return db_path

    def _create_backup_file(self) -> tuple[str, str, int]:
        db_path = self._resolve_db_path()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"sharechannel_backup_{ts}.sqlite3"
        backup_path = os.path.join(tempfile.gettempdir(), filename)

        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(backup_path)
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
            src.close()

        size_kb = os.path.getsize(backup_path) // 1024
        return backup_path, filename, size_kb

    async def send_backup(self, *, reason: str = "manual") -> Optional[str]:
        chat_id = settings.backup_chat_id
        if chat_id is None:
            raise RuntimeError(
                "Не задан BACKUP_CHAT_ID в .env. "
                "Укажите ID канала/чата и добавьте бота туда администратором."
            )

        backup_path = ""
        try:
            backup_path, filename, size_kb = await asyncio.to_thread(self._create_backup_file)
            caption = (
                f"💾 Резервная копия базы ShareChannel\n"
                f"Причина: {'по кнопке' if reason == 'manual' else 'автобэкап'}\n"
                f"Размер: {size_kb} КБ\n"
                f"Время: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
            )
            await self.bot.send_document(
                chat_id,
                document=FSInputFile(backup_path, filename=filename),
                caption=caption,
            )
            logger.info(
                "backup_sent",
                chat_id=chat_id,
                reason=reason,
                size_kb=size_kb,
                filename=filename,
            )
            return filename
        except Exception as exc:
            logger.error("backup_failed", reason=reason, error=str(exc))
            raise
        finally:
            if backup_path:
                try:
                    os.remove(backup_path)
                except OSError:
                    pass

    async def run_scheduled_backup(self) -> None:
        try:
            await self.send_backup(reason="scheduled")
        except Exception as exc:
            logger.error("scheduled_backup_failed", error=str(exc))


__all__ = ["BackupService"]
