"""
Единый слой коротких подписей товара для кнопок и списков наличия.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.utils.color_emoji import replace_color_with_emoji
from app.utils.iphone_parser import (
    get_iphone_version_from_model,
    get_model_display_name,
    get_short_model_key_for_new,
    parse_iphone_color_key,
    parse_iphone_memory,
    parse_iphone_model,
    parse_iphone_storage_type,
)
from app.utils.price_change import price_string_to_int_rub

COLOR_PALETTE = (
    "🟣", "🟢", "🔵", "⚪️", "⚫️", "🟠", "🟡", "🌸", "🔴", "⭐", "🔘",
)

APPLE_COLLECTIONS = frozenset({"iPhone новые", "Airpods", "Apple Watch", "iPad"})


@dataclass
class ProductLabel:
    model: str
    memory: Optional[str] = None
    size: Optional[str] = None
    color_emoji: Optional[str] = None
    storage: Optional[str] = None
    price: Optional[str] = None


def normalize_price_display(price_str: Optional[str]) -> str:
    if not price_str:
        return "—"
    s = str(price_str).strip()
    num = price_string_to_int_rub(s)
    if num is None:
        return s
    return f"{num}₽"


def _price_from_product(p: dict) -> Optional[str]:
    raw = (p.get("price") or "").strip()
    return normalize_price_display(raw) if raw else None


def resolve_color_emoji(name: str, overrides: Optional[Dict[str, str]] = None) -> str:
    """Единый резолвер цвета → эмодзи для всех категорий."""
    low = (name or "").lower()
    ov = overrides or {}
    for key, emoji in ov.items():
        if key in low:
            return emoji
    if "starlight" in low:
        return "⭐"
    if "rose gold" in low or "pink" in low:
        return "🌸"
    if "silver" in low:
        return "⚪️"
    if "space gray" in low or "space grey" in low:
        return "🔘"
    if "midnight" in low:
        return "⚫️"
    if "blue" in low:
        return "🔵"
    if "yellow" in low or ("gold" in low and "rose" not in low):
        return "🟡"
    if "white" in low:
        return "⚪️"
    if "purple" in low or "lavender" in low:
        return "🟣"
    replaced = replace_color_with_emoji(name or "")
    for emoji in COLOR_PALETTE:
        if emoji in replaced:
            return emoji
    parsed = parse_iphone_color_key(name)
    if parsed:
        return parsed
    return "⚫️"


def _compact_generic_name(name: str) -> str:
    s = name or "Без названия"
    s = re.sub(r"\s*Apple\s+", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*Новый\s*", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*\(без\s+RuStore\)\s*", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*-\s*\d+\s*RUB?\s*", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*ВК\s*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^iPhone\s+", "", s, flags=re.IGNORECASE)
    s = replace_color_with_emoji(s).strip()
    if len(s) > 64:
        s = s[:61] + "..."
    return s or "Без названия"


def _parse_airpods_model(name: str) -> Optional[str]:
    nl = name.lower()
    if "airpods pro 3" in nl or "pro 3" in nl:
        return "AirPods Pro 3"
    if "airpods pro 2" in nl or "pro 2" in nl:
        return "AirPods Pro 2"
    if "airpods 4 anc" in nl or "4 anc" in nl:
        return "AirPods 4 ANC"
    if "airpods 4" in nl:
        return "AirPods 4"
    if "airpods 3" in nl and "magsafe" in nl:
        return "AirPods 3 Magsafe"
    if "airpods 3" in nl:
        return "AirPods 3"
    return None


def _parse_watch_category(name: str) -> Optional[str]:
    nl = name.lower()
    if "se 3" in nl or "se3" in nl:
        return "SE 3"
    if "se 2" in nl or "se2" in nl:
        return "SE 2"
    if "11" in nl and ("watch" in nl or "aw" in nl):
        return "11"
    return None


def _parse_watch_size(name: str) -> Optional[str]:
    nl = name.lower()
    for sz in ("46mm", "45mm", "44mm", "41mm", "42mm", "40mm"):
        if sz in nl or sz.replace("mm", " mm") in nl:
            return sz
    return None


def _parse_ipad_model(name: str) -> Optional[str]:
    nl = name.lower()
    if "ipad air" in nl:
        base = "iPad Air"
        if "m4" in nl:
            return f"{base} M4"
        if "m3" in nl:
            return f"{base} M3"
        return base
    if "ipad 11" in nl or ("ipad" in nl and "11" in nl):
        return "iPad 11"
    return None


def _iphone_model_display_label(version: str, model_key: str) -> str:
    mk = (model_key or "").lower()
    if "air" in mk:
        return "Air"
    if "pro_max" in mk or "promax" in mk:
        return f"{version} Pro Max"
    if "pro" in mk:
        return f"{version} Pro"
    if "plus" in mk:
        return f"{version} Plus"
    if "mini" in mk:
        return f"{version} mini"
    if "16e" in mk or "16_e" in mk:
        return "16E"
    if "17e" in mk or "17_e" in mk:
        return "17E"
    return version


def _describe_iphone(p: dict) -> ProductLabel:
    name = p.get("name", "") or ""
    model = parse_iphone_model(name)
    ver = get_iphone_version_from_model(model) if model else "17"
    short = get_short_model_key_for_new(model or "")
    mem = parse_iphone_memory(name)
    st = parse_iphone_storage_type(name)
    mem_display = None
    if mem:
        mem_display = "1Tb" if mem == "1Tb" else f"{mem}Gb"
    color = parse_iphone_color_key(name) or resolve_color_emoji(name)
    storage = None
    if ver == "17" and mem_display:
        if st == "esim":
            storage = "eSim"
        elif st == "1+1" or st is None:
            storage = "(1+1)"
        elif st == "2sim":
            storage = "2sim"
    return ProductLabel(
        model=_iphone_model_display_label(ver, short),
        memory=mem_display,
        color_emoji=color,
        storage=storage,
        price=_price_from_product(p),
    )


def _describe_airpods(p: dict) -> ProductLabel:
    name = p.get("name", "") or ""
    model = _parse_airpods_model(name) or "AirPods"
    return ProductLabel(model=model, price=_price_from_product(p))


def _describe_watch(p: dict) -> ProductLabel:
    name = p.get("name", "") or ""
    cat = _parse_watch_category(name) or "—"
    size = _parse_watch_size(name)
    return ProductLabel(
        model=f"AW {cat}",
        size=size,
        color_emoji=resolve_color_emoji(name),
        price=_price_from_product(p),
    )


def _describe_ipad(p: dict) -> ProductLabel:
    name = p.get("name", "") or ""
    model = _parse_ipad_model(name) or "iPad"
    return ProductLabel(
        model=model,
        color_emoji=resolve_color_emoji(name),
        price=_price_from_product(p),
    )


def describe_product(p: dict) -> ProductLabel:
    """Единая точка: определяет категорию и заполняет поля ProductLabel."""
    dl = (p.get("display_label") or "").strip()
    if dl:
        return ProductLabel(model=dl[:128], price=_price_from_product(p))

    bl = (p.get("custom_button_label") or "").strip()
    if bl:
        return ProductLabel(model=bl[:128], price=_price_from_product(p))

    collection = (p.get("collection_name") or "").strip()
    if collection == "iPhone новые":
        return _describe_iphone(p)
    if collection == "Airpods":
        return _describe_airpods(p)
    if collection == "Apple Watch":
        return _describe_watch(p)
    if collection == "iPad":
        return _describe_ipad(p)

    return ProductLabel(model=_compact_generic_name(p.get("name", "") or ""), price=_price_from_product(p))


def render_label(pl: ProductLabel, *, with_price: bool) -> str:
    parts = [pl.model, pl.memory, pl.size, pl.color_emoji, pl.storage]
    head = " ".join(x for x in parts if x)
    if with_price and pl.price:
        head += f" - {pl.price}"
    return head


def button_label_for_product(p: dict) -> str:
    return render_label(describe_product(p), with_price=False)


def availability_line_for_product(p: dict) -> str:
    return render_label(describe_product(p), with_price=True)
