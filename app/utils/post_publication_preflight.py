"""Preflight: влезет ли текст поста в лимиты площадок после форматирования."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

from app.integrations.avito.autoload_xml import strip_avito_condition_lines
from app.services.settings_service import SettingsService, get_settings_service
from app.utils.product_parser import (
    extract_product_description,
    extract_product_name,
    parse_product_data,
)
from app.utils.telegram_text_units import telegram_text_units
from app.utils.text_formatter import (
    format_for_instagram,
    format_for_max,
    format_for_telegram,
    format_for_vk,
)

# Telegram Bot API
TELEGRAM_CAPTION_MAX_UNITS = 1024
TELEGRAM_MESSAGE_MAX_UNITS = 4096

# VK wall.post
VK_MESSAGE_MAX_LENGTH = 4096

# Instagram Graph API caption
INSTAGRAM_CAPTION_MAX_LENGTH = 2200

# Max API (консервативно; уточнить по доке при необходимости)
MAX_MESSAGE_MAX_LENGTH = 4096

# Avito autoload XML (build_ad_xml_body)
AVITO_TITLE_MAX_LENGTH = 100
AVITO_DESCRIPTION_MAX_LENGTH = 7500

PLATFORMS_ORDER = ("vk", "telegram", "instagram", "max", "avito")

PLATFORM_LABEL_RU = {
    "vk": "VK",
    "telegram": "TG",
    "instagram": "IG",
    "max": "Max",
    "avito": "Авито",
}


@dataclass(frozen=True)
class PlatformPreflightResult:
    platform: str
    label_ru: str
    ok: bool
    detail_ru: str


def enabled_platforms_for_preflight(service: SettingsService) -> List[str]:
    """Те же площадки, что попадут в очередь при enforce_enabled_filter=True."""
    platforms = [p for p in PLATFORMS_ORDER if service.is_platform_enabled(p)]
    return [
        p
        for p in platforms
        if p != "avito" or service.is_avito_queue_allowed()
    ]


def _has_media(post: dict) -> bool:
    photos = post.get("photos") or []
    videos = post.get("videos") or []
    return bool(photos or videos)


def _avito_title_and_description(post_text: str, post_name: Optional[str]) -> tuple[str, str]:
    parsed = parse_product_data(post_text or "")
    title = (
        parsed.get("name")
        or post_name
        or extract_product_name(post_text or "")
        or "Товар"
    )
    title = str(title).strip()
    desc_parts: list[str] = []
    if parsed.get("description"):
        desc_parts.append(str(parsed["description"]).strip())
    elif extract_product_description(post_text or ""):
        desc_parts.append(extract_product_description(post_text or "").strip())
    description = strip_avito_condition_lines("\n\n".join(p for p in desc_parts if p)).strip()
    return title, description


def _check_avito(post: dict) -> PlatformPreflightResult:
    text = post.get("text") or ""
    name = post.get("name")
    title, description = _avito_title_and_description(text, name)
    title_len = len(title)
    desc_len = len(description)
    ok = title_len <= AVITO_TITLE_MAX_LENGTH and desc_len <= AVITO_DESCRIPTION_MAX_LENGTH
    if ok:
        detail = "заголовок и описание в лимите"
    elif title_len > AVITO_TITLE_MAX_LENGTH and desc_len > AVITO_DESCRIPTION_MAX_LENGTH:
        detail = (
            f"заголовок ~{title_len} из {AVITO_TITLE_MAX_LENGTH}, "
            f"описание ~{desc_len} из {AVITO_DESCRIPTION_MAX_LENGTH}"
        )
    elif title_len > AVITO_TITLE_MAX_LENGTH:
        detail = f"заголовок ~{title_len} из {AVITO_TITLE_MAX_LENGTH}"
    else:
        detail = f"описание ~{desc_len} из {AVITO_DESCRIPTION_MAX_LENGTH}"
    return PlatformPreflightResult(
        platform="avito",
        label_ru=PLATFORM_LABEL_RU["avito"],
        ok=ok,
        detail_ru=detail,
    )


def _check_social_length(
    platform: str,
    *,
    formatted: str,
    length: int,
    limit: int,
    suffix: str = "",
) -> PlatformPreflightResult:
    ok = length <= limit
    detail = f"~{length} из {limit}{suffix}"
    return PlatformPreflightResult(
        platform=platform,
        label_ru=PLATFORM_LABEL_RU[platform],
        ok=ok,
        detail_ru=detail,
    )


def check_post_preflight(
    post: dict,
    *,
    signature_enabled: Optional[bool] = None,
    service: Optional[SettingsService] = None,
) -> List[PlatformPreflightResult]:
    """Проверка длины итогового текста по включённым площадкам."""
    service = service or get_settings_service()
    if signature_enabled is None:
        signature_enabled = service.is_signature_enabled()

    text = post.get("text") or ""
    has_media = _has_media(post)
    results: List[PlatformPreflightResult] = []

    for platform in enabled_platforms_for_preflight(service):
        if platform == "vk":
            body = format_for_vk(text, signature_enabled=signature_enabled)
            results.append(
                _check_social_length(
                    "vk",
                    formatted=body,
                    length=len(body),
                    limit=VK_MESSAGE_MAX_LENGTH,
                )
            )
        elif platform == "telegram":
            body = format_for_telegram(text, signature_enabled=signature_enabled)
            units = telegram_text_units(body)
            if has_media:
                limit = TELEGRAM_CAPTION_MAX_UNITS
                suffix = " (подпись к фото)"
            else:
                limit = TELEGRAM_MESSAGE_MAX_UNITS
                suffix = ""
            results.append(
                _check_social_length(
                    "telegram",
                    formatted=body,
                    length=units,
                    limit=limit,
                    suffix=suffix,
                )
            )
        elif platform == "instagram":
            body = format_for_instagram(text)
            results.append(
                _check_social_length(
                    "instagram",
                    formatted=body,
                    length=len(body),
                    limit=INSTAGRAM_CAPTION_MAX_LENGTH,
                )
            )
        elif platform == "max":
            body = format_for_max(text, signature_enabled=signature_enabled)
            results.append(
                _check_social_length(
                    "max",
                    formatted=body,
                    length=len(body),
                    limit=MAX_MESSAGE_MAX_LENGTH,
                )
            )
        elif platform == "avito":
            results.append(_check_avito(post))

    return results


def format_preflight_html(results: List[PlatformPreflightResult]) -> str:
    if not results:
        return ""
    lines = ["<b>Проверка перед публикацией:</b>"]
    any_bad = False
    for item in results:
        icon = "✅" if item.ok else "❌"
        if not item.ok:
            any_bad = True
        lines.append(f"{item.label_ru} {icon} {item.detail_ru}")
    if any_bad:
        lines.append("")
        lines.append("<i>❌ — пост может не опубликоваться в этой сети</i>")
    return "\n".join(lines)
