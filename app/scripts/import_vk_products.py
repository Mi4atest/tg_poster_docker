"""
Скрипт для импорта товаров из JSON файла в БД на VPS.
Оптимизирован для слабых серверов: импорт батчами по 20 товаров.

Использование:
  docker-compose exec app python -m app.scripts.import_vk_products --file vk_products_export.json --batch-size 20
"""
import json
import sys
import argparse
import time
from pathlib import Path

root = Path(__file__).resolve().parent.parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from app.db.database import SessionLocal
from app.api.models.product import Product
from app.api.models.post import Post

POST_ID_FOR_NEW_PRODUCTS = "vk_new_products_sync"


def get_or_create_sync_post(db):
    post = db.query(Post).filter(Post.id == POST_ID_FOR_NEW_PRODUCTS).first()
    if post:
        return post.id
    post = Post(id=POST_ID_FOR_NEW_PRODUCTS, text="Синхронизация товаров из ВК")
    db.add(post)
    db.commit()
    return post.id


def import_products_from_json(json_file: str, batch_size: int = 20):
    json_path = Path(json_file)
    
    if not json_path.exists():
        print(f"Ошибка: файл {json_path} не найден")
        sys.exit(1)
    
    print(f"Чтение файла {json_path}...")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    products = data.get("products", [])
    if not products:
        print("Файл не содержит товаров")
        return
    
    print(f"Найдено товаров: {len(products)}")
    print(f"Размер батча: {batch_size}")
    print(f"Импорт начнется через 2 секунды...")
    time.sleep(2)
    
    db = SessionLocal()
    try:
        post_id = get_or_create_sync_post(db)
        print(f"✓ Используется post_id: {post_id}\n")
        
        created = updated = skipped = 0
        
        for i in range(0, len(products), batch_size):
            batch = products[i:i + batch_size]
            batch_num = (i // batch_size) + 1
            total_batches = (len(products) + batch_size - 1) // batch_size
            
            print(f"Батч {batch_num}/{total_batches} ({len(batch)} товаров)...")
            
            try:
                for product_data in batch:
                    vk_product_id = product_data.get("vk_product_id")
                    collection_name = product_data.get("collection_name")
                    
                    if not vk_product_id:
                        skipped += 1
                        continue
                    
                    existing = db.query(Product).filter(
                        Product.vk_product_id == vk_product_id,
                        Product.collection_name == collection_name,
                    ).first()
                    
                    if existing:
                        existing.name = product_data.get("name", existing.name)
                        existing.price = product_data.get("price") or existing.price
                        existing.vk_product_link = product_data.get("vk_product_link") or existing.vk_product_link
                        existing.collection_id = product_data.get("collection_id") or existing.collection_id
                        updated += 1
                    else:
                        product = Product(
                            post_id=post_id,
                            vk_product_id=vk_product_id,
                            vk_product_link=product_data.get("vk_product_link"),
                            name=product_data.get("name", "Без названия"),
                            price=product_data.get("price"),
                            collection_id=product_data.get("collection_id"),
                            collection_name=collection_name,
                            status="active",
                        )
                        db.add(product)
                        created += 1
                
                db.commit()
                print(f"  ✓ Создано: {created}, Обновлено: {updated}, Пропущено: {skipped}")
                
                if i + batch_size < len(products):
                    time.sleep(1)
                    
            except Exception as e:
                print(f"  ✗ Ошибка в батче: {e}")
                db.rollback()
                continue
        
        print(f"\n✓ Импорт завершен! Создано: {created}, Обновлено: {updated}")
        
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", "-f", default="vk_products_export.json")
    parser.add_argument("--batch-size", "-b", type=int, default=20)
    
    args = parser.parse_args()
    
    try:
        import_products_from_json(args.file, args.batch_size)
    except KeyboardInterrupt:
        print("\n\nПрервано пользователем")
        sys.exit(1)
    except Exception as e:
        print(f"\nОшибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
