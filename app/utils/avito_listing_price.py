"""Per-platform цена при пуше на Avito (БД и остальные площадки не меняются)."""
from __future__ import annotations

from typing import Any, Mapping, Optional

from app.utils.iphone_parser import parse_iphone_model

AVITO_NEW_IPHONE_PRICE_DISCOUNT_RUB = 3000
_NEW_IPHONE_COLLECTION = "iPhone новые"


def resolve_avito_listing_price(
    product: Optional[Mapping[str, Any]],
    price_rub: int,
    *,
    discount_enabled: Optional[bool] = None,
) -> int:
    """Цена для Avito update_price: N или N−3000 для новых iPhone при включённом тоггле."""
    try:
        base = int(price_rub)
    except (TypeError, ValueError):
        return price_rub

    if discount_enabled is None:
        from app.services.settings_service import get_settings_service

        discount_enabled = get_settings_service().is_avito_new_iphone_price_minus_3000_enabled()
    if not discount_enabled:
        return base

    product = product or {}
    coll = str(product.get("collection_name") or "").strip()
    # «iPhone новые» или custom из конструктора кнопок («Список новых»).
    is_classic_new = coll == _NEW_IPHONE_COLLECTION
    is_menu_custom_new = coll == "custom" and bool(product.get("custom_button_id"))
    if not (is_classic_new or is_menu_custom_new):
        return base

    name = str(product.get("name") or "")
    if parse_iphone_model(name) is None:
        return base

    return max(base - AVITO_NEW_IPHONE_PRICE_DISCOUNT_RUB, 1)
