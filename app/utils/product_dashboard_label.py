"""Короткая подпись товара для дашборда синхронизации (как на inline-кнопках)."""
from __future__ import annotations

from typing import Optional

from app.utils.iphone_parser import (
    get_iphone_version_from_model,
    get_short_model_key_for_new,
    parse_iphone_memory,
    parse_iphone_model,
    parse_iphone_storage_type,
)
from app.utils.product_formatter import format_product_name_for_list

NEW_COLLECTION_VALUES = {"iPhone новые", "Airpods", "Apple Watch", "iPad", "custom"}


def _custom_button_label(product: dict) -> Optional[str]:
    bid = product.get("custom_button_id")
    if bid is None:
        return None
    try:
        from app.db.database import SessionLocal
        from app.services import menu_constructor_service as mcs

        with SessionLocal() as db:
            return mcs.get_custom_button_label(db, int(bid))
    except Exception:
        return None


def _dashboard_label_new_iphone(product: dict) -> str:
    custom = _custom_button_label(product)
    if custom:
        return custom

    from app.bot.handlers.new_products_management import (
        _custom_button_labels_map,
        _short_label_iphone_12_16,
        _short_label_iphone_product,
    )

    name = product.get("name") or ""
    model = parse_iphone_model(name)
    version = get_iphone_version_from_model(model) if model else None
    if not version:
        return format_product_name_for_list(name)

    model_key = get_short_model_key_for_new(model or "")
    memory = parse_iphone_memory(name) or ""
    memory_key = "1tb" if memory == "1Tb" else memory
    storage = parse_iphone_storage_type(name)
    storage_key = (storage or "esim").replace("+", "p") if storage else "esim"
    cmap = _custom_button_labels_map([product])

    if version in ("12", "13", "14", "15", "16"):
        return _short_label_iphone_12_16(
            product, version, model_key, memory_key, custom_labels=cmap
        )

    return _short_label_iphone_product(
        product, version, model_key, memory_key, storage_key, custom_labels=cmap
    )


def get_product_dashboard_label(product: dict) -> str:
    """Короткая подпись для дашборда: как на кнопках / в списке."""
    if not product:
        return "Товар"

    coll = (product.get("collection_name") or "").strip()
    name = product.get("name") or "Товар"

    if coll == "iPhone новые":
        return _dashboard_label_new_iphone(product)

    if coll == "custom":
        custom = _custom_button_label(product)
        if custom:
            return custom

    return format_product_name_for_list(name)
