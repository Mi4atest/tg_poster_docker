"""Однострочная оценка рынка б/у iPhone по Avito."""
from __future__ import annotations

import html
import logging
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.enums import ChatAction, ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.keyboards.product_keyboard import get_products_menu_keyboard
from app.config.settings import (
    AVITO_MARKET_CACHE_TTL_SEC,
    AVITO_MARKET_DAILY_REQUEST_LIMIT,
    AVITO_MARKET_MIN_REQUEST_INTERVAL_SEC,
    AVITO_MARKET_REGION,
)
from app.bot.utils.market_daily_formatter import format_market_daily_html
from app.db.avito_market_watchlist_queries import ShopPriceRange, get_used_shop_price_range
from app.db.avito_market_queries import list_market_daily
from app.db.database import run_db
from app.integrations.avito.market_search import MarketListing
from app.services.iphone_market_price_service import (
    MarketPriceEstimate,
    MarketTemporarilyUnavailable,
    get_iphone_market_price_service,
    user_facing_market_error,
)
from app.services.settings_service import get_settings_service
from app.integrations.avito.debug_agent_log import agent_dbg
from app.utils.iphone_market_query import (
    IphoneMarketQuery,
    MarketQueryError,
    parse_iphone_market_query,
)
from app.utils.iphone_parser import get_model_display_name, sort_models_for_display
from app.utils.price_stats import PriceSummary


logger = logging.getLogger(__name__)
router = Router()

_MSK = ZoneInfo("Europe/Moscow")
_MAX_LIST_LINES = 40
_HISTORY_PER_PAGE = 10
_HISTORY_RECENT = 3
_COL_MODEL = 12
_COL_MEM = 5
_COL_PRICE = 10
_AVITO_ORIGIN = "https://www.avito.ru"
_IPHONE_TITLE_PREFIX = re.compile(r"^(?:apple\s+)?iphone\s+", re.IGNORECASE)


class IphoneMarketPriceState(StatesGroup):
    waiting_for_query = State()


def _result_keyboard(
    *,
    history_page: int | None = None,
    offer_watchlist: bool = False,
) -> InlineKeyboardMarkup:
    """Назад на один шаг внутри блока, не сразу в меню Товары."""
    if history_page is None:
        rows = []
        if offer_watchlist:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="➕ В автообновление",
                        callback_data="avito_market_wl:fromr",
                    )
                ]
            )
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text="🗂 Последние отчёты",
                        callback_data="avito_market_history",
                    )
                ],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="avito_market_start")],
            ]
        )
        back = "avito_market_start"
    else:
        rows = [
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=f"avito_market_hist:{history_page}",
                )
            ]
        ]
        back = f"avito_market_hist:{history_page}"
    # region agent log
    agent_dbg(
        "D",
        "iphone_market_price.py:_result_keyboard",
        "result keyboard back target",
        {"history_page": history_page, "back": back},
        run_id="nav",
    )
    # endregion
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _cancel_keyboard() -> InlineKeyboardMarkup:
    """Только intro блока: выход в меню Товары."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Список автообновления",
                    callback_data="avito_market_wl",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗂 Последние отчёты",
                    callback_data="avito_market_history",
                )
            ],
            [InlineKeyboardButton(text="⬅️ В товары", callback_data="avito_market_cancel")],
        ]
    )


def _block_back_keyboard() -> InlineKeyboardMarkup:
    """Ошибка/пустая история: на intro поиска, не в Товары."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗂 Последние отчёты",
                    callback_data="avito_market_history",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="avito_market_start")],
        ]
    )


def sort_market_report_rows(rows: list[dict]) -> list[dict]:
    """Как список б/у: старые модели → новые, внутри модели — меньшая память."""
    models = [str(row.get("model") or "") for row in rows]
    order = {name: index for index, name in enumerate(sort_models_for_display(list(set(models))))}
    return sorted(
        rows,
        key=lambda row: (
            order.get(str(row.get("model") or ""), 999),
            int(row.get("memory_gb") or 0),
            str(row.get("model") or ""),
        ),
    )


def _memory_label(memory: object) -> str:
    try:
        value = int(memory)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    if value == 1024:
        return "1ТБ"
    return f"{value}ГБ"


def _fmt_msk_short(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(_MSK).strftime("%d.%m.%y %H:%M")


def _report_summary_line(row: dict) -> str:
    short = get_model_display_name(str(row.get("model") or "iPhone"))
    mem = _memory_label(row.get("memory_gb"))
    name = f"{short} {mem}".strip()
    median = row.get("median_rub")
    price = _rub(int(median)) if median is not None else "—"
    when = _fmt_msk_short(row.get("fetched_at"))
    chunks = [html.escape(name), html.escape(price)]
    if when:
        chunks.append(html.escape(when))
    return ": ".join([chunks[0], " · ".join(chunks[1:])])


def _history_keyboard(rows: list[dict], *, page: int = 0) -> InlineKeyboardMarkup:
    total = len(rows)
    last_page = max(0, (total - 1) // _HISTORY_PER_PAGE) if total else 0
    page = min(max(0, page), last_page)
    start = page * _HISTORY_PER_PAGE
    chunk = rows[start : start + _HISTORY_PER_PAGE]
    buttons: list[list[InlineKeyboardButton]] = []
    for row in chunk:
        snap_id = int(row["id"])
        model = str(row.get("model") or "iPhone")
        memory = row.get("memory_gb")
        mem = _memory_label(memory)
        short = get_model_display_name(model)
        median = row.get("median_rub")
        price = _rub(int(median)) if median is not None else "—"
        label = f"{short} {mem}: {price}"
        if len(label) > 60:
            label = label[:57] + "…"
        buttons.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"avito_market_open:{snap_id}:{page}",
                )
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="◀️",
                callback_data=f"avito_market_hist:{page - 1}",
            )
        )
    if start + _HISTORY_PER_PAGE < total:
        nav.append(
            InlineKeyboardButton(
                text="▶️",
                callback_data=f"avito_market_hist:{page + 1}",
            )
        )
    if nav:
        buttons.append(nav)
    buttons.append(
        [InlineKeyboardButton(text="🔎 Новый поиск", callback_data="avito_market_start")]
    )
    buttons.append(
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="avito_market_start")]
    )
    # region agent log
    agent_dbg(
        "A",
        "iphone_market_price.py:_history_keyboard",
        "history keyboard nav",
        {
            "page": page,
            "has_products_exit": any(
                btn.callback_data == "avito_market_cancel"
                for row in buttons
                for btn in row
            ),
            "screen_back": "avito_market_start",
        },
        run_id="nav",
    )
    # endregion
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _rows_by_fetched_at_desc(rows: list[dict]) -> list[dict]:
    def _ts(row: dict) -> datetime:
        value = row.get("fetched_at")
        if not isinstance(value, datetime):
            return datetime.min.replace(tzinfo=UTC)
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value

    return sorted(rows, key=_ts, reverse=True)


def _aligned_report_line(row: dict) -> str:
    short = get_model_display_name(str(row.get("model") or "iPhone"))
    mem = _memory_label(row.get("memory_gb"))
    median = row.get("median_rub")
    price = _rub(int(median)) if median is not None else "—"
    when = _fmt_msk_short(row.get("fetched_at"))
    return (
        f"{short[:_COL_MODEL].ljust(_COL_MODEL)} "
        f"{mem.rjust(_COL_MEM)} "
        f"{price.rjust(_COL_PRICE)}  "
        f"{when}"
    )


def _history_text(rows: list[dict]) -> str:
    lines = [
        "🗂 <b>Последние отчёты</b>",
        "",
        "Из памяти, без нового запроса к Avito.",
    ]
    table = html.escape("\n".join(_aligned_report_line(row) for row in rows))
    if len(rows) > _HISTORY_RECENT:
        lines.extend(["", "🕒 Свежие:"])
        for row in _rows_by_fetched_at_desc(rows)[:_HISTORY_RECENT]:
            lines.append(f"<b>{_report_summary_line(row)}</b>")
        lines.extend(
            [
                "",
                "📋 Все модели, старые → новые. Нажмите, чтобы раскрыть:",
                f"<blockquote expandable><pre>{table}</pre></blockquote>",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "От старых моделей к новым.",
                f"<pre>{table}</pre>",
            ]
        )
    return "\n".join(lines)


def _rub(value: int) -> str:
    return f"{value:,}".replace(",", " ") + " ₽"


def _fmt_msk(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(_MSK).strftime("%d.%m.%Y %H:%M")


def _seller_label(seller_type: str | None) -> str:
    if seller_type == "private":
        return "частник"
    if seller_type == "business":
        return "магазин"
    return ""


def _seller_line(label: str, summary: PriceSummary) -> str:
    return (
        f"{label}: <b>{_rub(summary.q25_rub)}–{_rub(summary.q75_rub)}</b>, "
        f"медиана <b>{_rub(summary.median_rub)}</b> ({summary.count} шт.)"
    )


def _shop_range_line(shop: ShopPriceRange) -> str:
    if shop.count == 1 or shop.min_rub == shop.max_rub:
        prices = _rub(shop.min_rub)
    else:
        prices = f"{_rub(shop.min_rub)}–{_rub(shop.max_rub)}"
    return f"В магазине ({shop.count} шт): <b>{prices}</b>"


async def load_shop_price_range(query: IphoneMarketQuery) -> ShopPriceRange | None:
    """Справка из каталога б/у; сбой БД не ломает карточку Avito."""
    try:
        return await run_db(get_used_shop_price_range, query.model, query.memory_gb)
    except Exception:
        logger.exception("Failed to load shop price range for market card")
        return None


async def load_market_daily_points(query: IphoneMarketQuery) -> list[dict]:
    """Дневной ряд медианы/вилки; сбой БД не ломает отчёт."""
    try:
        return await run_db(list_market_daily, query.model, query.memory_gb)
    except Exception:
        logger.exception("Failed to load Avito daily history")
        return []


def _short_model_name(display_name: str) -> str:
    name = display_name.strip()
    compact = _IPHONE_TITLE_PREFIX.sub("", name, count=1).strip(" ,")
    return compact or name


def _compact_listing_title(title: str, fallback: str = "") -> str:
    """Убирает бренд iPhone из заголовка объявления — он и так в шапке отчёта."""
    raw = (title or "").strip() or fallback
    compact = _IPHONE_TITLE_PREFIX.sub("", raw, count=1).strip(" ,")
    return compact or fallback or raw


def _avito_url(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if value.startswith("//"):
        return "https:" + value
    if not value.startswith("/"):
        value = "/" + value
    return _AVITO_ORIGIN + value


def _rejection_label(reason: str | None) -> str:
    return {
        "price": "цена вне диапазона",
        "excluded_title": "аксессуар/услуга",
        "material_defect": "существенный дефект",
        "model": "другая модель",
        "memory": "другая/не указана память",
        "new": "новый",
        "outlier": "ценовой выброс",
    }.get(reason or "", "не подошло")


def _listing_line(item: MarketListing, model_label: str) -> str:
    price_bit = _rub(item.price_rub)
    link = _avito_url(item.url)
    if link:
        safe_href = html.escape(link, quote=True)
        price_bit = f'<a href="{safe_href}">{html.escape(price_bit)}</a>'
    title = _compact_listing_title(item.title, model_label)
    if len(title) > 58:
        title = title[:57].rstrip() + "…"
    chunks = [price_bit]
    if title:
        chunks.append(html.escape(title))
    city = (item.city or "").strip()
    if city:
        chunks.append(html.escape(city))
    seller = _seller_label(item.seller_type)
    if seller:
        chunks.append(seller)
    line = " · ".join(chunks)
    if item.included is True:
        return f"<b>{line}</b>"
    if item.included is False:
        return f"{line} · <i>{html.escape(_rejection_label(item.rejection_reason))}</i>"
    return line


def _expandable_listings(title: str, items: list[MarketListing], model_label: str) -> str:
    rows = sorted(items, key=lambda item: item.price_rub)[:_MAX_LIST_LINES]
    body = "\n".join(_listing_line(item, model_label) for item in rows)
    more = ""
    if len(items) > _MAX_LIST_LINES:
        more = f"\n… и ещё {len(items) - _MAX_LIST_LINES}"
    return (
        f"\n{title}, нажмите чтобы раскрыть:\n"
        f"<blockquote expandable>{body}{more}</blockquote>"
    )


def _listings_block(estimate: MarketPriceEstimate) -> str:
    if not estimate.listings:
        return ""
    model_label = _short_model_name(estimate.query.display_name)
    included = [item for item in estimate.listings if item.included is not False]
    rejected = [item for item in estimate.listings if item.included is False]
    parts: list[str] = []
    if included:
        parts.append(_expandable_listings("📋 Учтённые объявления", included, model_label))
    if rejected:
        parts.append(_expandable_listings("📋 Отсеянные объявления", rejected, model_label))
    return "".join(parts)


def _seller_counts_line(estimate: MarketPriceEstimate) -> str:
    known = [item for item in estimate.listings if item.seller_type]
    if not known:
        return ""
    private = sum(item.seller_type == "private" for item in known)
    business = sum(item.seller_type == "business" for item in known)
    unknown = max(0, len(estimate.listings) - len(known))
    parts = [f"частники {private}", f"магазины {business}"]
    if unknown:
        parts.append(f"не указан {unknown}")
    return "Продавцы в выдаче: " + ", ".join(parts)


def format_market_estimate(
    estimate: MarketPriceEstimate,
    shop_range: ShopPriceRange | None = None,
    daily_points: list | None = None,
) -> str:
    lines = [
        f"📊 <b>{html.escape(estimate.query.display_name)}, б/у</b>",
        f"🗺 {html.escape(estimate.region)}",
    ]
    rejected = max(0, estimate.total_count - estimate.used_count)
    if estimate.summary:
        title = "Ориентир по цене" if estimate.is_soft else "Типичный диапазон"
        lines.extend(
            [
                "",
                f"{title}: <b>{_rub(estimate.summary.q25_rub)}–{_rub(estimate.summary.q75_rub)}</b>",
            ]
        )
        if shop_range:
            lines.append(_shop_range_line(shop_range))
        lines.extend(
            [
                f"Медиана: <b>{_rub(estimate.summary.median_rub)}</b>",
                f"Учтено: {estimate.used_count} из {estimate.total_count}",
            ]
        )
        if rejected:
            lines.append(
                f"Отсеяно: {rejected} (другая модель/память, новые, мусор"
                + (", выбросы" if estimate.outlier_count else "")
                + ")"
            )
        if estimate.quote_carried:
            when = ""
            if estimate.quote_as_of:
                when = f" с {_fmt_msk_short(estimate.quote_as_of)}"
            lines.append(
                "⚠️ Сегодня подходящих объявлений почти не было "
                f"({estimate.used_count} из {estimate.total_count}). "
                f"Цифры{when}, не обновлялись."
            )
        elif estimate.is_soft:
            lines.append(
                "⚠️ Мало объявлений для уверенной оценки — цифры ориентировочные."
            )
        if estimate.outlier_count:
            lines.append(f"Ценовых выбросов среди подходящих: {estimate.outlier_count}")
        if estimate.private_summary or estimate.business_summary:
            lines.append("")
            if estimate.private_summary:
                lines.append(_seller_line("Частные продавцы", estimate.private_summary))
            if estimate.business_summary:
                lines.append(_seller_line("Магазины", estimate.business_summary))
    else:
        lines.append("")
        if shop_range:
            lines.append(_shop_range_line(shop_range))
        lines.extend(
            [
                "Пока слишком мало подходящих объявлений, чтобы назвать цену.",
                f"По фильтрам подошло: {estimate.used_count} из {estimate.total_count}.",
            ]
        )
        if rejected:
            lines.append(f"Отсеяно фильтром: {rejected}.")
        lines.append("Попробуйте позже или другую модель/память.")

    seller_counts = _seller_counts_line(estimate)
    if seller_counts:
        lines.extend(["", seller_counts])
    lines.extend(["", f"Данные: {_fmt_msk(estimate.fetched_at)} (МСК)"])
    if estimate.is_stale:
        reason = html.escape(
            estimate.stale_reason
            or estimate.limit_hint
            or "обновление временно недоступно"
        )
        lines.append(f"⚠️ Показан сохранённый результат: {reason}")
    elif estimate.limit_hint:
        lines.append(f"ℹ️ {html.escape(estimate.limit_hint)}")

    text = "\n".join(lines)
    text += format_market_daily_html(daily_points or [])
    text += _listings_block(estimate)
    text += "\n\nМожно отправить следующий запрос или открыть последние отчёты."
    return text


async def _intro_text() -> str:
    cache_hours = max(1, AVITO_MARKET_CACHE_TTL_SEC // 3600)
    from app.integrations.avito.market_account_status import load_market_account_status_html

    status = await load_market_account_status_html(detailed=False)
    status_block = f"\n\n{status}" if status else ""
    return (
        "<b>Оценка рынка Avito</b>\n\n"
        "Напишите модель и память одной строкой.\n"
        "Например: <code>13 мини 128</code> или "
        "<code>15 pro max 256</code>.\n\n"
        f"Регион: {html.escape(AVITO_MARKET_REGION)}. Учитываются б/у телефоны.\n\n"
        f"Повтор того же запроса в течение ~{cache_hours} ч берётся из памяти "
        "(без нового обращения к Avito).\n"
        f"Между <b>новыми</b> поисками пауза {AVITO_MARKET_MIN_REQUEST_INTERVAL_SEC} сек, "
        f"в сутки не больше {AVITO_MARKET_DAILY_REQUEST_LIMIT} свежих запросов — "
        "чтобы не перегружать Avito."
        f"{status_block}"
    )


@router.callback_query(F.data == "avito_market_start")
async def avito_market_start(callback: CallbackQuery, state: FSMContext):
    if not get_settings_service().is_avito_market_enabled():
        await callback.answer("Оценка рынка сейчас выключена", show_alert=True)
        return
    await state.set_state(IphoneMarketPriceState.waiting_for_query)
    # region agent log
    agent_dbg(
        "A",
        "iphone_market_price.py:avito_market_start",
        "show intro, back to products",
        {"back": "avito_market_cancel"},
        run_id="nav",
    )
    # endregion
    await callback.message.edit_text(
        await _intro_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=_cancel_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "avito_market_cancel")
async def avito_market_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "📦 Управление товарами\n\nВыберите действие:",
        reply_markup=get_products_menu_keyboard(),
    )
    await callback.answer()


async def _show_history(callback: CallbackQuery, *, page: int = 0) -> None:
    rows = sort_market_report_rows(
        await get_iphone_market_price_service().list_recent_reports()
    )
    if not rows:
        await callback.message.edit_text(
            "🗂 Пока нет сохранённых отчётов.\nСделайте первый поиск модели.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="avito_market_start")],
                ]
            ),
        )
        await callback.answer()
        return
    last_page = max(0, (len(rows) - 1) // _HISTORY_PER_PAGE)
    page = min(max(0, page), last_page)
    await callback.message.edit_text(
        _history_text(rows),
        parse_mode=ParseMode.HTML,
        reply_markup=_history_keyboard(rows, page=page),
    )
    await callback.answer()


@router.callback_query(F.data == "avito_market_history")
async def avito_market_history(callback: CallbackQuery, state: FSMContext):
    await state.set_state(IphoneMarketPriceState.waiting_for_query)
    # region agent log
    agent_dbg(
        "C",
        "iphone_market_price.py:avito_market_history",
        "open history keep query state",
        {"page": 0, "state": "waiting_for_query"},
        run_id="nav",
    )
    # endregion
    await _show_history(callback, page=0)


@router.callback_query(F.data.startswith("avito_market_hist:"))
async def avito_market_history_page(callback: CallbackQuery, state: FSMContext):
    await state.set_state(IphoneMarketPriceState.waiting_for_query)
    raw = (callback.data or "").split(":", 1)[-1]
    try:
        page = int(raw)
    except ValueError:
        page = 0
    await _show_history(callback, page=page)


@router.callback_query(F.data.startswith("avito_market_open:"))
async def avito_market_open(callback: CallbackQuery, state: FSMContext):
    await state.set_state(IphoneMarketPriceState.waiting_for_query)
    parts = (callback.data or "").split(":")
    history_page: int | None = None
    try:
        snapshot_id = int(parts[1])
        if len(parts) > 2:
            history_page = int(parts[2])
    except (IndexError, ValueError):
        await callback.answer("Некорректный отчёт", show_alert=True)
        return
    # region agent log
    agent_dbg(
        "D",
        "iphone_market_price.py:avito_market_open",
        "open report from history",
        {"snapshot_id": snapshot_id, "history_page": history_page},
        run_id="nav",
    )
    # endregion
    try:
        estimate = await get_iphone_market_price_service().get_cached_report(snapshot_id)
    except MarketTemporarilyUnavailable as exc:
        await callback.answer(str(exc)[:180], show_alert=True)
        return
    except Exception:
        logger.exception("Failed to open cached market report")
        await callback.answer("Не удалось открыть отчёт", show_alert=True)
        return
    await callback.message.edit_text(
        format_market_estimate(
            estimate,
            shop_range=await load_shop_price_range(estimate.query),
            daily_points=await load_market_daily_points(estimate.query),
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=_result_keyboard(
            history_page=history_page if history_page is not None else 0
        ),
        disable_web_page_preview=True,
    )
    await callback.answer()


@router.message(IphoneMarketPriceState.waiting_for_query, F.text)
async def avito_market_query(message: Message, state: FSMContext):
    try:
        query = parse_iphone_market_query(message.text or "")
    except MarketQueryError as exc:
        await message.answer(
            f"{html.escape(str(exc))}\n\nПример: <code>13 мини 128</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=_block_back_keyboard(),
        )
        return

    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    try:
        estimate = await get_iphone_market_price_service().estimate(query)
    except MarketTemporarilyUnavailable as exc:
        await message.answer(
            user_facing_market_error(str(exc)),
            reply_markup=_block_back_keyboard(),
        )
        return
    except Exception:
        logger.exception("Avito market handler failed")
        await message.answer(
            "Не получилось посчитать оценку — сбой на нашей стороне. "
            "Подождите минуту и отправьте запрос ещё раз.",
            reply_markup=_block_back_keyboard(),
        )
        return

    await state.update_data(
        avito_last_model=query.model,
        avito_last_memory=query.memory_gb,
        avito_last_snapshot_id=estimate.snapshot_id,
    )
    await message.answer(
        format_market_estimate(
            estimate,
            shop_range=await load_shop_price_range(query),
            daily_points=await load_market_daily_points(query),
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=_result_keyboard(offer_watchlist=True),
        disable_web_page_preview=True,
    )
