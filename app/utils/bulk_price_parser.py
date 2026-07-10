"""Парсинг текстового списка цен для пакетного обновления."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from app.utils.product_label import resolve_color_emoji

_LINE_RE = re.compile(r"^(.+?):\s*(\d+)\s*→\s*(\d+)")
_MEMORY_RE = re.compile(r"\b(128|256|512|1\s*Tb)\b", re.IGNORECASE)
_COLOR_EMOJI = ["🟣", "🟢", "🔵", "⚪️", "⚫️", "🟠", "🟡", "🌸", "🔴", "⭐", "🔘"]
_STORAGE_ESIM_RE = re.compile(r"\(\s*esim\s*\)", re.IGNORECASE)
_STORAGE_11_RE = re.compile(r"\(\s*1\s*\+\s*1\s*\)", re.IGNORECASE)


@dataclass(frozen=True)
class BulkPriceLine:
    raw_label: str
    old_rub: int
    new_rub: int
    line_no: int


@dataclass(frozen=True)
class ParsedLabel:
    category: str  # iphone, ipad, other
    model: str
    memory: Optional[str] = None
    color: Optional[str] = None
    storage: Optional[str] = None


def _normalize_memory(token: str) -> str:
    t = token.strip().lower().replace(" ", "")
    if t in ("1tb", "1tb"):
        return "1Tb"
    return token.strip()


def _extract_color(label: str) -> tuple[str, Optional[str]]:
    for em in _COLOR_EMOJI:
        if em in label:
            return label.replace(em, " "), em
    m = re.search(
        r"\b(blue|yellow|gray|grey|black|white|purple|green|orange|pink|gold|silver)\b",
        label,
        re.IGNORECASE,
    )
    if m:
        color_word = m.group(1)
        cleaned = label[: m.start()] + label[m.end() :]
        emoji = resolve_color_emoji(color_word)
        return cleaned, emoji
    return label, None


def _extract_storage(label: str) -> tuple[str, Optional[str]]:
    if _STORAGE_ESIM_RE.search(label):
        cleaned = _STORAGE_ESIM_RE.sub(" ", label)
        return cleaned, "esim"
    if _STORAGE_11_RE.search(label):
        cleaned = _STORAGE_11_RE.sub(" ", label)
        return cleaned, "1+1"
    if "esim" in label.lower() and "1+1" not in label:
        return label, "esim"
    if "1+1" in label:
        return label, "1+1"
    return label, None


def _normalize_model_text(text: str) -> str:
    s = text.strip()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\(A16\s*\)", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\bwifi\b", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\bот\b", " ", s, flags=re.IGNORECASE)
    s = s.replace(" -", " ").replace("- ", " ").strip(" -")
    low = s.lower()

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

    m = re.match(r"^(\d+)\s+(.+)$", s)
    if m:
        ver, rest = m.group(1), m.group(2).strip()
        if rest.lower() in ("pro max", "pro", "plus", "mini", "air"):
            return f"{ver} {rest.title().replace('Pro Max', 'Pro Max').replace('Pro', 'Pro')}"
        if rest and not re.match(r"^\d", rest):
            return f"{ver} {rest}"
        return ver

    if s.lower() == "air":
        return "Air"
    return s.strip()


def parse_label(raw_label: str) -> ParsedLabel:
    label = raw_label.strip()
    label, storage = _extract_storage(label)
    label, color = _extract_color(label)

    memory = None
    mem_m = _MEMORY_RE.search(label)
    if mem_m:
        memory = _normalize_memory(mem_m.group(1))
        label = label[: mem_m.start()] + label[mem_m.end() :]

    model = _normalize_model_text(label)
    low = raw_label.lower()
    if "ipad" in low:
        category = "ipad"
    elif re.search(r"\b(air|\d+)\b", model.lower()):
        category = "iphone"
    else:
        category = "other"
    return ParsedLabel(category=category, model=model, memory=memory, color=color, storage=storage)


def parse_bulk_price_text(text: str) -> List[BulkPriceLine]:
    lines: List[BulkPriceLine] = []
    for i, raw in enumerate((text or "").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        lines.append(
            BulkPriceLine(
                raw_label=m.group(1).strip(),
                old_rub=int(m.group(2)),
                new_rub=int(m.group(3)),
                line_no=i,
            )
        )
    return lines
