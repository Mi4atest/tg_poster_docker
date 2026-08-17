#!/usr/bin/env python3
"""Резерв слотов каталога б/у в канале Max и заполнение списка.

По умолчанию публикует 15 текстовых заглушек от имени бота (только если ID ещё
не сохранены), затем редактирует их актуальным каталогом.

  docker-compose exec app python -m app.scripts.reserve_max_used_catalog
  docker-compose exec app python -m app.scripts.reserve_max_used_catalog --count 15
  docker-compose exec app python -m app.scripts.reserve_max_used_catalog --force
  docker-compose exec app python -m app.scripts.reserve_max_used_catalog --update-only
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def async_main(count: int, force: bool, update_only: bool) -> int:
    from app.bot.utils.used_products_max_channel_updater import (
        MAX_USED_CATALOG_RESERVED_COUNT,
        reserve_max_used_catalog_messages,
        update_used_products_list_in_max_channel,
    )
    from app.services.settings_service import get_settings_service

    service = get_settings_service()
    chat_id = (service.get_max_channel_id() or "").strip()
    if not chat_id:
        logger.error("MAX_CHANNEL_ID не задан")
        return 1

    if not update_only:
        ids = await reserve_max_used_catalog_messages(count=count, force=force)
        logger.info("Зарезервировано слотов: %s", len(ids))
        logger.info("IDs: %s", ", ".join(ids))
    else:
        ids = list(service.get_max_used_products_list_message_ids() or [])
        if not ids:
            logger.error("ID сообщений Max не заданы — уберите --update-only или зарезервируйте слоты")
            return 1
        logger.info("Обновляю существующие %s слотов", len(ids))

    ok = await update_used_products_list_in_max_channel()
    catalog_url = service.get_max_used_catalog_url() or "не задан"
    logger.info("Каталог обновлён: %s", "да" if ok else "нет")
    logger.info("URL каталога в постах Max: %s", catalog_url)
    if count != MAX_USED_CATALOG_RESERVED_COUNT:
        logger.info("Запрошено слотов: %s (дефолт %s)", count, MAX_USED_CATALOG_RESERVED_COUNT)
    return 0 if ok else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Резерв и заполнение каталога б/у в Max")
    parser.add_argument("--count", type=int, default=15, help="сколько сообщений зарезервировать")
    parser.add_argument(
        "--force",
        action="store_true",
        help="опубликовать новые слоты, даже если ID уже сохранены",
    )
    parser.add_argument(
        "--update-only",
        action="store_true",
        help="только edit существующих ID, без новых постов",
    )
    args = parser.parse_args()
    if args.count < 1 or args.count > 30:
        parser.error("--count должен быть от 1 до 30")
    return asyncio.run(async_main(args.count, args.force, args.update_only))


if __name__ == "__main__":
    raise SystemExit(main())
