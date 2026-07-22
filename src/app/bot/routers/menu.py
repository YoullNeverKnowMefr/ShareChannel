from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import main_menu_keyboard
from app.bot.middlewares import authorized_filter

router = Router(name="menu")


@router.message(Command("menu"), authorized_filter)
async def menu_command(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    is_authorized = data.get("authorized", False)
    await state.clear()
    if is_authorized:
        await state.update_data(authorized=True)
    await message.answer(
        "Главное меню:",
        reply_markup=main_menu_keyboard().as_markup(),
    )


@router.message(F.text == "◀️ Назад", authorized_filter)
async def back_button(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    wizard_messages = data.get("wizard_messages", [])
    for msg_id in wizard_messages:
        try:
            await message.chat.delete_message(msg_id)
        except Exception:
            pass

    await state.clear()
    await state.update_data(authorized=True)
    await message.answer(
        "Главное меню:",
        reply_markup=main_menu_keyboard().as_markup(),
    )


@router.callback_query(F.data == "menu:home", authorized_filter)
async def menu_home(callback: CallbackQuery, state: FSMContext) -> None:
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
    
    if callback.message:
        await callback.message.edit_text(
            "Главное меню:",
            reply_markup=main_menu_keyboard().as_markup(),
        )
    await callback.answer()
