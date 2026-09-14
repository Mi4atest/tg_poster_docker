"""Блокирует мутации для роли viewer; навигация витрины и Avito market проходят."""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.bot.utils.admin_auth import (
    is_viewer_callback_allowed,
    is_viewer_fsm_state_allowed,
    is_viewer_user,
)


class ViewerGuardMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = None
        if isinstance(event, Message):
            user = event.from_user
        elif isinstance(event, CallbackQuery):
            user = event.from_user
        if not user or not is_viewer_user(user.id):
            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            if is_viewer_callback_allowed(event.data):
                return await handler(event, data)
            await event.answer("👁 Только просмотр.", show_alert=True)
            return

        if isinstance(event, Message):
            text = (event.text or "").strip()
            if text.startswith("/start"):
                return await handler(event, data)
            state = data.get("state")
            current = await state.get_state() if state is not None else None
            if is_viewer_fsm_state_allowed(current):
                return await handler(event, data)
            await event.answer("👁 Только просмотр.")
            return

        return await handler(event, data)
