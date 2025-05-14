import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from app.config.settings import (
    TELEGRAM_BOT_TOKEN,
    USE_WEBHOOK,
    WEBHOOK_URL,
    WEBHOOK_SECRET
)
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

async def setup_webhook():
    """Setup webhook."""
    # Set webhook
    webhook_info = await bot.get_webhook_info()

    # If webhook URL is already set and matches our URL, do nothing
    if webhook_info.url == WEBHOOK_URL:
        return

    # Remove webhook before setting it
    await bot.delete_webhook()

    # Set webhook with secret token for improved security
    await bot.set_webhook(
        url=WEBHOOK_URL,
        secret_token=WEBHOOK_SECRET
    )

    logging.info(f"Webhook set to {WEBHOOK_URL}")

async def start_webhook():
    """Start webhook server."""
    # Create aiohttp application
    app = web.Application()

    # Setup webhook handler
    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=WEBHOOK_SECRET,
    )

    # Setup application
    webhook_requests_handler.register(app, path="/webhook")

    # Setup aiohttp application
    setup_application(app, dp, bot=bot)

    # Start aiohttp application
    runner = web.AppRunner(app)
    await runner.setup()

    # Extract host and port from webhook URL
    from urllib.parse import urlparse
    parsed_url = urlparse(WEBHOOK_URL)
    host = parsed_url.hostname or "0.0.0.0"
    port = parsed_url.port or 8443

    # Start site
    site = web.TCPSite(runner, host, port)
    await site.start()

    logging.info(f"Webhook server started at {host}:{port}")

    # Keep the server running
    while True:
        await asyncio.sleep(3600)

async def main():
    """Main function."""
    # Set bot commands
    await set_commands()

    if USE_WEBHOOK and WEBHOOK_URL:
        # Setup webhook
        await setup_webhook()

        # Start webhook server
        await start_webhook()
    else:
        # Start polling
        logging.info("Starting bot in polling mode")
        await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
