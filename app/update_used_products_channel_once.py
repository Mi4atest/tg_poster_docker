#!/usr/bin/env python3
"""Однократное обновление сообщений в канале со списком б/у (пересчёт списка и блока «Свежее поступление»)."""
import asyncio

from aiogram import Bot
from app.config.settings import TELEGRAM_BOT_TOKEN
from app.bot.utils.used_products_lists import refresh_used_products_catalogs


async def main():
    if not TELEGRAM_BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN не задан")
        return
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    try:
        result = await refresh_used_products_catalogs(bot)
        print(
            "OK TG={telegram} Max={max}".format(**result)
            if any(result.values())
            else "Не выполнено (проверьте ID сообщений каталога и каналы)"
        )
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
