"""Маппинг уровней экрана/корпуса (1–3) в значения автозагрузки Авито."""
from __future__ import annotations

from typing import Any, Dict, Optional

# Точные строки из GET /autoload/v1/user-docs/node/mobilnye_telefony/fields
SCREEN_LEVEL_TO_AVITO = {
    1: "Без дефектов",
    2: "1–2 мелкие царапины",
    3: "Много мелких царапин",
}

CASE_LEVEL_TO_AVITO = {
    1: "Без дефектов",
    2: "Мелкие царапины",
    3: "Глубокие царапины",
}


def clamp_avito_level(level: Any, default: int = 1) -> int:
    try:
        v = int(level)
    except (TypeError, ValueError):
        v = default
    if v < 1:
        return default
    if v > 3:
        return 3
    return v


def cycle_avito_level(level: Any) -> int:
    c = clamp_avito_level(level, default=1)
    return (c % 3) + 1


def screen_condition_from_draft(draft: Optional[Dict[str, Any]]) -> Optional[str]:
    if not draft or not isinstance(draft, dict):
        return None
    try:
        v = int(draft.get("screen_level") or 0)
    except (TypeError, ValueError):
        v = 0
    # 0 в черновике = «отличное» без явного уровня — для автозагрузки нужен обязательный ScreenCondition
    if v < 1:
        v = 1
    return SCREEN_LEVEL_TO_AVITO.get(clamp_avito_level(v))


def case_condition_from_draft(draft: Optional[Dict[str, Any]]) -> Optional[str]:
    if not draft or not isinstance(draft, dict):
        return None
    lvl = draft.get("body_level")
    try:
        v = int(lvl) if lvl is not None else 0
    except (TypeError, ValueError):
        v = 0
    if v < 1:
        v = 1
    return CASE_LEVEL_TO_AVITO.get(clamp_avito_level(v))
