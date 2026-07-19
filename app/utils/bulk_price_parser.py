"""Парсинг текстового списка цен для пакетного обновления."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from app.utils.product_label import (
    _parse_airpods_model,
    _parse_airtag_model,
    _parse_pencil_model,
    _parse_watch_category,
    _parse_watch_size,
    resolve_color_emoji,
)

_LINE_RE = re.compile(r"^(.+?):\s*(\d+)\s*→\s*(\d+)")
_NEW_ITEM_RE = re.compile(
    r"^(.+?):\s*Новый элемент,\s*цена:\s*(\d+)",
    re.IGNORECASE,
)
_MEMORY_RE = re.compile(
    r"\b(128|256|512|1\s*Tb)\s*(?:GB|Gb|gb)?\b",
    re.IGNORECASE,
)
_WATCH_SIZE_RE = re.compile(r"\b(40|41|42|44|45|46)\b")
_COLOR_EMOJI = ["🟣", "🟢", "🔵", "⚪️", "⚫️", "🟠", "🟡", "🌸", "🔴", "⭐", "🔘", "💛"]
_STORAGE_ESIM_RE = re.compile(r"\(\s*esim\s*\)", re.IGNORECASE)
_STORAGE_11_RE = re.compile(r"\(\s*1\s*\+\s*1\s*\)", re.IGNORECASE)
_STORAGE_SIM_ESIM_RE = re.compile(r"\bsim\s*\+\s*esim\b", re.IGNORECASE)
_WATCH_SE_RE = re.compile(r"^se\s*([23])\b", re.IGNORECASE)
_WATCH_S11_RE = re.compile(r"^11\s+(40|41|42|44|45|46)\b")
_CONDITION_TRADEIN_RE = re.compile(
    r"\(\s*обменк[аa]\s*\)|\bобменк[аa]\b",
    re.IGNORECASE,
)
_CONDITION_RFB_RE = re.compile(r"\brfb\b", re.IGNORECASE)
_LABEL_TRAILING_JUNK_RE = re.compile(r"\s*[—–-]\s*\d+@?\s*$")
_YEAR_PAREN_RE = re.compile(r"\(\s*(?:A16\s*,?\s*)?\d{4}\s*\)", re.IGNORECASE)
_A16_PAREN_RE = re.compile(r"\(\s*A16\s*\)", re.IGNORECASE)

# Кириллица, часто путаемая с латиницей в прайсах (17е → 17e).
_HOMOGLYPHS = str.maketrans(
    {
        "а": "a",
        "А": "A",
        "е": "e",
        "Е": "E",
        "о": "o",
        "О": "O",
        "с": "c",
        "С": "C",
        "р": "p",
        "Р": "P",
        "х": "x",
        "Х": "X",
        "у": "y",
        "У": "Y",
        "к": "k",
        "К": "K",
        "м": "m",
        "М": "M",
        "т": "t",
        "Т": "T",
    }
)


@dataclass(frozen=True)
class BulkPriceLine:
    raw_label: str
    old_rub: int
    new_rub: int
    line_no: int


@dataclass(frozen=True)
class BulkPriceNewItemLine:
    """Строка «Новый элемент» — только для предпросмотра, без применения."""

    raw_label: str
    new_rub: int
    line_no: int


@dataclass(frozen=True)
class ParsedLabel:
    category: str  # iphone, ipad, airpods, watch, pencil, airtag, tradein, rfb, other
    model: str
    memory: Optional[str] = None
    color: Optional[str] = None
    storage: Optional[str] = None
    size: Optional[str] = None  # 40mm, 42mm — Apple Watch
    condition: Optional[str] = None  # tradein | rfb


def _normalize_homoglyphs(text: str) -> str:
    return (text or "").translate(_HOMOGLYPHS)


def _normalize_memory(token: str) -> str:
    t = token.strip().lower().replace(" ", "")
    if t in ("1tb",):
        return "1Tb"
    return token.strip()


def _color_word_to_emoji(color_word: str) -> str:
    w = (color_word or "").lower()
    if w in ("gray", "grey"):
        return "🔘"
    if w == "rose":
        return "🌸"
    return resolve_color_emoji(color_word)


def _extract_color(label: str) -> tuple[str, Optional[str]]:
    for em in _COLOR_EMOJI:
        if em in label:
            cleaned = label.replace(em, " ")
            mapped = "🟡" if em == "💛" else em
            return cleaned, mapped
    m = re.search(
        r"\b(blue|yellow|gray|grey|black|white|purple|green|orange|pink|gold|silver|rose|midnight|starlight)\b",
        label,
        re.IGNORECASE,
    )
    if m:
        color_word = m.group(1)
        cleaned = label[: m.start()] + label[m.end() :]
        return cleaned, _color_word_to_emoji(color_word)
    return label, None


def _extract_storage(label: str) -> tuple[str, Optional[str]]:
    if _STORAGE_SIM_ESIM_RE.search(label):
        cleaned = _STORAGE_SIM_ESIM_RE.sub(" ", label)
        return cleaned, "1+1"
    if _STORAGE_11_RE.search(label):
        cleaned = _STORAGE_11_RE.sub(" ", label)
        return cleaned, "1+1"
    if _STORAGE_ESIM_RE.search(label):
        cleaned = _STORAGE_ESIM_RE.sub(" ", label)
        return cleaned, "esim"
    low = label.lower()
    if "1+1" in label:
        return label, "1+1"
    if re.search(r"\besim\b", low):
        cleaned = re.sub(r"\besim\b", " ", label, flags=re.IGNORECASE)
        return cleaned, "esim"
    return label, None


def _extract_condition(label: str) -> tuple[str, Optional[str]]:
    """Выделяет обменку/RFB до гомоглифов, чтобы кириллица не ломалась."""
    if _CONDITION_TRADEIN_RE.search(label):
        cleaned = _CONDITION_TRADEIN_RE.sub(" ", label)
        return re.sub(r"\s+", " ", cleaned).strip(" -,"), "tradein"
    if _CONDITION_RFB_RE.search(label):
        cleaned = _CONDITION_RFB_RE.sub(" ", label)
        return re.sub(r"\s+", " ", cleaned).strip(" -,"), "rfb"
    return label, None


def _extract_watch_size(label: str) -> tuple[str, Optional[str]]:
    m = _WATCH_SIZE_RE.search(label)
    if not m:
        return label, None
    size = f"{m.group(1)}mm"
    cleaned = label[: m.start()] + label[m.end() :]
    return cleaned.strip(), size


def _normalize_airpods_model(name: str) -> Optional[str]:
    low = name.lower()
    low = re.sub(r"\(usb-c\)", " ", low, flags=re.IGNORECASE)
    low = re.sub(r"\s+", " ", low).strip(" -")
    if "airpod" not in low and not low.startswith("4") and "pro" not in low and "max" not in low:
        low = f"airpods {low}"
    return _parse_airpods_model(low) or _parse_airpods_model(f"AirPods {low}")


def _parse_watch_label(raw: str) -> Optional[ParsedLabel]:
    norm = _normalize_homoglyphs(raw).strip()
    low = norm.lower()
    low = re.sub(r"\s+", " ", low).strip(" -")
    low = re.sub(r"\bмм\b", " ", low).strip(" -,")

    size = None
    sz_m = _WATCH_SIZE_RE.search(low)
    if sz_m:
        size = f"{sz_m.group(1)}mm"
        low = (low[: sz_m.start()] + low[sz_m.end() :]).strip(" -,")

    _, color = _extract_color(norm)

    se_m = _WATCH_SE_RE.match(low)
    if se_m:
        ver = se_m.group(1)
        return ParsedLabel(category="watch", model=f"AW SE {ver}", size=size, color=color)

    if _WATCH_S11_RE.match(_normalize_homoglyphs(raw).lower().strip()):
        return ParsedLabel(category="watch", model="AW 11", size=size, color=color)

    fake_name = norm
    if "watch" not in low and " aw " not in f" {low} ":
        fake_name = f"Apple Watch {norm}"
    cat = _parse_watch_category(fake_name)
    parsed_size = _parse_watch_size(fake_name) or size
    # «aw» встречается внутри «air» (iPad Air) — не считать это Apple Watch.
    if cat and "ipad" not in low and "airpod" not in low:
        return ParsedLabel(
            category="watch",
            model=f"AW {cat}",
            size=parsed_size,
            color=color or resolve_color_emoji(fake_name),
        )
    return None


def _normalize_model_text(text: str) -> str:
    s = _normalize_homoglyphs(text.strip())
    s = re.sub(r"\s+", " ", s)
    s = _YEAR_PAREN_RE.sub(" ", s)
    s = _A16_PAREN_RE.sub(" ", s)
    s = re.sub(r"\(usb-c\)", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\bwi-?fi\b", " ", s, flags=re.IGNORECASE)
    # «от» после гомоглифов становится «ot»
    s = re.sub(r"\b(?:от|ot)\b", " ", s, flags=re.IGNORECASE)
    s = s.replace('"', " ").replace("″", " ").replace("”", " ")
    s = re.sub(r"\b\d+\s*(?:GB|Gb|gb)\b", " ", s)
    s = s.replace(" -", " ").replace("- ", " ").strip(" -,")
    low = s.lower()

    em = re.match(r"^(\d+)e$", low)
    if em:
        return f"{em.group(1)}E"

    if "ipad air" in low:
        base = "iPad Air"
        if "m4" in low:
            return f"{base} M4"
        if "m3" in low:
            return f"{base} M3"
        return base
    if "ipad 11" in low or (low.startswith("ipad") and "11" in low):
        return "iPad 11"

    s = re.sub(r"\bpro\s*max\b", "Pro Max", s, flags=re.IGNORECASE)
    s = re.sub(r"\bpro\b", "Pro", s, flags=re.IGNORECASE)
    s = re.sub(r"\bplus\b", "Plus", s, flags=re.IGNORECASE)
    s = re.sub(r"\bmini\b", "mini", s, flags=re.IGNORECASE)
    s = re.sub(r"\bair\b", "Air", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip(" -,")

    m = re.match(r"^(\d+)e\s+(.+)$", low)
    if m:
        return f"{m.group(1)}E"

    m = re.match(r"^(\d+)\s+(.+)$", s)
    if m:
        ver, rest = m.group(1), m.group(2).strip()
        rest_low = rest.lower()
        if rest_low in ("pro max", "pro", "plus", "mini", "air"):
            titled = {
                "pro max": "Pro Max",
                "pro": "Pro",
                "plus": "Plus",
                "mini": "mini",
                "air": "Air",
            }[rest_low]
            return f"{ver} {titled}"
        # Обрезаем хвост памяти/мусора: "Pro Max 256" → "Pro Max"
        rest_clean = re.sub(
            r"\s+(?:128|256|512|1\s*Tb)\b.*$",
            "",
            rest,
            flags=re.IGNORECASE,
        ).strip()
        if rest_clean.lower() in ("pro max", "pro", "plus", "mini", "air"):
            titled = {
                "pro max": "Pro Max",
                "pro": "Pro",
                "plus": "Plus",
                "mini": "mini",
                "air": "Air",
            }[rest_clean.lower()]
            return f"{ver} {titled}"
        if rest_clean and not re.match(r"^\d", rest_clean):
            return f"{ver} {rest_clean}"
        return ver

    if s.lower() == "air":
        return "Air"
    # "Air 256" → Air
    air_m = re.match(r"^air\s+(\d+)", s, flags=re.IGNORECASE)
    if air_m:
        return "Air"
    return s.strip()


def _detect_category(raw_label: str, model: str) -> str:
    low = _normalize_homoglyphs(raw_label).lower()
    if "ipad" in low:
        return "ipad"
    if "airpod" in low:
        return "airpods"
    if _WATCH_SE_RE.match(low.strip()) or _WATCH_S11_RE.match(low.strip()):
        return "watch"
    if re.search(r"\b(air|\d+e|\d+)\b", model.lower()):
        return "iphone"
    return "other"


def _clean_raw_label(raw_label: str) -> str:
    s = (raw_label or "").strip()
    s = _LABEL_TRAILING_JUNK_RE.sub("", s)
    return s.strip(" -,")


def parse_label(raw_label: str) -> ParsedLabel:
    raw = _clean_raw_label(raw_label)
    # Условие (обменка/RFB) — до гомоглифов, чтобы «обменка» не ломалась.
    raw_cond, condition = _extract_condition(raw)
    raw = _normalize_homoglyphs(raw_cond.strip())
    low_raw = raw.lower()

    pencil = _parse_pencil_model(raw)
    if pencil:
        return ParsedLabel(category="pencil", model=pencil, condition=condition)

    airtag = _parse_airtag_model(raw)
    if airtag:
        return ParsedLabel(category="airtag", model=airtag, condition=condition)

    if "ipad" in low_raw:
        label = raw
        label, storage = _extract_storage(label)
        label, color = _extract_color(label)
        memory = None
        mem_m = _MEMORY_RE.search(label)
        if mem_m:
            memory = _normalize_memory(mem_m.group(1))
            label = label[: mem_m.start()] + label[mem_m.end() :]
        model = _normalize_model_text(label)
        return ParsedLabel(
            category="ipad",
            model=model,
            memory=memory,
            color=color,
            storage=storage,
            condition=condition,
        )

    if "airpod" in low_raw:
        model = _normalize_airpods_model(raw) or "AirPods"
        return ParsedLabel(category="airpods", model=model, condition=condition)

    watch = _parse_watch_label(raw)
    if watch is not None:
        return ParsedLabel(
            category=watch.category,
            model=watch.model,
            memory=watch.memory,
            color=watch.color,
            storage=watch.storage,
            size=watch.size,
            condition=condition,
        )

    label = raw
    label, storage = _extract_storage(label)
    label, color = _extract_color(label)

    memory = None
    mem_m = _MEMORY_RE.search(label)
    if mem_m:
        memory = _normalize_memory(mem_m.group(1))
        label = label[: mem_m.start()] + label[mem_m.end() :]

    model = _normalize_model_text(label)
    category = _detect_category(raw_label, model)
    if condition == "tradein":
        category = "tradein"
    elif condition == "rfb":
        category = "rfb"
    return ParsedLabel(
        category=category,
        model=model,
        memory=memory,
        color=color,
        storage=storage,
        condition=condition,
    )


def parse_bulk_price_text(text: str) -> List[BulkPriceLine]:
    lines, _new, _skip = parse_bulk_price_input(text)
    return lines


def parse_bulk_price_input(
    text: str,
) -> Tuple[List[BulkPriceLine], List[BulkPriceNewItemLine], List[str]]:
    """Парсит прайс: изменения цен, новые позиции и нераспознанные строки."""
    lines: List[BulkPriceLine] = []
    new_items: List[BulkPriceNewItemLine] = []
    skipped: List[str] = []

    for i, raw in enumerate((text or "").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue

        m_new = _NEW_ITEM_RE.match(line)
        if m_new:
            new_items.append(
                BulkPriceNewItemLine(
                    raw_label=_clean_raw_label(m_new.group(1)),
                    new_rub=int(m_new.group(2)),
                    line_no=i,
                )
            )
            continue

        m = _LINE_RE.match(line)
        if m:
            lines.append(
                BulkPriceLine(
                    raw_label=_clean_raw_label(m.group(1)),
                    old_rub=int(m.group(2)),
                    new_rub=int(m.group(3)),
                    line_no=i,
                )
            )
            continue

        skipped.append(line)

    return lines, new_items, skipped
