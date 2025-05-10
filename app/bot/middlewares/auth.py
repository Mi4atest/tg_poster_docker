from aiogram import types, BaseMiddleware
from typing import Any, Awaitable, Callable, Dict

from app.config.settings import ALLOWED_USER_IDS

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

        # If user is not allowed, ignore the message
        if user and user.id not in ALLOWED_USER_IDS:
            if isinstance(event, types.Message):
                await event.answer("У вас нет доступа к этому боту.")
            elif isinstance(event, types.CallbackQuery):
                await event.answer("У вас нет доступа к этому боту.", show_alert=True)
            return

        # If user is allowed, continue processing
        return await handler(event, data)
