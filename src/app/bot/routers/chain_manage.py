from __future__ import annotations

import asyncio

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import chain_card_keyboard, confirm_keyboard
from app.bot.middlewares import authorized_filter
from app.core.db import get_session
from app.core.scheduler import scheduler
from app.domain.dto import ChainDTO
from app.domain.models import ChainStatus
from app.domain.repositories import ChainRepository
from app.domain.services.channel_link import ChannelLinkService
from app.domain.services.forwarding import ForwardingService
from app.domain.services.permissions import PermissionsService
from app.core.logging import get_logger

router = Router(name="chain_manage")
logger = get_logger(__name__)


class ChainManageStates(StatesGroup):
    waiting_interval = State()


def _chain_text(chain: ChainDTO) -> str:
    status_icon = {
        ChainStatus.ACTIVE: "✅ активна",
        ChainStatus.PAUSED: "⏸ на паузе",
        ChainStatus.STOPPED: "⛔ остановлена",
        ChainStatus.ERROR: "🔴 ошибка",
    }[chain.status]
    
    chain_name = chain.source_chat_title or f"#{chain.id}"
    
    last_sent = f"#{chain.last_sent_number}" if chain.last_sent_number is not None else "не начато"

    def _chat_ref(chat_id: int, link: str | None) -> str:
        if link:
            return f"[{chat_id}]({link})"
        return f"`{chat_id}`"
    
    return (
        f"Цепочка {chain_name}\n"
        f"Статус: {status_icon}\n"
        f"Источник: {_chat_ref(chain.source_chat_id, chain.source_chat_link)}\n"
        f"Приёмник: {_chat_ref(chain.sink_chat_id, chain.sink_chat_link)}\n"
        f"Интервал: {chain.interval_seconds // 60} мин\n"
        f"Стартовый номер: #{chain.start_number}\n"
        f"Последний опубликованный: {last_sent}"
    )


async def _load_chain(chain_id: int, bot=None) -> ChainDTO | None:
    async with get_session() as session:
        repo = ChainRepository(session)
        chain = await repo.get_by_id(chain_id)
        if chain is None:
            return None
        if bot:
            link_service = ChannelLinkService(bot)
            chain = await link_service.ensure_chain_links(chain, repo)
        return ChainDTO.model_validate(chain)


@router.callback_query(F.data.startswith("chains:view:"), authorized_filter)
async def view_chain(callback: CallbackQuery, state: FSMContext) -> None:
    chain_id = int(callback.data.split(":")[-1])
    chain = await _load_chain(chain_id, callback.bot)
    if chain is None:
        await callback.answer("Цепочка не найдена", show_alert=True)
        return
    if callback.message:
        try:
            await callback.message.edit_text(
                _chain_text(chain),
                parse_mode="Markdown",
                reply_markup=chain_card_keyboard(chain).as_markup(),
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc):
                raise
    await callback.answer()


@router.callback_query(F.data.startswith("chains:interval:"), authorized_filter)
async def change_interval_start(callback: CallbackQuery, state: FSMContext) -> None:
    chain_id = int(callback.data.split(":")[-1])
    await state.set_state(ChainManageStates.waiting_interval)
    await state.update_data(chain_id=chain_id)
    if callback.message:
        await callback.message.answer("Введите новый интервал в минутах (>= 1):")
    await callback.answer()


@router.message(ChainManageStates.waiting_interval)
async def interval_received(message: Message, state: FSMContext, **kwargs) -> None:
    text = message.text
    if not text or not text.isdigit():
        await message.answer("Пожалуйста, введите целое число.")
        return

    interval_minutes = int(text)
    if interval_minutes < 1:
        await message.answer("Интервал должен быть >= 1 минуты.")
        return
    
    data = await state.get_data()
    chain_id = data["chain_id"]

    async with get_session() as session:
        repo = ChainRepository(session)
        await repo.update_interval(chain_id, interval_minutes * 60)

    await reschedule_chain(kwargs, chain_id, interval_minutes * 60)

    is_authorized = data.get("authorized", False)
    await state.clear()
    if is_authorized:
        await state.update_data(authorized=True)
    
    chain = await _load_chain(chain_id, message.bot)
    if chain:
        await message.answer(
            f"✅ Интервал изменён на {interval_minutes} мин\n\n" + _chain_text(chain),
            parse_mode="Markdown",
            reply_markup=chain_card_keyboard(chain).as_markup(),
        )
    else:
        await message.answer("✅ Интервал изменён")


@router.callback_query(F.data.startswith("chains:pause:"), authorized_filter)
async def pause_chain(callback: CallbackQuery, state: FSMContext) -> None:
    chain_id = int(callback.data.split(":")[-1])
    async with get_session() as session:
        repo = ChainRepository(session)
        await repo.update_status(chain_id, ChainStatus.PAUSED)
    scheduler.remove(f"chain:{chain_id}")
    await callback.answer("Цепочка поставлена на паузу")
    await view_chain(callback, state)


@router.callback_query(F.data.startswith("chains:resume:"), authorized_filter)
async def resume_chain(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    chain_id = int(callback.data.split(":")[-1])
    async with get_session() as session:
        repo = ChainRepository(session)
        await repo.update_status(chain_id, ChainStatus.ACTIVE)
        chain = await repo.get_by_id(chain_id)
    await reschedule_chain(kwargs, chain_id, chain.interval_seconds if chain else 60)
    await callback.answer("Цепочка возобновлена")
    await view_chain(callback, state)



@router.callback_query(F.data.startswith("chains:delete:confirm:"), authorized_filter)
async def delete_chain_confirm(callback: CallbackQuery) -> None:
    chain_id = int(callback.data.split(":")[-1])
    
    async with get_session() as session:
        repo = ChainRepository(session)
        chain = await repo.get_by_id(chain_id)
        if not chain:
            await callback.answer("Цепочка не найдена", show_alert=True)
            return
            
        category_id = chain.category_id
        shop_id = chain.shop_id
        await repo.delete(chain_id)
    
    scheduler.remove(f"chain:{chain_id}")
    
    if callback.message:
        if category_id:
            async with get_session() as session:
                from app.domain.repositories import CategoryRepository
                cat_repo = CategoryRepository(session)
                chain_repo = ChainRepository(session)
                
                category = await cat_repo.get_by_id(category_id)
                if category:
                    chains = await chain_repo.list_by_category(category_id)
                    subcategories = await cat_repo.list_by_parent(category_id)
                    has_subcategories = len(subcategories) > 0
                    
                    text = (
                        f"✅ Цепочка удалена\n\n"
                        f"📂 {category.name}\n"
                        f"Цепочек: {len(chains)}"
                    )
                    
                    from app.bot.keyboards import category_card_keyboard
                    from app.domain.dto import CategoryDTO
                    await callback.message.edit_text(
                        text,
                        reply_markup=category_card_keyboard(
                            CategoryDTO.model_validate(category),
                            [ChainDTO.model_validate(c) for c in chains],
                            subcategories=[CategoryDTO.model_validate(c) for c in subcategories] if has_subcategories else None
                        ).as_markup(),
                    )
        else:
            async with get_session() as session:
                from app.domain.repositories import ShopRepository
                shop_repo = ShopRepository(session)
                
                shop = await shop_repo.get_by_id(shop_id)
                if shop:
                    text = (
                        f"✅ Цепочка удалена\n\n"
                        f"Магазин «{shop.name}»\n"
                        f"Владелец: {shop.owner_tg_id}"
                    )
                    
                    from app.bot.keyboards import shop_card_keyboard
                    await callback.message.edit_text(
                        text,
                        reply_markup=shop_card_keyboard(shop.id).as_markup(),
                    )
    
    await callback.answer("Удалено")


@router.callback_query(F.data.startswith("chains:delete:cancel:"), authorized_filter)
async def delete_chain_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    chain_id = int(callback.data.split(":")[-1])
    await view_chain(callback, state)
    await callback.answer("Отменено")


@router.callback_query(F.data.startswith("chains:delete:"), authorized_filter)
async def delete_chain_prompt(callback: CallbackQuery) -> None:
    chain_id = int(callback.data.split(":")[-1])
    if callback.message:
        try:
            await callback.message.edit_text(
                "Удалить цепочку? Действие необратимо.",
                reply_markup=confirm_keyboard("chains:delete", chain_id).as_markup(),
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc):
                raise
    await callback.answer()


@router.callback_query(F.data.startswith("chains:permissions:"), authorized_filter)
async def check_permissions(callback: CallbackQuery, state: FSMContext) -> None:
    chain_id = int(callback.data.split(":")[-1])
    async with get_session() as session:
        repo = ChainRepository(session)
        chain = await repo.get_by_id(chain_id)
    if not chain:
        await callback.answer("Цепочка не найдена", show_alert=True)
        return

    await callback.answer("Проверяю права доступа...")
    
    try:
        permissions_service = PermissionsService(callback.bot)
        report = await permissions_service.check(
            source_chat_id=chain.source_chat_id,
            sink_chat_id=chain.sink_chat_id,
        )
        text = (
            "Права доступа:\n"
            f"Источник — чтение: {'✅' if report.source.can_read else '❌'}\n"
            f"Приёмник — отправка: {'✅' if report.sink.can_post else '❌'}, удаление: "
            f"{'✅' if report.sink.can_delete else '❌'}"
        )
        if callback.message:
            try:
                await callback.message.edit_text(text)
                await asyncio.sleep(3)
                await view_chain(callback, state)
            except TelegramBadRequest as exc:
                await callback.message.answer(text)
    except Exception as exc:
        if callback.message:
            try:
                await callback.message.edit_text(f"❌ Ошибка при проверке прав: {str(exc)}")
                await asyncio.sleep(3)
                await view_chain(callback, state)
            except TelegramBadRequest:
                await callback.message.answer(f"❌ Ошибка при проверке прав: {str(exc)}")


async def reschedule_chain(data: dict, chain_id: int, interval_seconds: int) -> None:
    forwarding: ForwardingService = _get_forwarding_service(data)
    scheduler.remove(f"chain:{chain_id}")
    scheduler.add_interval_job(
        forwarding.process_chain,
        job_id=f"chain:{chain_id}",
        seconds=interval_seconds,
        kwargs={"chain_id": chain_id},
    )


def _get_forwarding_service(data: dict) -> ForwardingService:
    forwarding = data.get("forwarding_service")
    if forwarding:
        return forwarding
    
    try:
        from aiogram import Dispatcher
        dp = Dispatcher.get_current()
        forwarding = dp.workflow_data.get("forwarding_service")
        if forwarding:
            return forwarding
    except Exception:
        pass
    
    logger.error("forwarding_service_missing")
    raise RuntimeError("forwarding_service is not available in context")
