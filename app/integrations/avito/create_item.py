"""Фаза B: сборка тела запроса и разбор ответа при создании объявления Авито."""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

from app.utils.product_parser import parse_product_data

# Уровни 0 = не указывать в тексте, 1–3 — для блока описания (атрибуты API уточняются по Swagger категории).
SCREEN_LEVEL_LABELS = ("", "экран: без дефектов", "экран: 1-2 мелкие царапины", "экран: много царапин")
BODY_LEVEL_LABELS = ("", "корпус: без царапин", "корпус: мелкие царапины", "корпус: глубокие царапины")


def draft_to_description_suffix(draft: Optional[Dict[str, Any]]) -> str:
    if not draft or not isinstance(draft, dict):
        return ""
    parts = []
    try:
        sl = int(draft.get("screen_level") or 0)
    except (TypeError, ValueError):
        sl = 0
    try:
        bl = int(draft.get("body_level") or 0)
    except (TypeError, ValueError):
        bl = 0
    if 0 < sl < len(SCREEN_LEVEL_LABELS):
        parts.append(SCREEN_LEVEL_LABELS[sl])
    if 0 < bl < len(BODY_LEVEL_LABELS):
        parts.append(BODY_LEVEL_LABELS[bl])
    if not parts:
        return ""
    return "\n\n" + " · ".join(parts)


def price_string_to_int_rub(price: Optional[str]) -> Optional[int]:
    if not price:
        return None
    clean = re.sub(r"[^\d]", "", str(price))
    if not clean:
        return None
    try:
        v = int(clean)
        return v if v > 0 else None
    except ValueError:
        return None


def build_core_item_payload(
    *,
    post_text: str,
    post_name: Optional[str],
    avito_draft: Optional[Dict[str, Any]],
    category_id: int,
    location_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Минимальное тело для POST /core/v1/accounts/{user}/items/0/ (черновик нового объявления)."""
    parsed = parse_product_data(post_text or "")
    title = (parsed.get("name") or post_name or "Товар").strip()[:110]
    desc_parts = []
    if parsed.get("description"):
        desc_parts.append(str(parsed["description"]).strip())
    desc_parts.append(draft_to_description_suffix(avito_draft).strip())
    desc_parts.append("Фото и видео — в оригинальном посте Telegram.")
    description = "\n\n".join(p for p in desc_parts if p).strip()[:5000]

    price = price_string_to_int_rub(parsed.get("price"))
    body: Dict[str, Any] = {
        "title": title,
        "description": description,
        "category_id": int(category_id),
    }
    if price is not None:
        body["price"] = int(price)
    if location_id is not None:
        try:
            body["location_id"] = int(location_id)
        except (TypeError, ValueError):
            pass
    return body


def extract_item_id_and_url(data: Any) -> Tuple[Optional[int], Optional[str]]:
    """Достаёт id и url из типичных форм ответов Авито."""
    if not isinstance(data, dict):
        return None, None

    def pick_id(obj: dict) -> Optional[int]:
        for key in ("id", "item_id", "itemId", "avito_item_id"):
            v = obj.get(key)
            if v is not None:
                try:
                    return int(v)
                except (TypeError, ValueError):
                    continue
        res = obj.get("result")
        if isinstance(res, dict):
            for key in ("id", "item_id", "itemId"):
                v = res.get(key)
                if v is not None:
                    try:
                        return int(v)
                    except (TypeError, ValueError):
                        continue
        return None

    def pick_url(obj: dict) -> Optional[str]:
        for key in ("url", "avito_url", "link", "public_url"):
            v = obj.get(key)
            if isinstance(v, str) and v.startswith("http"):
                return v
        res = obj.get("result")
        if isinstance(res, dict):
            for key in ("url", "avito_url", "link"):
                v = res.get(key)
                if isinstance(v, str) and v.startswith("http"):
                    return v
        return None

    iid = pick_id(data)
    url = pick_url(data)
    if iid is None:
        item = data.get("item")
        if isinstance(item, dict):
            iid = pick_id(item)
            if url is None:
                url = pick_url(item)
    return iid, url
