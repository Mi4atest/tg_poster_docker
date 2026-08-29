"""Клавиатуры управляемого списка автообновления рынка Avito."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.utils.iphone_parser import get_model_display_name


_PAGE = 8


def _mem_label(memory_gb: object) -> str:
    try:
        value = int(memory_gb)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    if value == 1024:
        return "1ТБ"
    return f"{value}ГБ"


def watchlist_item_label(row: dict, *, with_price: bool = True) -> str:
    short = get_model_display_name(str(row.get("model") or "iPhone"))
    mem = _mem_label(row.get("memory_gb"))
    name = f"{short} {mem}".strip()
    if not with_price:
        return name
    median = row.get("median_rub")
    if median is None:
        return f"{name} · —"
    price = f"{int(median):,}".replace(",", " ") + " ₽"
    return f"{name} · {price}"


def _page_slice(rows: list, page: int) -> tuple[list, int, int]:
    last = max(0, (len(rows) - 1) // _PAGE) if rows else 0
    page = min(max(0, page), last)
    start = page * _PAGE
    return rows[start : start + _PAGE], page, last


def watchlist_main_keyboard(rows: list[dict], *, page: int = 0) -> InlineKeyboardMarkup:
    chunk, page, last = _page_slice(rows, page)
    buttons: list[list[InlineKeyboardButton]] = []
    for row in chunk:
        mark = "🔥" if row.get("tier") == "daily" else "🕐"
        off = "" if row.get("enabled") else "⏸ "
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{off}{mark} {watchlist_item_label(row)}"[:64],
                    callback_data=f"avito_market_wl:i:{row['id']}",
                )
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"avito_market_wl:p:{page - 1}"))
    if page < last:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"avito_market_wl:p:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append(
        [
            InlineKeyboardButton(text="📥 Из отчётов", callback_data="avito_market_wl:imp"),
            InlineKeyboardButton(text="💡 Из каталога", callback_data="avito_market_wl:sug"),
        ]
    )
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="avito_market_start")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def watchlist_item_keyboard(item: dict) -> InlineKeyboardMarkup:
    item_id = int(item["id"])
    other = "slow" if item.get("tier") == "daily" else "daily"
    other_label = "🕐 72 ч" if other == "slow" else "🔥 24 ч"
    enabled = bool(item.get("enabled"))
    snap_id = item.get("last_snapshot_id")
    rows: list[list[InlineKeyboardButton]] = []
    if snap_id:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📄 Открыть отчёт",
                    callback_data=f"avito_market_open:{int(snap_id)}:0",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="🔄 Обновить сейчас", callback_data=f"avito_market_wl:run:{item_id}")]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=other_label,
                callback_data=f"avito_market_wl:t:{item_id}:{'s' if other == 'slow' else 'd'}",
            ),
            InlineKeyboardButton(
                text="⏸ Выкл" if enabled else "▶️ Вкл",
                callback_data=f"avito_market_wl:e:{item_id}",
            ),
        ]
    )
    rows.append(
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"avito_market_wl:rm:{item_id}")]
    )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="avito_market_wl")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def watchlist_confirm_delete_keyboard(item_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Удалить",
                    callback_data=f"avito_market_wl:rmok:{item_id}",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"avito_market_wl:i:{item_id}")],
        ]
    )


def watchlist_import_keyboard(
    rows: list[dict],
    *,
    page: int = 0,
    selected: set[int] | None = None,
    tier: str = "daily",
) -> InlineKeyboardMarkup:
    selected = selected or set()
    chunk, page, last = _page_slice(rows, page)
    buttons: list[list[InlineKeyboardButton]] = []
    for row in chunk:
        snap_id = int(row["id"])
        mark = "✅" if snap_id in selected else "☐"
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{mark} {watchlist_item_label(row)}"[:64],
                    callback_data=f"avito_market_wl:imp:g:{snap_id}",
                )
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"avito_market_wl:imp:p:{page - 1}"))
    if page < last:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"avito_market_wl:imp:p:{page + 1}"))
    if nav:
        buttons.append(nav)
    daily_mark = "✅ " if tier == "daily" else ""
    slow_mark = "✅ " if tier == "slow" else ""
    buttons.append(
        [
            InlineKeyboardButton(text=f"{daily_mark}🔥 24 ч", callback_data="avito_market_wl:imp:td"),
            InlineKeyboardButton(text=f"{slow_mark}🕐 72 ч", callback_data="avito_market_wl:imp:ts"),
        ]
    )
    count = len(selected)
    buttons.append(
        [
            InlineKeyboardButton(
                text=f"✅ Добавить выбранные ({count})",
                callback_data="avito_market_wl:imp:ok",
            )
        ]
    )
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="avito_market_wl")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def watchlist_suggest_keyboard(
    rows: list[dict],
    *,
    page: int = 0,
    tier: str = "daily",
) -> InlineKeyboardMarkup:
    chunk, page, last = _page_slice(rows, page)
    start = page * _PAGE
    buttons: list[list[InlineKeyboardButton]] = []
    for offset, row in enumerate(chunk):
        idx = start + offset
        count = int(row.get("product_count") or 0)
        extra = f" · {count} шт." if count else ""
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"➕ {watchlist_item_label(row, with_price=False)}{extra}"[:64],
                    callback_data=f"avito_market_wl:sug:a:{idx}",
                )
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"avito_market_wl:sug:p:{page - 1}"))
    if page < last:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"avito_market_wl:sug:p:{page + 1}"))
    if nav:
        buttons.append(nav)
    daily_mark = "✅ " if tier == "daily" else ""
    slow_mark = "✅ " if tier == "slow" else ""
    buttons.append(
        [
            InlineKeyboardButton(text=f"{daily_mark}🔥 24 ч", callback_data="avito_market_wl:sug:td"),
            InlineKeyboardButton(text=f"{slow_mark}🕐 72 ч", callback_data="avito_market_wl:sug:ts"),
        ]
    )
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="avito_market_wl")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def watchlist_from_result_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔥 24 ч", callback_data="avito_market_wl:fromr:d"),
                InlineKeyboardButton(text="🕐 72 ч", callback_data="avito_market_wl:fromr:s"),
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="avito_market_start")],
        ]
    )
