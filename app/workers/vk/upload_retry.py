"""Повторы загрузки медиа на стену и в маркет ВК: попытки и паузы."""
from __future__ import annotations

from typing import Optional

# 6 попыток; между ними 5 пауз 5–15 с (для flood — чуть длиннее).
VK_WALL_UPLOAD_ATTEMPTS = 6
VK_MARKET_UPLOAD_ATTEMPTS = 6
# connect, read: read=120с оставлял бота без ответа по 2 минуты на кадр.
VK_MARKET_POST_TIMEOUT = (10, 30)
VK_API_CALL_TIMEOUT = 25.0
_BACKOFF_SECONDS = (5, 8, 11, 13, 15)
_FLOOD_CODES = {6, 8, 9, 29}


def vk_upload_backoff_seconds(attempt: int, exc: Optional[BaseException] = None) -> float:
    """Пауза после неудачной попытки (attempt 1-based)."""
    idx = min(max(int(attempt), 1) - 1, len(_BACKOFF_SECONDS) - 1)
    delay = float(_BACKOFF_SECONDS[idx])
    if exc is not None:
        code = getattr(exc, "code", None)
        message = str(exc).lower()
        if code in _FLOOD_CODES or "flood" in message:
            delay = min(30.0, delay + 5.0)
    return delay
