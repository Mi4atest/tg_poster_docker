"""Генерация текста прайса VK-канала из шаблона слотов + БД."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import text

from app.db.database import SessionLocal
from app.utils.bulk_price_matcher import (
    NEW_COLLECTION_VALUES,
    ProductMatchKey,
    _keys_match,
    _normalize_memory,
    _normalize_model_for_match,
    _normalize_storage,
    _product_key,
)
from app.utils.iphone_parser import (
    get_iphone_version_from_model,
    get_short_model_key_for_new,
    parse_iphone_color_key,
    parse_iphone_details,
    parse_iphone_memory,
    parse_iphone_storage_type,
)
from app.utils.iphone_print_price_config import TRADEIN_PRODUCT_CODES
from app.utils.price_change import price_string_to_int_rub
from app.utils.product_formatter import extract_product_code
from app.utils.product_label import (
    _iphone_model_display_label,
    _parse_airtag_model,
    _parse_pencil_model,
)
from app.utils.vk_channel_price_template import (
    PriceSection,
    PriceSlot,
    PriceTemplate,
    load_active_template,
)

logger = logging.getLogger(__name__)

VK_MESSAGE_MAX_LENGTH = 4096
SAFE_MESSAGE_BUDGET = 3800

DEFAULT_MARKER_IN_STOCK = "●"
DEFAULT_MARKER_ON_ORDER = "○"
DEFAULT_LEGEND_TAIL = "в наличии, {on} — на заказ (1–4 дня)"


@dataclass
class RenderedPrice:
    text: str
    format_data: Optional[Dict[str, Any]] = None
    stats: Dict[str, Any] = field(default_factory=dict)
    parts: List[str] = field(default_factory=list)  # если >1 сообщения


def _utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def _product_key_extended(product: dict) -> Optional[ProductMatchKey]:
    """Ключ товара включая pencil/airtag/tradein/rfb (без подмешивания обычных б/у в обменки)."""
    name = product.get("name") or ""
    name_low = name.lower()
    collection = (product.get("collection_name") or "").strip()

    pencil = _parse_pencil_model(name)
    if pencil:
        return ProductMatchKey(category="pencil", model=pencil)

    airtag = _parse_airtag_model(name)
    if airtag:
        return ProductMatchKey(category="airtag", model=airtag)

    if "rfb" in name_low or collection.lower() == "rfb":
        details = parse_iphone_details(name)
        model_name = details.get("model")
        if model_name:
            ver = get_iphone_version_from_model(model_name) or ""
            short = get_short_model_key_for_new(model_name)
            model = _iphone_model_display_label(ver, short)
            memory = _normalize_memory(details.get("memory") or parse_iphone_memory(name))
            color = details.get("color") or parse_iphone_color_key(name)
            storage = _normalize_storage(parse_iphone_storage_type(name))
            return ProductMatchKey(
                category="rfb",
                model=model,
                memory=memory,
                color=color,
                storage=storage,
            )

    code = extract_product_code(name)
    if code and code in TRADEIN_PRODUCT_CODES:
        details = parse_iphone_details(name)
        model_name = details.get("model")
        if model_name:
            ver = get_iphone_version_from_model(model_name) or ""
            short = get_short_model_key_for_new(model_name)
            model = _iphone_model_display_label(ver, short)
            memory = _normalize_memory(details.get("memory") or parse_iphone_memory(name))
            storage = _normalize_storage(parse_iphone_storage_type(name))
            return ProductMatchKey(
                category="tradein",
                model=model,
                memory=memory,
                storage=storage,
            )

    # Обычные б/у iPhone НЕ считаем обменками — иначе слоты прайса линкуются не туда.
    if collection == "custom" and not product.get("custom_button_id"):
        return None

    return _product_key(product)


def _slot_to_want(slot: PriceSlot) -> ProductMatchKey:
    return ProductMatchKey(
        category=slot.category,
        model=slot.model,
        memory=_normalize_memory(slot.memory),
        color=slot.color,
        storage=_normalize_storage(slot.storage),
        size=slot.size,
    )


def _keys_match_channel(want: ProductMatchKey, have: ProductMatchKey) -> bool:
    if want.category in ("tradein", "rfb", "pencil", "airtag"):
        if want.category != have.category:
            return False
        if _normalize_model_for_match(want.model) != _normalize_model_for_match(have.model):
            return False
        if want.category in ("pencil", "airtag"):
            return True
        if want.memory and have.memory and want.memory != have.memory:
            return False
        if want.storage and have.storage and want.storage != have.storage:
            return False
        # tradein/rfb: цвет обычно не важен
        return True
    return _keys_match(want, have)


def _pick_best(candidates: List[dict]) -> Optional[dict]:
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    def score(p: dict) -> tuple:
        coll = (p.get("collection_name") or "").strip()
        # Каталог «новых» важнее custom-дублей без кнопки меню.
        coll_rank = 2 if coll in NEW_COLLECTION_VALUES else (1 if coll == "custom" else 0)
        avail = 1 if (p.get("availability_status") or "") == "available" else 0
        has_av_field = 1 if p.get("availability_status") in ("available", "on_order") else 0
        has_price = 1 if price_string_to_int_rub(p.get("price") or "") is not None else 0
        has_vk = 1 if p.get("vk_product_id") or p.get("vk_product_link") else 0
        return (coll_rank, has_av_field, avail, has_price, has_vk, -(p.get("id") or 0))

    return sorted(candidates, key=score, reverse=True)[0]


def fetch_channel_price_products() -> List[dict]:
    """Товары для прайса: новые + custom с кнопкой; обменки только по TRADEIN-кодам; RFB/аксессуары по имени."""
    sql = text(
        """
        SELECT id, name, price, collection_name, custom_button_id,
               availability_status, vk_product_id, vk_product_link
        FROM products
        WHERE status = 'active'
        ORDER BY id
        """
    )
    with SessionLocal() as db:
        rows = db.execute(sql).mappings().all()
    out: List[dict] = []
    for r in rows:
        d = dict(r)
        coll = (d.get("collection_name") or "").strip()
        if coll in NEW_COLLECTION_VALUES:
            out.append(d)
            continue
        if coll == "custom":
            if d.get("custom_button_id"):
                out.append(d)
            continue
        key = _product_key_extended(d)
        if key and key.category in ("tradein", "rfb", "pencil", "airtag"):
            out.append(d)
    return out


def _market_url(product: dict, group_id: Optional[int] = None) -> Optional[str]:
    from app.utils.vk_urls import market_product_url, rewrite_vk_com_to_ru

    link = (product.get("vk_product_link") or "").strip()
    if link:
        return rewrite_vk_com_to_ru(link)
    vk_id = product.get("vk_product_id")
    if not vk_id:
        return None
    gid = group_id
    if gid is None:
        try:
            from app.utils.vk_client import resolved_vk_group_id_int

            gid = resolved_vk_group_id_int()
        except Exception:
            return None
    return market_product_url(int(gid), int(vk_id))


def _format_price_rub(rub: Optional[int], *, prefix_ot: bool = False) -> str:
    if rub is None:
        return ""
    if prefix_ot:
        return f"от {rub}₽"
    return f"{rub}₽"


def _resolve_markers(
    marker_in_stock: Optional[str] = None,
    marker_on_order: Optional[str] = None,
) -> Tuple[str, str]:
    try:
        from app.services.settings_service import get_settings_service

        cfg = get_settings_service().get_vk_channel_price_config()
        ms = (marker_in_stock or cfg.get("marker_in_stock") or DEFAULT_MARKER_IN_STOCK).strip()
        mo = (marker_on_order or cfg.get("marker_on_order") or DEFAULT_MARKER_ON_ORDER).strip()
        return (ms or DEFAULT_MARKER_IN_STOCK), (mo or DEFAULT_MARKER_ON_ORDER)
    except Exception:
        return (
            (marker_in_stock or DEFAULT_MARKER_IN_STOCK).strip() or DEFAULT_MARKER_IN_STOCK,
            (marker_on_order or DEFAULT_MARKER_ON_ORDER).strip() or DEFAULT_MARKER_ON_ORDER,
        )


@dataclass
class _SlotResolved:
    slot: PriceSlot
    product: Optional[dict]
    price_rub: Optional[int]
    in_stock: bool
    from_subgroup_price: bool = False
    url: Optional[str] = None


def _match_slots(
    template: PriceTemplate,
    products: Sequence[dict],
) -> Tuple[List[Tuple[PriceSection, List[_SlotResolved]]], Dict[str, Any]]:
    keyed: List[Tuple[ProductMatchKey, dict]] = []
    for p in products:
        key = _product_key_extended(p)
        if key is None:
            continue
        keyed.append((key, p))

    used_ids: set[int] = set()
    section_resolved: List[Tuple[PriceSection, List[_SlotResolved]]] = []
    matched = 0
    empty = 0
    with_vk = 0

    for section in template.sections:
        resolved_list: List[_SlotResolved] = []
        # Группируем слоты секции по subgroup_key для общей цены
        by_sub: Dict[tuple, List[PriceSlot]] = {}
        for slot in section.slots:
            by_sub.setdefault(slot.subgroup_key(), []).append(slot)

        subgroup_prices: Dict[tuple, Optional[int]] = {}
        for sk, slots in by_sub.items():
            prices: List[int] = []
            for slot in slots:
                want = _slot_to_want(slot)
                cands = [p for k, p in keyed if _keys_match_channel(want, k)]
                # Prefer exact color when comparing for price collection
                exact = []
                for k, p in keyed:
                    if not _keys_match_channel(want, k):
                        continue
                    if want.color and k.color and want.color != k.color:
                        continue
                    exact.append(p)
                pool = exact or cands
                for p in pool:
                    rub = price_string_to_int_rub(p.get("price") or "")
                    if rub is not None:
                        prices.append(rub)
            uniq = sorted(set(prices))
            subgroup_prices[sk] = uniq[0] if len(uniq) == 1 else None

        for slot in section.slots:
            want = _slot_to_want(slot)
            cands: List[dict] = []
            for k, p in keyed:
                if p.get("id") in used_ids:
                    # allow reuse within same subgroup for shared price, but prefer unused
                    pass
                if _keys_match_channel(want, k):
                    if want.color and k.color and want.color != k.color:
                        continue
                    cands.append(p)
            # If color-strict empty, relax color for tradein/rfb already handled;
            # for iphone try without color only for picking shared later
            best = _pick_best(cands)
            price_rub = None
            in_stock = False
            from_sub = False
            url = None
            if best:
                price_rub = price_string_to_int_rub(best.get("price") or "")
                in_stock = (best.get("availability_status") or "") == "available"
                url = _market_url(best)
                if best.get("id") is not None:
                    used_ids.add(int(best["id"]))
            shared = subgroup_prices.get(slot.subgroup_key())
            if price_rub is None and shared is not None:
                price_rub = shared
                from_sub = True
                in_stock = False
            elif price_rub is not None and shared is not None and not want.color:
                pass
            # If shared unique price — apply to all colors (override only missing)
            if shared is not None and price_rub is None:
                price_rub = shared
                from_sub = True

            # Apply shared price to all slots in subgroup when unique
            if shared is not None and best is None:
                price_rub = shared
                from_sub = True
                in_stock = False
            elif shared is not None and best is not None:
                # keep exact product price; if product has no price use shared
                if price_rub is None:
                    price_rub = shared
                    from_sub = True

            if best or price_rub is not None:
                matched += 1
            else:
                empty += 1
            if url:
                with_vk += 1

            resolved_list.append(
                _SlotResolved(
                    slot=slot,
                    product=best,
                    price_rub=price_rub,
                    in_stock=in_stock,
                    from_subgroup_price=from_sub,
                    url=url,
                )
            )

        # Товары вне шаблона не добавляем в канал (шаблон обновляется paste раз в 3–6 мес.).
        # Считаем «лишние» только для лога/статистики.
        orphan_count = 0
        for k, p in keyed:
            if p.get("id") in used_ids:
                continue
            if k.category != section.category and not (
                section.category == "iphone" and k.category == "iphone"
            ):
                continue
            matched_any = False
            for slot in section.slots:
                if _keys_match_channel(_slot_to_want(slot), k):
                    matched_any = True
                    break
            if not matched_any:
                orphan_count += 1
        if orphan_count:
            logger.info(
                "VK price: %s products in DB not in template section %s (skipped)",
                orphan_count,
                section.id,
            )

        section_resolved.append((section, resolved_list))

    stats = {
        "sections": len(section_resolved),
        "slots_total": sum(len(r) for _, r in section_resolved),
        "matched_or_priced": matched,
        "empty_slots": empty,
        "with_vk_link": with_vk,
    }
    return section_resolved, stats


def _apply_subgroup_shared_prices(resolved: List[_SlotResolved]) -> None:
    """Если в подгруппе одна цена — проставить её на все цветовые слоты."""
    by_sub: Dict[tuple, List[_SlotResolved]] = {}
    for item in resolved:
        by_sub.setdefault(item.slot.subgroup_key(), []).append(item)
    for items in by_sub.values():
        prices = sorted({i.price_rub for i in items if i.price_rub is not None})
        if len(prices) != 1:
            continue
        only = prices[0]
        for i in items:
            if i.price_rub is None:
                i.price_rub = only
                i.from_subgroup_price = True
                if i.product is None:
                    i.in_stock = False
            elif i.price_rub != only and i.product is None:
                i.price_rub = only
                i.from_subgroup_price = True


def build_vk_channel_price(
    *,
    template: Optional[PriceTemplate] = None,
    products: Optional[List[dict]] = None,
    marker_in_stock: Optional[str] = None,
    marker_on_order: Optional[str] = None,
    with_links: bool = True,
    split_if_needed: bool = True,
) -> RenderedPrice:
    """
    Собирает текст прайса. Гиперссылки — через format_data (не увеличивают длину текста).
    """
    tpl = template or load_active_template()
    catalog = products if products is not None else fetch_channel_price_products()
    ms, mo = _resolve_markers(marker_in_stock, marker_on_order)

    section_data, stats = _match_slots(tpl, catalog)
    for _, resolved in section_data:
        _apply_subgroup_shared_prices(resolved)

    legend = f"{ms} — {DEFAULT_LEGEND_TAIL.format(on=mo)}"
    out_lines: List[str] = [legend, ""]
    link_specs: List[Tuple[str, str, str]] = []  # (full_line, label, url)

    for section, resolved in section_data:
        out_lines.append(section.title)
        out_lines.append("")
        current_sub = None
        for item in resolved:
            sub = item.slot.subgroup_title
            if sub and sub != current_sub:
                if current_sub is not None:
                    out_lines.append("")
                out_lines.append(sub)
                out_lines.append("")
                current_sub = sub
            marker = ms if item.in_stock else mo
            if item.price_rub is not None:
                line = f"{marker} {item.slot.label} — {_format_price_rub(item.price_rub)}"
            else:
                line = f"{marker} {item.slot.label} —"
            out_lines.append(line)
            if with_links and item.url:
                link_specs.append((line, item.slot.label, item.url))
        out_lines.append("")

    while out_lines and not out_lines[-1].strip():
        out_lines.pop()

    full_text = "\n".join(out_lines)
    stats["char_len"] = len(full_text)
    stats["utf16_len"] = _utf16_len(full_text)

    format_data = None
    if with_links and link_specs:
        items: List[Dict[str, Any]] = []
        search_from = 0
        for line, label, url in link_specs:
            idx = full_text.find(line, search_from)
            if idx < 0:
                idx = full_text.find(line)
            if idx < 0:
                continue
            marker_and_space = line.split(label, 1)[0]
            label_start = idx + len(marker_and_space)
            offset = _utf16_len(full_text[:label_start])
            length = _utf16_len(label)
            items.append({"type": "url", "offset": offset, "length": length, "url": url})
            search_from = idx + len(line)
        if items:
            format_data = {"version": "1", "items": items}
            stats["format_links"] = len(items)

    parts: List[str] = [full_text]
    if split_if_needed and len(full_text) > SAFE_MESSAGE_BUDGET:
        parts = _split_by_sections(legend, section_data, ms, mo)
        stats["parts"] = len(parts)
        stats["part_lengths"] = [len(p) for p in parts]

    return RenderedPrice(
        text=full_text,
        format_data=format_data,
        stats=stats,
        parts=parts,
    )


def _split_by_sections(
    legend: str,
    section_data: List[Tuple[PriceSection, List[_SlotResolved]]],
    ms: str,
    mo: str,
) -> List[str]:
    """Режет прайс по секциям, чтобы каждая часть ≤ SAFE_MESSAGE_BUDGET."""
    parts: List[str] = []
    current: List[str] = [legend, ""]

    def flush() -> None:
        nonlocal current
        while current and not current[-1].strip():
            current.pop()
        if current:
            parts.append("\n".join(current))
        current = []

    for section, resolved in section_data:
        chunk_lines: List[str] = [section.title, ""]
        current_sub = None
        for item in resolved:
            sub = item.slot.subgroup_title
            if sub and sub != current_sub:
                if current_sub is not None:
                    chunk_lines.append("")
                chunk_lines.append(sub)
                chunk_lines.append("")
                current_sub = sub
            marker = ms if item.in_stock else mo
            if item.price_rub is not None:
                chunk_lines.append(
                    f"{marker} {item.slot.label} — {_format_price_rub(item.price_rub)}"
                )
            else:
                chunk_lines.append(f"{marker} {item.slot.label} —")
        chunk_lines.append("")
        candidate = "\n".join(current + chunk_lines)
        if current and len(candidate) > SAFE_MESSAGE_BUDGET:
            flush()
            current = [legend, ""] + chunk_lines
        else:
            current.extend(chunk_lines)
    flush()
    return parts or [legend]


def build_format_data_json(format_data: Optional[Dict[str, Any]]) -> Optional[str]:
    if not format_data:
        return None
    return json.dumps(format_data, ensure_ascii=False, separators=(",", ":"))


def _html_escape(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _pack_section_blocks(
    blocks: List[str],
    *,
    max_len: int,
    max_parts: int,
    legend: str,
) -> List[str]:
    """
    Упаковывает блоки секций в ≤ max_parts частей, каждая ≤ max_len.
    Легенда только в первой части.
    """
    if max_parts < 1:
        max_parts = 1
    parts: List[str] = []
    current: List[str] = [legend, ""]

    def flush() -> None:
        nonlocal current
        while current and not current[-1].strip():
            current.pop()
        if current:
            parts.append("\n".join(current))
        current = []

    for block in blocks:
        candidate_lines = (current + [block, ""]) if current else [block, ""]
        candidate = "\n".join(candidate_lines)
        if current and len(candidate) > max_len:
            flush()
            if len(parts) >= max_parts:
                # Некуда класть — дописываем в последний кусок (лучше обрезать позже)
                if parts:
                    overflow = parts[-1] + "\n\n" + block
                    parts[-1] = overflow
                else:
                    parts.append(block)
                current = []
                continue
            current = [block, ""]
        else:
            if current:
                current.append(block)
                current.append("")
            else:
                current = [block, ""]
    flush()

    # Если частей больше лимита — склеиваем хвост
    while len(parts) > max_parts:
        tail = parts.pop()
        parts[-1] = parts[-1] + "\n\n" + tail

    # Если какой-то кусок всё ещё длиннее max_len — режем по строкам
    fixed: List[str] = []
    for p in parts:
        if len(p) <= max_len:
            fixed.append(p)
            continue
        rest = p
        while rest:
            if len(rest) <= max_len:
                fixed.append(rest)
                break
            cut = rest.rfind("\n", 0, max_len)
            if cut < max_len // 2:
                cut = max_len
            fixed.append(rest[:cut].rstrip())
            rest = rest[cut:].lstrip("\n")
    while len(fixed) > max_parts:
        tail = fixed.pop()
        fixed[-1] = fixed[-1] + "\n\n" + tail
    return fixed or [legend]


def build_telegram_channel_price(
    *,
    template: Optional[PriceTemplate] = None,
    products: Optional[List[dict]] = None,
    marker_in_stock: Optional[str] = None,
    marker_on_order: Optional[str] = None,
    with_links: bool = True,
    max_len: int = 4000,
    max_parts: int = 4,
) -> RenderedPrice:
    """
    Прайс для Telegram HTML: полный шаблон, нарезка по секциям на max_parts сообщений.
    Ссылки — <a href="vk_product_link">label</a>.
    """
    from datetime import datetime, timezone, timedelta

    tpl = template or load_active_template()
    catalog = products if products is not None else fetch_channel_price_products()
    ms, mo = _resolve_markers(marker_in_stock, marker_on_order)

    section_data, stats = _match_slots(tpl, catalog)
    for _, resolved in section_data:
        _apply_subgroup_shared_prices(resolved)

    msk = datetime.now(timezone.utc) + timedelta(hours=3)
    # Шапка первого сообщения: жирный заголовок + легенда в цитате (курсив) + дата без «по МСК».
    title = f"<b>{_html_escape('🍏 Актуальный прайс новой техники Apple')}</b>"
    legend_plain = f"{ms} — {DEFAULT_LEGEND_TAIL.format(on=mo)}"
    legend = f"<blockquote><i>{_html_escape(legend_plain)}</i></blockquote>"
    updated_line = _html_escape(
        f"Обновлено {msk.strftime('%d.%m.%y')} в {msk.strftime('%H:%M')}"
    )
    header = f"{title}\n{updated_line}\n{legend}"
    blocks: List[str] = []
    link_count = 0

    for section, resolved in section_data:
        lines: List[str] = [_html_escape(section.title), ""]
        current_sub = None
        for item in resolved:
            sub = item.slot.subgroup_title
            if sub and sub != current_sub:
                if current_sub is not None:
                    lines.append("")
                lines.append(_html_escape(sub))
                lines.append("")
                current_sub = sub
            marker = ms if item.in_stock else mo
            label_esc = _html_escape(item.slot.label)
            if with_links and item.url:
                label_html = f'<a href="{_html_escape(item.url)}">{label_esc}</a>'
                link_count += 1
            else:
                label_html = label_esc
            if item.price_rub is not None:
                price_esc = _html_escape(_format_price_rub(item.price_rub))
                lines.append(f"{_html_escape(marker)} {label_html} — {price_esc}")
            else:
                lines.append(f"{_html_escape(marker)} {label_html} —")
        while lines and not lines[-1].strip():
            lines.pop()
        blocks.append("\n".join(lines))

    parts = _pack_section_blocks(
        blocks,
        max_len=max_len,
        max_parts=max(1, int(max_parts or 1)),
        legend=header,
    )
    full_text = "\n\n".join(parts)
    stats["char_len"] = len(full_text)
    stats["parts"] = len(parts)
    stats["part_lengths"] = [len(p) for p in parts]
    stats["format_links"] = link_count
    stats["telegram_html"] = True

    return RenderedPrice(
        text=full_text,
        format_data=None,
        stats=stats,
        parts=parts,
    )
