"""Извлечение атрибутов телефона для XML автозагрузки Авито."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.integrations.avito.phone_color_catalog import resolve_avito_color_from_catalog

_IPHONE_PARSE_RE = re.compile(
    r"iPhone\s+"
    r"(?P<rest>\d+\s*(?:Pro\s*Max|Pro|Plus|mini|e|Air)?)\s+"
    r"(?P<mem>\d+)\s*(?:Gb|GB|Гб|ГБ)\s+"
    r"(?P<color>.+?)"
    r"(?:\s+\d{3,6})?\s*$",
    re.IGNORECASE,
)

_BATTERY_RE = re.compile(
    r"(?:аккумулятор|battery|🔋)[^\d]{0,20}(\d{1,3})\s*%",
    re.IGNORECASE,
)

# Строка «Комплект: …» из поста → тег Set (Комплектация) в XML автозагрузки.
_KIT_LINE_RE = re.compile(
    r"(?:📦\s*)?комплект\s*:\s*(.+?)(?:\s*[\(（]|\n|$)",
    re.IGNORECASE,
)

# Допустимые значения Set для категории «Мобильные телефоны» (Авито).
_AVITO_SET_VALUES = ("Коробка", "Блок зарядки", "Провод зарядки")


def parse_iphone_title(title: str) -> Dict[str, Optional[str]]:
    """Разбор «iPhone 15 Pro 128Gb Natural Titanium 8027»."""
    raw = (title or "").strip()
    m = _IPHONE_PARSE_RE.search(raw)
    if not m:
        return {"vendor": "Apple", "model": None, "memory_size": None, "color_token": None}
    model_part = re.sub(r"\s+", " ", m.group("rest").strip())
    model = f"iPhone {model_part}"
    mem = m.group("mem")
    color_token = m.group("color").strip()
    color_token = re.sub(r"\s*\d{3,6}\s*$", "", color_token).strip()
    return {
        "vendor": "Apple",
        "model": model,
        "memory_size": f"{mem} ГБ",
        "color_token": color_token,
    }


def parse_sim_config(post_text: str, model: Optional[str] = None) -> str:
    """
    Только по явным маркерам в тексте поста.
    По умолчанию — SIM + eSIM (если в описании нет указания про SIM).
    """
    del model  # модель не влияет на SIM
    low = (post_text or "").lower()
    if "поддерживает только esim" in low or "только esim" in low:
        return "Только eSIM"
    if re.search(r"2\s*физическ", low) or re.search(r"⚠️\s*2\s*sim", low, re.I):
        return "2 SIM"
    if re.search(r"\b1\s*sim\b", low) and "esim" not in low:
        return "1 SIM"
    return "SIM + eSIM"


def _normalize_kit_fragment(raw: str) -> str:
    text = (raw or "").strip().lower()
    text = re.sub(r"[\U0001f300-\U0001faff\U00002600-\U000027bf]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_kit_set(post_text: str) -> List[str]:
    """
    Разбор «Комплект: …» из текста поста в значения тега Set.

    Примеры:
    - коробка и кабель → Коробка, Провод зарядки
    - телефон и кабель → Провод зарядки
    - телефон → (пусто — сам телефон в комплект не входит)
    """
    m = _KIT_LINE_RE.search(post_text or "")
    if not m:
        return []
    frag = _normalize_kit_fragment(m.group(1))
    if not frag:
        return []

    has_box = "короб" in frag
    has_phone = "телефон" in frag
    has_cable = "кабел" in frag or "провод" in frag
    has_charger_block = ("блок" in frag and "заряд" in frag) or "зарядное устройство" in frag

    out: List[str] = []
    if has_box and has_cable:
        out.extend(["Коробка", "Провод зарядки"])
    elif has_phone and has_cable:
        out.append("Провод зарядки")
    elif has_box and has_charger_block:
        out.extend(["Коробка", "Блок зарядки"])
    elif has_box:
        out.append("Коробка")
    elif has_charger_block:
        out.append("Блок зарядки")
    elif has_cable:
        out.append("Провод зарядки")

    # Уникальные значения в порядке по справочнику Авито.
    seen: set[str] = set()
    ordered: List[str] = []
    for v in _AVITO_SET_VALUES:
        if v in out and v not in seen:
            seen.add(v)
            ordered.append(v)
    return ordered


def format_kit_set_xml(values: List[str]) -> Optional[str]:
    """Формат Set для XML: «Коробка | Провод зарядки»."""
    if not values:
        return None
    return " | ".join(values)


def parse_battery_percent(post_text: str) -> Optional[int]:
    text = post_text or ""
    best: Optional[int] = None
    for m in _BATTERY_RE.finditer(text):
        try:
            v = int(m.group(1))
        except (TypeError, ValueError):
            continue
        if 0 <= v <= 100:
            best = v
    return best


def build_phone_xml_fields(
    *,
    post_text: str,
    post_name: Optional[str],
    title: str,
) -> Dict[str, Any]:
    """Поля для XML: Vendor, Model, MemorySize, Color, SimConfig, Akb, Set."""
    parsed_title = parse_iphone_title(title or post_name or "")
    model = parsed_title.get("model")
    color_avito = resolve_avito_color_from_catalog(model, parsed_title.get("color_token"))
    sim = parse_sim_config(post_text)
    akb = parse_battery_percent(post_text)
    kit_set = format_kit_set_xml(parse_kit_set(post_text))
    return {
        "vendor": parsed_title.get("vendor") or "Apple",
        "model": model,
        "memory_size": parsed_title.get("memory_size"),
        "color": color_avito,
        "sim_config": sim,
        "akb": akb,
        "set": kit_set,
    }


def strip_avito_condition_lines(description: str) -> str:
    """Убрать из описания строки экран/корпус (они в ScreenCondition/CaseCondition)."""
    if not description:
        return ""
    lines = []
    for line in description.split("\n"):
        low = line.strip().lower()
        if low.startswith("экран:") or low.startswith("корпус:"):
            continue
        if "экран:" in low and "корпус:" in low and "·" in low:
            continue
        lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text
