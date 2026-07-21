from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime
from typing import Optional

from aiogram import Bot
from aiogram.types import FSInputFile

from app.config import settings
from app.core.db import recreate_engine
from app.core.logging import get_logger

logger = get_logger(__name__)

SQLITE_HEADER = b"SQLite format 3\x00"


class BackupService:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    def resolve_db_path(self) -> str:
        url = settings.database_url
        if not url.startswith("sqlite"):
            raise RuntimeError("Операции с бэкапом доступны только для SQLite-базы")
        raw = url.split("///", 1)[-1]
        db_path = raw if os.path.isabs(raw) else str((settings.project_root / raw).resolve())
        return db_path

    def _create_backup_file(self) -> tuple[str, str, int]:
        db_path = self.resolve_db_path()
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"Файл базы не найден: {db_path}")

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

    async def send_backup_to_chat(
        self,
        chat_id: int,
        *,
        reason: str = "manual",
    ) -> str:
        backup_path = ""
        try:
            backup_path, filename, size_kb = await asyncio.to_thread(self._create_backup_file)
            reason_label = {
                "manual": "по кнопке (в чат с ботом)",
                "channel": "по кнопке (в канал бэкапов)",
                "scheduled": "автобэкап",
            }.get(reason, reason)
            caption = (
                f"💾 Резервная копия базы ShareChannel\n"
                f"Причина: {reason_label}\n"
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

    async def send_backup(self, *, reason: str = "scheduled") -> Optional[str]:
        chat_id = settings.backup_chat_id
        if chat_id is None:
            raise RuntimeError(
                "Не задан BACKUP_CHAT_ID в .env. "
                "Укажите ID канала/чата и добавьте бота туда администратором."
            )
        return await self.send_backup_to_chat(chat_id, reason=reason)

    async def run_scheduled_backup(self) -> None:
        try:
            await self.send_backup(reason="scheduled")
        except Exception as exc:
            logger.error("scheduled_backup_failed", error=str(exc))

    @staticmethod
    def _validate_sqlite_file(path: str) -> None:
        if os.path.getsize(path) < 100:
            raise ValueError("Файл слишком маленький — это не база SQLite")
        with open(path, "rb") as fh:
            header = fh.read(16)
        if header != SQLITE_HEADER:
            raise ValueError("Файл не является базой SQLite")

        conn = sqlite3.connect(path)
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        finally:
            conn.close()

        required = {"chains", "security"}
        missing = required - tables
        if missing:
            raise ValueError(
                "В файле нет нужных таблиц ShareChannel: " + ", ".join(sorted(missing))
            )

    def _replace_db_file(self, uploaded_path: str) -> str:
        db_path = self.resolve_db_path()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safety_path = f"{db_path}.before_restore_{ts}"

        self._validate_sqlite_file(uploaded_path)

        if os.path.exists(db_path):
            shutil.copy2(db_path, safety_path)

        # Убираем WAL/SHM, иначе старые страницы могут пережить замену.
        for suffix in ("-wal", "-shm"):
            side = db_path + suffix
            if os.path.exists(side):
                os.remove(side)

        shutil.copy2(uploaded_path, db_path)
        return safety_path

    async def restore_from_path(self, uploaded_path: str) -> str:
        safety_path = await asyncio.to_thread(self._replace_db_file, uploaded_path)
        await recreate_engine()
        logger.info("database_restored", safety_backup=safety_path)
        return safety_path


__all__ = ["BackupService"]
