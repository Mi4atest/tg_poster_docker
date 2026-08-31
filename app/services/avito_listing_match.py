"""Сопоставление б/у товара с объявлениями кабинета Авито (title + price)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import AbstractSet, Any, Iterable, Optional, Sequence

from app.utils.iphone_parser import parse_iphone_model
from app.utils.price_change import price_string_to_int_rub

_SHOP_CODE_END = re.compile(r"\s+(\d{4,6})\s*$")
_SHOP_CODE_PAREN = re.compile(r"\s*\((\d{4,6})\)\s*$")
_MEM_GB = re.compile(r"\b(16|32|64|128|256|512|1024)\s*(?:gb|гб)\b", re.IGNORECASE)
_MEM_TB = re.compile(r"\b1\s*(?:tb|тб)\b", re.IGNORECASE)
_NON_WORD = re.compile(r"[^\wа-яё]+", re.IGNORECASE)
_STOP_NAME_WORDS = frozenset(
    {
        "gb",
        "гб",
        "tb",
        "тб",
        "iphone",
        "apple",
        "sim",
        "esim",
        "новый",
        "новые",
        "бу",
    }
)


@dataclass(frozen=True)
class AvitoListing:
    item_id: int
    title: str
    price_rub: Optional[int]
    url: str = ""
    category_id: Optional[int] = None
    category_name: Optional[str] = None


def parse_avito_listing_row(row: dict[str, Any]) -> Optional[AvitoListing]:
    if not isinstance(row, dict):
        return None
    raw_id = row.get("id")
    try:
        item_id = int(raw_id)
    except (TypeError, ValueError):
        return None
    if item_id <= 0:
        return None
    price = row.get("price")
    price_rub: Optional[int]
    try:
        price_rub = int(price) if price is not None else None
    except (TypeError, ValueError):
        price_rub = None
    cat = row.get("category") if isinstance(row.get("category"), dict) else {}
    title = str(row.get("title") or "").strip()
    url = str(row.get("url") or "").strip() or f"https://www.avito.ru/{item_id}"
    cat_id = cat.get("id")
    try:
        cat_id_int = int(cat_id) if cat_id is not None else None
    except (TypeError, ValueError):
        cat_id_int = None
    return AvitoListing(
        item_id=item_id,
        title=title,
        price_rub=price_rub,
        url=url,
        category_id=cat_id_int,
        category_name=str(cat.get("name") or "") or None,
    )


def listings_from_api_rows(rows: Iterable[dict[str, Any]]) -> list[AvitoListing]:
    out: list[AvitoListing] = []
    for row in rows:
        parsed = parse_avito_listing_row(row)
        if parsed:
            out.append(parsed)
    return out


def extract_shop_code(name: str) -> Optional[str]:
    """4–6 цифр в конце названия или в скобках (внутренний артикул магазина)."""
    text = (name or "").strip()
    if not text:
        return None
    match = _SHOP_CODE_END.search(text)
    if match:
        return match.group(1)
    match = _SHOP_CODE_PAREN.search(text)
    return match.group(1) if match else None


def listing_memory_gb(text: str) -> Optional[str]:
    """Объём из title Авито («128 ГБ») или имени товара («128Gb»), не модель «13»."""
    raw = text or ""
    if _MEM_TB.search(raw):
        return "1024"
    match = _MEM_GB.search(raw)
    return match.group(1) if match else None


def _memory_from_product_name(name: str) -> Optional[str]:
    mem = listing_memory_gb(name)
    if mem:
        return mem
    from app.utils.iphone_parser import parse_iphone_memory

    parsed = parse_iphone_memory(name)
    if not parsed:
        return None
    if str(parsed).lower() in ("1tb", "1тб"):
        return "1024"
    digits = re.sub(r"\D", "", str(parsed))
    return digits or None


def _code_in_title(code: str, title: str) -> bool:
    if not code:
        return False
    return re.search(rf"(?<!\d){re.escape(code)}(?!\d)", title or "") is not None


def _normalize_name(text: str) -> str:
    cleaned = extract_shop_code(text)
    base = text or ""
    if cleaned:
        base = _SHOP_CODE_END.sub("", base)
        base = _SHOP_CODE_PAREN.sub("", base)
    folded = _NON_WORD.sub(" ", base.lower())
    return re.sub(r"\s+", " ", folded).strip()


def _name_tokens(text: str) -> set[str]:
    return {
        tok
        for tok in _normalize_name(text).split()
        if len(tok) >= 3 and tok not in _STOP_NAME_WORDS and not tok.isdigit()
    }


def _product_price_rub(product: dict[str, Any]) -> Optional[int]:
    return price_string_to_int_rub(product.get("price"))


def match_product_to_listings(
    product: dict[str, Any],
    listings: Sequence[AvitoListing],
    *,
    occupied_item_ids: Optional[AbstractSet[int]] = None,
) -> list[AvitoListing]:
    """Кандидаты среди свободных объявлений. Без автопривязки — только список."""
    occupied = occupied_item_ids or set()
    pool = [item for item in listings if item.item_id not in occupied]
    name = str(product.get("name") or "")
    price = _product_price_rub(product)

    code = extract_shop_code(name)
    if code:
        by_code = [item for item in pool if _code_in_title(code, item.title)]
        if by_code:
            return by_code

    model = parse_iphone_model(name)
    mem = _memory_from_product_name(name)
    if model and mem and price:
        hits = [
            item
            for item in pool
            if parse_iphone_model(item.title) == model
            and listing_memory_gb(item.title) == mem
            and item.price_rub == price
        ]
        if hits:
            return hits

    if price is None:
        return []
    product_tokens = _name_tokens(name)
    product_norm = _normalize_name(name)
    fallback: list[AvitoListing] = []
    for item in pool:
        if item.price_rub != price:
            continue
        title_norm = _normalize_name(item.title)
        if product_norm and (product_norm in title_norm or title_norm in product_norm):
            fallback.append(item)
            continue
        title_tokens = _name_tokens(item.title)
        if product_tokens and title_tokens and product_tokens <= title_tokens:
            fallback.append(item)
    return fallback
