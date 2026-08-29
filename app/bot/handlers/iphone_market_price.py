"""Однострочная оценка рынка б/у iPhone по Avito."""
from __future__ import annotations

import html
import logging
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
from app.integrations.avito.market_search import MarketListing
from app.services.iphone_market_price_service import (
    MarketPriceEstimate,
    MarketTemporarilyUnavailable,
    get_iphone_market_price_service,
    user_facing_market_error,
)
from app.services.settings_service import get_settings_service
from app.utils.iphone_market_query import MarketQueryError, parse_iphone_market_query
from app.utils.price_stats import PriceSummary


logger = logging.getLogger(__name__)
router = Router()

_MSK = ZoneInfo("Europe/Moscow")
_MAX_LIST_LINES = 40
_AVITO_ORIGIN = "https://www.avito.ru"


class IphoneMarketPriceState(StatesGroup):
    waiting_for_query = State()


def _result_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗂 Последние отчёты",
                    callback_data="avito_market_history",
                )
            ],
            [InlineKeyboardButton(text="⬅️ В товары", callback_data="avito_market_cancel")],
        ]
    )


def _cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗂 Последние отчёты",
                    callback_data="avito_market_history",
                )
            ],
            [InlineKeyboardButton(text="⬅️ В товары", callback_data="avito_market_cancel")],
        ]
    )


def _history_keyboard(rows: list[dict]) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    for row in rows:
        snap_id = int(row["id"])
        model = str(row.get("model") or "iPhone")
        memory = row.get("memory_gb")
        mem = "1ТБ" if memory == 1024 else f"{memory}ГБ"
        short = model.replace("iPhone ", "")
        median = row.get("median_rub")
        price = _rub(int(median)) if median is not None else "—"
        label = f"{short} {mem}: {price}"
        if len(label) > 60:
            label = label[:57] + "…"
        buttons.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"avito_market_open:{snap_id}",
                )
            ]
        )
    buttons.append(
        [InlineKeyboardButton(text="🔎 Новый поиск", callback_data="avito_market_start")]
    )
    buttons.append(
        [InlineKeyboardButton(text="⬅️ В товары", callback_data="avito_market_cancel")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


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


def _short_model_name(display_name: str) -> str:
    name = display_name.strip()
    if name.lower().startswith("iphone "):
        return name[7:]
    return name


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


def _listing_line(item: MarketListing, model_label: str) -> str:
    price_bit = _rub(item.price_rub)
    link = _avito_url(item.url)
    if link:
        safe_href = html.escape(link, quote=True)
        price_bit = f'<a href="{safe_href}">{html.escape(price_bit)}</a>'
    chunks = [f"{html.escape(model_label)}: {price_bit}"]
    city = (item.city or "").strip()
    if city:
        chunks.append(html.escape(city))
    seller = _seller_label(item.seller_type)
    if seller:
        chunks.append(seller)
    return " · ".join(chunks)


def _listings_block(estimate: MarketPriceEstimate) -> str:
    if not estimate.listings:
        return ""
    model_label = _short_model_name(estimate.query.display_name)
    rows = sorted(estimate.listings, key=lambda item: item.price_rub)[:_MAX_LIST_LINES]
    body = "\n".join(_listing_line(item, model_label) for item in rows)
    more = ""
    if len(estimate.listings) > _MAX_LIST_LINES:
        more = f"\n… и ещё {len(estimate.listings) - _MAX_LIST_LINES}"
    return (
        "\n📋 Учтённые объявления (дешевле → дороже), нажмите чтобы раскрыть:\n"
        f"<blockquote expandable>{body}{more}</blockquote>"
    )


def format_market_estimate(estimate: MarketPriceEstimate) -> str:
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
        if estimate.is_soft:
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
        lines.extend(
            [
                "",
                "Пока слишком мало подходящих объявлений, чтобы назвать цену.",
                f"По фильтрам подошло: {estimate.used_count} из {estimate.total_count}.",
            ]
        )
        if rejected:
            lines.append(f"Отсеяно фильтром: {rejected}.")
        lines.append("Попробуйте позже или другую модель/память.")

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

    text = "\n".join(lines) + _listings_block(estimate)
    text += "\n\nМожно отправить следующий запрос или открыть последние отчёты."
    return text


def _intro_text() -> str:
    cache_hours = max(1, AVITO_MARKET_CACHE_TTL_SEC // 3600)
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
    )


@router.callback_query(F.data == "avito_market_start")
async def avito_market_start(callback: CallbackQuery, state: FSMContext):
    if not get_settings_service().is_avito_market_enabled():
        await callback.answer("Оценка рынка сейчас выключена", show_alert=True)
        return
    await state.set_state(IphoneMarketPriceState.waiting_for_query)
    await callback.message.edit_text(
        _intro_text(),
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


@router.callback_query(F.data == "avito_market_history")
async def avito_market_history(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    rows = await get_iphone_market_price_service().list_recent_reports(limit=12)
    if not rows:
        await callback.message.edit_text(
            "🗂 Пока нет сохранённых отчётов.\nСделайте первый поиск модели.",
            reply_markup=_cancel_keyboard(),
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        "🗂 <b>Последние отчёты</b>\n\n"
        "Открываются из памяти, без нового запроса к Avito.",
        parse_mode=ParseMode.HTML,
        reply_markup=_history_keyboard(rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("avito_market_open:"))
async def avito_market_open(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    raw_id = (callback.data or "").split(":", 1)[-1]
    try:
        snapshot_id = int(raw_id)
    except ValueError:
        await callback.answer("Некорректный отчёт", show_alert=True)
        return
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
        format_market_estimate(estimate),
        parse_mode=ParseMode.HTML,
        reply_markup=_result_keyboard(),
        disable_web_page_preview=True,
    )
    await callback.answer()


@router.message(IphoneMarketPriceState.waiting_for_query, F.text)
async def avito_market_query(message: Message):
    try:
        query = parse_iphone_market_query(message.text or "")
    except MarketQueryError as exc:
        await message.answer(
            f"{html.escape(str(exc))}\n\nПример: <code>13 мини 128</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=_cancel_keyboard(),
        )
        return

    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    try:
        estimate = await get_iphone_market_price_service().estimate(query)
    except MarketTemporarilyUnavailable as exc:
        await message.answer(
            user_facing_market_error(str(exc)),
            reply_markup=_cancel_keyboard(),
        )
        return
    except Exception:
        logger.exception("Avito market handler failed")
        await message.answer(
            "Сейчас не удалось посчитать оценку. Попробуйте позже.",
            reply_markup=_cancel_keyboard(),
        )
        return

    await message.answer(
        format_market_estimate(estimate),
        parse_mode=ParseMode.HTML,
        reply_markup=_result_keyboard(),
        disable_web_page_preview=True,
    )
