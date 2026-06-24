"""
Форматирование списка наличия новых товаров для сообщения в канале Telegram.
"""
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional

from app.utils.iphone_parser import group_products_by_model, sort_models_for_display
from app.utils.product_label import availability_line_for_product


def _parse_price_number(price: str) -> float:
    if not price:
        return 0.0
    cleaned = "".join(c for c in str(price) if c.isdigit() or c in ".,").replace(",", ".")
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0


def _product_sort_key(p: Dict) -> tuple:
    name = p.get("name", "")
    price = p.get("price", "")
    mem = 0
    if "256" in name or "256gb" in name.lower():
        mem = 256
    elif "512" in name or "512gb" in name.lower():
        mem = 512
    elif "1tb" in name.lower() or "1 tb" in name.lower():
        mem = 1024
    return (mem, _parse_price_number(price))


def _msk_now() -> datetime:
    utc = datetime.now(timezone.utc)
    msk = utc + timedelta(hours=3)
    return msk


def format_availability_list(
    products: List[Dict],
    exclude_product_id: Optional[int] = None,
    updated_at: Optional[datetime] = None,
) -> str:
    """
    Форматирует список наличия новых товаров для поста в канале.

    - Группировка по моделям, сортировка от старых к новым.
    - Раздел «В наличии» (🟢) и «На заказ» (🔴).
    - Формат строки: 🟢/🔴 [название] - [цена]
    - В конце: «Обновлено DD.MM.YY в HH:MM (по МСК)».
    """
    if exclude_product_id is not None:
        products = [p for p in products if p.get("id") != exclude_product_id]

    available = [p for p in products if p.get("availability_status") == "available"]
    on_order = [p for p in products if p.get("availability_status") == "on_order"]

    grouped_av = group_products_by_model(available) if available else {}
    grouped_order = group_products_by_model(on_order) if on_order else {}

    all_models = sort_models_for_display(
        list(set(list(grouped_av.keys()) + list(grouped_order.keys())))
    )

    dt = updated_at or _msk_now()
    header = (
        f"🟢 — в наличии, 🔴 — на заказ\n\n"
        f"Обновлено {dt.strftime('%d.%m.%y')} в {dt.strftime('%H:%M')} (по МСК)\n\n"
    )

    lines_av = ["В наличии:", ""]
    for model in all_models:
        prods = grouped_av.get(model, [])
        prods = sorted(prods, key=_product_sort_key)
        for p in prods:
            name = availability_line_for_product(p)
            lines_av.append(f"🟢{name}")
        if prods:
            lines_av.append("")

    lines_order = ["На заказ:", ""]
    for model in all_models:
        prods = grouped_order.get(model, [])
        prods = sorted(prods, key=_product_sort_key)
        for p in prods:
            name = availability_line_for_product(p)
            lines_order.append(f"🔴{name}")
        if prods:
            lines_order.append("")

    return header + "\n".join(lines_av).strip() + "\n\n" + "\n".join(lines_order).strip()
