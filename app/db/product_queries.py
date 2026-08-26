"""Быстрые raw-SQL операции с товарами (без ORM в asyncio)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

USED_EXCLUDED_COLLECTIONS = ("iPhone новые", "Airpods", "Apple Watch", "iPad", "custom")

# Широкие строки (>~26 колонок за раз) рвут app→PG в Docker — грузим частями.
_PRODUCT_DETAIL_COLUMNS_1 = """
    id, post_id, vk_product_id, vk_product_link, telegram_link,
    name, price, payment_method, final_price, archive_kind, category_id, category_name,
    collection_id, collection_name, status
""".strip()

_PRODUCT_DETAIL_COLUMNS_2 = """
    created_at, updated_at, archived_at, price_changed_at, availability_status,
    channel_message_id, availability_message_ids,
    max_link, custom_button_id, max_share_url,
    avito_item_id, avito_url, instagram_link, instagram_media_id
""".strip()

_PRODUCT_SYNC_COLUMNS = """
    id, name, price, telegram_link, max_link, max_share_url,
    instagram_link, instagram_media_id, post_id, vk_product_id,
    avito_item_id, collection_name, custom_button_id
""".strip()

_PRODUCT_LIST_COLUMNS = """
    id, name, price, status, collection_name, category_name,
    vk_product_link, telegram_link, created_at, archived_at, price_changed_at,
    availability_status, payment_method, final_price, archive_kind, post_id, vk_product_id
""".strip()


def insert_product_row(
    db: Session,
    *,
    post_id: str,
    name: str,
    price: Optional[str] = None,
    vk_product_id: Optional[int] = None,
    vk_product_link: Optional[str] = None,
    telegram_link: Optional[str] = None,
    category_id: Optional[int] = None,
    category_name: Optional[str] = None,
    collection_id: Optional[int] = None,
    collection_name: Optional[str] = None,
    status: str = "active",
) -> int:
    """Узкий INSERT товара без ORM (широкий ORM INSERT рвёт соединение app↔PG)."""
    product_id = db.execute(
        text(
            "INSERT INTO products ("
            "post_id, vk_product_id, vk_product_link, telegram_link, "
            "name, price, category_id, category_name, collection_id, collection_name, "
            "status, created_at, updated_at"
            ") VALUES ("
            ":post_id, :vk_product_id, :vk_product_link, :telegram_link, "
            ":name, :price, :category_id, :category_name, :collection_id, :collection_name, "
            ":status, NOW(), NOW()"
            ") RETURNING id"
        ),
        {
            "post_id": post_id,
            "vk_product_id": vk_product_id,
            "vk_product_link": vk_product_link,
            "telegram_link": telegram_link,
            "name": name,
            "price": price,
            "category_id": category_id,
            "category_name": category_name,
            "collection_id": collection_id,
            "collection_name": collection_name,
            "status": status or "active",
        },
    ).scalar_one()
    return int(product_id)


def ensure_missing_products_for_tg_posts(db: Session, *, days: int = 3) -> int:
    """Создаёт строки products только для недавних TG-постов без товара.

    Без окна по дате бэкап поднимал архивные/рекламные посты в каталог б/у.
    Берём посты за последние `days` суток и только с распознанной ценой.
    """
    from app.utils.product_parser import parse_product_data

    rows = db.execute(
        text(
            "SELECT po.id, po.text, po.telegram_link "
            "FROM posts po "
            "WHERE po.is_published_telegram IS TRUE "
            "AND po.telegram_link IS NOT NULL AND po.telegram_link != '' "
            "AND po.published_telegram_at >= NOW() - make_interval(days => :days) "
            "AND NOT EXISTS (SELECT 1 FROM products p WHERE p.post_id = po.id) "
            "ORDER BY po.published_telegram_at DESC NULLS LAST "
            "LIMIT 50"
        ),
        {"days": int(days)},
    ).mappings().all()

    created = 0
    for row in rows:
        post_id = row["id"]
        parsed = parse_product_data(row["text"] or "")
        price = parsed.get("price")
        if not price:
            continue
        name = (parsed.get("name") or "").strip()
        if not name:
            name = ((row["text"] or "").strip().split("\n")[0] or "Товар")[:120]
        collection = parsed.get("collection")
        if (collection or "").strip() in USED_EXCLUDED_COLLECTIONS:
            continue
        insert_product_row(
            db,
            post_id=post_id,
            name=name,
            price=price,
            telegram_link=row["telegram_link"],
            category_name=parsed.get("category"),
            collection_name=collection,
            status="active",
        )
        created += 1
    if created:
        db.commit()
    return created


def sync_telegram_links_to_products(db: Session) -> tuple[int, int, int, int]:
    """
    Копирует telegram_link из posts в products.
    Returns: (posts_with_link, posts_with_products, updated_products, created_missing).
    """
    created_missing = ensure_missing_products_for_tg_posts(db)
    posts_with_link = db.execute(
        text(
            "SELECT COUNT(*) FROM posts "
            "WHERE telegram_link IS NOT NULL AND telegram_link != ''"
        )
    ).scalar() or 0
    posts_with_products = db.execute(
        text(
            "SELECT COUNT(DISTINCT po.id) FROM posts po "
            "INNER JOIN products p ON p.post_id = po.id "
            "WHERE po.telegram_link IS NOT NULL AND po.telegram_link != ''"
        )
    ).scalar() or 0
    updated = db.execute(
        text(
            "UPDATE products p SET telegram_link = po.telegram_link, updated_at = NOW() "
            "FROM posts po "
            "WHERE p.post_id = po.id "
            "AND po.telegram_link IS NOT NULL AND po.telegram_link != '' "
            "AND (p.telegram_link IS DISTINCT FROM po.telegram_link)"
        )
    ).rowcount
    db.commit()
    return int(posts_with_link), int(posts_with_products), int(updated or 0), int(created_missing)


def fetch_used_products_for_list(db: Session) -> list[dict[str, Any]]:
    """Активные б/у товары со ссылками и датами публикации TG/Max из поста."""
    rows = db.execute(
        text(
            "SELECT p.id, p.name, p.price, p.telegram_link, p.vk_product_link, "
            "p.max_link, p.max_share_url, "
            "po.published_telegram_at, po.published_max_at, "
            "po.max_share_url AS post_max_share_url "
            "FROM products p "
            "LEFT JOIN posts po ON po.id = p.post_id "
            "WHERE p.status = 'active' "
            "AND (p.collection_name IS NULL OR p.collection_name NOT IN "
            "(:c1, :c2, :c3, :c4, :c5)) "
            "ORDER BY p.id"
        ),
        {
            "c1": USED_EXCLUDED_COLLECTIONS[0],
            "c2": USED_EXCLUDED_COLLECTIONS[1],
            "c3": USED_EXCLUDED_COLLECTIONS[2],
            "c4": USED_EXCLUDED_COLLECTIONS[3],
            "c5": USED_EXCLUDED_COLLECTIONS[4],
        },
    ).mappings().all()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "price": r["price"] or "Цена не указана",
            "telegram_link": r["telegram_link"],
            "vk_product_link": r["vk_product_link"],
            "max_link": r["max_link"],
            "max_share_url": r["max_share_url"] or r["post_max_share_url"],
            "published_telegram_at": r["published_telegram_at"],
            "published_max_at": r["published_max_at"],
        }
        for r in rows
    ]


_POST_DETAIL_SELECT = """
SELECT max_share_url, vk_post_id, vk_post_link,
       published_telegram_at, published_vk_at, published_max_at, published_instagram_at
FROM posts
WHERE id = :id
LIMIT 1
"""


def _resolve_published_at(product_data: dict[str, Any], post_row: Optional[dict[str, Any]]) -> Optional[datetime]:
    """Самая ранняя дата публикации на площадках; иначе created_at товара."""
    candidates: list[datetime] = []
    if post_row:
        for key in (
            "published_telegram_at",
            "published_vk_at",
            "published_max_at",
            "published_instagram_at",
        ):
            val = post_row.get(key)
            if isinstance(val, datetime):
                candidates.append(val)
    created = product_data.get("created_at")
    if isinstance(created, datetime):
        candidates.append(created)
    return min(candidates) if candidates else None


def _enrich_product_detail_row(conn, row) -> Optional[dict[str, Any]]:
    """Дополняет строку товара полями поста (два коротких запроса вместо тяжёлого JOIN)."""
    if not row:
        return None
    data = dict(row)
    post_id = data.get("post_id")
    if post_id:
        post_row = (
            conn.execute(text(_POST_DETAIL_SELECT), {"id": post_id})
            .mappings()
            .first()
        )
        if post_row:
            if not data.get("max_share_url") and post_row.get("max_share_url"):
                data["max_share_url"] = post_row["max_share_url"]
            data["vk_post_id"] = post_row.get("vk_post_id")
            data["vk_post_link"] = post_row.get("vk_post_link")
            data["published_at"] = _resolve_published_at(data, post_row)
    data.setdefault("vk_post_id", None)
    data.setdefault("vk_post_link", None)
    if "published_at" not in data:
        data["published_at"] = _resolve_published_at(data, None)
    return data


def _fetch_product_row_core(conn, product_id: int) -> Optional[dict[str, Any]]:
    row1 = (
        conn.execute(
            text(f"SELECT {_PRODUCT_DETAIL_COLUMNS_1} FROM products WHERE id = :id LIMIT 1"),
            {"id": product_id},
        )
        .mappings()
        .first()
    )
    if not row1:
        return None
    row2 = (
        conn.execute(
            text(f"SELECT {_PRODUCT_DETAIL_COLUMNS_2} FROM products WHERE id = :id LIMIT 1"),
            {"id": product_id},
        )
        .mappings()
        .first()
    )
    data = {**dict(row1), **dict(row2 or {})}
    dl = conn.execute(
        text("SELECT display_label FROM products WHERE id = :id LIMIT 1"),
        {"id": product_id},
    ).scalar()
    data["display_label"] = dl
    pt_sub = conn.execute(
        text("SELECT price_tag_subtitle FROM products WHERE id = :id LIMIT 1"),
        {"id": product_id},
    ).scalar()
    pt_desc = conn.execute(
        text("SELECT price_tag_description FROM products WHERE id = :id LIMIT 1"),
        {"id": product_id},
    ).scalar()
    data["price_tag_subtitle"] = pt_sub
    data["price_tag_description"] = pt_desc
    return data


def update_product_price_tag_fields(
    db: Session,
    product_id: int,
    *,
    price_tag_subtitle: Optional[str] = None,
    price_tag_description: Optional[str] = None,
    clear_subtitle: bool = False,
    clear_description: bool = False,
) -> bool:
    """Обновить поля описания ценника."""
    sets = ["updated_at = NOW()"]
    params: dict[str, Any] = {"id": product_id}
    if clear_subtitle:
        sets.append("price_tag_subtitle = NULL")
    elif price_tag_subtitle is not None:
        sets.append("price_tag_subtitle = :subtitle")
        params["subtitle"] = (price_tag_subtitle or "").strip()[:64] or None
    if clear_description:
        sets.append("price_tag_description = NULL")
    elif price_tag_description is not None:
        sets.append("price_tag_description = :description")
        params["description"] = (price_tag_description or "").strip()[:512] or None
    if len(sets) == 1:
        return False
    n = db.execute(
        text(f"UPDATE products SET {', '.join(sets)} WHERE id = :id"),
        params,
    ).rowcount
    db.commit()
    return n > 0


def update_custom_product_fields(
    db: Session,
    product_id: int,
    *,
    name: Optional[str] = None,
    display_label: Optional[str] = None,
    clear_display_label: bool = False,
    price_tag_subtitle: Optional[str] = None,
    price_tag_description: Optional[str] = None,
    clear_subtitle: bool = False,
    clear_description: bool = False,
) -> bool:
    """Обновить название / подпись / поля ценника у товара меню «новые»."""
    # iPhone/Airpods/Watch/iPad + custom — без импорта menu_constructor_service (цикл).
    allowed = ("iPhone новые", "Airpods", "Apple Watch", "iPad", "custom")
    sets = ["updated_at = NOW()"]
    params: dict[str, Any] = {"id": product_id, "cols": list(allowed)}
    if name is not None:
        nm = (name or "").strip()[:512]
        if not nm:
            return False
        sets.append("name = :name")
        params["name"] = nm
    if clear_display_label:
        sets.append("display_label = NULL")
    elif display_label is not None:
        sets.append("display_label = :display_label")
        params["display_label"] = (display_label or "").strip()[:128] or None
    if clear_subtitle:
        sets.append("price_tag_subtitle = NULL")
    elif price_tag_subtitle is not None:
        sets.append("price_tag_subtitle = :subtitle")
        params["subtitle"] = (price_tag_subtitle or "").strip()[:64] or None
    if clear_description:
        sets.append("price_tag_description = NULL")
    elif price_tag_description is not None:
        sets.append("price_tag_description = :description")
        params["description"] = (price_tag_description or "").strip()[:512] or None
    if len(sets) == 1:
        return False
    n = db.execute(
        text(
            f"UPDATE products SET {', '.join(sets)} "
            "WHERE id = :id AND collection_name = ANY(:cols)"
        ),
        params,
    ).rowcount
    db.commit()
    return n > 0


def fetch_product_detail_row(db: Session, product_id: int) -> Optional[dict[str, Any]]:
    """Товар + поля поста для карточки (без ORM, без SELECT *)."""
    row = _fetch_product_row_core(db.connection(), product_id)
    return _enrich_product_detail_row(db.connection(), row)


def fetch_product_detail_row_by_id(product_id: int) -> Optional[dict[str, Any]]:
    """Товар для карточки через engine.connect (лёгкие запросы, без SELECT *)."""
    from app.db.database import engine

    with engine.connect() as conn:
        row = _fetch_product_row_core(conn, product_id)
        return _enrich_product_detail_row(conn, row)


def product_detail_row_to_api_dict(row: dict[str, Any]) -> dict[str, Any]:
    """Словарь для бота/API (JSON-serializable)."""
    from app.api.schemas.product import Product as ProductSchema

    published_at = row.get("published_at")
    out = ProductSchema.model_validate(row).model_dump(mode="json")
    for key, val in out.items():
        if isinstance(val, datetime):
            out[key] = val.isoformat()
    if published_at is not None:
        out["published_at"] = (
            published_at.isoformat() if isinstance(published_at, datetime) else published_at
        )
    return out
