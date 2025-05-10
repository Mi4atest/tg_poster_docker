import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from app.config.settings import TELEGRAM_BOT_TOKEN
from app.bot.handlers import start, post_creation, post_management
from app.bot.middlewares.auth import AuthMiddleware

# Configure logging
logging.basicConfig(level=logging.INFO)

# Initialize bot and dispatcher
bot = Bot(token=TELEGRAM_BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Add user_data dictionary to bot
bot.user_data = {}

# Register middlewares
dp.message.middleware(AuthMiddleware())
dp.callback_query.middleware(AuthMiddleware())

# Register handlers
dp.include_router(start.router)
dp.include_router(post_creation.router)
dp.include_router(post_management.router)

async def set_commands():
    """Set bot commands."""
    commands = [
        BotCommand(command="start", description="Запустить бота"),
    ]
    await bot.set_my_commands(commands)

async def main():
    """Main function."""
    # Set bot commands
    await set_commands()

    # Start polling
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
