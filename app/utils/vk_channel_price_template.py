"""Шаблон слотов прайса VK-канала: парсинг, хранение, дефолт из канонического текста."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.utils.bulk_price_parser import ParsedLabel, parse_label

logger = logging.getLogger(__name__)

TEMPLATE_PATH = Path(__file__).with_name("vk_channel_price_template.json")

# Маркеры, которые могут встретиться в pasted-шаблоне (не путать с заголовками).
_KNOWN_MARKERS = ("●", "○", "✓", "↻", "◆", "◇", "•", "🟢", "🔴")
_SLOT_RE = re.compile(
    r"^(?P<marker>[●○✓↻◆◇•🟢🔴])\s*(?P<label>.+?)(?:\s*[—–\-]\s*(?:от\s+)?(?P<price>\d*)\s*₽?)?\s*$"
)
_SECTION_META: Tuple[Tuple[str, str, str], ...] = (
    ("airpods", "🎧", "airpods"),
    ("pencil", "✏️", "pencil"),
    ("airtag", "🗺", "airtag"),
    ("watch_se", "⌚️ Apple Watch SE", "watch"),
    ("watch_11", "⌚️ Apple Watch 11", "watch"),
    ("ipad", "📋 iPad", "ipad"),
    ("ipad_air", "📋 iPad Air", "ipad"),
    ("tradein", "🔄", "tradein"),
    ("rfb", "♻️", "rfb"),
    ("iphone_new", "📱", "iphone"),
)

DEFAULT_CANONICAL_PRICE = """\
● — в наличии, ○ — на заказ (1–4 дня)

🎧 AirPods

● AirPods 4 — 10400₽
● AirPods 4 ANC — 14500₽
○ AirPods Pro 2 (USB-C) — 13900₽
● AirPods Pro 3 — 18500₽
○ AirPods Max 2 —

✏️ Apple Pencil

● Pencil USB-C — 7500₽
○ Pencil 2 — 7500₽
● Pencil Pro — 9200₽

🗺 AirTag

● AirTag — 3500₽
○ AirTag (4 шт.) — 9500₽

⌚️ Apple Watch SE

● SE2 40 мм, Midnight — 18900₽
○ SE2 44 мм, Midnight — 19900₽
● SE3 40 мм, Midnight — 20900₽
● SE3 40 мм, Starlight — 21500₽
○ SE3 44 мм, Midnight — 22900₽
● SE3 44 мм, Starlight — 23900₽

⌚️ Apple Watch 11

● 42 мм, Black — 28500₽
● 42 мм, Space Gray — 29500₽
○ 42 мм, Rose — 27900₽
● 42 мм, Silver — 28900₽
● 46 мм, Black — 30900₽
○ 46 мм, Space Gray — 30900₽
● 46 мм, Rose — 31500₽
● 46 мм, Silver — 30900₽

📋 iPad

● iPad 11 (A16, 2025) 128GB, Blue — 35500₽
○ iPad 11 (A16, 2025) 128GB, White — 36900₽
● iPad 11 (A16, 2025) 128GB, Pink — 37500₽
○ iPad 11 (A16, 2025) 128GB, Yellow — 37500₽

📋 iPad Air

● iPad Air 11" M4 (2026) 128GB Wi-Fi, Blue — 55900₽
○ iPad Air 11" M4 (2026) 128GB Wi-Fi, Starlight — 59900₽
● iPad Air 11" M4 (2026) 128GB Wi-Fi, Purple — 57500₽
● iPad Air 11" M4 (2026) 128GB Wi-Fi, Gray — 57500₽

🔄 iPhone (обменки)

● iPhone 13 Pro 128GB — 37900₽
○ iPhone 14 Pro 128GB eSim — 43500₽
○ iPhone 15 128GB —
○ iPhone 16 256GB —
○ iPhone 16 Pro 128GB —
○ iPhone 16 Pro 256GB eSim —
● iPhone 16 Pro Max 256GB eSim — 79900₽
○ iPhone 17 Pro 256GB eSim —
○ iPhone 17 Pro 512GB eSim —
○ iPhone 17 Pro Max 512GB eSim —

♻️ Восстановленные (RFB)

● iPhone 13 Pro 128GB — 41900₽

📱 iPhone (новые)

iPhone 14 Sim+eSim

● 128GB ⚫️ — 44900₽
● 128GB 🔵 — 44900₽
○ 128GB 🟡 — 42900₽

iPhone 15 Sim+eSim

● 128GB ⚫️ — 51900₽
○ 128GB 🔵 — 51900₽

iPhone 15 Plus Sim+eSim

○ 128GB — от 54900₽

iPhone 16 Sim+eSim

● 128GB ⚫️ — 59900₽
● 128GB ⚪️ — 59900₽
○ 128GB 🌸 — 60900₽
● 128GB 🟢 — 58900₽
● 128GB 🔵 — 59900₽
○ 256GB —

iPhone 16 Plus Sim+eSim

○ 128GB —

iPhone 16e Sim+eSim

● 128GB ⚫️ — 46900
● 128GB ⚪️ — 46900

iPhone 17e

● 256GB ⚫️ eSim — 53500₽
○ 256GB ⚪️ eSim — 54500₽
● 256GB 🌸 eSim — 53500₽
● 256GB ⚫️ Sim+eSim — 56500₽
○ 256GB ⚪️ Sim+eSim — 56500₽
● 256GB 🌸 Sim+eSim — 56500₽

iPhone 17

● 256GB ⚫️ eSim — 73500₽
● 256GB ⚪️ eSim — 73500₽
○ 256GB 🔵 eSim — 71900₽
● 256GB 🟢 eSim — 73500₽
○ 256GB 🟣 eSim — 73500₽
● 256GB ⚫️ Sim+eSim — 74900₽
● 256GB ⚪️ Sim+eSim — 74900₽
○ 256GB 🔵 Sim+eSim — 72500₽
● 256GB 🟢 Sim+eSim — 74900₽
○ 256GB 🟣 Sim+eSim — 74500₽

iPhone Air

● 256GB ⚫️ eSim — 73500₽
○ 256GB ⚪️ eSim — 72900₽
● 256GB 🟡 eSim — 73500₽
● 256GB 🔵 eSim — 72900₽
○ 512GB eSim — от 79900₽

iPhone 17 Pro

● 256GB ⚪️ eSim — 94500₽
● 256GB 🔵 eSim — 93500₽
○ 256GB 🟠 eSim — 91500₽
● 256GB ⚪️ Sim+eSim — 98900₽
○ 256GB 🔵 Sim+eSim — 98900₽
● 256GB 🟠 Sim+eSim — 98900₽
● 512GB ⚪️ eSim — 109900₽
○ 512GB 🔵 eSim — 110900₽
● 512GB 🟠 eSim — 110500₽
○ 512GB ⚪️ Sim+eSim — 123500₽
● 512GB 🔵 Sim+eSim — 117500₽
● 512GB 🟠 Sim+eSim — 115900₽

iPhone 17 Pro Max

● 256GB ⚪️ eSim — 99900₽
○ 256GB 🔵 eSim — 99900₽
● 256GB 🟠 eSim — 99900₽
● 256GB ⚪️ Sim+eSim — 110900₽
○ 256GB 🔵 Sim+eSim — 108900₽
● 256GB 🟠 Sim+eSim — 108900₽
● 512GB ⚪️ eSim — 116900₽
○ 512GB 🔵 eSim — 116900₽
● 512GB 🟠 eSim — 116900₽
○ 512GB ⚪️ Sim+eSim — 126900₽
● 512GB 🔵 Sim+eSim — 124900₽
● 512GB 🟠 Sim+eSim — 124900₽
"""


@dataclass
class PriceSlot:
    label: str
    category: str
    model: str
    memory: Optional[str] = None
    color: Optional[str] = None
    storage: Optional[str] = None
    size: Optional[str] = None
    condition: Optional[str] = None
    subgroup_title: Optional[str] = None

    def subgroup_key(self) -> tuple:
        """Ключ подгруппы без цвета (для общей цены)."""
        return (
            self.category,
            self.model or "",
            self.memory or "",
            self.storage or "",
            self.size or "",
            self.condition or "",
        )

    def match_key(self) -> tuple:
        return (
            self.category,
            self.model or "",
            self.memory or "",
            self.color or "",
            self.storage or "",
            self.size or "",
            self.condition or "",
        )


@dataclass
class PriceSection:
    id: str
    title: str
    category: str
    slots: List[PriceSlot] = field(default_factory=list)


@dataclass
class PriceTemplate:
    sections: List[PriceSection] = field(default_factory=list)

    def all_slots(self) -> List[PriceSlot]:
        out: List[PriceSlot] = []
        for sec in self.sections:
            out.extend(sec.slots)
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sections": [
                {
                    "id": s.id,
                    "title": s.title,
                    "category": s.category,
                    "slots": [asdict(slot) for slot in s.slots],
                }
                for s in self.sections
            ]
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PriceTemplate":
        sections: List[PriceSection] = []
        for s in data.get("sections") or []:
            slots = [PriceSlot(**slot) for slot in (s.get("slots") or [])]
            sections.append(
                PriceSection(
                    id=str(s.get("id") or ""),
                    title=str(s.get("title") or ""),
                    category=str(s.get("category") or "other"),
                    slots=slots,
                )
            )
        return cls(sections=sections)


def _detect_section(line: str) -> Optional[Tuple[str, str, str]]:
    stripped = line.strip()
    for sid, needle, cat in _SECTION_META:
        if needle in stripped or stripped.startswith(needle):
            # Различаем iPad / iPad Air и Watch SE / Watch 11
            if sid == "ipad" and "air" in stripped.lower():
                continue
            if sid == "ipad_air" and "air" not in stripped.lower():
                continue
            if sid == "watch_se" and "SE" not in stripped:
                continue
            if sid == "watch_11" and "11" not in stripped:
                continue
            return sid, stripped, cat
    return None


def _is_legend_line(line: str) -> bool:
    low = line.lower()
    return "в наличии" in low and ("на заказ" in low or "заказ" in low)


def _is_marker_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    for m in _KNOWN_MARKERS:
        if s.startswith(m):
            return True
    return False


def _iphone_subgroup_context(title: str) -> Tuple[str, Optional[str]]:
    """Из заголовка «iPhone 17 Pro» / «iPhone 14 Sim+eSim» → (model, storage)."""
    t = title.strip()
    storage = None
    low = t.lower()
    if "sim+esim" in low or "sim + esim" in low:
        storage = "1+1"
        t = re.sub(r"sim\s*\+\s*esim", "", t, flags=re.IGNORECASE).strip()
    elif re.search(r"\besim\b", low):
        storage = "esim"
        t = re.sub(r"\besim\b", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"^iphone\s+", "", t, flags=re.IGNORECASE).strip()
    # 16e / 17e
    t = t.replace("16e", "16E").replace("17e", "17E")
    if t.lower() == "air":
        return "Air", storage
    return t, storage


def _build_parse_label_for_slot(
    *,
    section_category: str,
    section_title: str,
    subgroup_title: Optional[str],
    label: str,
) -> str:
    """Собирает строку для parse_label с учётом контекста секции/подгруппы."""
    lab = label.strip()
    if section_category == "tradein":
        base = re.sub(r"^iphone\s+", "", lab, flags=re.IGNORECASE)
        return f"{base} (обменка)"
    if section_category == "rfb":
        base = re.sub(r"^iphone\s+", "", lab, flags=re.IGNORECASE)
        return f"{base} RFB"
    if section_category == "watch":
        if "watch" not in lab.lower() and not re.match(r"^(se|11)\b", lab, re.I):
            # «42 мм, Black» в секции Watch 11
            if "SE" in section_title:
                return lab
            return f"11 {lab}"
        return lab
    if section_category == "iphone" and subgroup_title:
        model, storage = _iphone_subgroup_context(subgroup_title)
        # Короткий слот: «128GB ⚫️» / «256GB ⚫️ eSim»
        fake = f"{model} {lab}"
        if storage == "1+1" and "sim" not in lab.lower() and "esim" not in lab.lower():
            fake = f"{fake} Sim+eSim"
        elif storage == "esim" and "esim" not in lab.lower():
            fake = f"{fake} eSim"
        return fake
    return lab


def _slot_from_parsed(
    label: str,
    parsed: ParsedLabel,
    *,
    section_category: str,
    subgroup_title: Optional[str],
) -> PriceSlot:
    category = parsed.category
    condition = parsed.condition
    if section_category in ("tradein", "rfb"):
        category = section_category
        condition = section_category
    elif section_category in ("pencil", "airtag", "airpods", "watch", "ipad", "iphone"):
        if parsed.category == "other" or (
            section_category == "iphone" and parsed.category == "iphone"
        ):
            category = section_category if section_category != "iphone" else parsed.category
        if section_category in ("pencil", "airtag") and parsed.category in (
            "pencil",
            "airtag",
            "other",
        ):
            category = section_category
    return PriceSlot(
        label=label,
        category=category,
        model=parsed.model,
        memory=parsed.memory,
        color=parsed.color,
        storage=parsed.storage,
        size=parsed.size,
        condition=condition,
        subgroup_title=subgroup_title,
    )


def parse_price_list_template(text: str) -> PriceTemplate:
    """Парсит канонический текст прайса в структурированный шаблон."""
    sections: List[PriceSection] = []
    current: Optional[PriceSection] = None
    subgroup_title: Optional[str] = None

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if _is_legend_line(line):
            continue

        sec = _detect_section(line)
        if sec and not _is_marker_line(line):
            sid, title, cat = sec
            current = PriceSection(id=sid, title=title, category=cat, slots=[])
            sections.append(current)
            subgroup_title = None
            continue

        if current is None:
            continue

        if not _is_marker_line(line):
            # Подзаголовок внутри секции (iPhone модели)
            if current.category == "iphone":
                subgroup_title = line
            continue

        m = _SLOT_RE.match(line)
        if not m:
            continue
        label = (m.group("label") or "").strip()
        if not label:
            continue

        parse_src = _build_parse_label_for_slot(
            section_category=current.category,
            section_title=current.title,
            subgroup_title=subgroup_title,
            label=label,
        )
        try:
            parsed = parse_label(parse_src)
        except Exception:
            logger.debug("parse_label failed for %r", parse_src, exc_info=True)
            parsed = ParsedLabel(category=current.category, model=label)

        # Для watch «42 мм» без SE — подтянуть модель из секции
        if current.category == "watch" and parsed.category == "watch":
            if "SE" in current.title and "SE" not in (parsed.model or ""):
                # SE2 / SE3 уже в label
                pass
            elif "11" in current.title and parsed.model in ("AW 11", "11"):
                parsed = ParsedLabel(
                    category="watch",
                    model="AW 11",
                    size=parsed.size,
                    color=parsed.color,
                    memory=parsed.memory,
                    storage=parsed.storage,
                )

        slot = _slot_from_parsed(
            label,
            parsed,
            section_category=current.category,
            subgroup_title=subgroup_title,
        )
        # Override model for short iPhone subgroup slots if parse missed
        if current.category == "iphone" and subgroup_title and not slot.model:
            model, storage = _iphone_subgroup_context(subgroup_title)
            slot.model = model
            if storage and not slot.storage:
                slot.storage = storage

        current.slots.append(slot)

    return PriceTemplate(sections=sections)


def ensure_default_template_file() -> Path:
    """Создаёт JSON-шаблон из канона, если файла ещё нет."""
    if not TEMPLATE_PATH.exists():
        tpl = parse_price_list_template(DEFAULT_CANONICAL_PRICE)
        TEMPLATE_PATH.write_text(
            json.dumps(tpl.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.info("Created VK channel price template at %s", TEMPLATE_PATH)
    return TEMPLATE_PATH


def load_template_from_file(path: Optional[Path] = None) -> PriceTemplate:
    p = path or TEMPLATE_PATH
    if not p.exists():
        ensure_default_template_file()
    data = json.loads(p.read_text(encoding="utf-8"))
    return PriceTemplate.from_dict(data)


def save_template_to_file(template: PriceTemplate, path: Optional[Path] = None) -> Path:
    p = path or TEMPLATE_PATH
    p.write_text(
        json.dumps(template.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return p


def load_active_template() -> PriceTemplate:
    """
    Активный шаблон: override из app_settings.vk_channel_price.template,
    иначе файл, иначе канон.
    """
    try:
        from app.services.settings_service import get_settings_service

        cfg = get_settings_service().get_vk_channel_price_config()
        override = cfg.get("template")
        if isinstance(override, dict) and override.get("sections"):
            return PriceTemplate.from_dict(override)
    except Exception:
        logger.debug("VK price template override unavailable", exc_info=True)
    try:
        return load_template_from_file()
    except Exception:
        logger.exception("Failed to load VK price template file, using canonical")
        return parse_price_list_template(DEFAULT_CANONICAL_PRICE)
