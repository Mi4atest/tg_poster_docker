"""Синхронные операции с товарами для бота.

Все функции блокирующие (raw SQL/ORM) — вызывать из async-кода только через
``app.db.database.run_db`` (asyncio.to_thread), чтобы не блокировать event loop.
Формат ответов совместим с JSON прежних HTTP-эндпоинтов /api/products/*.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text

from app.db.database import SessionLocal
from app.db.product_queries import fetch_product_detail_row, product_detail_row_to_api_dict

logger = logging.getLogger(__name__)


def _row_to_json_dict(row: Any) -> dict:
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


def _sync_block(status: str = "skipped", detail: Optional[str] = None) -> dict:
    out: dict = {"status": status}
    if detail:
        out["detail"] = detail
    return out


def _product_api_dict(db, product_id: int) -> Optional[dict]:
    row = fetch_product_detail_row(db, product_id)
    if not row:
        return None
    return product_detail_row_to_api_dict(row)


def _invalidate_menu_cache() -> None:
    try:
        from app.services.menu_constructor_service import invalidate_new_products_cache

        invalidate_new_products_cache()
    except Exception:
        pass


def fetch_products_list(
    status_filter: Optional[str] = None,
    search: Optional[str] = None,
    collection_name: Optional[str] = None,
    skip: int = 0,
    limit: int = 500,
) -> tuple[list[dict], int]:
    """Список товаров (аналог GET /api/products/ без HTTP)."""
    from app.db.product_queries import _PRODUCT_LIST_COLUMNS

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
    with SessionLocal() as db:
        total = db.execute(
            text(f"SELECT COUNT(*) FROM products WHERE {where_sql}"), params
        ).scalar() or 0
        rows = (
            db.execute(
                text(
                    f"SELECT {_PRODUCT_LIST_COLUMNS} FROM products WHERE {where_sql} "
                    "ORDER BY created_at DESC OFFSET :skip LIMIT :limit"
                ),
                params,
            )
            .mappings()
            .all()
        )
    return [_row_to_json_dict(r) for r in rows], int(total)


def set_product_status(
    product_id: int,
    status: str,
    sync_platforms: bool = True,
    archive_kind: Optional[str] = None,
) -> Optional[dict]:
    """Смена статуса товара (active/unavailable/deleted); ВК — по возможности.

    archive_kind: sale | transfer — только при снятии в unavailable (б/у).
    При восстановлении в active поле сбрасывается.

    Возвращает {"product": {...}, "status_sync": {...}} или None при ошибке.
    """
    if status not in ("active", "unavailable", "deleted"):
        logger.error("set_product_status: invalid status %r", status)
        return None

    with SessionLocal() as db:
        row = (
            db.execute(
                text(
                    "SELECT id, status, archived_at, avito_item_id, post_id, name, vk_product_id "
                    "FROM products WHERE id = :id LIMIT 1"
                ),
                {"id": product_id},
            )
            .mappings()
            .first()
        )
        if not row:
            return None
        row = dict(row)

        old_status = row.get("status")
        now = datetime.now(timezone.utc)
        archived_at = row.get("archived_at")
        if status == "unavailable" and old_status == "active":
            archived_at = now
        elif status == "active" and old_status == "unavailable":
            archived_at = None
            if row.get("avito_item_id"):
                try:
                    from app.integrations.avito import archive_queue as avito_archive_queue

                    avito_archive_queue.cancel_pending_product(int(product_id))
                except Exception:
                    pass

        sets = ["status = :status", "archived_at = :archived_at", "updated_at = :updated_at"]
        params: dict[str, Any] = {
            "status": status,
            "archived_at": archived_at,
            "updated_at": now,
            "id": product_id,
        }
        if status == "active":
            sets.append("archive_kind = NULL")
        elif status == "unavailable" and archive_kind is not None:
            kind = str(archive_kind).strip()
            if kind in ("sale", "transfer"):
                sets.append("archive_kind = :archive_kind")
                params["archive_kind"] = kind

        db.execute(
            text(
                f"UPDATE products SET {', '.join(sets)} WHERE id = :id"
            ),
            params,
        )

        vk_sync = _sync_block()
        vk_product_id = row.get("vk_product_id")
        if sync_platforms and vk_product_id:
            try:
                from app.utils.vk_client import get_market_vk_session, resolved_vk_group_id_int

                owner_id = -resolved_vk_group_id_int()
                vk = get_market_vk_session().get_api()
                if status == "deleted":
                    vk.market.delete(owner_id=owner_id, item_id=vk_product_id)
                elif status == "unavailable":
                    vk.market.edit(owner_id=owner_id, item_id=vk_product_id, deleted=1)
                elif status == "active" and old_status in ("unavailable", "deleted"):
                    vk.market.edit(owner_id=owner_id, item_id=vk_product_id, deleted=0)
                vk_sync = _sync_block("ok")
            except Exception as e:
                logger.error("Error updating product status in VK: %s", e)
                vk_sync = _sync_block("error", str(e)[:200])

        avito_sync = _sync_block()
        avito_item_id = row.get("avito_item_id")
        if sync_platforms and avito_item_id:
            if status in ("unavailable", "deleted"):
                from app.integrations.avito import archive_queue as avito_archive_queue

                item_id = int(str(avito_item_id).strip())
                if not row.get("post_id"):
                    avito_sync = _sync_block(
                        "skipped",
                        "Нет поста для автозагрузки — снимите объявление вручную в ЛК Авито",
                    )
                else:
                    _created, detail = avito_archive_queue.enqueue(
                        product_id=int(product_id),
                        avito_item_id=item_id,
                        post_id=str(row["post_id"]),
                        product_name=row.get("name") or "",
                    )
                    avito_sync = _sync_block("pending", detail)
            else:
                avito_sync = _sync_block(
                    "skipped", "Объявление на Авито не меняли (только локальный статус)"
                )

        db.commit()
        product = _product_api_dict(db, product_id)

    _invalidate_menu_cache()
    if not product:
        return None
    return {
        "product": product,
        "status_sync": {
            "vk": vk_sync,
            "avito": avito_sync,
            "database": _sync_block("ok"),
        },
    }


def save_product_price(product_id: int, price: str) -> Optional[dict]:
    """Сохранить цену в БД (без синхронизации площадок — её делает PriceSyncService).

    Возвращает {"product": {...}, "price_sync": {...}} или None при ошибке.
    """
    if not price:
        return None
    price_clean = re.sub(r"[^\d.,]", "", str(price)).replace(",", ".")
    try:
        int(float(price_clean))
    except (ValueError, TypeError):
        logger.error("save_product_price: invalid price %r", price)
        return None

    from app.services.product_price_history_service import record_price_change

    with SessionLocal() as db:
        row = (
            db.execute(
                text("SELECT id, price FROM products WHERE id = :id LIMIT 1"),
                {"id": product_id},
            )
            .mappings()
            .first()
        )
        if not row:
            return None
        old_price = row.get("price")
        changed = record_price_change(
            db, product_id, old_price, price, source="manual", update_product_price=True
        )
        if changed:
            db.commit()
        product = _product_api_dict(db, product_id)

    _invalidate_menu_cache()
    if not product:
        return None
    return {
        "product": product,
        "price_sync": {
            "vk": _sync_block(),
            "avito": _sync_block(),
            "database": _sync_block("ok"),
        },
    }


def set_product_avito_link(product_id: int, avito_link_or_id: str) -> Optional[dict]:
    """Привязать объявление Авито (id или URL) к товару и его посту."""
    from app.integrations.avito.parse import parse_avito_item_ref

    raw = (avito_link_or_id or "").strip()
    if not raw:
        return None
    parsed = parse_avito_item_ref(raw)
    if not parsed.item_id:
        return None

    item_id = str(parsed.item_id)
    url = parsed.canonical_url or raw
    with SessionLocal() as db:
        row = (
            db.execute(
                text("SELECT id, post_id FROM products WHERE id = :id LIMIT 1"),
                {"id": product_id},
            )
            .mappings()
            .first()
        )
        if not row:
            return None
        db.execute(
            text(
                "UPDATE products SET avito_item_id = :iid, avito_url = :url, "
                "updated_at = NOW() WHERE id = :id"
            ),
            {"iid": item_id, "url": url, "id": product_id},
        )
        if row.get("post_id"):
            db.execute(
                text(
                    "UPDATE posts SET avito_item_id = :iid, avito_url = :url "
                    "WHERE id = :pid"
                ),
                {"iid": item_id, "url": url, "pid": row["post_id"]},
            )
        db.commit()
        return _product_api_dict(db, product_id)


def delete_product(product_id: int) -> bool:
    """Удалить товар из VK Market (если привязан) и из БД."""
    with SessionLocal() as db:
        row = (
            db.execute(
                text("SELECT id, vk_product_id FROM products WHERE id = :id LIMIT 1"),
                {"id": product_id},
            )
            .mappings()
            .first()
        )
        if not row:
            return False
        if row.get("vk_product_id"):
            try:
                from app.utils.vk_client import get_market_vk_session, resolved_vk_group_id_int

                vk = get_market_vk_session().get_api()
                vk.market.delete(
                    owner_id=-resolved_vk_group_id_int(),
                    item_id=row["vk_product_id"],
                )
            except Exception as e:
                logger.error("Error deleting product from VK: %s", e)
        db.execute(text("DELETE FROM products WHERE id = :id"), {"id": product_id})
        db.commit()
    _invalidate_menu_cache()
    return True


def set_product_availability(product_id: int, availability_status: str) -> Optional[dict]:
    """Переключить наличие товара (available / on_order)."""
    if availability_status not in ("available", "on_order"):
        return None
    with SessionLocal() as db:
        row = db.execute(
            text("SELECT id FROM products WHERE id = :id LIMIT 1"), {"id": product_id}
        ).first()
        if not row:
            return None
        db.execute(
            text(
                "UPDATE products SET availability_status = :st, updated_at = NOW() "
                "WHERE id = :id"
            ),
            {"st": availability_status, "id": product_id},
        )
        db.commit()
        product = _product_api_dict(db, product_id)
    _invalidate_menu_cache()
    return product


def fetch_stale_price_list(
    min_days: int = 60,
    *,
    sort_mode: str = "price",
) -> tuple[list[dict], int]:
    """Активные б/у для экрана застоя и счётчик для бейджа."""
    from app.db.stale_price_queries import count_stale_badge, fetch_stale_used_products

    with SessionLocal() as db:
        products = fetch_stale_used_products(db, sort_mode=sort_mode)
        badge = count_stale_badge(db, min_days)
    return products, int(badge)


def fetch_product_price_history(product_id: int, limit: int = 20) -> list[dict]:
    """История цен для карточки товара."""
    from app.db.stale_price_queries import fetch_price_history

    with SessionLocal() as db:
        return fetch_price_history(db, product_id, limit=limit)


def fetch_stale_price_detail(product_id: int) -> tuple[Optional[dict], list[dict]]:
    """Товар и история цен для экрана price_stale_item."""
    from app.db.stale_price_queries import fetch_price_history, fetch_stale_used_products_by_id

    with SessionLocal() as db:
        product = fetch_stale_used_products_by_id(db, product_id)
        history = fetch_price_history(db, product_id) if product else []
    return product, history
