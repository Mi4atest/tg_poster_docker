"""Проверка прав администратора (подмножество ALLOWED_USER_IDS)."""
from __future__ import annotations

from typing import Optional

from aiogram.types import CallbackQuery, Message

from app.config.settings import ADMIN_USER_IDS


def is_admin_user(user_id: Optional[int]) -> bool:
    if user_id is None:
        return False
    return user_id in ADMIN_USER_IDS


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
