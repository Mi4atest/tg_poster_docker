"""Публикация поста через автозагрузку (XML + upload)."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from app.config import settings as env_settings
from app.integrations.avito import autoload_actions
from app.integrations.avito.autoload_xml import build_feed_xml_for_post, sanitize_ad_id
from app.integrations.avito import http_client as avito_http
from app.integrations.avito.feed_store import save_feed
from app.services.settings_service import get_settings_service

logger = logging.getLogger(__name__)


def _public_base_url() -> str:
    svc = get_settings_service()
    custom = str(svc.get_all().get("integrations", {}).get("avito_feed_public_base_url") or "").strip()
    if custom:
        return custom.rstrip("/")
    return env_settings.AVITO_FEED_PUBLIC_BASE_URL


def _contact_phone() -> str:
    svc = get_settings_service()
    custom = str(svc.get_all().get("integrations", {}).get("avito_contact_phone") or "").strip()
    raw = custom or env_settings.AVITO_AUTOLOAD_CONTACT_PHONE or env_settings.TELEGRAM_CONTACT_PHONE or ""
    return "".join(c for c in str(raw) if c.isdigit())


def _address() -> str:
    svc = get_settings_service()
    custom = str(svc.get_all().get("integrations", {}).get("avito_autoload_address") or "").strip()
    return custom or env_settings.AVITO_AUTOLOAD_ADDRESS or "Киров"


async def publish_post_via_autoload(
    *,
    post_id: str,
    post_text: str,
    post_name: Optional[str],
    photos: list,
    avito_draft: Optional[Dict[str, Any]],
) -> Tuple[bool, Optional[int], Optional[str], str]:
    """
    Сохраняет XML-фид, запускает upload, ждёт avito_id.
    Возвращает (success, avito_item_id, avito_url, user_message).
    """
    ad_id = sanitize_ad_id(post_id)
    xml = build_feed_xml_for_post(
        post_id=post_id,
        post_text=post_text or "",
        post_name=post_name,
        photos=photos or [],
        avito_draft=avito_draft,
        public_base_url=_public_base_url(),
        contact_phone=_contact_phone(),
        address=_address(),
    )
    save_feed(xml, post_id, ad_id)
    logger.info("Avito autoload: feed saved for post %s ad_id=%s", post_id, ad_id)

    try:
        await autoload_actions.trigger_autoload_upload()
    except avito_http.AvitoApiError as e:
        msg = f"Авито: не удалось запустить выгрузку ({e.status}): {(e.body or str(e))[:300]}"
        logger.error(msg)
        return False, None, None, msg

    avito_id, status, raw = await autoload_actions.wait_for_avito_id_after_upload(ad_id)
    if avito_id:
        url = f"https://www.avito.ru/{avito_id}"
        return True, avito_id, url, f"Объявление создано через автозагрузку (id {avito_id})"

    _, _, msgs = autoload_actions.parse_item_report(raw)
    err = "; ".join(msgs[:5]) if msgs else (status or "нет avito_id в отчёте")
    msg = (
        f"Автозагрузка завершилась без id объявления. Статус: {status or '—'}. "
        f"Проверьте фид и отчёт в ЛК Авито. {err}"[:500]
    )
    logger.warning("Avito autoload no avito_id post=%s ad_id=%s raw=%s", post_id, ad_id, str(raw)[:400])
    return False, None, None, msg
