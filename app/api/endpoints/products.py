from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Optional
from sqlalchemy import or_

from app.db.database import get_db
from app.api.models.product import Product
from app.api.models.post import Post
from app.api.schemas.product import (
    Product as ProductSchema,
    ProductList,
    ProductStatusUpdate,
    ProductTelegramLinkUpdate,
    ProductAvailabilityUpdate,
    ProductAvitoLinkUpdate,
    PricePlatformSync,
    PriceSyncReport,
    ProductPriceUpdateResponse,
    ProductStatusUpdateResponse,
)
from app.workers.vk.product_publisher import publish_product_to_vk
from app.utils.vk_market_sync import sync_new_products_from_vk
from app.integrations.avito.parse import parse_avito_item_ref

router = APIRouter()


def product_to_schema(db: Session, product) -> ProductSchema:
    """Схема товара; max_share_url и ссылки на пост ленты VK подставляем с поста."""
    if isinstance(product, dict):
        data = dict(product)
    else:
        data = ProductSchema.model_validate(product).model_dump()
        needs_post = (
            not data.get("max_share_url")
            or not data.get("vk_post_id")
            or not data.get("vk_post_link")
        )
        post_id = getattr(product, "post_id", None)
        if needs_post and post_id:
            row = (
                db.execute(
                    text(
                        "SELECT max_share_url, vk_post_id, vk_post_link "
                        "FROM posts WHERE id = :id LIMIT 1"
                    ),
                    {"id": post_id},
                )
                .mappings()
                .first()
            )
            if row:
                if not data.get("max_share_url") and row.get("max_share_url"):
                    data["max_share_url"] = row["max_share_url"]
                if not data.get("vk_post_id") and row.get("vk_post_id"):
                    data["vk_post_id"] = row["vk_post_id"]
                if not data.get("vk_post_link") and row.get("vk_post_link"):
                    data["vk_post_link"] = row["vk_post_link"]
    return ProductSchema(**data)


def _fetch_product_row(db: Session, product_id: int) -> Optional[dict]:
    row = (
        db.execute(
            text("SELECT * FROM products WHERE id = :id LIMIT 1"),
            {"id": product_id},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def _row_as_product(row: dict):
    from types import SimpleNamespace
    return SimpleNamespace(**row)


@router.get("/", response_model=ProductList)
def get_products(
    skip: int = 0,
    limit: int = 300,
    status_filter: Optional[str] = None,
    search: Optional[str] = None,
    collection_name: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Get list of products with optional filtering."""
    where = ["1=1"]
    params: dict = {"skip": skip, "limit": limit}
    if status_filter:
        where.append("status = :status_filter")
        params["status_filter"] = status_filter
    if search:
        where.append("name ILIKE :search")
        params["search"] = f"%{search}%"
    if collection_name:
        where.append("collection_name = :collection_name")
        params["collection_name"] = collection_name
    where_sql = " AND ".join(where)
    total = db.execute(
        text(f"SELECT COUNT(*) FROM products WHERE {where_sql}"), params
    ).scalar() or 0
    rows = (
        db.execute(
            text(
                f"SELECT * FROM products WHERE {where_sql} "
                "ORDER BY created_at DESC OFFSET :skip LIMIT :limit"
            ),
            params,
        )
        .mappings()
        .all()
    )
    items = [ProductSchema.model_validate(dict(r)) for r in rows]
    return ProductList(items=items, total=total)


@router.post("/sync-from-vk")
def sync_products_from_vk():
    """Синхронизировать новые товары из подборок ВК в БД."""
    result = sync_new_products_from_vk()
    return {"ok": True, "synced": result}


@router.get("/{product_id}", response_model=ProductSchema)
def get_product(product_id: int, db: Session = Depends(get_db)):
    """Get product by ID."""
    from app.db.product_queries import fetch_product_detail_row_by_id

    row = fetch_product_detail_row_by_id(product_id)
    if not row:
        raise HTTPException(status_code=404, detail="Product not found")
    return ProductSchema.model_validate(row)


@router.get("/post/{post_id}", response_model=ProductSchema)
def get_product_by_post(post_id: str, db: Session = Depends(get_db)):
    """Get product by post ID."""
    product = db.query(Product).filter(Product.post_id == post_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found for this post")
    return product_to_schema(db, product)


@router.put("/{product_id}/status", response_model=ProductStatusUpdateResponse)
async def update_product_status(
    product_id: int,
    status_update: ProductStatusUpdate,
    db: Session = Depends(get_db)
):
    """Update product status (active, unavailable, deleted); ВК и Авито — по возможности."""
    import logging
    from datetime import datetime, timezone

    logger = logging.getLogger(__name__)

    row = _fetch_product_row(db, product_id)
    if not row:
        raise HTTPException(status_code=404, detail="Product not found")

    valid_statuses = ["active", "unavailable", "deleted"]
    if status_update.status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}",
        )

    old_status = row.get("status")
    now = datetime.now(timezone.utc)
    archived_at = row.get("archived_at")
    if status_update.status == "unavailable" and old_status == "active":
        archived_at = now
    elif status_update.status == "active" and old_status == "unavailable":
        archived_at = None
        if row.get("avito_item_id"):
            try:
                from app.integrations.avito import archive_queue as avito_archive_queue

                avito_archive_queue.cancel_pending_product(int(product_id))
            except Exception:
                pass

    db.execute(
        text(
            "UPDATE products SET status = :status, archived_at = :archived_at, "
            "updated_at = :updated_at WHERE id = :id"
        ),
        {
            "status": status_update.status,
            "archived_at": archived_at,
            "updated_at": now,
            "id": product_id,
        },
    )

    vk_sync = PricePlatformSync(status="skipped")
    vk_product_id = row.get("vk_product_id")
    if status_update.sync_platforms and vk_product_id:
        try:
            from app.utils.vk_client import get_market_vk_session, resolved_vk_group_id_int

            owner_id = -resolved_vk_group_id_int()
            vk = get_market_vk_session().get_api()

            if status_update.status == "deleted":
                vk.market.delete(
                    owner_id=owner_id,
                    item_id=vk_product_id,
                )
            elif status_update.status == "unavailable":
                vk.market.edit(
                    owner_id=owner_id,
                    item_id=vk_product_id,
                    deleted=1,
                )
            elif status_update.status == "active" and old_status in ["unavailable", "deleted"]:
                vk.market.edit(
                    owner_id=owner_id,
                    item_id=vk_product_id,
                    deleted=0,
                )
            vk_sync = PricePlatformSync(status="ok")
        except Exception as e:
            logger.error("Error updating product status in VK: %s", e)
            vk_sync = PricePlatformSync(status="error", detail=str(e)[:200])

    avito_sync = PricePlatformSync(status="skipped")
    avito_item_id = row.get("avito_item_id")
    post_id = row.get("post_id")
    product_name = row.get("name") or ""
    if status_update.sync_platforms and avito_item_id:
        if status_update.status in ("unavailable", "deleted"):
            from app.integrations.avito import archive_queue as avito_archive_queue

            item_id = int(str(avito_item_id).strip())
            if not post_id:
                avito_sync = PricePlatformSync(
                    status="skipped",
                    detail="Нет поста для автозагрузки — снимите объявление вручную в ЛК Авито",
                )
            else:
                _created, detail = avito_archive_queue.enqueue(
                    product_id=int(product_id),
                    avito_item_id=item_id,
                    post_id=str(post_id),
                    product_name=product_name,
                )
                avito_sync = PricePlatformSync(status="pending", detail=detail)
        else:
            avito_sync = PricePlatformSync(
                status="skipped",
                detail="Объявление на Авито не меняли (только локальный статус)",
            )

    db.commit()
    updated_row = _fetch_product_row(db, product_id) or row
    schema = product_to_schema(db, _row_as_product(updated_row))
    sync = PriceSyncReport(
        vk=vk_sync,
        avito=avito_sync,
        database=PricePlatformSync(status="ok"),
    )
    return ProductStatusUpdateResponse(product=schema, status_sync=sync)


@router.post("/{post_id}/publish", response_model=ProductSchema)
async def publish_product(post_id: str, db: Session = Depends(get_db)):
    """Manually publish product to VK Market."""
    # Проверяем, существует ли пост
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    # Проверяем, не опубликован ли уже товар
    existing_product = db.query(Product).filter(Product.post_id == post_id).first()
    if existing_product and existing_product.vk_product_id:
        raise HTTPException(
            status_code=400,
            detail="Product already published for this post"
        )
    
    # Публикуем товар
    success = await publish_product_to_vk(post_id)
    
    if not success:
        raise HTTPException(
            status_code=500,
            detail="Failed to publish product to VK Market"
        )
    
    # Получаем созданный товар
    product = db.query(Product).filter(Product.post_id == post_id).first()
    if not product:
        raise HTTPException(
            status_code=500,
            detail="Product was published but not found in database"
        )
    
    return product_to_schema(db, product)


@router.delete("/{product_id}")
async def delete_product(product_id: int, db: Session = Depends(get_db)):
    """Delete product from VK Market and database."""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Удаляем товар из ВК, если он там есть
    if product.vk_product_id:
        try:
            from app.utils.vk_client import get_market_vk_session, resolved_vk_group_id_int

            vk = get_market_vk_session().get_api()

            vk.market.delete(
                owner_id=-resolved_vk_group_id_int(),
                item_id=product.vk_product_id
            )
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error deleting product from VK: {str(e)}")
            # Продолжаем удаление из БД даже если не удалось удалить из ВК
    
    # Удаляем товар из БД
    db.delete(product)
    db.commit()
    
    return {"message": "Product deleted successfully"}


@router.put("/{product_id}/price", response_model=ProductPriceUpdateResponse)
async def update_product_price(
    product_id: int,
    price_data: dict,
    db: Session = Depends(get_db)
):
    """Обновить цену в БД, ВК и Авито; вернуть товар и статусы по платформам."""
    import logging
    import re

    logger = logging.getLogger(__name__)
    row = _fetch_product_row(db, product_id)
    if not row:
        raise HTTPException(status_code=404, detail="Product not found")

    new_price = price_data.get("price")
    if not new_price:
        raise HTTPException(status_code=400, detail="Price is required")

    sync_platforms = price_data.get("sync_platforms", True)
    if isinstance(sync_platforms, str):
        sync_platforms = sync_platforms.strip().lower() not in ("0", "false", "no")

    price_clean = re.sub(r"[^\d.,]", "", str(new_price))
    price_clean = price_clean.replace(",", ".")
    try:
        price_value = int(float(price_clean))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid price format")

    vk_sync = PricePlatformSync(status="skipped")
    vk_product_id = row.get("vk_product_id")
    if sync_platforms and vk_product_id:
        try:
            from app.workers.vk.product_publisher import VKProductPublisher

            publisher = VKProductPublisher()
            ok = await publisher.update_product_price(vk_product_id, price_value)
            vk_sync = PricePlatformSync(
                status="ok" if ok else "error",
                detail=None if ok else "Не удалось обновить цену в VK Market",
            )
        except Exception as e:
            logger.error("Error updating product price in VK: %s", e)
            vk_sync = PricePlatformSync(status="error", detail=str(e)[:200])

    avito_sync = PricePlatformSync(status="skipped")
    avito_item_id = row.get("avito_item_id")
    if sync_platforms and avito_item_id:
        try:
            from app.integrations.avito import actions as avito_actions

            item_id = int(str(avito_item_id).strip())
            await avito_actions.update_item_price_rub(item_id, price_value)
            avito_sync = PricePlatformSync(status="ok")
        except Exception as e:
            logger.error("Error updating product price in Avito: %s", e)
            avito_sync = PricePlatformSync(status="error", detail=str(e)[:500])

    db.execute(
        text("UPDATE products SET price = :price, updated_at = NOW() WHERE id = :id"),
        {"price": new_price, "id": product_id},
    )
    db.commit()

    row = _fetch_product_row(db, product_id)
    schema = product_to_schema(db, _row_as_product(row))
    sync = PriceSyncReport(vk=vk_sync, avito=avito_sync, database=PricePlatformSync(status="ok"))
    return ProductPriceUpdateResponse(product=schema, price_sync=sync)


@router.put("/{product_id}/availability", response_model=ProductSchema)
def update_product_availability(
    product_id: int,
    data: ProductAvailabilityUpdate,
    db: Session = Depends(get_db)
):
    """Update product availability_status (available, on_order)."""
    row = _fetch_product_row(db, product_id)
    if not row:
        raise HTTPException(status_code=404, detail="Product not found")
    if data.availability_status is not None:
        if data.availability_status not in ("available", "on_order"):
            raise HTTPException(
                status_code=400,
                detail="availability_status must be 'available' or 'on_order'"
            )
        db.execute(
            text(
                "UPDATE products SET availability_status = :availability_status, "
                "updated_at = NOW() WHERE id = :id"
            ),
            {"availability_status": data.availability_status, "id": product_id},
        )
        db.commit()
    row = _fetch_product_row(db, product_id)
    return product_to_schema(db, _row_as_product(row))


@router.put("/{product_id}/avito_link", response_model=ProductSchema)
async def update_product_avito_link(
    product_id: int,
    data: ProductAvitoLinkUpdate,
    db: Session = Depends(get_db),
):
    """Привязать существующее объявление Авито к товару (id или URL)."""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    raw = (data.avito_link_or_id or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="avito_link_or_id is required")

    parsed = parse_avito_item_ref(raw)
    if not parsed.item_id:
        raise HTTPException(status_code=400, detail="Could not parse Avito item id from input")

    product.avito_item_id = str(parsed.item_id)
    product.avito_url = parsed.canonical_url or raw

    post = db.query(Post).filter(Post.id == product.post_id).first()
    if post:
        post.avito_item_id = product.avito_item_id
        post.avito_url = product.avito_url

    db.commit()
    db.refresh(product)
    return product_to_schema(db, product)


@router.put("/{product_id}/telegram_link", response_model=ProductSchema)
def update_product_telegram_link(
    product_id: int,
    data: ProductTelegramLinkUpdate,
    db: Session = Depends(get_db)
):
    """Обновить ссылку на пост в Telegram у товара и у связанного поста."""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    post = db.query(Post).filter(Post.id == product.post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found for this product")

    link = (data.telegram_link or "").strip()
    if not link:
        raise HTTPException(status_code=400, detail="telegram_link is required")

    product.telegram_link = link
    post.telegram_link = link
    if not post.is_published_telegram:
        post.is_published_telegram = True

    db.commit()
    db.refresh(product)
    return product_to_schema(db, product)