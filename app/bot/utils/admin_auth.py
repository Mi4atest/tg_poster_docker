"""Проверка прав: admin / operator / viewer (подмножество ALLOWED_USER_IDS)."""
from __future__ import annotations

import re
from typing import Literal, Optional

from aiogram.types import CallbackQuery, Message

from app.config.settings import ADMIN_USER_IDS, VIEWER_USER_IDS

UserRole = Literal["admin", "operator", "viewer"]

_VIEWER_PRODUCT_CARD_RE = re.compile(r"^product_\d+$")
_VIEWER_NEW_PRODUCT_CARD_RE = re.compile(r"^new_product_\d+$")
_VIEWER_ALLOWED_CALLBACKS = frozenset({
    "back_to_main",
    "products_menu",
    "products_list",
    "products_search",
    "products_archive",
    "psearch_collapse",
    "psearch_back",
    "new_products_menu",
    "avito_market_start",
    "avito_market_cancel",
    "avito_market_history",
    "avito_market_wl",
    "price_stale_list",
    "price_stale_sort_price",
    "price_stale_sort_sale",
})
_VIEWER_ALLOWED_PREFIXES = (
    "products_page_",
    "products_archive_year_",
    "products_archive_month_",
    "products_archive_day_",
    "iphone_version_",
    "iphone_model_",
    "psearch_",
    "new_cat_",
    "new_iphone_",
    "new_airpods_",
    "new_watch_",
    "new_ipad_",
    "new_custom_",
    "avito_market_",
    "price_stale_",
)
VIEWER_ALLOWED_FSM_STATES = frozenset({
    "ProductSearch:waiting_for_query",
    "IphoneMarketPriceState:waiting_for_query",
})


def is_admin_user(user_id: Optional[int]) -> bool:
    if user_id is None:
        return False
    return user_id in ADMIN_USER_IDS


def is_viewer_user(user_id: Optional[int]) -> bool:
    """Директор / витрина. Admin в VIEWER не считается viewer (приоритет admin)."""
    if user_id is None:
        return False
    if is_admin_user(user_id):
        return False
    return user_id in VIEWER_USER_IDS


def get_user_role(user_id: Optional[int]) -> UserRole:
    if is_admin_user(user_id):
        return "admin"
    if is_viewer_user(user_id):
        return "viewer"
    return "operator"


async def deny_unless_admin_callback(callback: CallbackQuery) -> bool:
    """True — можно продолжать; False — доступ запрещён (ответ пользователю уже отправлен)."""
    if is_admin_user(callback.from_user.id if callback.from_user else None):
        return True
    await callback.answer("⛔ Доступно только администраторам.", show_alert=True)
    return False


async def deny_unless_admin_message(message: Message) -> bool:
    if is_admin_user(message.from_user.id if message.from_user else None):
        return True
    await message.answer("⛔ Доступно только администраторам.")
    return False


async def deny_if_viewer_callback(callback: CallbackQuery) -> bool:
    """True — можно продолжать; False — viewer, мутация запрещена."""
    if not is_viewer_user(callback.from_user.id if callback.from_user else None):
        return True
    await callback.answer("👁 Только просмотр.", show_alert=True)
    return False


async def deny_if_viewer_message(message: Message) -> bool:
    if not is_viewer_user(message.from_user.id if message.from_user else None):
        return True
    await message.answer("👁 Только просмотр.")
    return False


def is_viewer_callback_allowed(data: Optional[str]) -> bool:
    """Навигация витрины и весь Avito market; мутации — нет."""
    if not data:
        return False
    if data in _VIEWER_ALLOWED_CALLBACKS:
        return True
    if any(data.startswith(prefix) for prefix in _VIEWER_ALLOWED_PREFIXES):
        return True
    if _VIEWER_PRODUCT_CARD_RE.fullmatch(data):
        return True
    if _VIEWER_NEW_PRODUCT_CARD_RE.fullmatch(data):
        return True
    return False


def is_viewer_fsm_state_allowed(state: Optional[str]) -> bool:
    return bool(state) and state in VIEWER_ALLOWED_FSM_STATES
