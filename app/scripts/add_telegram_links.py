"""
Скрипт для добавления ссылок на посты в Telegram в записи товаров (Product) и постов (Post).

Запуск с хоста (рекомендуется — без флага -m):
  docker-compose exec app python /app/app/scripts/add_telegram_links.py

Или через sh -c:
  docker-compose exec app sh -c "python -m app.scripts.add_telegram_links"
"""
import sys
from pathlib import Path

# Обеспечиваем импорт app при запуске из корня
root = Path(__file__).resolve().parent.parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))


def main():
    from app.db.database import SessionLocal
    from app.api.models.product import Product
    from app.api.models.post import Post

    # Пары: (подстрока в названии товара для поиска, ссылка на пост в ТГ)
    # Код в конце названия (5969, 8710, 0511, 0605) уникально идентифицирует товар
    UPDATES = [
        ("5969", "https://t.me/AppleShop43/7547"),   # iPhone 14 128Gb Yellow 5969
        ("8710", "https://t.me/AppleShop43/7872"),   # iPhone 14 Pro 128Gb Deep Purple 8710
        ("0511", "https://t.me/AppleShop43/8304"),   # iPhone 14 Pro Max 128Gb Silver 0511
        ("0605", "https://t.me/AppleShop43/8175"),   # iPhone 15 Pro Max 256Gb Blue Titanium 0605
        ("6627", "https://t.me/AppleShop43/14006"),  # iPhone 12 Pro 256Gb Pacific Blue 6627
        ("6786", "https://t.me/AppleShop43/13962"),  # iPhone 14 256Gb Purple 6786
    ]

    db = SessionLocal()
    updated = 0
    try:
        for code, telegram_link in UPDATES:
            # Ищем товар по коду в названии (в т.ч. архив / unavailable, не deleted)
            product = (
                db.query(Product)
                .filter(
                    Product.name.like(f"%{code}"),
                    Product.status != "deleted",
                )
                .order_by(Product.id.desc())
                .first()
            )

            if not product:
                print(f"  Товар с кодом '{code}' не найден, пропуск.")
                continue

            post = db.query(Post).filter(Post.id == product.post_id).first()
            if not post:
                print(f"  Пост post_id={product.post_id} для товара '{product.name}' не найден, пропуск.")
                continue

            product.telegram_link = telegram_link
            post.telegram_link = telegram_link
            # Помечаем пост как опубликованный в ТГ, если ещё не помечен
            if not post.is_published_telegram:
                post.is_published_telegram = True

            print(f"  OK: {product.name[:50]}... -> {telegram_link}")
            updated += 1

        db.commit()
        print(f"\nОбновлено записей: {updated} из {len(UPDATES)}.")
    except Exception as e:
        db.rollback()
        print(f"Ошибка: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
