"""Редактирование сообщения(й) прайса в VK-канале через messages.edit."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional, Tuple

from vk_api.exceptions import ApiError

from app.services.settings_service import get_settings_service
from app.utils.vk_channel_price_formatter import (
    VK_MESSAGE_MAX_LENGTH,
    build_format_data_json,
    build_vk_channel_price,
)
from app.utils.vk_client import community_token, get_community_vk_session

logger = logging.getLogger(__name__)


def _edit_message_sync(
    *,
    peer_id: int,
    cmid: int,
    message: str,
    format_data: Optional[dict] = None,
) -> Tuple[bool, str]:
    token = community_token()
    if not token:
        return False, "VK community token не задан"

    if len(message) > VK_MESSAGE_MAX_LENGTH:
        return False, f"Текст длиннее лимита VK ({len(message)} > {VK_MESSAGE_MAX_LENGTH})"

    try:
        vk = get_community_vk_session().get_api()
        params = {
            "peer_id": int(peer_id),
            "cmid": int(cmid),
            "message": message,
            "keep_forward_messages": 1,
            "keep_snippets": 1,
        }
        if format_data:
            params["format_data"] = json.dumps(
                format_data, ensure_ascii=False, separators=(",", ":")
            )
        result = vk.messages.edit(**params)
        if result in (1, True):
            return True, f"ok peer={peer_id} cmid={cmid}"
        return False, f"messages.edit вернул {result!r}"
    except ApiError as e:
        code = getattr(e, "code", None)
        # Fallback: без format_data, если канал/API не принимает разметку
        if format_data and code in (100, 920, None):
            logger.warning(
                "VK messages.edit with format_data failed (code=%s): %s; retry plain",
                code,
                e,
            )
            try:
                vk = get_community_vk_session().get_api()
                result = vk.messages.edit(
                    peer_id=int(peer_id),
                    cmid=int(cmid),
                    message=message,
                    keep_forward_messages=1,
                    keep_snippets=1,
                )
                if result in (1, True):
                    return True, f"ok peer={peer_id} cmid={cmid} (без ссылок)"
            except ApiError as e2:
                return False, _format_api_error(e2)
        return False, _format_api_error(e)
    except Exception as e:
        logger.exception("VK channel price edit failed")
        return False, str(e)


def _format_api_error(e: ApiError) -> str:
    code = getattr(e, "code", None)
    msg = str(e)
    if code == 909:
        return (
            "909: сообщение слишком старое для edit "
            "(для канала проверьте права токена / возможность правки)"
        )
    if code == 949:
        return "949: нельзя редактировать закреплённое сообщение сейчас"
    if code == 914:
        return "914: сообщение слишком длинное"
    if code == 917:
        return "917: нет доступа к этому чату/каналу"
    if code == 15:
        return (
            "15: Access denied — у community-токена нет права messages "
            "или нет доступа к VK-каналу. В настройках сообщества включите "
            "«Сообщения сообщества» и выдайте токену доступ messages."
        )
    return f"VK API error {code}: {msg}"


async def update_vk_channel_price_message() -> Tuple[bool, str]:
    """
    Собирает прайс из шаблона+БД и правит привязанные сообщения канала.
    Возвращает (ok, detail).
    """
    cfg = get_settings_service().get_vk_channel_price_config()
    peer_id = cfg.get("peer_id")
    cmids = list(cfg.get("message_cmids") or [])
    if peer_id is None or not cmids:
        return False, "Привязка VK-прайса не задана (Настройки → Отчёты → Прайс VK-канала)"

    rendered = await asyncio.to_thread(
        build_vk_channel_price,
        with_links=bool(cfg.get("links_enabled")),
        split_if_needed=True,
        marker_in_stock=cfg.get("marker_in_stock"),
        marker_on_order=cfg.get("marker_on_order"),
    )

    parts = rendered.parts if rendered.parts else [rendered.text]
    if len(parts) > len(cmids):
        return (
            False,
            f"Прайс разбит на {len(parts)} частей, а привязано cmid: {len(cmids)}. "
            f"Добавьте сообщения или укоротите шаблон. Длины: "
            + ", ".join(str(len(p)) for p in parts),
        )

    # Один текст на все cmid, если частей меньше (или ровно одна часть)
    errors = []
    oks = 0
    for idx, cmid in enumerate(cmids):
        text = parts[idx] if idx < len(parts) else parts[-1]
        # format_data только для полного single-message режима
        fmt = rendered.format_data if (len(parts) == 1 and idx == 0) else None
        if len(parts) > 1:
            # Для частей пересобираем ссылки не делаем (проще plain)
            fmt = None
        ok, detail = await asyncio.to_thread(
            _edit_message_sync,
            peer_id=int(peer_id),
            cmid=int(cmid),
            message=text,
            format_data=fmt,
        )
        if ok:
            oks += 1
            logger.info("VK channel price updated: %s", detail)
        else:
            errors.append(detail)
            logger.error("VK channel price update failed: %s", detail)

    if oks == len(cmids):
        n_links = (rendered.stats or {}).get("format_links") or 0
        return True, (
            f"Обновлено сообщений: {oks}. "
            f"Длина {rendered.stats.get('char_len')} симв., ссылок {n_links}."
        )
    if oks:
        return False, f"Частично ({oks}/{len(cmids)}): " + "; ".join(errors)
    return False, "; ".join(errors) or "неизвестно"


# re-export helper for tests
__all__ = ["update_vk_channel_price_message", "build_format_data_json"]
