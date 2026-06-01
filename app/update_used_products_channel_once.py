#!/usr/bin/env python3
"""Однократное обновление сообщений в канале со списком б/у (пересчёт списка и блока «Свежее поступление»)."""
import asyncio

from aiogram import Bot
from app.config.settings import TELEGRAM_BOT_TOKEN
from app.bot.utils.used_products_channel_updater import update_used_products_list_in_channel


async def main():
    if not TELEGRAM_BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN не задан")
        return
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    try:
        ok = await update_used_products_list_in_channel(bot)
        print("OK" if ok else "Не выполнено (проверьте USED_PRODUCTS_LIST_MESSAGE_IDS и TELEGRAM_CHANNEL_ID)")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
