"""Сборка данных для PDF-ценников."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Sequence

from sqlalchemy import bindparam, text

from app.db.database import SessionLocal
from app.services.settings_service import get_settings_service
from app.utils.price_change import price_string_to_int_rub
from app.utils.price_tag_pricing import calc_strike_price, format_price_tag_amount

NEW_COLLECTION_VALUES = ("iPhone новые", "Airpods", "Apple Watch", "iPad", "custom")


@dataclass(frozen=True)
class PriceTagItem:
    product_id: int
    name: str
    subtitle: str
    description: str
    cash_price_rub: int
    cash_price_display: str
    strike_price_rub: int
    strike_price_display: str
    print_date: str


def _resolve_subtitle(product: dict, settings: dict) -> str:
    raw = (product.get("price_tag_subtitle") or "").strip()
    if raw:
        return raw
    default = (settings.get("default_subtitle") or "").strip()
    return default


def _resolve_description(product: dict, settings: dict) -> str:
    raw = (product.get("price_tag_description") or "").strip()
    if raw:
        return raw
    coll = (product.get("collection_name") or "").strip()
    defaults = settings.get("default_descriptions") or {}
    by_cat = (defaults.get(coll) or "").strip()
    if by_cat:
        return by_cat
    return (settings.get("fixed_footer_text") or "").strip()


def build_price_tag_item(
    product: dict,
    *,
    markup_percent: int,
    on_date: Optional[date] = None,
    settings: Optional[dict] = None,
) -> Optional[PriceTagItem]:
    cash = price_string_to_int_rub(product.get("price"))
    if cash is None or cash <= 0:
        return None
    if settings is None:
        settings = get_settings_service().get_price_tags_settings()
    strike = calc_strike_price(cash, markup_percent)
    d = on_date or date.today()
    return PriceTagItem(
        product_id=int(product["id"]),
        name=(product.get("name") or "Без названия").strip(),
        subtitle=_resolve_subtitle(product, settings),
        description=_resolve_description(product, settings),
        cash_price_rub=cash,
        cash_price_display=format_price_tag_amount(cash),
        strike_price_rub=strike,
        strike_price_display=format_price_tag_amount(strike),
        print_date=d.strftime("%d.%m.%Y"),
    )


def fetch_available_products_for_tags() -> List[dict]:
    """Все новые товары «В наличии» для экрана выбора."""
    sql = text(
        """
        SELECT id, name, display_label, price, collection_name, custom_button_id,
               availability_status, price_tag_subtitle, price_tag_description
        FROM products
        WHERE availability_status = 'available'
          AND status = 'active'
          AND (
            collection_name = ANY(:cols)
            OR (collection_name = 'custom' AND custom_button_id IS NOT NULL)
          )
        ORDER BY collection_name, price, id
        """
    )
    with SessionLocal() as db:
        rows = db.execute(sql, {"cols": list(NEW_COLLECTION_VALUES)}).mappings().all()
    return [dict(r) for r in rows]


def fetch_products_for_tag_pdf(product_ids: Sequence[int]) -> List[dict]:
    if not product_ids:
        return []
    ids = [int(x) for x in product_ids]
    # Тот же набор колонок, что у fetch_available_products_for_tags: узкий SELECT
    # на части хостов зависает на чтении ответа (MTU Docker bridge vs eth0 контейнера).
    sql = text(
        """
        SELECT id, name, display_label, price, collection_name, custom_button_id,
               availability_status, price_tag_subtitle, price_tag_description
        FROM products
        WHERE id IN :ids
          AND availability_status = 'available'
          AND status = 'active'
        ORDER BY collection_name, price, id
        """
    ).bindparams(bindparam("ids", expanding=True))
    with SessionLocal() as db:
        rows = db.execute(sql, {"ids": ids}).mappings().all()
    return [dict(r) for r in rows]


def build_price_tag_items(product_ids: Sequence[int]) -> List[PriceTagItem]:
    markup = get_settings_service().get_price_tag_strike_markup_percent()
    products = fetch_products_for_tag_pdf(product_ids)
    items: List[PriceTagItem] = []
    for p in products:
        item = build_price_tag_item(p, markup_percent=markup)
        if item:
            items.append(item)
    return items


def filter_in_stock_product_ids(product_ids: Sequence[int]) -> List[int]:
    """Оставить только id товаров «В наличии»."""
    if not product_ids:
        return []
    ids = [int(x) for x in product_ids]
    sql = text(
        """
        SELECT id FROM products
        WHERE id IN :ids
          AND availability_status = 'available'
          AND status = 'active'
        ORDER BY id
        """
    ).bindparams(bindparam("ids", expanding=True))
    with SessionLocal() as db:
        rows = db.execute(sql, {"ids": ids}).scalars().all()
    return [int(r) for r in rows]
