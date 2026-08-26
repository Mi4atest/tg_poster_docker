import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from app.config.settings import TELEGRAM_BOT_TOKEN
from app.bot.handlers import (
    start,
    post_creation,
    post_management,
    scheduler,
    product_management,
    evening_report,
    new_products_management,
    bulk_price_update,
    price_tags,
    settings,
    menu_constructor,
    shop_notes,
)
from app.bot.middlewares.auth import AuthMiddleware
from app.bot.middlewares.album import AlbumMiddleware
from app.scheduler.orchestrator import PublicationOrchestrator
from app.services.settings_service import get_settings_service
from app.services.price_sync_service import get_price_sync_service

# Configure logging
logging.basicConfig(level=logging.INFO)

# Initialize bot and dispatcher
bot = Bot(token=TELEGRAM_BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Add user_data dictionary to bot
bot.user_data = {}
bot.signature_enabled = True  # По умолчанию включено, как в .env
bot.vk_market_enabled = True  # По умолчанию включено

# Register middlewares
dp.message.middleware(AuthMiddleware())
dp.callback_query.middleware(AuthMiddleware())
# Album middleware buffers media-group messages so handlers receive the whole
# album sorted by message_id (preserves photo order without "send ungrouped").
dp.message.middleware(AlbumMiddleware())

# Register handlers
# Важно: product_management должен быть зарегистрирован ПЕРЕД post_management,
# чтобы обработчики товаров (с состояниями FSM) обрабатывались раньше общих обработчиков постов
dp.include_router(start.router)
dp.include_router(shop_notes.router)
dp.include_router(post_creation.router)
dp.include_router(product_management.router)  # Перемещено выше для приоритета
dp.include_router(evening_report.router)
dp.include_router(new_products_management.router)
dp.include_router(bulk_price_update.router)
dp.include_router(price_tags.router)
dp.include_router(settings.router)
dp.include_router(post_management.router)
dp.include_router(scheduler.router)
dp.include_router(menu_constructor.router)

async def set_commands():
    """Set bot commands."""
    commands = [
        BotCommand(command="start", description="Запустить бота"),
    ]
    await bot.set_my_commands(commands)

async def main():
    """Main function."""
    get_settings_service().bootstrap_from_env_once()

    # Set bot commands
    await set_commands()
    
    # Initialize orchestrator
    orchestrator = PublicationOrchestrator(signature_enabled=bot.signature_enabled)
    orchestrator.start()
    bot.orchestrator = orchestrator
    
    # Start workers in background
    await orchestrator.start_workers()

    get_price_sync_service().start(bot)

    # Автоматическое резервное копирование БД (расписание из «Настройки → Бэкап»)
    from app.services.backup_service import backup_scheduler_loop
    backup_task = asyncio.create_task(backup_scheduler_loop())

    try:
        # Start polling
        await dp.start_polling(bot)
    finally:
        backup_task.cancel()
        await get_price_sync_service().stop()
        # Stop orchestrator on shutdown
        await orchestrator.stop()

if __name__ == "__main__":
    asyncio.run(main())
