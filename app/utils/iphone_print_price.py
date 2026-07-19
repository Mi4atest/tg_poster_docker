"""Сборка строк печатного прайса iPhone из БД."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import text

from app.db.database import SessionLocal
from app.db.product_queries import USED_EXCLUDED_COLLECTIONS
from app.utils.color_emoji import COLOR_EMOJI_MAP
from app.utils.iphone_parser import (
    get_iphone_version_from_model,
    get_short_model_key_for_new,
    parse_iphone_memory,
    parse_iphone_model,
    parse_iphone_storage_type,
)
from app.utils.iphone_print_price_config import (
    MODEL_SORT_ORDER,
    RIGHT_COLUMN_START_MODEL,
    STOCK_MARKER,
    TRADEIN_PRODUCT_CODES,
)
from app.utils.price_change import price_string_to_int_rub
from app.utils.product_formatter import extract_product_code
from app.utils.product_label import _iphone_model_display_label

# Доп. цвета для печати (нет в COLOR_EMOJI_MAP или нужны полные имена).
_EXTRA_PRINT_COLORS: Dict[str, str] = {
    "Soft Pink": "Soft Pink",
    "Space Black": "Space Black",
    "Cloud White": "Cloud White",
    "Cosmic Orange": "Cosmic Orange",
    "Mist Blue": "Mist Blue",
    "Sky Blue": "Sky Blue",
    "Deep Blue": "Deep Blue",
    "Ultramarine": "Ultramarine",
    "Lavender": "Lavender",
    "Lavander": "Lavender",
}


@dataclass(frozen=True)
class PrintPriceLine:
    """Одна строка прайса."""

    text: str
    sort_model: str
    memory: str
    price_rub: int
    storage: str = ""
    in_stock: bool = False
    is_blank: bool = False
    is_section: bool = False


# Короткие цвета в БД → маркетинговые имена для печати (линейка 17 / Air).
_MODEL_COLOR_PRINT_ALIASES: Dict[str, Dict[str, str]] = {
    "Air": {
        "Black": "Space Black",
        "White": "Cloud White",
        "Blue": "Sky Blue",
    },
    "17": {
        "Blue": "Mist Blue",
    },
    "17E": {
        "Pink": "Soft Pink",
    },
    "17 Pro": {
        "Blue": "Deep Blue",
        "Orange": "Cosmic Orange",
    },
    "17 Pro Max": {
        "Blue": "Deep Blue",
        "Orange": "Cosmic Orange",
    },
}

_STORAGE_SORT = {"eSim": 0, "Sim+eSim": 1, "2sim": 2}


def _storage_sort_key(storage: str) -> int:
    return _STORAGE_SORT.get(storage or "", 9)


def resolve_print_color(sort_model: str, color: str) -> str:
    """Подставляет маркетинговое имя цвета для печати, если в БД короткое."""
    aliases = _MODEL_COLOR_PRINT_ALIASES.get(sort_model) or {}
    return aliases.get(color, color)


def product_in_stock(product: dict) -> bool:
    return (product.get("availability_status") or "").strip() == "available"


def _with_stock_marker(text: str, in_stock: bool) -> str:
    if in_stock:
        return f"{text}{STOCK_MARKER}"
    return text


def _model_sort_index(model: str) -> int:
    try:
        return MODEL_SORT_ORDER.index(model)
    except ValueError:
        # Неизвестные модели — в конец левой колонки (перед 17 Pro).
        try:
            return MODEL_SORT_ORDER.index(RIGHT_COLUMN_START_MODEL) - 1
        except ValueError:
            return len(MODEL_SORT_ORDER)


def print_model_label(name: str) -> Optional[str]:
    """Короткий ключ модели для сортировки/логики: '17 Pro', 'Air', '16E'."""
    model = parse_iphone_model(name)
    if not model:
        return None
    ver = get_iphone_version_from_model(model) or ""
    short = get_short_model_key_for_new(model)
    return _iphone_model_display_label(ver, short)


def print_model_display(sort_model: str) -> str:
    """Подпись модели в стиле скрина: PRO капсом."""
    m = (sort_model or "").strip()
    low = m.lower()
    if low == "air":
        return "Air"
    if low.endswith("e") and len(m) <= 4 and m[0].isdigit():
        # 16E / 17E → 16e / 17e как на скрине
        return m[:-1] + "e"
    parts = m.split()
    out = []
    for p in parts:
        pl = p.lower()
        if pl == "pro":
            out.append("PRO")
        elif pl == "max":
            out.append("MAX")
        elif pl == "plus":
            out.append("Plus")
        else:
            out.append(p)
    return " ".join(out)


def extract_color_name_en(name: str) -> Optional[str]:
    """Английское имя цвета из названия товара (longest match)."""
    if not name:
        return None
    # Собираем кандидатов: ключи COLOR_EMOJI_MAP + доп.
    candidates: List[str] = list(COLOR_EMOJI_MAP.keys()) + list(_EXTRA_PRINT_COLORS.keys())
    # Уникальные, длинные первыми
    seen = set()
    ordered: List[str] = []
    for c in sorted(candidates, key=lambda x: len(x), reverse=True):
        cl = c.lower()
        if cl in seen:
            continue
        seen.add(cl)
        ordered.append(c)

    low = name.lower()
    for c in ordered:
        if c.lower() in low:
            # Нормализация опечаток
            if c.lower() == "lavander":
                return "Lavender"
            return _EXTRA_PRINT_COLORS.get(c, c)

    # Эмодзи → типовое английское имя (fallback)
    emoji_to_en = {
        "⚫️": "Black",
        "⚪️": "White",
        "🔵": "Blue",
        "🟢": "Green",
        "🟡": "Yellow",
        "🟣": "Purple",
        "🟠": "Orange",
        "🌸": "Pink",
        "🔴": "Red",
        "⭐": "Starlight",
        "🔘": "Natural Titanium",
    }
    for em, en in emoji_to_en.items():
        if em in name:
            return en
    return None


def print_storage_label(name: str, sort_model: str) -> str:
    """eSim / Sim+eSim для печати."""
    st = parse_iphone_storage_type(name)
    ver = ""
    model = parse_iphone_model(name)
    if model:
        ver = get_iphone_version_from_model(model) or ""

    # 17 / Air / 17E: без маркера = Sim+eSim
    needs_default = ver == "17" or sort_model in ("Air", "17E")
    if st == "esim":
        return "eSim"
    if st == "1+1":
        return "Sim+eSim"
    if st == "2sim":
        return "2sim"
    if needs_default:
        return "Sim+eSim"
    # Для 14–16 на скрине тоже Sim+eSim
    return "Sim+eSim"


def format_new_iphone_line(product: dict) -> Optional[PrintPriceLine]:
    name = product.get("name") or ""
    sort_model = print_model_label(name)
    if not sort_model:
        return None
    memory = parse_iphone_memory(name)
    if not memory:
        return None
    mem_print = memory  # без Gb
    color_raw = extract_color_name_en(name)
    if not color_raw:
        return None
    color = resolve_print_color(sort_model, color_raw)
    storage = print_storage_label(name, sort_model)
    price = price_string_to_int_rub(product.get("price"))
    if price is None:
        return None
    in_stock = product_in_stock(product)
    model_disp = print_model_display(sort_model)
    text = _with_stock_marker(
        f"{model_disp} {mem_print} {color} {storage} - {price}",
        in_stock,
    )
    return PrintPriceLine(
        text=text,
        sort_model=sort_model,
        memory=mem_print,
        price_rub=price,
        storage=storage,
        in_stock=in_stock,
    )


def format_tradein_line(product: dict) -> Optional[PrintPriceLine]:
    name = product.get("name") or ""
    sort_model = print_model_label(name)
    if not sort_model:
        return None
    memory = parse_iphone_memory(name)
    if not memory:
        return None
    storage = print_storage_label(name, sort_model)
    # Для обменок на скрине: 13 PRO часто Sim+eSim; если в имени нет esim — Sim+eSim
    st = parse_iphone_storage_type(name)
    if st == "esim":
        storage = "eSim"
    elif st == "1+1":
        storage = "Sim+eSim"
    else:
        # эвристика как на скрине: Pro Max / 14 Pro → eSim, 13 Pro → Sim+eSim
        if "pro max" in sort_model.lower() or sort_model in ("14 Pro", "16 Pro", "17 Pro"):
            storage = "eSim"
        else:
            storage = "Sim+eSim"
    price = price_string_to_int_rub(product.get("price"))
    if price is None:
        return None
    in_stock = product_in_stock(product)
    model_disp = print_model_display(sort_model)
    text = _with_stock_marker(
        f"{model_disp} {memory} {storage} - {price}",
        in_stock,
    )
    return PrintPriceLine(
        text=text,
        sort_model=sort_model,
        memory=memory,
        price_rub=price,
        storage=storage,
        in_stock=in_stock,
    )


def _blank() -> PrintPriceLine:
    return PrintPriceLine(text="", sort_model="", memory="", price_rub=0, storage="", is_blank=True)


def group_lines_with_blanks(lines: Sequence[PrintPriceLine]) -> List[PrintPriceLine]:
    """Пустая строка между группами (модель + память + тип SIM)."""
    if not lines:
        return []
    sorted_lines = sorted(
        lines,
        key=lambda L: (
            _model_sort_index(L.sort_model),
            L.memory,
            _storage_sort_key(L.storage),
            L.price_rub,
            L.text,
        ),
    )
    out: List[PrintPriceLine] = []
    prev_key: Optional[Tuple[str, str, str]] = None
    for L in sorted_lines:
        key = (L.sort_model, L.memory, L.storage)
        if prev_key is not None and key != prev_key:
            out.append(_blank())
        out.append(L)
        prev_key = key
    return out


def dedupe_tradein_lines(lines: Sequence[PrintPriceLine]) -> List[PrintPriceLine]:
    """Одинаковые печатные строки обменок — один раз."""
    seen = set()
    out: List[PrintPriceLine] = []
    for L in lines:
        if L.text in seen:
            continue
        seen.add(L.text)
        out.append(L)
    return out


def fetch_new_iphones() -> List[dict]:
    """Активные iPhone новые + custom с кнопкой меню (туда же попадают 17e и т.п.)."""
    sql = text(
        """
        SELECT id, name, price, collection_name, custom_button_id, availability_status
        FROM products
        WHERE status = 'active'
          AND (
            collection_name = 'iPhone новые'
            OR (collection_name = 'custom' AND custom_button_id IS NOT NULL)
          )
        ORDER BY id
        """
    )
    with SessionLocal() as db:
        rows = db.execute(sql).mappings().all()
    return [dict(r) for r in rows]


def fetch_tradein_iphones(
    codes: Optional[frozenset] = None,
) -> List[dict]:
    code_set = codes if codes is not None else TRADEIN_PRODUCT_CODES
    sql = text(
        """
        SELECT id, name, price, collection_name, availability_status
        FROM products
        WHERE status = 'active'
          AND (
            collection_name IS NULL
            OR collection_name NOT IN (:c1, :c2, :c3, :c4, :c5)
          )
        ORDER BY id
        """
    )
    params = {
        "c1": USED_EXCLUDED_COLLECTIONS[0],
        "c2": USED_EXCLUDED_COLLECTIONS[1],
        "c3": USED_EXCLUDED_COLLECTIONS[2],
        "c4": USED_EXCLUDED_COLLECTIONS[3],
        "c5": USED_EXCLUDED_COLLECTIONS[4],
    }
    with SessionLocal() as db:
        rows = db.execute(sql, params).mappings().all()
    result = []
    for r in rows:
        d = dict(r)
        code = extract_product_code(d.get("name") or "")
        if code and code in code_set:
            result.append(d)
    return result


def split_new_into_columns(
    lines: Sequence[PrintPriceLine],
    right_start: str = RIGHT_COLUMN_START_MODEL,
) -> Tuple[List[PrintPriceLine], List[PrintPriceLine]]:
    """Левая колонка до right_start (не включая), правая — начиная с right_start."""
    threshold = _model_sort_index(right_start)
    left: List[PrintPriceLine] = []
    right: List[PrintPriceLine] = []
    # lines уже с blank-разделителями; blank наследует «текущую» сторону
    current_side = "left"
    for L in lines:
        if L.is_blank:
            if current_side == "left":
                left.append(L)
            else:
                right.append(L)
            continue
        if _model_sort_index(L.sort_model) >= threshold:
            current_side = "right"
            right.append(L)
        else:
            current_side = "left"
            left.append(L)
    # Убрать хвостовые blank
    while left and left[-1].is_blank:
        left.pop()
    while right and right[-1].is_blank:
        right.pop()
    return left, right


def build_print_catalog(
    new_products: Optional[List[dict]] = None,
    tradein_products: Optional[List[dict]] = None,
) -> Tuple[List[PrintPriceLine], List[PrintPriceLine], List[PrintPriceLine]]:
    """
    Возвращает (left_lines, right_lines_without_tradein, tradein_lines).
    Обменки всегда отдельным списком для секции внизу правой колонки.
    """
    news = new_products if new_products is not None else fetch_new_iphones()
    trades = tradein_products if tradein_products is not None else fetch_tradein_iphones()

    new_lines: List[PrintPriceLine] = []
    for p in news:
        line = format_new_iphone_line(p)
        if line:
            new_lines.append(line)

    grouped = group_lines_with_blanks(new_lines)
    left, right = split_new_into_columns(grouped)

    trade_lines: List[PrintPriceLine] = []
    for p in trades:
        line = format_tradein_line(p)
        if line:
            trade_lines.append(line)
    trade_lines = dedupe_tradein_lines(trade_lines)
    trade_lines = sorted(
        trade_lines,
        key=lambda L: (_model_sort_index(L.sort_model), L.memory, L.price_rub, L.text),
    )
    return left, right, trade_lines
