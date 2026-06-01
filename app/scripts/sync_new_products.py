"""
Скрипт для первоначальной синхронизации новых товаров из подборок ВК в БД.

Запуск:
  docker-compose exec app python -m app.scripts.sync_new_products

Или с хоста из корня проекта:
  python -m app.scripts.sync_new_products
"""
import sys
import logging
from pathlib import Path

root = Path(__file__).resolve().parent.parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    from app.utils.vk_market_sync import sync_new_products_from_vk
    result = sync_new_products_from_vk()
    for coll, count in result.items():
        logger.info("%s: %d товаров синхронизировано", coll, count)
    total = sum(result.values())
    logger.info("Всего синхронизировано: %d", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
