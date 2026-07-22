from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, Update
from telethon import events

from app.bot import create_dispatcher
from app.config import settings
from app.core.db import get_session
from app.core.logging import configure_logging, get_logger
from app.core.scheduler import scheduler
from app.domain.models import ChainStatus
from app.domain.repositories import ChainRepository
from app.domain.services.backup_service import BackupService
from app.domain.services.deletion_sync import DeletionSyncService
from app.domain.services.forwarding import ForwardingService, extract_number_tag
from app.domain.services.account_manager import account_manager
from app.domain.services.notifications import NotificationService, set_notifier

logger = get_logger(__name__)


async def setup_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Перезапустить бота / открыть меню"),
            BotCommand(command="menu", description="Главное меню"),
            BotCommand(command="restart", description="Полный перезапуск процесса бота"),
            BotCommand(command="shops", description="Магазины"),
        ]
    )
    logger.info("bot_commands_registered")


async def schedule_existing_chains(forwarding: ForwardingService) -> None:
    async with get_session() as session:
        repo = ChainRepository(session)
        chains = await repo.list_active()
    for chain in chains:
        scheduler.add_interval_job(
            forwarding.process_chain,
            job_id=f"chain:{chain.id}",
            seconds=chain.interval_seconds,
            kwargs={"chain_id": chain.id},
        )
        scheduler.add_one_off_job(
            forwarding.process_chain,
            job_id=f"chain:{chain.id}:bootstrap",
            run_date=datetime.now(timezone.utc) + timedelta(seconds=random.uniform(1, 5)),
            kwargs={"chain_id": chain.id},
        )


async def handle_new_message(event: events.NewMessage.Event, forwarding: ForwardingService) -> None:
    if event.chat_id is None:
        return
    
    if event.message.grouped_id is not None:
        logger.debug(
            "skipped_grouped_message",
            chat_id=event.chat_id,
            message_id=event.message.id,
            grouped_id=event.message.grouped_id,
        )
        return
    
    number = extract_number_tag(event.raw_text)
    if number is None:
        return

    async with get_session() as session:
        repo = ChainRepository(session)
        chains = await repo.list_by_source(event.chat_id)

    for chain in chains:
        if chain.status != ChainStatus.ACTIVE:
            continue
        
        if chain.last_sent_number is None:
            if number < chain.start_number:
                continue
        
        run_at = datetime.now(timezone.utc) + timedelta(seconds=random.uniform(1, 3))
        scheduler.add_one_off_job(
            forwarding.process_chain,
            job_id=f"chain:{chain.id}:new:{event.message.id}",
            run_date=run_at,
            kwargs={"chain_id": chain.id, "bypass_pickup_delay": True},
        )
        logger.info(
            "scheduled_new_message",
            chain_id=chain.id,
            number=number,
            run_at=run_at.isoformat(),
        )


async def main() -> None:
    configure_logging()
    bot = Bot(settings.bot_token, parse_mode=ParseMode.HTML)
    dp = create_dispatcher()

    forwarding_service = ForwardingService(bot)
    deletion_service = DeletionSyncService(bot)

    notifier = NotificationService(bot)
    set_notifier(notifier)

    dp["forwarding_service"] = forwarding_service
    dp["scheduler"] = scheduler
    dp["deletion_service"] = deletion_service
    dp["notifier"] = notifier

    scheduler.start()
    await account_manager.start()

    await schedule_existing_chains(forwarding_service)

    backup_service = BackupService(bot)
    dp["backup_service"] = backup_service
    if settings.backup_chat_id is not None:
        # По умолчанию каждые 2 минуты (минимум 30 секунд)
        interval_seconds = max(30.0, settings.backup_interval_minutes * 60.0)
        scheduler.add_interval_job(
            backup_service.run_scheduled_backup,
            job_id="db_backup",
            seconds=interval_seconds,
        )
        scheduler.add_one_off_job(
            backup_service.run_scheduled_backup,
            job_id="db_backup:bootstrap",
            run_date=datetime.now(timezone.utc) + timedelta(seconds=min(60.0, interval_seconds)),
        )
        logger.info(
            "backup_scheduler_enabled",
            chat_id=settings.backup_chat_id,
            interval_minutes=settings.backup_interval_minutes,
            interval_seconds=interval_seconds,
        )
    else:
        logger.warning("backup_scheduler_disabled", reason="BACKUP_CHAT_ID not set")

    async def new_message_handler(event: events.NewMessage.Event) -> None:
        await handle_new_message(event, forwarding_service)

    async def deleted_handler(event: events.MessageDeleted.Event) -> None:
        await deletion_service.handle_event(event)

    account_manager.add_event_handler(new_message_handler, events.NewMessage())
    account_manager.add_event_handler(deleted_handler, events.MessageDeleted())

    await setup_bot_commands(bot)

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown()
        await account_manager.stop()


if __name__ == "__main__":
    asyncio.run(main())
