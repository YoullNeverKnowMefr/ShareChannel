from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import confirm_keyboard, shop_card_keyboard, shops_keyboard
from app.bot.middlewares import authorized_filter
from app.core.db import get_session
from app.domain.dto import ChainDTO, ShopDTO
from app.domain.repositories import ChainRepository, ShopRepository

router = Router(name="shops")

PAGE_SIZE = 10


class ShopManageStates(StatesGroup):
    waiting_new_name = State()


@router.message(Command("shops"), authorized_filter)
async def list_shops_command(message: Message) -> None:
    await send_shops_page(message.chat.id, message.from_user.id, 0, reply_message=message)


@router.callback_query(F.data.startswith("shops:list:"), authorized_filter)
async def list_shops_callback(callback: CallbackQuery, state: FSMContext) -> None:
    page = int(callback.data.split(":")[-1])
    user_id = callback.from_user.id if callback.from_user else None
    if user_id is None:
        await callback.answer("Неизвестный пользователь", show_alert=True)
        return
    await send_shops_page(callback.message.chat.id, user_id, page, edit_message=callback.message)
    await callback.answer()


@router.callback_query(F.data.startswith("shops:view:"), authorized_filter)
async def view_shop(callback: CallbackQuery, state: FSMContext) -> None:
    shop_id = int(callback.data.split(":")[-1])
    
    data = await state.get_data()
    wizard_messages = data.get("wizard_messages", [])
    if wizard_messages and callback.message:
        for msg_id in wizard_messages:
            try:
                await callback.message.chat.delete_message(msg_id)
            except Exception:
                pass
    
    await state.clear()
    await state.update_data(authorized=True)

    async with get_session() as session:
        shop_repo = ShopRepository(session)
        from app.domain.repositories import CategoryRepository
        cat_repo = CategoryRepository(session)

        shop = await shop_repo.get_by_id(shop_id)
        if not shop:
            await callback.answer("Магазин не найден", show_alert=True)
            return

        categories = await cat_repo.list_by_shop(shop_id)

    text = (
        f"🛒 Магазин «{shop.name}»\n"
        f"Владелец: {shop.owner_tg_id}\n"
        f"Категорий: {len(categories)}"
    )

    if callback.message:
        try:
            await callback.message.edit_text(
                text,
                reply_markup=shop_card_keyboard(shop.id).as_markup(),
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc):
                raise
    await callback.answer()


async def send_shops_page(
    chat_id: int,
    user_id: int,
    page: int,
    *,
    reply_message: Message | None = None,
    edit_message: Message | None = None,
) -> None:
    async with get_session() as session:
        repo = ShopRepository(session)
        all_shops = await repo.list_by_owner(user_id)

    shops_slice = all_shops[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    has_next = len(all_shops) > (page + 1) * PAGE_SIZE

    text = "Список магазинов. Выберите магазин или добавьте новый."
    keyboard = shops_keyboard([ShopDTO.model_validate(s) for s in shops_slice], page, has_next).as_markup()

    if edit_message:
        try:
            await edit_message.edit_text(text, reply_markup=keyboard)
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc):
                raise
    elif reply_message:
        await reply_message.answer(text, reply_markup=keyboard)
    else:
        raise RuntimeError("Either reply_message or edit_message must be provided")


@router.callback_query(F.data.startswith("shops:rename:"), authorized_filter)
async def rename_shop_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    shop_id = int(callback.data.split(":")[-1])
    await state.set_state(ShopManageStates.waiting_new_name)
    await state.update_data(shop_id=shop_id, authorized=True, wizard_messages=[])
    if callback.message:
        msg = await callback.message.answer("Введите новое название магазина:")
        await state.update_data(wizard_messages=[msg.message_id])
    await callback.answer()


@router.message(ShopManageStates.waiting_new_name)
async def rename_shop_confirm(message: Message, state: FSMContext) -> None:
    new_name = (message.text or "").strip()
    if not new_name:
        await message.answer("Название не может быть пустым. Попробуйте снова.")
        return
    
    data = await state.get_data()
    shop_id = data["shop_id"]
    
    async with get_session() as session:
        repo = ShopRepository(session)
        
        existing = await repo.get_by_name(0, new_name)
        if existing and existing.id != shop_id:
            await message.answer(f"Магазин с названием «{new_name}» уже существует. Введите другое название.")
            return
        
        shop = await repo.get_by_id(shop_id)
        if shop:
            shop.name = new_name
            await session.commit()
            
            from app.domain.repositories import CategoryRepository
            cat_repo = CategoryRepository(session)
            categories = await cat_repo.list_by_shop(shop_id)
            
            text = (
                f"✅ Магазин переименован в «{new_name}»\n\n"
                f"🛒 Магазин «{shop.name}»\n"
                f"Владелец: {shop.owner_tg_id}\n"
                f"Категорий: {len(categories)}"
            )
            
            is_authorized = data.get("authorized", False)
            await state.clear()
            if is_authorized:
                await state.update_data(authorized=True)
            
            await message.answer(
                text,
                reply_markup=shop_card_keyboard(shop.id).as_markup(),
            )
        else:
            await message.answer("Магазин не найден")
            await state.clear()



@router.callback_query(F.data.startswith("shops:delete:confirm:"), authorized_filter)
async def delete_shop_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    shop_id = int(callback.data.split(":")[-1])
    
    async with get_session() as session:
        repo = ShopRepository(session)
        shop = await repo.get_by_id(shop_id)
        if shop:
            await session.delete(shop)
            await session.commit()
    
    if callback.message:
        await callback.message.edit_text("✅ Магазин удалён")
    await callback.answer("Удалено")
    
    user_id = callback.from_user.id if callback.from_user else None
    if user_id and callback.message:
        await send_shops_page(callback.message.chat.id, user_id, 0, reply_message=callback.message)


@router.callback_query(F.data.startswith("shops:delete:cancel:"), authorized_filter)
async def delete_shop_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    shop_id = int(callback.data.split(":")[-1])
    
    async with get_session() as session:
        shop_repo = ShopRepository(session)
        from app.domain.repositories import CategoryRepository
        cat_repo = CategoryRepository(session)
        
        shop = await shop_repo.get_by_id(shop_id)
        if not shop:
            await callback.answer("Магазин не найден", show_alert=True)
            return
        
        categories = await cat_repo.list_by_shop(shop_id)
    
    text = (
        f"🛒 Магазин «{shop.name}»\n"
        f"Владелец: {shop.owner_tg_id}\n"
        f"Категорий: {len(categories)}"
    )
    
    if callback.message:
        try:
            await callback.message.edit_text(
                text,
                reply_markup=shop_card_keyboard(shop.id).as_markup(),
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc):
                raise
    await callback.answer("Отменено")


@router.callback_query(F.data.startswith("shops:delete:"), authorized_filter)
async def delete_shop_prompt(callback: CallbackQuery) -> None:
    shop_id = int(callback.data.split(":")[-1])
    
    async with get_session() as session:
        from app.domain.repositories import CategoryRepository
        cat_repo = CategoryRepository(session)
        categories = await cat_repo.list_by_shop(shop_id)
    
    if categories:
        await callback.answer(
            f"Нельзя удалить магазин с категориями! Сначала удалите все {len(categories)} категорий.",
            show_alert=True
        )
        return
    
    if callback.message:
        try:
            await callback.message.edit_text(
                "Удалить магазин? Действие необратимо.",
                reply_markup=confirm_keyboard("shops:delete", shop_id).as_markup(),
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc):
                raise
    await callback.answer()
