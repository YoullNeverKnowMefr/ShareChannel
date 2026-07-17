from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import main_menu_keyboard, wizard_confirm_keyboard
from app.bot.middlewares import authorized_filter
from app.core.db import get_session
from app.core.logging import get_logger
from app.core.scheduler import scheduler
from app.domain.repositories import ChainRepository, ShopRepository
from app.domain.services.channel_link import ChannelLinkService
from app.domain.services.forwarding import ForwardingService
from app.domain.services.permissions import PermissionsService

router = Router(name="chains_wizard")
logger = get_logger(__name__)


class ChainWizardStates(StatesGroup):
    waiting_shop_name = State()
    waiting_category_level1 = State()
    waiting_category_level2 = State()
    waiting_source = State()
    waiting_sink = State()
    waiting_start = State()
    waiting_interval = State()
    waiting_confirm = State()


def _extract_chat(message: Message) -> tuple[int, str]:
    chat = message.forward_from_chat or message.sender_chat
    if not chat:
        raise ValueError("Чат не определён. Перешлите сообщение из канала.")
    return chat.id, chat.title or "Без названия"


def _format_chat_ref(chat_id: int, link: str | None) -> str:
    if not link:
        if chat_id < 0:
            short_id = abs(chat_id) % 10**10
            link = f"https://t.me/c/{short_id}"
        else:
            link = f"tg://openmessage?chat_id={chat_id}"
    return f'<a href="{link}">{chat_id}</a>'


@router.callback_query(F.data == "shops:new", authorized_filter)
async def create_shop_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ChainWizardStates.waiting_shop_name)
    await state.update_data(is_new_shop=True, processed_media_groups=[], wizard_messages=[])
    if callback.message:
        msg = await callback.message.answer("Введите уникальное название магазина:")
        await state.update_data(wizard_messages=[msg.message_id])
    await callback.answer()


@router.callback_query(F.data.startswith("chains:new:"), authorized_filter)
async def chain_wizard_start(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    shop_id = int(parts[2])
    
    if len(parts) > 3:
        category_id = int(parts[3])
        await state.update_data(
            is_new_shop=False,
            shop_id=shop_id,
            category_id=category_id,
            processed_media_groups=[],
            wizard_messages=[]
        )
        await state.set_state(ChainWizardStates.waiting_source)
        if callback.message:
            msg = await callback.message.answer("Перешлите сообщение из канала-источника:")
            await state.update_data(wizard_messages=[msg.message_id])
    else:
        await callback.answer("Необходимо выбрать категорию", show_alert=True)
        return
    
    await callback.answer()


@router.message(ChainWizardStates.waiting_shop_name)
async def wizard_shop_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        msg = await message.answer("Название не может быть пустым. Попробуйте снова.")
        data = await state.get_data()
        wizard_messages = data.get("wizard_messages", [])
        wizard_messages.extend([message.message_id, msg.message_id])
        await state.update_data(wizard_messages=wizard_messages)
        return

    async with get_session() as session:
        repo = ShopRepository(session)
        existing = await repo.list_by_owner(message.from_user.id)
        if any(shop.name.lower() == name.lower() for shop in existing):
            msg = await message.answer("У вас уже есть магазин с таким названием. Введите другое имя.")
            data = await state.get_data()
            wizard_messages = data.get("wizard_messages", [])
            wizard_messages.extend([message.message_id, msg.message_id])
            await state.update_data(wizard_messages=wizard_messages)
            return

    await state.update_data(shop_name=name)
    await state.set_state(ChainWizardStates.waiting_category_level1)
    msg = await message.answer("Придумайте название раздела 1-го уровня (например: Мужской, Женский, Детский, Техника):")
    data = await state.get_data()
    wizard_messages = data.get("wizard_messages", [])
    wizard_messages.extend([message.message_id, msg.message_id])
    await state.update_data(wizard_messages=wizard_messages)


@router.message(ChainWizardStates.waiting_category_level1)
async def wizard_category_level1(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        msg = await message.answer("Название не может быть пустым. Попробуйте снова.")
        data = await state.get_data()
        wizard_messages = data.get("wizard_messages", [])
        wizard_messages.extend([message.message_id, msg.message_id])
        await state.update_data(wizard_messages=wizard_messages)
        return

    await state.update_data(category_level1_name=name)
    await state.set_state(ChainWizardStates.waiting_category_level2)
    msg = await message.answer("Придумайте название раздела 2-го уровня (например: Обувь, Сумки, Одежда, Ремни, Куртки):")
    data = await state.get_data()
    wizard_messages = data.get("wizard_messages", [])
    wizard_messages.extend([message.message_id, msg.message_id])
    await state.update_data(wizard_messages=wizard_messages)


@router.message(ChainWizardStates.waiting_category_level2)
async def wizard_category_level2(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        msg = await message.answer("Название не может быть пустым. Попробуйте снова.")
        data = await state.get_data()
        wizard_messages = data.get("wizard_messages", [])
        wizard_messages.extend([message.message_id, msg.message_id])
        await state.update_data(wizard_messages=wizard_messages)
        return

    await state.update_data(category_level2_name=name)
    await state.set_state(ChainWizardStates.waiting_source)
    data = await state.get_data()
    msg = await message.answer(
        "✅ Готово! Структура создана:\n"
        f"📂 Раздел 1: {data.get('category_level1_name')}\n"
        f"📂 Раздел 2: {name}\n\n"
        "Теперь перешлите любое сообщение из канала-источника:"
    )
    wizard_messages = data.get("wizard_messages", [])
    wizard_messages.extend([message.message_id, msg.message_id])
    await state.update_data(wizard_messages=wizard_messages)


@router.message(ChainWizardStates.waiting_source)
async def wizard_source(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if "source_chat_id" in data:
        return
    
    if message.media_group_id:
        processed_groups = data.get("processed_media_groups", [])
        
        if message.media_group_id in processed_groups:
            return
        
        processed_groups.append(message.media_group_id)
        await state.update_data(processed_media_groups=processed_groups)
        
        await asyncio.sleep(0.5)
    
    try:
        chat_id, title = _extract_chat(message)
    except ValueError as exc:
        msg = await message.answer(str(exc))
        data = await state.get_data()
        wizard_messages = data.get("wizard_messages", [])
        wizard_messages.extend([message.message_id, msg.message_id])
        await state.update_data(wizard_messages=wizard_messages)
        return
    
    link_service = ChannelLinkService(message.bot)
    source_link = await link_service.resolve_link(
        chat_id=chat_id,
        prefer_bot=False,
        aiogram_chat=message.forward_from_chat or message.sender_chat,
    )

    await state.update_data(
        source_chat_id=chat_id,
        source_title=title,
        source_chat_link=source_link,
    )
    await state.set_state(ChainWizardStates.waiting_sink)
    msg = await message.answer("✅ Канал-источник получен!\n\nТеперь перешлите сообщение из канала-приёмника:")
    data = await state.get_data()
    wizard_messages = data.get("wizard_messages", [])
    wizard_messages.extend([message.message_id, msg.message_id])
    await state.update_data(wizard_messages=wizard_messages)


@router.message(ChainWizardStates.waiting_sink)
async def wizard_sink(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    
    if "sink_chat_id" not in data:
        await state.update_data(processed_media_groups=[])
        data = await state.get_data()
    
    if message.media_group_id:
        processed_groups = data.get("processed_media_groups", [])
        
        if message.media_group_id in processed_groups:
            return
        
        processed_groups.append(message.media_group_id)
        await state.update_data(processed_media_groups=processed_groups)
        
        await asyncio.sleep(0.5)
    
    try:
        chat_id, title = _extract_chat(message)
    except ValueError as exc:
        msg = await message.answer(str(exc))
        data = await state.get_data()
        wizard_messages = data.get("wizard_messages", [])
        wizard_messages.extend([message.message_id, msg.message_id])
        await state.update_data(wizard_messages=wizard_messages)
        return

    if chat_id == data.get("source_chat_id"):
        msg = await message.answer("Источник и приёмник не могут совпадать. Перешлите другой канал.")
        data = await state.get_data()
        wizard_messages = data.get("wizard_messages", [])
        wizard_messages.extend([message.message_id, msg.message_id])
        await state.update_data(wizard_messages=wizard_messages)
        return

    link_service = ChannelLinkService(message.bot)
    sink_link = await link_service.resolve_link(
        chat_id=chat_id,
        prefer_bot=True,
        aiogram_chat=message.forward_from_chat or message.sender_chat,
    )

    await state.update_data(
        sink_chat_id=chat_id,
        sink_title=title,
        sink_chat_link=sink_link,
    )
    await state.set_state(ChainWizardStates.waiting_start)
    msg = await message.answer("Введите стартовый номер поста (например, #123):")
    data = await state.get_data()
    wizard_messages = data.get("wizard_messages", [])
    wizard_messages.extend([message.message_id, msg.message_id])
    await state.update_data(wizard_messages=wizard_messages)


@router.message(ChainWizardStates.waiting_start)
async def wizard_start_number(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text.startswith("#"):
        text = text[1:]
    if not text.isdigit() or int(text) < 1:
        msg = await message.answer("Номер должен быть целым числом >= 1. Попробуйте снова.")
        data = await state.get_data()
        wizard_messages = data.get("wizard_messages", [])
        wizard_messages.extend([message.message_id, msg.message_id])
        await state.update_data(wizard_messages=wizard_messages)
        return
    await state.update_data(start_number=int(text))
    await state.set_state(ChainWizardStates.waiting_interval)
    msg = await message.answer("Введите интервал отправки в минутах (целое число >= 1):")
    data = await state.get_data()
    wizard_messages = data.get("wizard_messages", [])
    wizard_messages.extend([message.message_id, msg.message_id])
    await state.update_data(wizard_messages=wizard_messages)


@router.message(ChainWizardStates.waiting_interval)
async def wizard_interval(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text.isdigit() or int(text) < 1:
        msg = await message.answer("Интервал должен быть целым числом >= 1. Попробуйте снова.")
        data = await state.get_data()
        wizard_messages = data.get("wizard_messages", [])
        wizard_messages.extend([message.message_id, msg.message_id])
        await state.update_data(wizard_messages=wizard_messages)
        return
    interval_minutes = int(text)
    await state.update_data(interval_seconds=interval_minutes * 60)

    data = await state.get_data()
    source_chat_id = data["source_chat_id"]
    sink_chat_id = data["sink_chat_id"]
    source_chat_link = data.get("source_chat_link")
    sink_chat_link = data.get("sink_chat_link")

    permissions_service = PermissionsService(message.bot)
    try:
        permissions = await permissions_service.check(
            source_chat_id=source_chat_id,
            sink_chat_id=sink_chat_id,
        )
    except Exception as exc:
        logger.error("wizard_permission_check_failed", error=str(exc))
        msg = await message.answer(
            "Не удалось проверить доступы. Убедитесь, что добавлен аккаунт-наблюдатель "
            "(🛡 Безопасность → 🔑 Telethon аккаунты) и он подписан на канал-источник, затем попробуйте снова."
        )
        data = await state.get_data()
        wizard_messages = data.get("wizard_messages", [])
        wizard_messages.extend([message.message_id, msg.message_id])
        await state.update_data(wizard_messages=wizard_messages)
        return

    if not permissions.source.can_read:
        msg = await message.answer(
            "Не удаётся прочитать канал-источник.\n"
            "Проверьте, что добавлен аккаунт-наблюдатель (🛡 Безопасность → 🔑 Telethon аккаунты) "
            "и что он подписан на этот канал. Затем попробуйте снова."
        )
        data = await state.get_data()
        wizard_messages = data.get("wizard_messages", [])
        wizard_messages.extend([message.message_id, msg.message_id])
        await state.update_data(wizard_messages=wizard_messages)
        return
    if not (permissions.sink.can_post and permissions.sink.can_delete):
        msg = await message.answer(
            "Недостаточно прав в канале-приёмнике. Выдайте права на отправку и удаление сообщений."
        )
        data = await state.get_data()
        wizard_messages = data.get("wizard_messages", [])
        wizard_messages.extend([message.message_id, msg.message_id])
        await state.update_data(wizard_messages=wizard_messages)
        return

    summary = (
        "Проверьте параметры:\n"
        f"• Магазин: {data.get('shop_name', 'существующий')}\n"
        f"• Источник: {data['source_title']} ({_format_chat_ref(source_chat_id, source_chat_link)})\n"
        f"• Приёмник: {data['sink_title']} ({_format_chat_ref(sink_chat_id, sink_chat_link)})\n"
        f"• Стартовый номер: #{data['start_number']}\n"
        f"• Интервал: {interval_minutes} мин\n"
        "Нажмите «Активировать», чтобы запустить цепочку."
    )

    await state.set_state(ChainWizardStates.waiting_confirm)
    msg = await message.answer(summary, parse_mode="HTML", reply_markup=wizard_confirm_keyboard().as_markup())
    wizard_messages = data.get("wizard_messages", [])
    wizard_messages.extend([message.message_id, msg.message_id])
    await state.update_data(wizard_messages=wizard_messages)


@router.callback_query(F.data == "wizard:cancel", authorized_filter)
async def wizard_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    is_authorized = data.get("authorized", False)
    category_id = data.get("category_id")

    await state.clear()
    if is_authorized:
        await state.update_data(authorized=True, processed_media_groups=[])

    if callback.message:
        summary_id = callback.message.message_id
        for mid in data.get("wizard_messages", []):
            if mid == summary_id:
                continue
            try:
                await callback.message.chat.delete_message(mid)
            except Exception:
                pass

    if callback.message and category_id:
        async with get_session() as session:
            from app.domain.repositories import CategoryRepository
            from app.domain.dto import CategoryDTO, ChainDTO
            from app.bot.keyboards import category_card_keyboard

            cat_repo = CategoryRepository(session)
            chain_repo = ChainRepository(session)

            category = await cat_repo.get_by_id(category_id)
            if category:
                subcategories = await cat_repo.list_by_parent(category_id)
                has_subcategories = len(subcategories) > 0
                chains = await chain_repo.list_by_category(category_id)
                text = (
                    "Создание цепочки отменено.\n\n"
                    f"📂 {category.name}\n"
                    f"Цепочек: {len(chains)}"
                )
                await callback.message.edit_text(
                    text,
                    reply_markup=category_card_keyboard(
                        CategoryDTO.model_validate(category),
                        [ChainDTO.model_validate(c) for c in chains],
                        subcategories=[CategoryDTO.model_validate(c) for c in subcategories] if has_subcategories else None,
                    ).as_markup(),
                )
                await callback.answer("Отменено")
                return

    if callback.message:
        await callback.message.edit_text(
            "Мастер создания цепочки отменён.",
            reply_markup=main_menu_keyboard().as_markup(),
        )
    await callback.answer()


@router.callback_query(F.data == "wizard:activate", authorized_filter)
async def wizard_activate(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    data = await state.get_data()
    user_id = callback.from_user.id if callback.from_user else None
    if user_id is None:
        await callback.answer("Не удалось определить пользователя", show_alert=True)
        return

    async with get_session() as session:
        shop_repo = ShopRepository(session)
        chain_repo = ChainRepository(session)
        from app.domain.repositories import CategoryRepository
        cat_repo = CategoryRepository(session)

        if data.get("is_new_shop"):
            existing_shop = await shop_repo.get_by_name(user_id, data["shop_name"])
            if existing_shop:
                shop_id = existing_shop.id
            else:
                shop = await shop_repo.create(owner_tg_id=user_id, name=data["shop_name"])
                shop_id = shop.id
                await session.flush()
            
            cat_level1 = await cat_repo.create(
                shop_id=shop_id,
                name=data["category_level1_name"]
            )
            await session.flush()
            
            cat_level2 = await cat_repo.create(
                shop_id=shop_id,
                name=data["category_level2_name"],
                parent_id=cat_level1.id
            )
            await session.flush()
            
            category_id = cat_level2.id
        else:
            shop_id = data.get("shop_id")
            category_id = data.get("category_id")
            
            if not shop_id or not category_id:
                await callback.answer("Ошибка: данные сессии устарели", show_alert=True)
                await state.clear()
                await state.update_data(authorized=True)
                if callback.message:
                    await callback.message.edit_text(
                        "⚠️ Данные сессии устарели.\n\n"
                        "Это произошло потому что прошло много времени с момента начала создания цепочки.\n\n"
                        "Пожалуйста, начните процесс заново:\n"
                        "1️⃣ Выберите магазин\n"
                        "2️⃣ Выберите категорию\n"
                        "3️⃣ Нажмите 'Добавить канал'",
                        reply_markup=main_menu_keyboard().as_markup(),
                    )
                return

        duplicate = await chain_repo.find_duplicate(
            shop_id=shop_id,
            source_chat_id=data["source_chat_id"],
            sink_chat_id=data["sink_chat_id"],
        )
        
        if duplicate:
            if callback.message:
                warning_text = (
                    "⚠️ Внимание! Такая цепочка уже создана!\n\n"
                    f"• ID цепочки: #{duplicate.id}\n"
                    f"• Источник: {duplicate.source_chat_title or 'без названия'}\n"
                    f"• Приёмник: {data.get('sink_title', 'без названия')}\n"
                    f"• Стартовый номер: #{duplicate.start_number}\n"
                    f"• Статус: {duplicate.status.value}\n\n"
                    "Создание дубликата отменено. Вы можете добавить другую цепочку."
                )
                
                if category_id:
                    category = await cat_repo.get_by_id(category_id)
                    if category:
                        subcategories = await cat_repo.list_by_parent(category_id)
                        has_subcategories = len(subcategories) > 0
                        
                        chains = await chain_repo.list_by_category(category_id)
                        
                        from app.domain.dto import CategoryDTO, ChainDTO
                        from app.bot.keyboards import category_card_keyboard
                        
                        final_text = (
                            warning_text + f"\n\n📂 {category.name}\n"
                            f"Цепочек: {len(chains)}"
                        )
                        
                        await callback.message.edit_text(
                            final_text,
                            reply_markup=category_card_keyboard(
                                CategoryDTO.model_validate(category),
                                [ChainDTO.from_orm(c) for c in chains],
                                subcategories=[CategoryDTO.model_validate(c) for c in subcategories] if has_subcategories else None
                            ).as_markup(),
                        )
                    else:
                        await callback.message.edit_text(warning_text)
                else:
                    await callback.message.edit_text(warning_text)
            
            await callback.answer("Цепочка уже существует", show_alert=True)
            
            is_authorized = data.get("authorized", False)
            await state.clear()
            if is_authorized:
                await state.update_data(authorized=True, processed_media_groups=[])
            return

        chain = await chain_repo.create(
            shop_id=shop_id,
            category_id=category_id,
            source_chat_id=data["source_chat_id"],
            source_chat_title=data.get("source_title"),
            source_chat_link=data.get("source_chat_link"),
            sink_chat_id=data["sink_chat_id"],
            sink_chat_link=data.get("sink_chat_link"),
            start_number=data["start_number"],
            interval_seconds=data["interval_seconds"],
        )

    try:
        await schedule_chain_job(kwargs, chain.id, data["interval_seconds"])
    except Exception as exc:
        logger.error("chain_schedule_failed", chain_id=chain.id, error=str(exc))
        await callback.answer("Не удалось запустить цепочку. Попробуйте ещё раз.", show_alert=True)
        return

    is_authorized = data.get("authorized", False)
    await state.clear()
    if is_authorized:
        await state.update_data(authorized=True, processed_media_groups=[])

    if callback.message:
        summary_id = callback.message.message_id
        for mid in data.get("wizard_messages", []):
            if mid == summary_id:
                continue
            try:
                await callback.message.chat.delete_message(mid)
            except Exception:
                pass

    if callback.message:
        async with get_session() as session:
            from app.domain.repositories import CategoryRepository
            from app.domain.dto import CategoryDTO, ChainDTO
            from app.bot.keyboards import category_card_keyboard
            
            cat_repo = CategoryRepository(session)
            chain_repo = ChainRepository(session)
            
            category = await cat_repo.get_by_id(category_id)
            if category:
                subcategories = await cat_repo.list_by_parent(category_id)
                has_subcategories = len(subcategories) > 0
                
                chains = await chain_repo.list_by_category(category_id)
                
                text = (
                    f"✅ Цепочка #{chain.id} активирована и готова к работе.\n\n"
                    f"📂 {category.name}\n"
                    f"Цепочек: {len(chains)}"
                )
                
                await callback.message.edit_text(
                    text,
                    reply_markup=category_card_keyboard(
                        CategoryDTO.model_validate(category),
                        [ChainDTO.model_validate(c) for c in chains],
                        subcategories=[CategoryDTO.model_validate(c) for c in subcategories] if has_subcategories else None
                    ).as_markup(),
                )
            else:
                await callback.message.edit_text(
                    f"Цепочка #{chain.id} активирована и готова к работе.",
                    reply_markup=main_menu_keyboard().as_markup(),
                )
    await callback.answer("Цепочка активирована")


async def schedule_chain_job(data: dict, chain_id: int, interval_seconds: int) -> None:
    forwarding: ForwardingService = _get_forwarding_service(data)

    scheduler.add_interval_job(
        forwarding.process_chain,
        job_id=f"chain:{chain_id}",
        seconds=interval_seconds,
        kwargs={"chain_id": chain_id},
    )
    scheduler.add_one_off_job(
        forwarding.process_chain,
        job_id=f"chain:{chain_id}:bootstrap",
        run_date=datetime.now(timezone.utc) + timedelta(seconds=random.uniform(1, 5)),
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
    
    raise RuntimeError("forwarding_service is not available in context")
