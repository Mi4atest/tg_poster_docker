"""Стили inline-кнопок (Bot API 9.4+: primary / success / danger)."""
from __future__ import annotations

from typing import Literal, Optional

from aiogram.types import InlineKeyboardButton

ButtonStyle = Literal["primary", "success", "danger"]

_PRIMARY_EXACT = frozenset({
    "create_post",
    "publish_all",
    "publish_all_pending",
    "confirm_publish_all_pending",
    "evening_report_start",
    "add_to_queue_and_create",
})

_SUCCESS_EXACT = frozenset({
    "confirm_yes",
    "avito_pub_go",
    "evening_report_send",
})

_DANGER_EXACT = frozenset({
    "delete",
    "confirm_delete",
    "cancel_publish_all_pending",
})

_PRIMARY_PREFIXES = ("queue_publish_now_", "add_to_queue_and_create_")
_DANGER_PREFIXES = ("queue_cancel_post_",)


def style_for_callback(callback_data: str) -> Optional[ButtonStyle]:
    """Вернуть style для callback_data или None (стандартная кнопка)."""
    if callback_data in _PRIMARY_EXACT:
        return "primary"
    if callback_data in _SUCCESS_EXACT:
        return "success"
    if callback_data in _DANGER_EXACT:
        return "danger"
    for prefix in _PRIMARY_PREFIXES:
        if callback_data.startswith(prefix):
            return "primary"
    for prefix in _DANGER_PREFIXES:
        if callback_data.startswith(prefix):
            return "danger"
    return None


def ikb(
    text: str,
    callback_data: str,
    *,
    style: Optional[ButtonStyle] = None,
    **kwargs,
) -> InlineKeyboardButton:
    """Inline-кнопка с автоматическим style по callback_data."""
    resolved = style if style is not None else style_for_callback(callback_data)
    params = {"text": text, "callback_data": callback_data, **kwargs}
    if resolved is not None:
        params["style"] = resolved
    return InlineKeyboardButton(**params)
