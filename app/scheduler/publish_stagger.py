"""Минимальный зазор между стартом публикаций на разных площадках."""
from __future__ import annotations

import asyncio
import time
from typing import Callable, Optional

STAGGERED_PLATFORMS = frozenset({"vk", "telegram", "max", "instagram"})
DEFAULT_GAP_SECONDS = 20.0


class PublishStagger:
    """Не даёт VK/TG/Max/IG начать публикацию в одну секунду."""

    def __init__(self, gap_seconds: float = DEFAULT_GAP_SECONDS):
        self.gap_seconds = max(0.0, float(gap_seconds))
        self._lock = asyncio.Lock()
        self._last_start_monotonic: Optional[float] = None

    async def wait_turn(
        self,
        platform: str,
        should_abort: Optional[Callable[[], bool]] = None,
    ) -> bool:
        """Дождаться слота старта. False — пауза прервала ожидание, слот не занят."""
        if platform not in STAGGERED_PLATFORMS or self.gap_seconds <= 0:
            return not bool(should_abort and should_abort())

        async with self._lock:
            while True:
                if should_abort and should_abort():
                    return False
                now = time.monotonic()
                if self._last_start_monotonic is None:
                    self._last_start_monotonic = now
                    return True
                remaining = self.gap_seconds - (now - self._last_start_monotonic)
                if remaining <= 0:
                    self._last_start_monotonic = now
                    return True
                await asyncio.sleep(min(1.0, remaining))
