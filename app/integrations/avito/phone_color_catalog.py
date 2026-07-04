"""Сопоставление английского цвета из названия поста → Color Авито (по модели, iphone_color_map.json)."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Tuple

_MAP_PATH = Path(__file__).resolve().parent / "data" / "iphone_color_map.json"

_CATALOG_PATHS = (
    Path(__file__).resolve().parents[3] / "media" / "avito_feed" / "phone_catalog.xml",
    Path(__file__).resolve().parent / "data" / "phone_catalog.xml",
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


@lru_cache(maxsize=1)
def load_color_map() -> Dict[str, Dict[str, str]]:
    """model → {en_token: avito_color}."""
    if not _MAP_PATH.is_file():
        return {}
    try:
        data = json.loads(_MAP_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw = data.get("by_model") or {}
    out: Dict[str, Dict[str, str]] = {}
    for model, mapping in raw.items():
        if isinstance(mapping, dict):
            out[str(model)] = {_norm(k): str(v) for k, v in mapping.items()}
    return out


@lru_cache(maxsize=1)
def load_apple_model_colors() -> Dict[str, Tuple[str, ...]]:
    """Допустимые Color из phone_catalog.xml (валидация)."""
    path = next((p for p in _CATALOG_PATHS if p.is_file()), None)
    if not path:
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    m = re.search(r'<Vendor name="Apple">(.*?)</Vendor>', text, re.DOTALL)
    if not m:
        return {}
    out: Dict[str, Tuple[str, ...]] = {}
    for mm in re.finditer(r'<Model name="([^"]+)">(.*?)</Model>', m.group(1), re.DOTALL):
        name = mm.group(1).strip()
        if not name.startswith("iPhone"):
            continue
        raw = re.findall(r'<Color name="([^"]+)"', mm.group(2))
        colors = tuple(c for c in dict.fromkeys(raw) if _norm(c) != "другое")
        if colors:
            out[name] = colors
    return out


def _find_model_key(model: str, keys: Dict[str, object]) -> Optional[str]:
    if not model:
        return None
    if model in keys:
        return model
    mn = _norm(model)
    for key in keys:
        if _norm(key) == mn:
            return key
    best: Optional[str] = None
    best_len = 0
    for key in keys:
        kn = _norm(key)
        if mn.startswith(kn) or kn in mn:
            if len(kn) > best_len:
                best = key
                best_len = len(kn)
    return best


def _match_token_in_map(color_token: str, model_map: Dict[str, str]) -> Optional[str]:
    token = _norm(color_token)
    if not token or not model_map:
        return None
    if token in model_map:
        return model_map[token]
    # Самый длинный ключ-вхождение (Pacific Blue > Blue)
    best_key: Optional[str] = None
    for en_key in sorted(model_map, key=len, reverse=True):
        if en_key in token:
            if best_key is None or len(en_key) > len(best_key):
                best_key = en_key
    return model_map.get(best_key) if best_key else None


def _validate_allowed(avito_color: str, model: str) -> str:
    allowed = load_apple_model_colors().get(model)
    if not allowed:
        return avito_color
    by_norm = {_norm(c): c for c in allowed}
    return by_norm.get(_norm(avito_color), avito_color)


def resolve_avito_color_from_catalog(
    model: Optional[str],
    color_token: Optional[str],
) -> Optional[str]:
    """Цвет из ручного каталога EN→RU для модели; при наличии XML — проверка допустимости."""
    if not color_token or not model:
        return None
    color_map = load_color_map()
    model_key = _find_model_key(model, color_map)
    if not model_key:
        return None
    model_map = color_map[model_key]
    result = _match_token_in_map(color_token, model_map)
    if result and model_key in load_apple_model_colors():
        result = _validate_allowed(result, model_key)
    return result
