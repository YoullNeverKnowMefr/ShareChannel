from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import (
    category_card_keyboard,
    categories_keyboard,
    confirm_keyboard,
)
from app.bot.middlewares import authorized_filter
from app.core.db import get_session
from app.domain.dto import CategoryDTO, ChainDTO
from app.domain.repositories import CategoryRepository, ChainRepository

router = Router(name="categories")


class CategoryManageStates(StatesGroup):
    waiting_new_name = State()


@router.callback_query(F.data.startswith("categories:list:"), authorized_filter)
async def list_categories(callback: CallbackQuery, state: FSMContext) -> None:
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
        cat_repo = CategoryRepository(session)
        categories = await cat_repo.list_by_shop(shop_id, parent_id=None)
    
    if not categories:
        text = "📂 Категории\n\nКатегорий пока нет. Создайте первую категорию."
    else:
        text = f"📂 Категории 1-го уровня\n\nВсего категорий: {len(categories)}"
    
    if callback.message:
        try:
            await callback.message.edit_text(
                text,
                reply_markup=categories_keyboard(
                    [CategoryDTO.model_validate(c) for c in categories],
                    shop_id,
                    parent_id=None
                ).as_markup(),
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc):
                raise
    await callback.answer()


@router.callback_query(F.data.startswith("categories:view:"), authorized_filter)
async def view_category(callback: CallbackQuery, state: FSMContext) -> None:
    category_id = int(callback.data.split(":")[-1])
    
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
        cat_repo = CategoryRepository(session)
        chain_repo = ChainRepository(session)
        
        category = await cat_repo.get_by_id(category_id)
        if not category:
            await callback.answer("Категория не найдена", show_alert=True)
            return
        
        subcategories = await cat_repo.list_by_parent(category_id)
        has_subcategories = len(subcategories) > 0
        
        chains = await chain_repo.list_by_category(category_id) if not has_subcategories else []
    
    if has_subcategories:
        text = (
            f"📂 {category.name}\n"
            f"Подкатегорий: {len(subcategories)}"
        )
    else:
        text = (
            f"📂 {category.name}\n"
            f"Цепочек: {len(chains)}"
        )
    
    if callback.message:
        try:
            await callback.message.edit_text(
                text,
                reply_markup=category_card_keyboard(
                    CategoryDTO.model_validate(category),
                    [ChainDTO.model_validate(c) for c in chains],
                    subcategories=[CategoryDTO.model_validate(c) for c in subcategories] if has_subcategories else None
                ).as_markup(),
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc):
                raise
    await callback.answer()


@router.callback_query(F.data.startswith("categories:create:"), authorized_filter)
async def create_category_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    shop_id = int(parts[2])
    parent_id = int(parts[3]) if len(parts) > 3 else None
    
    await state.set_state(CategoryManageStates.waiting_new_name)
    await state.update_data(shop_id=shop_id, parent_id=parent_id, authorized=True, wizard_messages=[])
    
    if callback.message:
        if parent_id is None:
            msg = await callback.message.answer("Введите название категории 1-го уровня (например: Мужской, Женский, Детский, Техника):")
        else:
            msg = await callback.message.answer("Введите название подкатегории (например: Обувь, Сумки, Одежда, Ремни, Куртки):")
        await state.update_data(wizard_messages=[msg.message_id])
    await callback.answer()


@router.message(CategoryManageStates.waiting_new_name)
async def create_category_confirm(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    
    if data.get("is_rename"):
        await rename_category_confirm(message, state)
        return
    
    name = (message.text or "").strip()
    if not name:
        await message.answer("Название не может быть пустым. Попробуйте снова.")
        return
    
    shop_id = data["shop_id"]
    parent_id = data.get("parent_id")
    
    async with get_session() as session:
        cat_repo = CategoryRepository(session)
        
        existing = await cat_repo.get_by_name(shop_id, name, parent_id)
        if existing:
            await message.answer(f"Категория «{name}» уже существует. Введите другое название.")
            return
        
        category = await cat_repo.create(shop_id, name, parent_id)
        await session.commit()
        
        subcategories = await cat_repo.list_by_parent(category.id)
        has_subcategories = len(subcategories) > 0
        
        text = f"✅ Категория «{name}» создана"
        
        is_authorized = data.get("authorized", False)
        await state.clear()
        if is_authorized:
            await state.update_data(authorized=True)
        
        await message.answer(
            text + f"\n\n📂 {category.name}\n" + (f"Подкатегорий: 0" if parent_id is None else "Цепочек: 0"),
            reply_markup=category_card_keyboard(
                CategoryDTO.model_validate(category),
                [],
                subcategories=[CategoryDTO.model_validate(c) for c in subcategories] if has_subcategories else None
            ).as_markup(),
        )


@router.callback_query(F.data.startswith("categories:rename:"), authorized_filter)
async def rename_category_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    category_id = int(callback.data.split(":")[-1])
    await state.set_state(CategoryManageStates.waiting_new_name)
    await state.update_data(category_id=category_id, is_rename=True, authorized=True, wizard_messages=[])
    
    if callback.message:
        msg = await callback.message.answer("Введите новое название категории:")
        await state.update_data(wizard_messages=[msg.message_id])
    await callback.answer()


async def rename_category_confirm(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    
    name = (message.text or "").strip()
    if not name:
        await message.answer("Название не может быть пустым. Попробуйте снова.")
        return
    
    category_id = data["category_id"]
    
    async with get_session() as session:
        cat_repo = CategoryRepository(session)
        chain_repo = ChainRepository(session)
        
        category = await cat_repo.get_by_id(category_id)
        if not category:
            await message.answer("Категория не найдена")
            await state.clear()
            return
        
        existing = await cat_repo.get_by_name(category.shop_id, name, category.parent_id)
        if existing and existing.id != category_id:
            await message.answer(f"Категория «{name}» уже существует. Введите другое название.")
            return
        
        category.name = name
        await session.commit()
        
        subcategories = await cat_repo.list_by_parent(category_id)
        has_subcategories = len(subcategories) > 0
        
        chains = await chain_repo.list_by_category(category_id) if not has_subcategories else []
        
        if has_subcategories:
            text = (
                f"✅ Категория переименована в «{name}»\n\n"
                f"📂 {category.name}\n"
                f"Подкатегорий: {len(subcategories)}"
            )
        else:
            text = (
                f"✅ Категория переименована в «{name}»\n\n"
                f"📂 {category.name}\n"
                f"Цепочек: {len(chains)}"
            )
        
        is_authorized = data.get("authorized", False)
        await state.clear()
        if is_authorized:
            await state.update_data(authorized=True)
        
        await message.answer(
            text,
            reply_markup=category_card_keyboard(
                CategoryDTO.model_validate(category),
                [ChainDTO.model_validate(c) for c in chains],
                subcategories=[CategoryDTO.model_validate(c) for c in subcategories] if has_subcategories else None
            ).as_markup(),
        )


@router.callback_query(F.data.startswith("categories:delete:confirm:"), authorized_filter)
async def delete_category_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    category_id = int(callback.data.split(":")[-1])
    
    async with get_session() as session:
        cat_repo = CategoryRepository(session)
        category = await cat_repo.get_by_id(category_id)
        if not category:
            await callback.answer("Категория не найдена", show_alert=True)
            return
            
        shop_id = category.shop_id
        parent_id = category.parent_id
        category_name = category.name
        
        await session.delete(category)
        await session.commit()
        
        if parent_id:
            parent_category = await cat_repo.get_by_id(parent_id)
            subcategories = await cat_repo.list_by_parent(parent_id)
            
            text = f"✅ Категория «{category_name}» удалена\n\n📂 {parent_category.name}\nПодкатегорий: {len(subcategories)}"
            keyboard = categories_keyboard(
                [CategoryDTO.model_validate(c) for c in subcategories],
                shop_id,
                parent_id=parent_id
            )
        else:
            categories = await cat_repo.list_by_shop(shop_id, parent_id=None)
            
            text = f"✅ Категория «{category_name}» удалена\n\n📂 Категории 1-го уровня\n\nВсего категорий: {len(categories)}"
            keyboard = categories_keyboard(
                [CategoryDTO.model_validate(c) for c in categories],
                shop_id,
                parent_id=None
            )
    
    if callback.message:
        try:
            await callback.message.delete()
        except Exception:
            pass
        
        await callback.message.answer(
            text,
            reply_markup=keyboard.as_markup()
        )
    
    await callback.answer("Удалено")


@router.callback_query(F.data.startswith("categories:delete:cancel:"), authorized_filter)
async def delete_category_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    category_id = int(callback.data.split(":")[-1])
    
    async with get_session() as session:
        cat_repo = CategoryRepository(session)
        chain_repo = ChainRepository(session)
        
        category = await cat_repo.get_by_id(category_id)
        if not category:
            await callback.answer("Категория не найдена", show_alert=True)
            return
        
        subcategories = await cat_repo.list_by_parent(category_id)
        has_subcategories = len(subcategories) > 0
        
        chains = await chain_repo.list_by_category(category_id) if not has_subcategories else []
        
        if has_subcategories:
            text = f"📂 {category.name}\nПодкатегорий: {len(subcategories)}"
        else:
            text = f"📂 {category.name}\nЦепочек: {len(chains)}"
    
    if callback.message:
        try:
            await callback.message.edit_text(
                text,
                reply_markup=category_card_keyboard(
                    CategoryDTO.model_validate(category),
                    [ChainDTO.model_validate(c) for c in chains],
                    subcategories=[CategoryDTO.model_validate(c) for c in subcategories] if has_subcategories else None
                ).as_markup(),
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc):
                raise
    
    await callback.answer("Отменено")


@router.callback_query(F.data.startswith("categories:delete:"), authorized_filter)
async def delete_category_prompt(callback: CallbackQuery) -> None:
    category_id = int(callback.data.split(":")[-1])
    
    async with get_session() as session:
        cat_repo = CategoryRepository(session)
        chain_repo = ChainRepository(session)
        
        category = await cat_repo.get_by_id(category_id)
        if not category:
            await callback.answer("Категория не найдена", show_alert=True)
            return
        
        subcategories = await cat_repo.list_by_parent(category_id)
        if subcategories:
            await callback.answer(
                f"Нельзя удалить категорию с подкатегориями! Сначала удалите все {len(subcategories)} подкатегорий.",
                show_alert=True
            )
            return
        
        chains = await chain_repo.list_by_category(category_id)
        if chains:
            await callback.answer(
                f"Нельзя удалить категорию с цепочками! Сначала удалите все {len(chains)} цепочек.",
                show_alert=True
            )
            return
    
    if callback.message:
        try:
            await callback.message.edit_text(
                "Удалить категорию? Действие необратимо.",
                reply_markup=confirm_keyboard("categories:delete", category_id).as_markup(),
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc):
                raise
    await callback.answer()

