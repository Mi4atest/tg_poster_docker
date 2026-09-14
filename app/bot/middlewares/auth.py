from aiogram import types, BaseMiddleware
from typing import Any, Awaitable, Callable, Dict

from app.config.settings import ALLOWED_USER_IDS, VIEWER_USER_IDS


class AuthMiddleware(BaseMiddleware):
    """Middleware to check if user is allowed to use the bot."""

    async def __call__(
        self,
        handler: Callable[[types.TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: types.TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        # Check if event is a message or callback query
        user = None
        if isinstance(event, types.Message):
            user = event.from_user
        elif isinstance(event, types.CallbackQuery):
            user = event.from_user

        in_allowed = bool(user and user.id in ALLOWED_USER_IDS)
        in_viewer = bool(user and user.id in VIEWER_USER_IDS)
        # Viewer — отдельный whitelist: не смешивать с ALLOWED (иначе пустой ADMIN = viewer станет админом).
        if user and not in_allowed and not in_viewer:
            if isinstance(event, types.Message):
                await event.answer("У вас нет доступа к этому боту.")
            elif isinstance(event, types.CallbackQuery):
                await event.answer("У вас нет доступа к этому боту.", show_alert=True)
            return

        # If user is allowed, continue processing
        return await handler(event, data)
