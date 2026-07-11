"""Сопоставление строк списка цен с товарами в БД."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from sqlalchemy import text

from app.db.database import SessionLocal
from app.utils.bulk_price_parser import BulkPriceLine, ParsedLabel, parse_label
from app.utils.iphone_parser import (
    get_iphone_version_from_model,
    get_short_model_key_for_new,
    parse_iphone_color_key,
    parse_iphone_details,
    parse_iphone_memory,
    parse_iphone_storage_type,
)
from app.utils.price_change import PriceChangeInfo, PriceChangeLevel, analyze_price_change, price_string_to_int_rub
from app.utils.product_label import (
    _parse_airpods_model,
    _parse_watch_category,
    _parse_watch_size,
    _parse_ipad_model,
    _iphone_model_display_label,
    resolve_color_emoji,
)

NEW_COLLECTION_VALUES = ("iPhone новые", "Airpods", "Apple Watch", "iPad")
PRICE_TOLERANCE_RUB = 100


class MatchStatus(str, Enum):
    MATCHED = "matched"
    PRICE_MISMATCH = "price_mismatch"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    NEW_ITEM = "new_item"


@dataclass
class ProductMatchKey:
    category: str
    model: str
    memory: Optional[str] = None
    color: Optional[str] = None
    storage: Optional[str] = None
    size: Optional[str] = None

    def as_tuple(self) -> tuple:
        return (
            self.category,
            self.model,
            self.memory or "",
            self.color or "",
            self.storage or "",
            self.size or "",
        )


@dataclass
class BulkMatchResult:
    line: BulkPriceLine
    parsed: ParsedLabel
    status: MatchStatus
    product_id: Optional[int] = None
    product_name: Optional[str] = None
    db_price_rub: Optional[int] = None
    price_change: Optional[PriceChangeInfo] = None
    candidates: List[dict] = field(default_factory=list)
    display_label: str = ""

    @property
    def is_ready(self) -> bool:
        return self.status == MatchStatus.MATCHED and self.product_id is not None

    @property
    def is_critical(self) -> bool:
        return (
            self.price_change is not None
            and self.price_change.level == PriceChangeLevel.CRITICAL
        )


def _normalize_storage(st: Optional[str]) -> Optional[str]:
    if not st:
        return None
    s = st.strip().lower()
    if s in ("esim", "esim"):
        return "esim"
    if s in ("1+1", "(1+1)"):
        return "1+1"
    if s == "2sim":
        return "2sim"
    return s


def _normalize_memory(mem: Optional[str]) -> Optional[str]:
    if not mem:
        return None
    m = mem.strip().lower().replace("gb", "").replace(" ", "")
    if m == "1tb":
        return "1Tb"
    return m


def _product_key(product: dict) -> Optional[ProductMatchKey]:
    collection = (product.get("collection_name") or "").strip()
    name = product.get("name") or ""
    name_low = name.lower()

    if collection == "iPad" or "ipad" in name_low:
        model = _parse_ipad_model(name) or "iPad"
        memory = _normalize_memory(parse_iphone_memory(name))
        color = parse_iphone_color_key(name) or resolve_color_emoji(name)
        return ProductMatchKey(category="ipad", model=model, memory=memory, color=color)

    if collection == "Airpods" or "airpod" in name_low:
        model = _parse_airpods_model(name) or "AirPods"
        return ProductMatchKey(category="airpods", model=model)

    if collection == "Apple Watch" or "watch" in name_low or " aw " in f" {name_low} ":
        cat = _parse_watch_category(name)
        if not cat:
            return None
        size = _parse_watch_size(name)
        color = parse_iphone_color_key(name) or resolve_color_emoji(name)
        return ProductMatchKey(
            category="watch",
            model=f"AW {cat}",
            size=size,
            color=color,
        )

    if collection in NEW_COLLECTION_VALUES or collection == "custom":
        if "iphone" in name_low or collection == "iPhone новые":
            details = parse_iphone_details(name)
            model_name = details.get("model")
            if not model_name:
                return None
            ver = get_iphone_version_from_model(model_name) or ""
            short = get_short_model_key_for_new(model_name)
            model = _iphone_model_display_label(ver, short)
            memory = _normalize_memory(details.get("memory"))
            color = details.get("color") or resolve_color_emoji(name)
            storage = None
            if ver in ("17",) or model in ("Air", "17E"):
                st = parse_iphone_storage_type(name)
                if st == "esim":
                    storage = "esim"
                elif st in ("1+1", None):
                    storage = "1+1"
                else:
                    storage = _normalize_storage(st)
            return ProductMatchKey(
                category="iphone",
                model=model,
                memory=memory,
                color=color,
                storage=storage,
            )

    return None


def _parsed_to_key(parsed: ParsedLabel) -> ProductMatchKey:
    return ProductMatchKey(
        category=parsed.category,
        model=parsed.model,
        memory=_normalize_memory(parsed.memory),
        color=parsed.color,
        storage=_normalize_storage(parsed.storage),
        size=parsed.size,
    )


def _normalize_model_for_match(model: str) -> str:
    m = (model or "").strip().lower()
    em = re.match(r"^(\d+)e$", m)
    if em:
        return f"{em.group(1)}e"
    return m


def _keys_match(want: ProductMatchKey, have: ProductMatchKey) -> bool:
    if want.category != have.category:
        return False
    if _normalize_model_for_match(want.model) != _normalize_model_for_match(have.model):
        return False
    if want.category == "airpods":
        return True
    if want.memory and have.memory and want.memory != have.memory:
        return False
    if want.size and have.size and want.size != have.size:
        return False
    if want.color and have.color and want.color != have.color:
        return False
    if want.category == "iphone" and want.storage:
        if have.storage and want.storage != have.storage:
            return False
    return True


def fetch_active_new_products() -> List[dict]:
    sql = text(
        """
        SELECT id, name, display_label, price, collection_name, custom_button_id
        FROM products
        WHERE status = 'active'
          AND (
            collection_name = ANY(:cols)
            OR (collection_name = 'custom' AND custom_button_id IS NOT NULL)
          )
        ORDER BY id
        """
    )
    with SessionLocal() as db:
        rows = db.execute(sql, {"cols": list(NEW_COLLECTION_VALUES)}).mappings().all()
    return [dict(r) for r in rows]


def match_bulk_lines(
    lines: List[BulkPriceLine],
    products: Optional[List[dict]] = None,
) -> List[BulkMatchResult]:
    catalog = products if products is not None else fetch_active_new_products()
    keyed: Dict[tuple, List[dict]] = {}
    for p in catalog:
        key = _product_key(p)
        if key is None:
            continue
        keyed.setdefault(key.as_tuple(), []).append(p)

    results: List[BulkMatchResult] = []
    used_product_ids: set[int] = set()

    for line in lines:
        parsed = parse_label(line.raw_label)
        want = _parsed_to_key(parsed)
        matches: List[dict] = []
        for key_tuple, prods in keyed.items():
            cat, model, mem, color, storage, size = key_tuple
            have = ProductMatchKey(
                category=cat,
                model=model,
                memory=mem or None,
                color=color or None,
                storage=storage or None,
                size=size or None,
            )
            if _keys_match(want, have):
                matches.extend(prods)

        display = line.raw_label
        if not matches:
            results.append(
                BulkMatchResult(
                    line=line,
                    parsed=parsed,
                    status=MatchStatus.NOT_FOUND,
                    display_label=display,
                )
            )
            continue

        unique = []
        seen = set()
        for p in matches:
            pid = p["id"]
            if pid in seen:
                continue
            seen.add(pid)
            unique.append(p)

        if len(unique) > 1:
            results.append(
                BulkMatchResult(
                    line=line,
                    parsed=parsed,
                    status=MatchStatus.AMBIGUOUS,
                    candidates=unique,
                    display_label=display,
                )
            )
            continue

        product = unique[0]
        pid = product["id"]
        if pid in used_product_ids:
            results.append(
                BulkMatchResult(
                    line=line,
                    parsed=parsed,
                    status=MatchStatus.AMBIGUOUS,
                    candidates=unique,
                    display_label=display,
                )
            )
            continue

        db_price = price_string_to_int_rub(product.get("price")) or 0
        if abs(db_price - line.old_rub) > PRICE_TOLERANCE_RUB:
            results.append(
                BulkMatchResult(
                    line=line,
                    parsed=parsed,
                    status=MatchStatus.PRICE_MISMATCH,
                    product_id=pid,
                    product_name=product.get("name"),
                    db_price_rub=db_price,
                    display_label=display,
                )
            )
            continue

        price_change = analyze_price_change(db_price, line.new_rub)
        used_product_ids.add(pid)
        results.append(
            BulkMatchResult(
                line=line,
                parsed=parsed,
                status=MatchStatus.MATCHED,
                product_id=pid,
                product_name=product.get("name"),
                db_price_rub=db_price,
                price_change=price_change,
                display_label=display,
            )
        )

    return results
