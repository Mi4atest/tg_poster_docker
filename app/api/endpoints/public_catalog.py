"""Read-only публичный каталог для внешней витрины (сервер B).

Не светит мутации и внутренние поля. Медиа — те же URL, что для Avito:
https://appleshop.ap43.ru/api/telegram/file/{file_id}

Ссылки: coalesce(product, post) — у б/у Max/IG/VK-пост часто только в posts.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.schemas.public_catalog import (
    PublicProduct,
    PublicProductLinks,
    PublicProductList,
)
from app.config.settings import AVITO_FEED_PUBLIC_BASE_URL
from app.db.database import get_db

router = APIRouter()

# Как в price_sync_service / фильтрах бота
_NEW_CORE = ("iPhone новые", "Airpods", "Apple Watch", "iPad")
_NEW_AND_CUSTOM = (*_NEW_CORE, "custom")

_KIND = Literal["used", "new"]

_MAX_CHANNEL_LINK_RE = re.compile(
    r"^max://(?:max\.ru/)?(?P<chat>-?\d+)/(?P<mid>\d+)",
    re.IGNORECASE,
)


def _public_base() -> str:
    return (AVITO_FEED_PUBLIC_BASE_URL or "https://appleshop.ap43.ru").rstrip("/")


def _nz(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _file_urls(file_ids: Any) -> list[str]:
    """Telegram file_id → публичные URL через уже открытый /api/telegram/file/."""
    if not file_ids:
        return []
    if isinstance(file_ids, str):
        try:
            file_ids = json.loads(file_ids)
        except json.JSONDecodeError:
            file_ids = [file_ids]
    if not isinstance(file_ids, list):
        return []
    base = _public_base()
    out: list[str] = []
    for fid in file_ids:
        s = str(fid or "").strip()
        if s:
            out.append(f"{base}/api/telegram/file/{s}")
    return out


def _best_max_href(max_link: Optional[str], max_share_url: Optional[str]) -> Optional[str]:
    """Кликабельный Max: приоритет max_share_url, иначе https из max_link / max://."""
    share = _nz(max_share_url)
    if share and share.lower().startswith(("http://", "https://")):
        return share
    s = _nz(max_link)
    if not s:
        return share
    low = s.lower()
    if low.startswith(("http://", "https://")):
        return s
    m = _MAX_CHANNEL_LINK_RE.match(s)
    if m:
        return f"https://max.ru/c/{m.group('chat')}/{m.group('mid')}"
    return s


def _avito_public_url(avito_url: Optional[str], avito_item_id: Optional[str]) -> Optional[str]:
    url = _nz(avito_url)
    if url:
        return url
    item_id = _nz(avito_item_id)
    if item_id and item_id.isdigit():
        return f"https://www.avito.ru/{item_id}"
    return None


def _build_links(
    *,
    telegram: Optional[str],
    vk_market: Optional[str],
    vk_post: Optional[str],
    max_link: Optional[str],
    max_share_url: Optional[str],
    instagram: Optional[str],
    avito: Optional[str],
) -> PublicProductLinks:
    return PublicProductLinks(
        telegram=_nz(telegram),
        vk_market=_nz(vk_market),
        vk_post=_nz(vk_post),
        max=_best_max_href(max_link, max_share_url),
        max_link=_nz(max_link),
        max_share_url=_nz(max_share_url),
        instagram=_nz(instagram),
        avito=_nz(avito),
    )


def _kind_sql(kind: str) -> str:
    if kind == "used":
        placeholders = ", ".join(f":nc{i}" for i in range(len(_NEW_AND_CUSTOM)))
        return f"(COALESCE(TRIM(p.collection_name), '') NOT IN ({placeholders}))"
    core = ", ".join(f":core{i}" for i in range(len(_NEW_CORE)))
    return (
        f"(TRIM(COALESCE(p.collection_name, '')) IN ({core}) "
        f"OR (TRIM(COALESCE(p.collection_name, '')) = 'custom' "
        f"AND p.custom_button_id IS NOT NULL))"
    )


def _kind_params(kind: str) -> dict[str, str]:
    if kind == "used":
        return {f"nc{i}": v for i, v in enumerate(_NEW_AND_CUSTOM)}
    return {f"core{i}": v for i, v in enumerate(_NEW_CORE)}


def _row_to_public(row: dict, kind: str) -> PublicProduct:
    telegram = _nz(row.get("telegram_link"))
    vk_market = _nz(row.get("vk_product_link"))
    vk_post = _nz(row.get("vk_post_link"))
    max_link = _nz(row.get("max_link"))
    max_share = _nz(row.get("max_share_url"))
    instagram = _nz(row.get("instagram_link"))
    avito_item_id = _nz(row.get("avito_item_id"))
    avito = _avito_public_url(row.get("avito_url"), avito_item_id)
    links = _build_links(
        telegram=telegram,
        vk_market=vk_market,
        vk_post=vk_post,
        max_link=max_link,
        max_share_url=max_share,
        instagram=instagram,
        avito=avito,
    )
    return PublicProduct(
        id=row["id"],
        name=row["name"] or "",
        display_label=_nz(row.get("display_label")),
        price=_nz(row.get("price")),
        collection_name=_nz(row.get("collection_name")),
        kind=kind,  # type: ignore[arg-type]
        status=_nz(row.get("status")) or "active",
        availability_status=_nz(row.get("availability_status")),
        image_urls=_file_urls(row.get("photos")),
        video_urls=_file_urls(row.get("videos")),
        storage_path=_nz(row.get("storage_path")),
        telegram_link=telegram,
        vk_product_id=row.get("vk_product_id"),
        vk_product_link=vk_market,
        vk_post_link=vk_post,
        max_link=max_link,
        max_share_url=max_share,
        instagram_link=instagram,
        avito_item_id=avito_item_id,
        avito_url=avito,
        links=links,
        created_at=row.get("created_at"),
    )


# COALESCE: значение с products, иначе с posts (типично для б/у: Max/IG/VK-пост на посте)
_SELECT = """
SELECT
    p.id, p.name, p.display_label, p.price, p.collection_name,
    p.status, p.availability_status, p.created_at, p.custom_button_id,
    p.vk_product_id,
    NULLIF(TRIM(COALESCE(p.vk_product_link, '')), '') AS vk_product_link,
    NULLIF(TRIM(COALESCE(NULLIF(TRIM(COALESCE(p.telegram_link, '')), ''),
                         NULLIF(TRIM(COALESCE(posts.telegram_link, '')), ''))), '')
        AS telegram_link,
    NULLIF(TRIM(COALESCE(NULLIF(TRIM(COALESCE(p.max_link, '')), ''),
                         NULLIF(TRIM(COALESCE(posts.max_link, '')), ''))), '')
        AS max_link,
    NULLIF(TRIM(COALESCE(NULLIF(TRIM(COALESCE(p.max_share_url, '')), ''),
                         NULLIF(TRIM(COALESCE(posts.max_share_url, '')), ''))), '')
        AS max_share_url,
    NULLIF(TRIM(COALESCE(NULLIF(TRIM(COALESCE(p.instagram_link, '')), ''),
                         NULLIF(TRIM(COALESCE(posts.instagram_link, '')), ''))), '')
        AS instagram_link,
    NULLIF(TRIM(COALESCE(NULLIF(TRIM(COALESCE(p.avito_url, '')), ''),
                         NULLIF(TRIM(COALESCE(posts.avito_url, '')), ''))), '')
        AS avito_url,
    NULLIF(TRIM(COALESCE(NULLIF(TRIM(COALESCE(p.avito_item_id, '')), ''),
                         NULLIF(TRIM(COALESCE(posts.avito_item_id, '')), ''))), '')
        AS avito_item_id,
    NULLIF(TRIM(COALESCE(posts.vk_post_link, '')), '') AS vk_post_link,
    posts.photos, posts.videos, posts.storage_path
FROM products p
LEFT JOIN posts ON posts.id = p.post_id
"""


@router.get("/health")
def public_health():
    return {"ok": True, "service": "public_catalog"}


@router.get("/products", response_model=PublicProductList)
def list_public_products(
    kind: _KIND = Query(..., description="used | new"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    status_filter: Optional[str] = Query(
        "active",
        description="Фильтр status; по умолчанию только active",
    ),
    db: Session = Depends(get_db),
):
    """Список товаров витрины: б/у или новые, только чтение."""
    where = [_kind_sql(kind)]
    params: dict[str, Any] = {**_kind_params(kind), "skip": skip, "limit": limit}
    if status_filter:
        where.append("p.status = :status_filter")
        params["status_filter"] = status_filter
    where_sql = " AND ".join(where)

    total = (
        db.execute(
            text(f"SELECT COUNT(*) FROM products p WHERE {where_sql}"),
            params,
        ).scalar()
        or 0
    )
    rows = (
        db.execute(
            text(
                f"{_SELECT} WHERE {where_sql} "
                "ORDER BY p.created_at DESC NULLS LAST "
                "OFFSET :skip LIMIT :limit"
            ),
            params,
        )
        .mappings()
        .all()
    )
    items = [_row_to_public(dict(r), kind) for r in rows]
    return PublicProductList(items=items, total=int(total), kind=kind)


@router.get("/products/{product_id}", response_model=PublicProduct)
def get_public_product(
    product_id: int,
    db: Session = Depends(get_db),
):
    """Одна карточка; kind вычисляется по collection_name."""
    row = (
        db.execute(
            text(f"{_SELECT} WHERE p.id = :id LIMIT 1"),
            {"id": product_id},
        )
        .mappings()
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Product not found")
    data = dict(row)
    coll = (data.get("collection_name") or "").strip()
    if coll in _NEW_CORE or (coll == "custom" and data.get("custom_button_id")):
        kind: _KIND = "new"
    else:
        kind = "used"
    return _row_to_public(data, kind)
