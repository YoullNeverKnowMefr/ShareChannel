from __future__ import annotations

from typing import Sequence

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.domain.dto import ChainDTO, ShopDTO, CategoryDTO
from app.domain.models import ChainStatus


def reply_back_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="◀️ Назад")]],
        resize_keyboard=True,
        is_persistent=True,
    )


def main_menu_keyboard() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.button(text="🛡 Безопасность", callback_data="security:menu")
    builder.button(text="🛒 Магазины", callback_data="shops:list:0")
    builder.adjust(1, 1)
    return builder


def security_menu_keyboard() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔐 Сменить пароль", callback_data="security:change")
    builder.button(text="⏱️ Задержка подхвата постов", callback_data="security:pickup_delay")
    builder.button(text="👥 Авторизованные", callback_data="security:authorized_users")
    builder.button(text="🚫 Заблокированные", callback_data="security:blocked_users")
    builder.button(text="📋 История входов", callback_data="security:login_history")
    builder.button(text="♻️ Обнулить попытки", callback_data="security:reset")
    builder.button(text="🔑 Telethon аккаунты", callback_data="accounts:menu")
    builder.button(text="💾 Бэкап базы", callback_data="security:backup")
    builder.button(text="⬅️ Назад", callback_data="menu:home")
    builder.adjust(1)
    return builder


def shops_keyboard(shops: Sequence[ShopDTO], page: int, has_next: bool) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    for shop in shops:
        builder.button(text=f"Магазин {shop.name}", callback_data=f"shops:view:{shop.id}")
    builder.button(text="➕ Добавить магазин", callback_data="shops:new")
    if page > 0:
        builder.button(text="⬅️ Назад", callback_data=f"shops:list:{page-1}")
    if has_next:
        builder.button(text="➡️ Далее", callback_data=f"shops:list:{page+1}")
    builder.button(text="🏠 Главное меню", callback_data="menu:home")
    builder.adjust(1)
    return builder


def shop_card_keyboard(shop_id: int) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.button(text="📂 Категории", callback_data=f"categories:list:{shop_id}")
    builder.button(text="✏️ Переименовать магазин", callback_data=f"shops:rename:{shop_id}")
    builder.button(text="🗑 Удалить магазин", callback_data=f"shops:delete:{shop_id}")
    builder.button(text="⬅️ Назад", callback_data="shops:list:0")
    builder.adjust(1)
    return builder


def categories_keyboard(categories: Sequence[CategoryDTO], shop_id: int, parent_id: int | None = None) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    
    for category in categories:
        builder.button(text=f"📂 {category.name}", callback_data=f"categories:view:{category.id}")
    
    if parent_id is None:
        builder.button(text="➕ Добавить категорию", callback_data=f"categories:create:{shop_id}")
        builder.button(text="⬅️ Назад", callback_data=f"shops:view:{shop_id}")
    else:
        builder.button(text="➕ Добавить подкатегорию", callback_data=f"categories:create:{shop_id}:{parent_id}")
        builder.button(text="⬅️ Назад", callback_data=f"categories:list:{shop_id}")
    
    builder.adjust(1)
    return builder


def category_card_keyboard(category: CategoryDTO, chains: Sequence[ChainDTO], subcategories: Sequence[CategoryDTO] | None = None) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    
    has_subcategories = subcategories is not None and len(subcategories) > 0
    
    if has_subcategories:
        for subcat in subcategories:
            builder.button(text=f"📂 {subcat.name}", callback_data=f"categories:view:{subcat.id}")
    
    for chain in chains:
        if chain.status == ChainStatus.ACTIVE:
            status_icon = "✅"
        elif chain.status == ChainStatus.ERROR:
            status_icon = "🔴"
        else:
            status_icon = "⏸"
        chain_name = chain.source_chat_title or f"ID {chain.source_chat_id}"
        text = f"{status_icon} {chain_name} | {chain.interval_seconds // 60}м"
        builder.button(text=text, callback_data=f"chains:view:{chain.id}")
    
    if not has_subcategories:
        builder.button(text="➕ Добавить канал", callback_data=f"chains:new:{category.shop_id}:{category.id}")
    
    if category.parent_id is None:
        builder.button(text="➕ Добавить подкатегорию", callback_data=f"categories:create:{category.shop_id}:{category.id}")
    
    builder.button(text="✏️ Переименовать", callback_data=f"categories:rename:{category.id}")
    builder.button(text="🗑 Удалить категорию", callback_data=f"categories:delete:{category.id}")
    builder.button(text="⬅️ Назад", callback_data=f"categories:list:{category.shop_id}")
    builder.adjust(1)
    return builder


def chain_card_keyboard(chain: ChainDTO) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    toggle_action = "pause" if chain.status == ChainStatus.ACTIVE else "resume"
    toggle_text = "⏸ Пауза" if chain.status == ChainStatus.ACTIVE else "▶️ Возобновить"
    builder.button(text="⏱ Изменить интервал", callback_data=f"chains:interval:{chain.id}")
    builder.button(text=toggle_text, callback_data=f"chains:{toggle_action}:{chain.id}")
    builder.button(text="🗑 Удалить", callback_data=f"chains:delete:{chain.id}")
    builder.button(text="🔍 Проверить доступы", callback_data=f"chains:permissions:{chain.id}")
    if chain.category_id:
        builder.button(text="⬅️ Назад", callback_data=f"categories:view:{chain.category_id}")
    else:
        builder.button(text="⬅️ Назад", callback_data=f"shops:view:{chain.shop_id}")
    builder.adjust(1)
    return builder


def confirm_keyboard(action: str, entity_id: int) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да", callback_data=f"{action}:confirm:{entity_id}")
    builder.button(text="❌ Нет", callback_data=f"{action}:cancel:{entity_id}")
    builder.adjust(2)
    return builder


def wizard_confirm_keyboard() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Активировать", callback_data="wizard:activate")
    builder.button(text="❌ Отмена", callback_data="wizard:cancel")
    builder.adjust(1)
    return builder
