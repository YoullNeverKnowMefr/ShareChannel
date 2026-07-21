from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Sequence

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InputMediaPhoto, InputMediaVideo, Message as BotMessage
from structlog.stdlib import BoundLogger
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.custom import Message as TlMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.db import get_session
from app.core.limiter import RateLimiterSet, bot_post_cooldowns, rate_limiters
from app.core.logging import get_logger
from app.core.redis import redis_lock
from app.core.scheduler import scheduler
from app.domain import models
from app.domain.repositories import ChainRepository, MessageMapRepository, RateLimitRepository, ShopRepository
from app.domain.services.mapping import MappingService
from app.domain.services.account_manager import account_manager
from app.domain.services.notifications import notify_admins
from app.domain.services.text_sanitizer import sanitize

NUMBER_PATTERN = re.compile(r"(?<!\w)#(\d{1,10})\b")
MAX_HISTORY_SCAN = 2000
START_SEARCH_LIMIT = 10000
SINK_DEDUP_SCAN = 200

history_limiters = RateLimiterSet()

SIDE_BOT = "у бота (публикация в приёмник)"
SIDE_OBSERVER = "у аккаунта-наблюдателя (чтение источника)"


def extract_number_tag(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    matches = NUMBER_PATTERN.findall(text)
    if not matches:
        return None
    return int(matches[-1])


class ForwardingService:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self.logger: BoundLogger = get_logger(__name__)

    def _log_chain_skipped(self, chain_id: int, reason: str, **fields: object) -> None:
        self.logger.info(
            "chain_skipped",
            chain_id=chain_id,
            reason=reason,
            message="chain skipped, other chains continue",
            **fields,
        )

    async def process_chain(self, chain_id: int, bypass_pickup_delay: bool = False) -> None:
        lock_key = f"chain:{chain_id}:lock"

        async with redis_lock(lock_key, ttl=300) as acquired:
            if not acquired:
                self._log_chain_skipped(chain_id, "lock_busy")
                return

            async with get_session() as session:
                chain_repo = ChainRepository(session)
                chain = await chain_repo.get_by_id(chain_id)
                if chain is None:
                    self.logger.warning("chain_not_found", chain_id=chain_id)
                    return

                if chain.status != models.ChainStatus.ACTIVE:
                    self._log_chain_skipped(chain_id, "not_active", status=chain.status.value)
                    return

                try:
                    message = await self._find_unpublished_message(chain, bypass_pickup_delay, session)
                except RuntimeError as exc:
                    self._log_chain_skipped(chain.id, "no_observer", error=str(exc))
                    await notify_admins(
                        "⚠️ Нет доступного аккаунта-наблюдателя — пересылка приостановлена.\n"
                        "Добавьте или переподключите Telethon-аккаунт: 🛡 Безопасность → 🔑 Telethon аккаунты.",
                        dedup_key="no_observer",
                        cooldown_seconds=600,
                    )
                    return

                if message is None:
                    self._log_chain_skipped(chain.id, "no_new_messages")
                    return

                await self._send_message(chain, message, session)

    async def _find_unpublished_message(
        self, 
        chain: models.Chain, 
        bypass_pickup_delay: bool, 
        session: AsyncSession
    ) -> Optional[TlMessage]:
        msg_map_repo = MessageMapRepository(session)
        last_mapping = await msg_map_repo.get_last_for_chain(chain.id)
        start_from_id = last_mapping.source_msg_id if last_mapping else None
        start_found = last_mapping is not None
        start_message: Optional[TlMessage] = None
        expected_min_number = last_mapping.number_tag if last_mapping else chain.start_number

        if last_mapping:
            self.logger.info(
                "continuing_from_last",
                chain_id=chain.id,
                last_number=last_mapping.number_tag,
                last_source_id=start_from_id,
                expected_min_number=expected_min_number,
            )
        else:
            start_message = await self._locate_start_message(chain, bypass_pickup_delay)
            if start_message is None:
                self.logger.warning(
                    "start_message_not_found",
                    chain_id=chain.id,
                    start_number=chain.start_number,
                    scanned=MAX_HISTORY_SCAN,
                )
                return None
            start_from_id = max(0, (start_message.id or 0) - 1)
            start_found = True
            self.logger.info(
                "found_start_message",
                chain_id=chain.id,
                start_number=chain.start_number,
                start_source_id=start_message.id,
            )

        messages_by_id = await self._collect_candidates(
            chain.source_chat_id,
            bypass_pickup_delay=bypass_pickup_delay,
            start_from_id=start_from_id,
        )

        if not messages_by_id:
            self.logger.debug(
                "no_candidates_after_start",
                chain_id=chain.id,
                start_from_id=start_from_id,
            )
            return None

        sorted_messages = sorted(messages_by_id.items(), key=lambda x: x[0])

        for msg_id, message in sorted_messages:
            number = extract_number_tag(message.text or message.message)
            if number is None:
                continue

            existing = await msg_map_repo.get_by_source(chain.id, msg_id)
            if existing is None:
                if number > expected_min_number:
                    self.logger.info(
                        "gap_detected",
                        chain_id=chain.id,
                        expected=expected_min_number,
                        found=number,
                        source_msg_id=msg_id,
                    )
                self.logger.info(
                    "found_unpublished_message",
                    chain_id=chain.id,
                    source_msg_id=msg_id,
                    number_tag=number,
                    message_date=message.date.isoformat(),
                )
                return message

        if not start_found:
            self.logger.warning(
                "start_message_not_found",
                chain_id=chain.id,
                start_number=chain.start_number,
                scanned=len(sorted_messages),
            )

        return None

    async def _locate_start_message(
        self,
        chain: models.Chain,
        bypass_pickup_delay: bool,
    ) -> Optional[TlMessage]:
        delay_minutes = 0
        if not bypass_pickup_delay:
            async with get_session() as session:
                from app.domain.services.security_service import SecurityService
                delay_minutes = await SecurityService(session).get_pickup_delay_minutes()

        min_pickup_date = datetime.now(timezone.utc) - timedelta(minutes=delay_minutes)

        scanned_recent = 0
        recent_messages: list[TlMessage] = []
        attempts = 0
        while attempts < 3:
            try:
                async with history_limiters.throttle(chain.source_chat_id):
                    async for message in account_manager.iter_messages(
                        chain.source_chat_id,
                        limit=MAX_HISTORY_SCAN,
                        reverse=False,
                    ):
                        recent_messages.append(message)
                break
            except FloodWaitError as exc:
                attempts += 1
                self.logger.warning(
                    "start_history_flood_wait",
                    chain_id=chain.id,
                    seconds=exc.seconds,
                    attempt=attempts,
                )
                await asyncio.sleep(exc.seconds + 1)
            except RPCError as exc:
                self.logger.error("start_history_rpc_error", chain_id=chain.id, error=str(exc))
                return None

        for message in recent_messages:
            scanned_recent += 1
            number = extract_number_tag(message.message or "")
            if number != chain.start_number:
                continue

            if not bypass_pickup_delay:
                message_date = self._ensure_aware(message.date)
                if message_date > min_pickup_date:
                    continue

            return message

        candidates: list[TlMessage] = []
        try:
            attempts = 0
            while attempts < 3:
                try:
                    async with history_limiters.throttle(chain.source_chat_id):
                        async for message in account_manager.iter_messages(
                            chain.source_chat_id,
                            search=f"#{chain.start_number}",
                            limit=START_SEARCH_LIMIT,
                            reverse=True,
                        ):
                            number = extract_number_tag(message.message or "")
                            if number != chain.start_number:
                                continue

                            if not bypass_pickup_delay:
                                message_date = self._ensure_aware(message.date)
                                if message_date > min_pickup_date:
                                    continue

                            candidates.append(message)
                    break
                except FloodWaitError as exc:
                    attempts += 1
                    self.logger.warning(
                        "start_search_flood_wait",
                        chain_id=chain.id,
                        seconds=exc.seconds,
                        attempt=attempts,
                    )
                    await asyncio.sleep(exc.seconds + 1)
                except RPCError as exc:
                    self.logger.error("start_search_rpc_error", chain_id=chain.id, error=str(exc))
                    return None
        except Exception as exc:
            self.logger.warning(
                "start_search_failed",
                chain_id=chain.id,
                start_number=chain.start_number,
                error=str(exc),
            )

        if candidates:
            start_message = min(candidates, key=lambda m: m.id or 0)
            self.logger.info(
                "start_found_by_search",
                chain_id=chain.id,
                start_number=chain.start_number,
                source_msg_id=start_message.id,
                scanned_recent=scanned_recent,
                candidates=len(candidates),
            )
            return start_message

        self.logger.info(
            "start_not_found_in_recent_history",
            chain_id=chain.id,
            start_number=chain.start_number,
            scanned_recent=scanned_recent,
            searched=START_SEARCH_LIMIT,
        )
        return None

    async def _collect_candidates(
        self, 
        chat_id: int, 
        bypass_pickup_delay: bool = False,
        start_from_id: int | None = None
    ) -> dict[int, TlMessage]:
        delay_minutes = 0
        if not bypass_pickup_delay:
            async with get_session() as session:
                from app.domain.services.security_service import SecurityService
                security_service = SecurityService(session)
                delay_minutes = await security_service.get_pickup_delay_minutes()
        
        min_pickup_date = datetime.now(timezone.utc) - timedelta(minutes=delay_minutes)
        
        result: dict[int, TlMessage] = {}
        grouped_messages: dict[int, list[TlMessage]] = {}
        count = 0

        min_id = start_from_id if start_from_id is not None else 0

        messages: list[TlMessage] = []
        flood_wait_happened = False
        attempts = 0

        while attempts < 3:
            try:
                async with history_limiters.throttle(chat_id):
                    async for message in account_manager.iter_messages(
                        chat_id,
                        limit=MAX_HISTORY_SCAN,
                        reverse=True,
                        min_id=min_id,
                    ):
                        messages.append(message)
                break
            except FloodWaitError as exc:
                flood_wait_happened = True
                attempts += 1
                self.logger.warning(
                    "history_flood_wait",
                    chat_id=chat_id,
                    seconds=exc.seconds,
                    attempt=attempts,
                )
                await asyncio.sleep(exc.seconds + 1)
            except RPCError as exc:
                self.logger.error("history_rpc_error", chat_id=chat_id, error=str(exc))
                return {}

        for message in messages:
            count += 1
            
            number = extract_number_tag(message.message or "")
            if number is None:
                continue
            
            if not bypass_pickup_delay:
                message_date = self._ensure_aware(message.date)
                
                if message_date > min_pickup_date:
                    continue
            
            if message.grouped_id:
                if message.grouped_id not in grouped_messages:
                    grouped_messages[message.grouped_id] = []
                grouped_messages[message.grouped_id].append(message)
            else:
                result[message.id] = message
        
        for grouped_id, messages in grouped_messages.items():
            if not messages:
                continue
            first_msg = min(messages, key=lambda m: m.id)
            result[first_msg.id] = first_msg
                
        self.logger.info(
            "candidate_scan", 
            chat_id=chat_id, 
            scanned=count, 
            matched=len(result),
            scan_direction="forward",
            start_from_id=start_from_id,
            flood_wait=flood_wait_happened,
        )
        return result

    async def _send_message(self, chain: models.Chain, message: TlMessage, session: AsyncSession) -> None:
        assert message.id is not None
        repository = MessageMapRepository(session)

        existing = await repository.get_by_source(chain.id, message.id)
        if existing:
            self._log_chain_skipped(
                chain.id,
                "already_published",
                source_msg_id=message.id,
                number_tag=existing.number_tag,
            )
            return

        number_tag = extract_number_tag(message.message or message.raw_text)
        if number_tag is None:
            self._log_chain_skipped(chain.id, "message_without_number", source_msg_id=message.id)
            return

        # Уже есть в БД по номеру (на случай рассинхрона source_msg_id).
        existing_by_number = await repository.get_last_by_number(chain.id, number_tag)
        if existing_by_number is not None:
            await ChainRepository(session).advance_pointer(
                chain_id=chain.id,
                next_expected_number=number_tag + 1,
                last_sent_number=number_tag,
            )
            self._log_chain_skipped(
                chain.id,
                "already_published_by_number",
                source_msg_id=message.id,
                number_tag=number_tag,
                sink_msg_id=existing_by_number.sink_msg_id,
            )
            return

        # Пост уже в приёмнике (после отката БД) — не дублируем, только синхронизируем карту.
        sink_existing = await self._find_number_in_sink(chain.sink_chat_id, number_tag)
        if sink_existing is not None:
            media_group = await self._collect_album(message) if message.grouped_id else None
            await self._register_without_send(
                chain=chain,
                message=message,
                media_group=media_group,
                number_tag=number_tag,
                sink_msg_id=sink_existing.id,
                sink_msg_date=self._ensure_aware(sink_existing.date),
                session=session,
            )
            self.logger.info(
                "duplicate_avoided_sink_match",
                chain_id=chain.id,
                source_msg_id=message.id,
                sink_msg_id=sink_existing.id,
                number_tag=number_tag,
            )
            return

        media_group = await self._collect_album(message) if message.grouped_id else None

        sent_messages = None
        last_error = None
        for attempt in range(1, 4):
            try:
                sent_messages = await self._deliver(chain, message, media_group)
                break
            except TelegramRetryAfter as exc:
                await self._record_rate_limit(chain, exc.retry_after)
                self.logger.warning(
                    "bot_flood_wait_retry",
                    chain_id=chain.id,
                    attempt=attempt,
                    retry_after=exc.retry_after,
                    sink_chat_id=chain.sink_chat_id,
                )
                await notify_admins(
                    f"⏳ Цепочка #{chain.id}: лимит Telegram (FloodWait {exc.retry_after}с), ждём и повторяем.",
                    dedup_key=f"pub_flood:{chain.id}",
                    cooldown_seconds=120,
                )
                await asyncio.sleep(exc.retry_after)
                last_error = exc
                continue
            except TelegramForbiddenError as exc:
                await self._mark_chain_error(chain, f"Нет доступа к приёмнику: {exc.message}", side=SIDE_BOT)
                return
            except Exception as exc:
                last_error = exc
                if self._is_bot_flood_error(exc):
                    self.logger.warning(
                        "bot_flood_wait_retry",
                        chain_id=chain.id,
                        attempt=attempt,
                        error=str(exc),
                        sink_chat_id=chain.sink_chat_id,
                    )
                    await asyncio.sleep(min(8 * attempt, 30))
                    continue
                self.logger.warning("deliver_retry", chain_id=chain.id, attempt=attempt, error=str(exc))
                await asyncio.sleep(min(5 * attempt, 15))
                continue

        if sent_messages is None:
            # FloodWait бота: не останавливаем цепочку, пропускаем тик и идём дальше.
            if isinstance(last_error, TelegramRetryAfter) or self._is_bot_flood_error(last_error):
                retry_after = getattr(last_error, "retry_after", None)
                self._log_chain_skipped(
                    chain.id,
                    "bot_flood_wait",
                    retry_after=retry_after,
                    sink_chat_id=chain.sink_chat_id,
                    source_msg_id=message.id,
                    error=str(last_error),
                )
                await notify_admins(
                    f"⏭️ Цепочка #{chain.id}: пропущена из‑за FloodWait бота"
                    + (f" ({retry_after}с)" if retry_after else "")
                    + ". Цепочка активна, повтор на следующем интервале.",
                    dedup_key=f"chain_skip_flood:{chain.id}",
                    cooldown_seconds=180,
                )
                return

            await self._mark_chain_error(
                chain,
                f"Не удалось скопировать пост после 3 попыток: {last_error}",
                side=self._error_side(last_error),
            )
            return

        mapper = MappingService(session)
        
        if media_group and len(sent_messages) > 0:
            for i, album_msg in enumerate(media_group):
                if i < len(sent_messages):
                    sink_msg = sent_messages[i]
                    await mapper.register(
                        chain_id=chain.id,
                        source_msg_id=album_msg.id,
                        source_msg_date=self._ensure_aware(album_msg.date),
                        sink_msg_id=sink_msg.message_id,
                        sink_msg_date=self._ensure_aware(sink_msg.date),
                        number_tag=number_tag,
                        media_type=self._deduce_media_type(message, media_group),
                    )
        else:
            sink_message = sent_messages[0]
            await mapper.register(
                chain_id=chain.id,
                source_msg_id=message.id,
                source_msg_date=self._ensure_aware(message.date),
                sink_msg_id=sink_message.message_id,
                sink_msg_date=self._ensure_aware(sink_message.date),
                number_tag=number_tag,
                media_type=self._deduce_media_type(message, media_group),
            )

        await ChainRepository(session).advance_pointer(
            chain_id=chain.id,
            next_expected_number=number_tag + 1,
            last_sent_number=number_tag,
        )

        self.logger.info(
            "message_forwarded",
            chain_id=chain.id,
            source_msg_id=message.id,
            sink_msg_id=sent_messages[0].message_id if sent_messages else None,
            number_tag=number_tag,
        )

    async def _find_number_in_sink(self, sink_chat_id: int, number_tag: int) -> Optional[TlMessage]:
        """Ищет в приёмнике уже опубликованный пост с тем же #N. Ошибки не роняют цепочку."""
        try:
            async for sink_msg in account_manager.iter_messages(
                sink_chat_id,
                limit=SINK_DEDUP_SCAN,
                reverse=False,
            ):
                text = sink_msg.text or sink_msg.message or ""
                found = extract_number_tag(text)
                if found == number_tag:
                    return sink_msg
        except Exception as exc:
            self.logger.warning(
                "sink_dedup_scan_failed",
                sink_chat_id=sink_chat_id,
                number_tag=number_tag,
                error=str(exc),
            )
        return None

    async def _register_without_send(
        self,
        *,
        chain: models.Chain,
        message: TlMessage,
        media_group: Optional[List[TlMessage]],
        number_tag: int,
        sink_msg_id: int,
        sink_msg_date: datetime,
        session: AsyncSession,
    ) -> None:
        mapper = MappingService(session)
        media_type = self._deduce_media_type(message, media_group)
        if media_group and len(media_group) > 1:
            for album_msg in media_group:
                already = await MessageMapRepository(session).get_by_source(chain.id, album_msg.id)
                if already:
                    continue
                await mapper.register(
                    chain_id=chain.id,
                    source_msg_id=album_msg.id,
                    source_msg_date=self._ensure_aware(album_msg.date),
                    sink_msg_id=sink_msg_id,
                    sink_msg_date=sink_msg_date,
                    number_tag=number_tag,
                    media_type=media_type,
                )
        else:
            await mapper.register(
                chain_id=chain.id,
                source_msg_id=message.id,
                source_msg_date=self._ensure_aware(message.date),
                sink_msg_id=sink_msg_id,
                sink_msg_date=sink_msg_date,
                number_tag=number_tag,
                media_type=media_type,
            )
        await ChainRepository(session).advance_pointer(
            chain_id=chain.id,
            next_expected_number=number_tag + 1,
            last_sent_number=number_tag,
        )

    def _ensure_aware(self, dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    async def _record_rate_limit(self, chain: models.Chain, retry_after: int) -> None:
        async with get_session() as session:
            repo = RateLimitRepository(session)
            await repo.record(
                scope=f"chat:{chain.sink_chat_id}",
                until=datetime.now(timezone.utc) + timedelta(seconds=retry_after),
                meta={"chain_id": chain.id},
            )

    @staticmethod
    def _is_bot_flood_error(exc: object) -> bool:
        if isinstance(exc, TelegramRetryAfter):
            return True
        text = str(exc).lower()
        return "flood control" in text or "too many requests" in text or "retry after" in text

    @staticmethod
    def _error_side(exc: object) -> str:
        if isinstance(exc, (FloodWaitError, RPCError)):
            return SIDE_OBSERVER
        return SIDE_BOT

    async def _mark_chain_error(self, chain: models.Chain, reason: str, side: str = SIDE_BOT) -> None:
        self.logger.error("chain_error", chain_id=chain.id, reason=reason, side=side)
        shop_name = "—"
        try:
            async with get_session() as session:
                await ChainRepository(session).update_status(chain.id, models.ChainStatus.ERROR)
                shop = await ShopRepository(session).get_by_id(chain.shop_id)
                if shop:
                    shop_name = shop.name
        except Exception as exc:
            self.logger.error("chain_error_status_update_failed", chain_id=chain.id, error=str(exc))
        scheduler.remove(f"chain:{chain.id}")
        source_name = chain.source_chat_title or str(chain.source_chat_id)
        await notify_admins(
            f"🔴 <b>Ошибка пересылки — цепочка остановлена</b>\n"
            f"🛒 Магазин: {shop_name}\n"
            f"🔗 Цепочка #{chain.id}: {source_name} → <code>{chain.sink_chat_id}</code>\n"
            f"⚠️ Ошибка {side}\n"
            f"Причина: {reason}\n\n"
            f"Проверьте доступ и нажмите «Возобновить» в карточке цепочки.",
            dedup_key=f"chain_error:{chain.id}",
            cooldown_seconds=300,
        )

    async def _collect_album(self, message: TlMessage) -> List[TlMessage]:
        if not message.grouped_id:
            return []
        grouped_id = message.grouped_id
        assert message.chat_id is not None

        album: list[TlMessage] = []
        async for part in account_manager.iter_messages(
            message.chat_id,
            min_id=message.id - 1,
            reverse=True,
            limit=20,
        ):
            if part.grouped_id == grouped_id:
                album.append(part)
            elif album:
                break

        album.sort(key=lambda item: item.id)
        return album or [message]

    def _deduce_media_type(self, message: TlMessage, media_group: Optional[List[TlMessage]]) -> models.MediaType:
        if media_group and len(media_group) > 1:
            return models.MediaType.ALBUM
        if message.photo:
            return models.MediaType.PHOTO
        if message.video or message.document:
            return models.MediaType.VIDEO
        return models.MediaType.TEXT

    async def _deliver(
        self,
        chain: models.Chain,
        message: TlMessage,
        media_group: Optional[List[TlMessage]],
    ) -> List[BotMessage]:
        async with bot_post_cooldowns.throttle(chain.sink_chat_id):
            async with rate_limiters.throttle(chain.sink_chat_id):
                caption = sanitize(message.message or message.raw_text)

                if media_group and len(media_group) > 1:
                    payload = await self._album_payload(media_group, caption)
                    result = await self.bot.send_media_group(
                        chain.sink_chat_id,
                        media=payload,
                    )
                    return result if result else []

                if message.photo:
                    data = await self._download_media(message)
                    buff = BufferedInputFile(data, filename=f"{message.id}.jpg")
                    return [await self.bot.send_photo(
                        chain.sink_chat_id,
                        photo=buff,
                        caption=caption,
                        parse_mode=ParseMode.HTML,
                    )]

                if message.video or message.document:
                    data = await self._download_media(message)
                    buff = BufferedInputFile(data, filename=f"{message.id}.mp4")
                    return [await self.bot.send_video(
                        chain.sink_chat_id,
                        video=buff,
                        caption=caption,
                        parse_mode=ParseMode.HTML,
                    )]

                return [await self.bot.send_message(
                    chain.sink_chat_id,
                    text=caption or "",
                    parse_mode=ParseMode.HTML,
                )]



    async def _album_payload(
        self, album: Sequence[TlMessage], caption: Optional[str]
    ) -> List[InputMediaPhoto | InputMediaVideo]:
        media: List[InputMediaPhoto | InputMediaVideo] = []
        for idx, part in enumerate(album):
            data = await self._download_media(part)
            filename = f"{part.id}"
            if part.photo:
                media_obj = InputMediaPhoto(
                    media=BufferedInputFile(data, filename=f"{filename}.jpg"),
                    caption=caption if idx == 0 else None,
                    parse_mode=ParseMode.HTML if idx == 0 and caption else None,
                )
            else:
                media_obj = InputMediaVideo(
                    media=BufferedInputFile(data, filename=f"{filename}.mp4"),
                    caption=caption if idx == 0 else None,
                    parse_mode=ParseMode.HTML if idx == 0 and caption else None,
                )
            media.append(media_obj)
        return media

    async def _download_media(self, message: TlMessage) -> bytes:
        try:
            data = await message.download_media(bytes)
            if data is None:
                raise RuntimeError("download returned None")
            return data
        except FloodWaitError as exc:
            self.logger.warning("media_download_flood_wait", seconds=exc.seconds)
            await asyncio.sleep(exc.seconds)
            return await self._download_media(message)
        except RPCError as exc:
            self.logger.error("media_download_error", error=str(exc))
            raise


__all__ = ["ForwardingService", "extract_number_tag"]
