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
from app.core.db import dispose_engine, recreate_engine
from app.core.logging import get_logger

logger = get_logger(__name__)

SQLITE_HEADER = b"SQLite format 3\x00"
_backup_lock = asyncio.Lock()


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

    @staticmethod
    def _connect(db_path: str) -> sqlite3.Connection:
        conn = sqlite3.connect(db_path, timeout=60.0, check_same_thread=False)
        conn.execute("PRAGMA busy_timeout=60000")
        return conn

    def _create_backup_file(self) -> tuple[str, str, int]:
        db_path = self.resolve_db_path()
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"Файл базы не найден: {db_path}")

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"sharechannel_backup_{ts}.sqlite3"
        backup_path = os.path.join(tempfile.gettempdir(), filename)

        src = self._connect(db_path)
        dst = self._connect(backup_path)
        try:
            # Online backup API — безопасно при параллельных читателях/писателях.
            with dst:
                src.backup(dst, pages=100)
            dst.execute("PRAGMA integrity_check").fetchone()
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
        async with _backup_lock:
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
        if _backup_lock.locked():
            logger.warning("scheduled_backup_skipped_already_running")
            return
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

        conn = sqlite3.connect(path, timeout=30.0)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            if not row or row[0] != "ok":
                raise ValueError(f"Загруженная база повреждена: {row}")
            tables = {
                name
                for (name,) in conn.execute(
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

    def _checkpoint_and_close_side_files(self, db_path: str) -> None:
        if os.path.exists(db_path):
            conn = self._connect(db_path)
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.commit()
            except sqlite3.Error as exc:
                logger.warning("wal_checkpoint_failed", error=str(exc))
            finally:
                conn.close()

        for suffix in ("-wal", "-shm"):
            side = db_path + suffix
            if os.path.exists(side):
                try:
                    os.remove(side)
                except OSError as exc:
                    logger.warning("side_file_remove_failed", path=side, error=str(exc))

    def _replace_db_file(self, uploaded_path: str) -> str:
        """
        ВАЖНО: вызывать только после dispose_engine() — иначе подмена файла
        при живых соединениях портит SQLite (database disk image is malformed).
        """
        db_path = self.resolve_db_path()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safety_path = f"{db_path}.before_restore_{ts}"

        self._validate_sqlite_file(uploaded_path)

        # Безопасная копия текущей БД через backup API (не shutil.copy2 по живому WAL).
        if os.path.exists(db_path):
            src = self._connect(db_path)
            dst = self._connect(safety_path)
            try:
                with dst:
                    src.backup(dst, pages=100)
            finally:
                dst.close()
                src.close()

        self._checkpoint_and_close_side_files(db_path)

        # Атомарнее: копируем в temp рядом, потом replace.
        tmp_target = f"{db_path}.restoring_{ts}"
        src_up = self._connect(uploaded_path)
        dst_up = self._connect(tmp_target)
        try:
            with dst_up:
                src_up.backup(dst_up, pages=100)
            check = dst_up.execute("PRAGMA integrity_check").fetchone()
            if not check or check[0] != "ok":
                raise ValueError(f"Не удалось подготовить файл восстановления: {check}")
        finally:
            dst_up.close()
            src_up.close()

        os.replace(tmp_target, db_path)
        self._checkpoint_and_close_side_files(db_path)
        return safety_path

    async def restore_from_path(self, uploaded_path: str) -> str:
        # Сначала закрываем все SQLAlchemy-соединения, потом трогаем файлы на диске.
        await dispose_engine()
        try:
            safety_path = await asyncio.to_thread(self._replace_db_file, uploaded_path)
        except Exception:
            # Пытаемся поднять engine обратно на старом файле.
            await recreate_engine()
            raise
        await recreate_engine()
        logger.info("database_restored", safety_backup=safety_path)
        return safety_path


__all__ = ["BackupService"]
