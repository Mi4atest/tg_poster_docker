"""Сводка архива за месяц для главного экрана (expandable HTML)."""
from __future__ import annotations

import html
from collections import Counter
from typing import Any, Iterable

from app.db.product_queries import USED_EXCLUDED_COLLECTIONS
from app.utils.iphone_parser import (
    get_model_display_name,
    group_products_by_model,
    parse_iphone_model,
    sort_models_for_display,
)
from app.utils.product_label import describe_product

NEW_COLLECTIONS = frozenset(USED_EXCLUDED_COLLECTIONS)
_LEADERS_MAX = 3


def compact_preview_name(display_name: str) -> str:
    """13 Pro Max → 13 PM; остальное без iPhone."""
    s = (display_name or "").replace("iPhone ", "").strip()
    return s.replace("Pro Max", "PM")


def _used_model_counts(products: list[dict[str, Any]]) -> list[tuple[str, int]]:
    grouped = group_products_by_model(products)
    others = grouped.pop("Другие", [])
    ordered = sort_models_for_display(list(grouped.keys()))
    rows: list[tuple[str, int]] = []
    for model in ordered:
        n = len(grouped[model])
        if n:
            rows.append((get_model_display_name(model), n))
    if others:
        extra = Counter()
        for p in others:
            extra[describe_product(p).model or "Другие"] += 1
        for label, n in sorted(extra.items(), key=lambda x: x[0].lower()):
            if n:
                rows.append((label, n))
    return rows


def _new_model_counts(products: list[dict[str, Any]]) -> list[tuple[str, int]]:
    buckets: dict[tuple[int, str], int] = {}

    for p in products:
        name = p.get("name") or ""
        collection = (p.get("collection_name") or "").strip()
        parsed = parse_iphone_model(name)
        if parsed:
            label = get_model_display_name(parsed)
            section = 0
            sort_name = parsed
        else:
            pl = describe_product(p)
            label = pl.model or "Другие"
            if label.startswith("AW "):
                label = "Watch " + label[3:]
            if collection == "Airpods" or "airpod" in name.lower():
                section = 1
            elif collection == "Apple Watch" or "watch" in name.lower():
                section = 2
            elif collection == "iPad" or "ipad" in name.lower():
                section = 3
            else:
                section = 4
            sort_name = label
        key = (section, label)
        buckets[key] = buckets.get(key, 0) + 1

    iphone_labels = [lbl for (sec, lbl), _ in buckets.items() if sec == 0]
    # sort_models_for_display wants full "iPhone …"
    iphone_full = []
    for lbl in iphone_labels:
        full = lbl if lbl.startswith("iPhone ") else f"iPhone {lbl}"
        iphone_full.append((full, lbl))
    sorted_iphone = sort_models_for_display([f for f, _ in iphone_full])
    full_to_short = {f: s for f, s in iphone_full}

    rows: list[tuple[str, int]] = []
    for full in sorted_iphone:
        short = full_to_short.get(full, get_model_display_name(full))
        n = buckets.get((0, short), 0)
        if n:
            rows.append((short, n))
    rest = [(sec, lbl, n) for (sec, lbl), n in buckets.items() if sec != 0 and n]
    rest.sort(key=lambda x: (x[0], x[1].lower()))
    for _, lbl, n in rest:
        rows.append((lbl, n))
    return rows


def split_used_and_new(
    products: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    used: list[dict[str, Any]] = []
    new: list[dict[str, Any]] = []
    for p in products:
        coll = (p.get("collection_name") or "").strip()
        if coll in NEW_COLLECTIONS:
            new.append(p)
        else:
            used.append(p)
    return used, new


def _leaders(rows: list[tuple[str, int]]) -> list[tuple[str, int]]:
    ranked = sorted(rows, key=lambda x: (-x[1], x[0]))
    return ranked[:_LEADERS_MAX]


def format_monthly_sales_html(
    products: list[dict[str, Any]],
    month_name: str,
) -> str:
    """Раскрываемый блок: превью в первой строке, полный список внутри."""
    used, new = split_used_and_new(products)
    used_rows = _used_model_counts(used)
    new_rows = _new_model_counts(new)
    total = sum(n for _, n in used_rows) + sum(n for _, n in new_rows)

    leader_src = [r for r in used_rows if r[0] != "Другие"] or used_rows or new_rows
    leaders = _leaders(leader_src)
    preview = f"{html.escape(month_name)} · {total}"
    if leaders:
        bits = [f"{html.escape(compact_preview_name(name))} {n}" for name, n in leaders]
        preview += " · " + " · ".join(bits)

    lines = [preview]
    for name, n in used_rows:
        lines.append(f"{html.escape(name)} — {n}")
    if new_rows:
        lines.append("—— новые ——")
        for name, n in new_rows:
            lines.append(f"{html.escape(name)} — {n}")
    body = "\n".join(lines)
    return f"<blockquote expandable>{body}</blockquote>"
