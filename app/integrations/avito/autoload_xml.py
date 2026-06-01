"""Сборка XML-фида автозагрузки Авито (формат 3, категория «Телефоны»)."""
from __future__ import annotations

import json
import re
import time
import xml.sax.saxutils as saxutils
from typing import Any, Dict, List, Optional

from app.integrations.avito.condition_maps import (
    case_condition_from_draft,
    screen_condition_from_draft,
)
from app.config.settings import AVITO_PROMO_ENABLED, AVITO_PROMO_MANUAL_OPTIONS
from app.integrations.avito.create_item import price_string_to_int_rub
from app.integrations.avito.phone_attributes import (
    build_phone_xml_fields,
    strip_avito_condition_lines,
)
from app.utils.product_parser import extract_product_description, extract_product_name, parse_product_data

# Допустимые символы в Id по документации Авито
_AD_ID_RE = re.compile(r"[^0-9A-Za-z,\\/\(\)\[\]\-=|]+")

_DEBUG_LOG_PATH = "/root/tg_poster_docker/.cursor/debug-7ef12c.log"


def _agent_log(location: str, message: str, data: dict, hypothesis_id: str) -> None:
    # #region agent log
    try:
        with open(_DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "sessionId": "7ef12c",
                        "location": location,
                        "message": message,
                        "data": data,
                        "timestamp": int(time.time() * 1000),
                        "hypothesisId": hypothesis_id,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion


def sanitize_ad_id(post_id: str) -> str:
    raw = (post_id or "").strip()
    if not raw:
        return "post_unknown"
    cleaned = _AD_ID_RE.sub("_", raw)
    return cleaned[:100]


def _el(name: str, value: Optional[str]) -> str:
    if value is None or value == "":
        return ""
    return f"<{name}>{saxutils.escape(str(value))}</{name}>\n"


def _cdata_el(name: str, value: str) -> str:
    if not value:
        return ""
    safe = value.replace("]]>", "]]]]><![CDATA[>")
    return f"<{name}><![CDATA[{safe}]]></{name}>\n"


def build_image_url_list(photo_file_ids: List[str], public_base_url: str) -> List[str]:
    base = (public_base_url or "").rstrip("/")
    if not base:
        return []
    urls = []
    for fid in photo_file_ids or []:
        fid = str(fid).strip()
        if not fid:
            continue
        urls.append(f"{base}/api/telegram/file/{fid}")
    return urls[:10]


def build_images_xml(photo_file_ids: List[str], public_base_url: str) -> str:
    """Блок Images: вложенные Image с атрибутом url (требование автозагрузки)."""
    urls = build_image_url_list(photo_file_ids, public_base_url)
    if not urls:
        return ""
    lines = ["<Images>\n"]
    for url in urls:
        lines.append(f'<Image url="{saxutils.escape(url)}"/>\n')
    lines.append("</Images>\n")
    return "".join(lines)


def build_image_urls(photo_file_ids: List[str], public_base_url: str) -> str:
    """Устаревший pipe-формат; оставлен для тестов совместимости."""
    return "|".join(build_image_url_list(photo_file_ids, public_base_url))


def build_ad_xml_body(
    *,
    post_id: str,
    post_text: str,
    post_name: Optional[str],
    photo_file_ids: List[str],
    avito_draft: Optional[Dict[str, Any]],
    public_base_url: str,
    contact_phone: str,
    address: str,
    category: str = "Телефоны",
    goods_type: str = "Мобильные телефоны",
    condition: str = "Б/у",
    ad_type: str = "Товар приобретен на продажу",
) -> str:
    """Фрагмент одного <Ad>...</Ad> без обёртки Ads."""
    parsed = parse_product_data(post_text or "")
    title = (parsed.get("name") or post_name or extract_product_name(post_text or "") or "Товар").strip()[:100]
    desc_parts = []
    if parsed.get("description"):
        desc_parts.append(str(parsed["description"]).strip())
    elif extract_product_description(post_text or ""):
        desc_parts.append(extract_product_description(post_text or "").strip())
    description = strip_avito_condition_lines("\n\n".join(p for p in desc_parts if p)).strip()[:7500]

    phone = build_phone_xml_fields(post_text=post_text or "", post_name=post_name, title=title)

    price = price_string_to_int_rub(parsed.get("price"))

    ad_id = sanitize_ad_id(post_id)
    images_xml = build_images_xml(photo_file_ids, public_base_url)
    screen_cond = screen_condition_from_draft(avito_draft)
    case_cond = case_condition_from_draft(avito_draft)

    parts = ["<Ad>\n"]
    parts.append(_el("Id", ad_id))
    parts.append(_el("Category", category))
    parts.append(_el("GoodsType", goods_type))
    parts.append(_el("AdType", ad_type))
    parts.append(_el("Title", title))
    parts.append(_cdata_el("Description", description))
    if price is not None:
        parts.append(_el("Price", str(int(price))))
    parts.append(_el("Condition", condition))
    if screen_cond:
        parts.append(_el("ScreenCondition", screen_cond))
    if case_cond:
        parts.append(_el("CaseCondition", case_cond))
    if phone.get("vendor"):
        parts.append(_el("Vendor", str(phone["vendor"])))
    if phone.get("model"):
        parts.append(_el("Model", str(phone["model"])))
    if phone.get("memory_size"):
        parts.append(_el("MemorySize", str(phone["memory_size"])))
    if phone.get("color"):
        parts.append(_el("Color", str(phone["color"])))
    if phone.get("sim_config"):
        parts.append(_el("SimConfig", str(phone["sim_config"])))
    if phone.get("akb") is not None:
        parts.append(_el("Akb", str(int(phone["akb"]))))
    if phone.get("set"):
        parts.append(_el("Set", str(phone["set"])))
    if AVITO_PROMO_ENABLED and AVITO_PROMO_MANUAL_OPTIONS:
        parts.append(_el("Promo", "Manual"))
        parts.append(_el("PromoManualOptions", AVITO_PROMO_MANUAL_OPTIONS))
    if contact_phone:
        parts.append(_el("ContactPhone", contact_phone))
    if address:
        parts.append(_el("Address", address))
    if images_xml:
        parts.append(images_xml)
    parts.append("</Ad>\n")
    return "".join(parts)


def build_feed_xml_for_posts(
    posts: List[Dict[str, Any]],
    *,
    public_base_url: str,
    contact_phone: str,
    address: str,
) -> str:
    """Фид с несколькими объявлениями — один upload API на весь файл."""
    parts = ['<?xml version="1.0" encoding="UTF-8"?>\n', '<Ads formatVersion="3" target="Avito.ru">\n']
    for p in posts:
        parts.append(
            build_ad_xml_body(
                post_id=str(p["post_id"]),
                post_text=p.get("post_text") or "",
                post_name=p.get("post_name"),
                photo_file_ids=p.get("photos") or [],
                avito_draft=p.get("avito_draft"),
                public_base_url=public_base_url,
                contact_phone=contact_phone,
                address=address,
            )
        )
    parts.append("</Ads>\n")
    return "".join(parts)


def build_autoload_ad_xml(
    *,
    post_id: str,
    post_text: str,
    post_name: Optional[str],
    photo_file_ids: List[str],
    avito_draft: Optional[Dict[str, Any]],
    public_base_url: str,
    contact_phone: str,
    address: str,
    category: str = "Телефоны",
    goods_type: str = "Мобильные телефоны",
    condition: str = "Б/у",
    ad_type: str = "Товар приобретен на продажу",
) -> str:
    """Одно объявление в фиде."""
    photo_count = len(build_image_url_list(photo_file_ids, public_base_url))
    # #region agent log
    _agent_log(
        "autoload_xml.py:build_autoload_ad_xml",
        "feed ad fields",
        {
            "post_id": post_id,
            "screen_condition": screen_condition_from_draft(avito_draft),
            "case_condition": case_condition_from_draft(avito_draft),
            "photo_count": photo_count,
            "draft": avito_draft,
        },
        "H1-H3",
    )
    # #endregion
    return build_feed_xml_for_posts(
        [
            {
                "post_id": post_id,
                "post_text": post_text,
                "post_name": post_name,
                "photos": photo_file_ids,
                "avito_draft": avito_draft,
            }
        ],
        public_base_url=public_base_url,
        contact_phone=contact_phone,
        address=address,
    )


def build_feed_xml_for_post(
    *,
    post_id: str,
    post_text: str,
    post_name: Optional[str],
    photos: List[str],
    avito_draft: Optional[Dict[str, Any]],
    public_base_url: str,
    contact_phone: str,
    address: str,
) -> str:
    return build_autoload_ad_xml(
        post_id=post_id,
        post_text=post_text,
        post_name=post_name,
        photo_file_ids=photos or [],
        avito_draft=avito_draft,
        public_base_url=public_base_url,
        contact_phone=contact_phone,
        address=address,
    )
