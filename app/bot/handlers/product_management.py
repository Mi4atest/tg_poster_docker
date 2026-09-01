from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, LinkPreviewOptions, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest
from aiogram.enums import ChatAction
import aiohttp
import html
import logging
import re
import math
from datetime import datetime, timezone
from typing import Optional

from app.bot.keyboards.product_keyboard import (
    get_products_menu_keyboard,
    get_product_list_keyboard,
    get_search_results_keyboard,
    get_product_detail_keyboard,
    get_avito_match_keyboard,
    get_product_price_edit_keyboard,
    get_product_status_confirmation_keyboard,
    get_stale_price_detail_keyboard,
    get_stale_price_list_keyboard,
    get_iphone_versions_keyboard,
    get_iphone_models_keyboard,
    get_iphone_model_products_keyboard
)
from app.utils.iphone_parser import group_products_by_model, get_model_display_name
from app.bot.utils.product_list_formatter import format_full_products_list
from app.bot.utils.stale_price_formatter import (
    format_stale_detail_text,
    format_stale_list_text,
    stale_button_label,
)
from app.utils.stale_price_utils import STALE_SORT_PRICE, STALE_SORT_SALE
from app.bot.utils.price_history_formatter import format_price_history_expandable_html
from app.config.settings import MAX_SHARE_FALLBACK_PREFIX, STALE_BADGE_MIN_DAYS
from app.utils.time_msk import format_status_date_msk, to_msk
from app.utils.price_change import (
    PriceChangeInfo,
    analyze_price_change,
    format_price_change_confirm_prompt,
    format_price_change_html_lines,
    price_string_to_int_rub,
)
from app.bot.utils.button_styles import ikb
from app.utils.archive_kind import (
    ARCHIVE_KIND_SALE,
    ARCHIVE_KIND_TRANSFER,
    format_unavailable_confirm_text,
    is_transfer_archive,
    normalize_archive_kind,
)
logger = logging.getLogger(__name__)

router = Router()


def _products_back_from_state(data: dict) -> str:
    return data.get("products_back") or "products_list"


def _archive_products_back_data(
    year: Optional[int] = None,
    month: Optional[int] = None,
    day: Optional[int] = None,
) -> str:
    """Callback для «Назад к списку» из карточки товара в архиве."""
    if day is not None:
        return f"products_archive_day_{year}_{month}_{day}"
    if month is not None:
        return f"products_archive_month_{year}_{month}"
    if year is not None:
        return f"products_archive_year_{year}"
    return "products_archive"


async def _clear_state_keep_products_back(state: FSMContext) -> str:
    """Сброс FSM с сохранением точки возврата в списке б/у."""
    data = await state.get_data()
    back_data = _products_back_from_state(data)
    await state.clear()
    await state.update_data(products_back=back_data)
    return back_data


def _filter_used_products_only(products: list[dict]) -> list[dict]:
    """Оставляет только б/у-ветку, исключая новые и custom-конструктор."""
    new_collection_values = {"iPhone новые", "Airpods", "Apple Watch", "iPad", "custom"}
    return [
        p for p in products
        if (p.get("collection_name") or "").strip() not in new_collection_values
    ]


async def products_menu_markup():
    """Клавиатура меню товаров со счётчиком б/у без ссылки Авито."""
    from app.db.database import SessionLocal, run_db
    from app.db.product_queries import count_unlinked_used_avito_products

    def _count():
        db = SessionLocal()
        try:
            return count_unlinked_used_avito_products(db)
        finally:
            db.close()

    n = 0
    try:
        n = int(await run_db(_count) or 0)
    except Exception:
        logger.exception("Failed to count unlinked Avito used products")
    return get_products_menu_keyboard(avito_unlinked_count=n)


class ProductSearch(StatesGroup):
    waiting_for_query = State()

class ProductPriceEdit(StatesGroup):
    waiting_for_price = State()
    waiting_for_confirm = State()


class ProductAvitoLinkEdit(StatesGroup):
    waiting_for_avito_ref = State()


class ProductUnavailableOptions(StatesGroup):
    waiting_for_payment_method = State()


def _archive_product_title(product: dict) -> str:
    name = product.get("name") or "Без названия"
    if is_transfer_archive(product):
        return f"📦 {name}"
    return name


async def _show_unavailable_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    product_id: int,
    *,
    report_enabled: bool,
    mark_telegram_enabled: bool,
    answer_text: Optional[str] = None,
) -> bool:
    """Перерисовать экран снятия б/у: шапка + клавиатура."""
    await state.update_data(
        product_id=product_id,
        report_enabled=report_enabled,
        mark_telegram_enabled=mark_telegram_enabled,
        archive_kind=ARCHIVE_KIND_SALE,
    )
    product = await get_product_api(product_id)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return False
    await safe_edit_message(
        callback.message,
        format_unavailable_confirm_text(
            product.get("name", "Без названия"),
            product.get("avito_url"),
        ),
        reply_markup=get_product_status_confirmation_keyboard(
            product_id,
            "unavailable",
            report_enabled=report_enabled,
            mark_telegram_enabled=mark_telegram_enabled,
        ),
        parse_mode="HTML",
        disable_link_preview=True,
    )
    if answer_text:
        await callback.answer(answer_text)
    else:
        await callback.answer()
    return True


# Вспомогательная функция для безопасного редактирования сообщений
async def safe_edit_message(message, text, reply_markup=None, parse_mode=None, disable_link_preview=False):
    """Безопасно редактирует сообщение или отправляет новое."""
    try:
        kwargs = {"reply_markup": reply_markup}
        if parse_mode:
            kwargs["parse_mode"] = parse_mode
        if disable_link_preview:
            kwargs["link_preview_options"] = LinkPreviewOptions(is_disabled=True)
        await message.edit_text(text, **kwargs)
        return message
    except TelegramBadRequest as e:
        if "message can't be edited" in str(e):
            kwargs = {"reply_markup": reply_markup}
            if parse_mode:
                kwargs["parse_mode"] = parse_mode
            if disable_link_preview:
                kwargs["link_preview_options"] = LinkPreviewOptions(is_disabled=True)
            return await message.reply(text, **kwargs)
        else:
            raise e
    except Exception as e:
        logger.error(f"Error editing message: {str(e)}")
        kwargs = {"reply_markup": reply_markup}
        if parse_mode:
            kwargs["parse_mode"] = parse_mode
        if disable_link_preview:
            kwargs["link_preview_options"] = LinkPreviewOptions(is_disabled=True)
        return await message.reply(text, **kwargs)


# API client functions
async def get_products_api(status_filter: Optional[str] = None, search: Optional[str] = None, skip: int = 0, limit: int = 500):
    """Получить товары (прямой SQL в отдельном потоке, без HTTP к API)."""
    from app.db.database import run_db
    from app.services.product_ops_service import fetch_products_list

    try:
        return await run_db(
            fetch_products_list,
            status_filter=status_filter,
            search=search,
            skip=skip,
            limit=limit,
        )
    except Exception as e:
        logger.error(f"Error getting products: {str(e)}")
        return [], 0


async def get_all_products_api(
    status_filter: Optional[str] = None,
    search: Optional[str] = None,
    batch_size: int = 1000,
):
    """Получить все товары из API, обходя пагинацию."""
    all_items = []
    skip = 0
    total = 0
    while True:
        items, total = await get_products_api(
            status_filter=status_filter,
            search=search,
            skip=skip,
            limit=batch_size,
        )
        all_items.extend(items)
        if not items or len(all_items) >= total:
            break
        skip += batch_size
    return all_items, total


async def get_product_api(product_id: int):
    """Получить товар по ID (raw SQL в отдельном потоке, без HTTP к API)."""
    import asyncio

    from app.db.product_queries import fetch_product_detail_row_by_id, product_detail_row_to_api_dict

    def _load():
        row = fetch_product_detail_row_by_id(product_id)
        if not row:
            return None
        return product_detail_row_to_api_dict(row)

    try:
        return await asyncio.to_thread(_load)
    except Exception as e:
        logger.error("Error getting product %s: %s", product_id, e)
        return None


def _fetch_post_by_telegram_link(telegram_link: str) -> Optional[dict]:
    """Поля поста по ссылке ТГ (без ORM — иначе блокируется event loop)."""
    if not telegram_link:
        return None
    try:
        from sqlalchemy import text
        from app.db.database import SessionLocal

        db = SessionLocal()
        try:
            row = (
                db.execute(
                    text(
                        "SELECT id, text, photos, videos FROM posts "
                        "WHERE telegram_link = :link LIMIT 1"
                    ),
                    {"link": telegram_link},
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None
        finally:
            db.close()
    except Exception:
        logger.exception("Failed to fetch post by telegram_link")
        return None


def _fetch_post_column(post_id: str, column: str) -> Optional[str]:
    """Одно поле поста через raw SQL (ORM-запросы к Post в asyncio зависают)."""
    if not post_id:
        return None
    allowed = frozenset({"max_link", "instagram_link", "instagram_media_id"})
    if column not in allowed:
        return None
    try:
        from sqlalchemy import text
        from app.db.database import SessionLocal

        db = SessionLocal()
        try:
            row = db.execute(
                text(f"SELECT {column} FROM posts WHERE id = :id LIMIT 1"),
                {"id": post_id},
            ).first()
            if not row:
                return None
            val = (row[0] or "").strip()
            return val or None
        finally:
            db.close()
    except Exception:
        logger.exception("Failed to fetch post.%s for post_id=%s", column, post_id)
        return None


def resolve_product_max_link(product: Optional[dict]) -> Optional[str]:
    """Вернуть max_link товара, при отсутствии — попробовать взять из связанного Post."""
    if not product:
        return None
    direct_link = (product.get("max_link") or "").strip()
    if direct_link:
        return direct_link
    return _fetch_post_column(product.get("post_id"), "max_link")


def resolve_product_instagram_media_id(product: Optional[dict]) -> Optional[str]:
    """Вернуть instagram_media_id товара, при отсутствии — из связанного Post."""
    if not product:
        return None
    direct_id = (product.get("instagram_media_id") or "").strip()
    if direct_id:
        return direct_id
    return _fetch_post_column(product.get("post_id"), "instagram_media_id")


def resolve_product_instagram_link(product: Optional[dict]) -> Optional[str]:
    """Вернуть instagram_link товара, при отсутствии — из связанного Post."""
    if not product:
        return None
    direct_link = (product.get("instagram_link") or "").strip()
    if direct_link:
        return direct_link
    return _fetch_post_column(product.get("post_id"), "instagram_link")


def format_product_platform_links_html(product: dict) -> str:
    """Ссылки на площадки для карточки товара."""
    text = ""
    if product.get("vk_product_link"):
        text += f"\n🔗 <a href='{product['vk_product_link']}'>Ссылка на товар в ВК</a>"
    if product.get("vk_post_link"):
        text += f"\n🔗 <a href='{product['vk_post_link']}'>Ссылка на пост в ленте ВК</a>"
    if product.get("avito_url"):
        text += f"\n🛒 <a href='{product['avito_url']}'>Ссылка на Авито</a>"
    if product.get("telegram_link"):
        text += f"\n🔗 <a href='{product['telegram_link']}'>Ссылка на товар в ТГ</a>"
    if product.get("max_link") or product.get("max_share_url"):
        _max_href = html.escape(
            max_link_href_for_telegram_html(
                product.get("max_link"),
                product.get("max_share_url"),
            ),
            quote=True,
        )
        text += f'\n🔗 <a href="{_max_href}">Ссылка на товар в MAX</a>'
    ig_link = resolve_product_instagram_link(product)
    if ig_link:
        text += f"\n🔗 <a href='{html.escape(ig_link, quote=True)}'>Ссылка на товар в IG</a>"
    return text


def _parse_product_datetime(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def resolve_product_published_at(product: dict) -> Optional[datetime]:
    """Дата/время публикации: enriched published_at или fallback по полям товара."""
    dt = _parse_product_datetime(product.get("published_at"))
    if dt:
        return dt
    candidates: list[datetime] = []
    for key in ("published_telegram_at", "published_vk_at", "created_at"):
        parsed = _parse_product_datetime(product.get(key))
        if parsed:
            candidates.append(parsed)
    return min(candidates) if candidates else None


def _product_sale_end(product: dict) -> datetime:
    status = product.get("status", "active")
    if status == "active":
        return datetime.now(timezone.utc)
    end = _parse_product_datetime(product.get("archived_at")) or _parse_product_datetime(
        product.get("updated_at")
    )
    return end or datetime.now(timezone.utc)


def _days_in_sale(start: datetime, end: datetime) -> int:
    from app.utils.time_msk import to_msk

    d0 = to_msk(start).date()
    d1 = to_msk(end).date()
    return max(1, (d1 - d0).days + 1)


def format_product_published_html(product: dict) -> str:
    """Строка с датой и временем публикации (МСК)."""
    dt = resolve_product_published_at(product)
    if not dt:
        return ""
    return f"📅 с {format_status_date_msk(dt)}\n"


def format_product_status_html(product: dict) -> str:
    """Строка статуса товара; дни в продаже и дата снятия для архива (МСК)."""
    status = product.get("status", "active")
    status_emoji = {"active": "✅", "unavailable": "🚫", "deleted": "🗑️"}
    status_text = {"active": "Активен", "unavailable": "Недоступен", "deleted": "Удален"}
    line = f"\n{status_emoji.get(status, '❓')} Статус: {status_text.get(status, status)}"
    start = resolve_product_published_at(product)

    if status == "unavailable":
        if is_transfer_archive(product):
            line += "\n📦 Архив · не продажа"
        dt = _parse_product_datetime(product.get("archived_at")) or _parse_product_datetime(
            product.get("updated_at")
        )
        if dt:
            line += f"\n<i>с {format_status_date_msk(dt)}"
            if start:
                line += f" · {_days_in_sale(start, dt)} дн. в продаже"
            line += "</i>"
    elif status == "active" and start:
        line += f"\n{_days_in_sale(start, _product_sale_end(product))} дн. в продаже"
    return line + "\n"


async def get_product_price_history_api(product_id: int) -> list:
    """История смены цены товара для карточки."""
    import asyncio

    from app.services.product_ops_service import fetch_product_price_history

    try:
        return await asyncio.to_thread(fetch_product_price_history, product_id)
    except Exception as e:
        logger.error("Error getting price history for product %s: %s", product_id, e)
        return []


def _avito_hint_rub(value: int) -> str:
    return f"{value:,}".replace(",", " ") + " ₽"


def format_avito_market_hint_html(snapshot: Optional[dict]) -> str:
    """Одна строка типичного диапазона Avito из кэша; пусто, если снимка нет."""
    if not snapshot:
        return ""
    try:
        q25 = int(snapshot["q25_rub"])
        q75 = int(snapshot["q75_rub"])
    except (KeyError, TypeError, ValueError):
        return ""
    fetched = snapshot.get("quote_as_of") or snapshot.get("fetched_at")
    date_bit = ""
    if isinstance(fetched, datetime):
        date_bit = f" · {to_msk(fetched).strftime('%d.%m')}"
    if q25 == q75:
        prices = _avito_hint_rub(q25)
    else:
        prices = f"{_avito_hint_rub(q25)}–{_avito_hint_rub(q75)}"
    return f"📊 Avito: {prices}{date_bit}\n"


async def load_avito_market_hint_for_product(product: dict) -> str:
    """Справка из сохранённого отчёта Avito; живой запрос не делается."""
    from app.db.avito_market_queries import get_latest_success_snapshot_for_config
    from app.db.avito_market_watchlist_queries import used_catalog_config
    from app.db.database import run_db

    try:
        matched = used_catalog_config(
            product.get("name") or "",
            product.get("collection_name"),
        )
        if not matched:
            return ""
        model, memory_gb = matched
        snapshot = await run_db(get_latest_success_snapshot_for_config, model, memory_gb)
        return format_avito_market_hint_html(snapshot)
    except Exception:
        logger.exception("Failed to load Avito market hint for product card")
        return ""


def format_product_card_html(
    product: dict,
    price_history: Optional[list] = None,
    avito_hint: Optional[str] = None,
) -> str:
    """Полная карточка б/у-товара для Telegram."""
    text = f"📦 <b>{product.get('name', 'Без названия')}</b>\n\n"
    if product.get("price"):
        text += f"💵 Цена: {product['price']}\n"
    if avito_hint:
        text += avito_hint if avito_hint.endswith("\n") else avito_hint + "\n"
    if price_history is not None:
        history_block = format_price_history_expandable_html(price_history)
        if history_block:
            text += history_block
    if product.get("category_name"):
        text += f"📂 Категория: {product['category_name']}\n"
    if product.get("collection_name"):
        text += f"📁 Подборка: {product['collection_name']}\n"
    text += format_product_published_html(product)
    text += format_product_status_html(product)
    text += format_product_platform_links_html(product)
    return text


async def build_product_card_html(
    product: dict,
    *,
    price_history: Optional[list] = None,
) -> str:
    """Карточка товара с подгрузкой истории цен при необходимости."""
    if price_history is None:
        product_id = product.get("id")
        if product_id:
            price_history = await get_product_price_history_api(product_id)
        else:
            price_history = []
    avito_hint = await load_avito_market_hint_for_product(product)
    return format_product_card_html(
        product,
        price_history=price_history,
        avito_hint=avito_hint,
    )


_MAX_TELEGRAM_CHANNEL_LINK_RE = re.compile(
    r"^max://channel/(?P<chat>[^/]+)/(?P<mid>[^/?#]+)\s*$",
    re.IGNORECASE,
)


def max_link_href_for_telegram_html(
    max_link: Optional[str], max_share_url: Optional[str] = None
) -> str:
    """Кликабельная https-ссылка для Telegram: приоритет max_share_url из API MAX, иначе max.ru/c/... из max://."""
    share = (max_share_url or "").strip()
    if share:
        low = share.lower()
        if low.startswith("http://") or low.startswith("https://"):
            return share
    s = (max_link or "").strip()
    if not s:
        return s
    low = s.lower()
    if low.startswith("http://") or low.startswith("https://"):
        return s
    m = _MAX_TELEGRAM_CHANNEL_LINK_RE.match(s)
    if m:
        prefix = MAX_SHARE_FALLBACK_PREFIX.rstrip("/")
        return f"{prefix}/{m.group('chat')}/{m.group('mid')}"
    return s


async def update_product_status_api(
    product_id: int,
    status: str,
    *,
    sync_platforms: bool = True,
    archive_kind: Optional[str] = None,
):
    """Обновить статус товара (в отдельном потоке). Ответ: {product, status_sync} или None."""
    from app.db.database import run_db
    from app.services.product_ops_service import set_product_status

    try:
        return await run_db(
            set_product_status, product_id, status, sync_platforms, archive_kind
        )
    except Exception as e:
        logger.error("Error updating product status: %s", e)
        return None


def _platform_sync_line_html(label: str, block: dict) -> str:
    """Одна строка статуса для ВК/Авито/БД (HTML)."""
    st = (block or {}).get("status") or "skipped"
    detail = (block or {}).get("detail")
    if st == "ok":
        return f"✅ {html.escape(label)}"
    if st == "pending":
        if detail:
            return f"🕐 {html.escape(label)} <i>({html.escape(str(detail)[:220])})</i>"
        return f"🕐 {html.escape(label)} <i>(в очереди на снятие на Авито)</i>"
    if st == "skipped":
        if detail:
            return f"⏭️ {html.escape(label)} <i>({html.escape(str(detail)[:160])})</i>"
        return f"⏭️ {html.escape(label)} <i>(нет привязки)</i>"
    tail = f" — {html.escape(str(detail)[:220])}" if detail else ""
    return f"❌ {html.escape(label)}{tail}"


async def delete_product_api(product_id: int):
    """Удалить товар (в отдельном потоке): из VK Market и из БД."""
    from app.db.database import run_db
    from app.services.product_ops_service import delete_product

    try:
        return await run_db(delete_product, product_id)
    except Exception as e:
        logger.error(f"Error deleting product: {str(e)}")
        return False


async def update_product_price_api(product_id: int, price: str, *, sync_platforms: bool = True):
    """Сохранить цену товара в БД (в отдельном потоке). Ответ: {product, price_sync} или None.

    Синхронизацию площадок делает PriceSyncService — сюда ходим только с sync_platforms=False.
    """
    from app.db.database import run_db
    from app.services.product_ops_service import save_product_price

    try:
        return await run_db(save_product_price, product_id, price)
    except Exception as e:
        logger.error("Error updating product price: %s", e)
        return None


def format_price_update_user_message(
    price_sync: dict,
    telegram_ok: Optional[bool],
    max_ok: Optional[bool],
    formatted_price: str,
    *,
    price_change: Optional[PriceChangeInfo] = None,
) -> str:
    """Текст итогового сообщения по платформам (HTML)."""

    def line_bot(label: str, ok: Optional[bool]) -> str:
        if ok is True:
            return f"✅ {html.escape(label)}"
        if ok is False:
            return f"❌ {html.escape(label)}"
        return f"⏭️ {html.escape(label)} <i>(нет ссылки)</i>"

    ps = price_sync or {}
    lines: list[str] = []
    if price_change is not None:
        lines.extend(format_price_change_html_lines(price_change))
    lines.extend(
        [
            _platform_sync_line_html("Товары ВК", ps.get("vk") or {}),
            line_bot("Телеграм", telegram_ok),
            _platform_sync_line_html("Авито", ps.get("avito") or {}),
            line_bot("Max", max_ok),
            f"✅ Цена успешно обновлена: {html.escape(formatted_price)}",
        ]
    )
    return "\n".join(lines)


def get_price_change_confirm_keyboard(product_id: int, *, callback_prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, изменить",
                    callback_data=f"{callback_prefix}_confirm_{product_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=f"{callback_prefix}_cancel_{product_id}",
                )
            ],
        ]
    )


async def execute_product_price_update(
    product_id: int,
    formatted_price: str,
    old_price_rub: int,
    *,
    bot,
    chat_id: int,
) -> tuple[Optional[str], Optional[dict]]:
    """Сохранить цену в БД, поставить синхронизацию площадок в очередь; вернуть (summary_html, product)."""
    from app.services.price_sync_service import (
        format_price_saved_immediate_message,
        get_price_sync_service,
        is_new_product_branch,
        is_used_product_branch,
    )

    result = await update_product_price_api(product_id, formatted_price, sync_platforms=False)
    if not result:
        return None, None

    updated_product = result.get("product") or {}

    new_rub = price_string_to_int_rub(formatted_price) or price_string_to_int_rub(
        updated_product.get("price")
    )
    price_change = None
    if old_price_rub and new_rub:
        price_change = analyze_price_change(old_price_rub, new_rub)

    price_value = new_rub or 0

    service = get_price_sync_service()
    await service.enqueue_price_sync(
        bot,
        chat_id=chat_id,
        product_id=product_id,
        product=updated_product,
        formatted_price=formatted_price,
        price_value=price_value,
        refresh_used_list=is_used_product_branch(updated_product),
        refresh_availability_list=is_new_product_branch(updated_product),
    )

    summary = format_price_saved_immediate_message(
        formatted_price,
        price_change=price_change,
    )
    return summary, updated_product


def format_status_unavailable_summary(
    status_sync: dict,
    *,
    telegram_ok: Optional[bool] = None,
    max_ok: Optional[bool] = None,
    instagram_ok: Optional[bool] = None,
    mark_telegram_enabled: bool = True,
    has_telegram_link: bool = False,
    has_max_link: bool = False,
    has_instagram_media: bool = False,
) -> str:
    """Краткий отчёт после «Товар недоступен»: ВК, ТГ, Авито, Max, IG, БД."""

    def line_mark(label: str, ok: Optional[bool], enabled: bool, has_link: bool) -> str:
        if not enabled:
            return f"⏭️ {html.escape(label)} <i>(не выбрано «Пометить ТГ/IG/Max»)</i>"
        if not has_link:
            return f"⏭️ {html.escape(label)} <i>(нет ссылки)</i>"
        if ok is True:
            return f"✅ {html.escape(label)}"
        if ok is False:
            return f"❌ {html.escape(label)}"
        return f"⏭️ {html.escape(label)}"

    ps = status_sync or {}
    lines = [
        "<b>Синхронизация при «недоступен»:</b>",
        _platform_sync_line_html("Товары ВК (скрыт с витрины)", ps.get("vk") or {}),
        line_mark("Телеграм (#неактуально)", telegram_ok, mark_telegram_enabled, has_telegram_link),
        _platform_sync_line_html("Авито (архив объявления)", ps.get("avito") or {}),
        line_mark("Max (#неактуально)", max_ok, mark_telegram_enabled, has_max_link),
        line_mark("Instagram (#неактуально)", instagram_ok, mark_telegram_enabled, has_instagram_media),
        _platform_sync_line_html("База данных", ps.get("database") or {}),
        "✅ Товар отмечен как недоступный",
    ]
    return "\n".join(lines)


async def update_product_avito_link_api(product_id: int, avito_link_or_id: str):
    """Привязать объявление Авито к товару (в отдельном потоке)."""
    from app.db.database import run_db
    from app.services.product_ops_service import set_product_avito_link

    try:
        return await run_db(set_product_avito_link, product_id, avito_link_or_id)
    except Exception as e:
        logger.error("Error updating avito link: %s", e)
        return None


# Handlers
@router.callback_query(F.data == "products_menu")
async def products_menu(callback: CallbackQuery):
    """Показать меню товаров."""
    text = "📦 Управление товарами\n\nВыберите действие:"
    await safe_edit_message(callback.message, text, reply_markup=await products_menu_markup())
    await callback.answer()


@router.callback_query(F.data == "sync_telegram_links")
async def sync_telegram_links(callback: CallbackQuery):
    """Синхронизировать ссылки ТГ в товары и обновить список/новинки в канале."""
    import asyncio

    from app.bot.utils.used_products_channel_updater import update_used_products_list_in_channel
    from app.db.database import SessionLocal
    from app.db.product_queries import sync_telegram_links_to_products

    await callback.answer("Проверяю посты…")
    try:

        def _sync():
            db = SessionLocal()
            try:
                return sync_telegram_links_to_products(db)
            finally:
                db.close()

        posts_count, posts_processed, updated_products, created_missing = await asyncio.to_thread(_sync)
        await safe_edit_message(
            callback.message,
            (
                "🔄 Обновление постов\n\n"
                f"Ссылки синхронизированы ({updated_products} обновлено).\n"
                "Обновляю список и новинки в канале…"
            ),
            reply_markup=await products_menu_markup(),
        )
        channel_ok = await update_used_products_list_in_channel(callback.bot)
        max_ok = False
        try:
            from app.bot.utils.used_products_max_channel_updater import (
                update_used_products_list_in_max_channel,
            )

            max_ok = await update_used_products_list_in_max_channel()
        except Exception as max_err:
            logger.warning("Failed to update used products list in Max channel: %s", max_err)
        channel_line = (
            "✅ Список и новинки в канале обновлены."
            if channel_ok
            else "⚠️ Канал не обновлён (проверьте USED_PRODUCTS_LIST_MESSAGE_IDS в настройках)."
        )
        max_line = (
            "✅ Каталог б/у в Max обновлён."
            if max_ok
            else "⚠️ Каталог Max не обновлён (проверьте ID сообщений Max в настройках)."
        )
        text = (
            "🔄 Обновление постов\n\n"
            f"Проверено постов с ссылкой: {posts_count}\n"
            f"Постов с товарами: {posts_processed}\n"
            f"Создано отсутствующих товаров: {created_missing}\n"
            f"Обновлено ссылок у товаров: {updated_products}\n\n"
            f"{channel_line}\n"
            f"{max_line}"
        )
    except Exception as e:
        logger.exception("sync_telegram_links failed")
        text = f"🔄 Обновление постов\n\n❌ Ошибка: {e}"
    await safe_edit_message(callback.message, text, reply_markup=await products_menu_markup())


@router.callback_query(F.data == "products_list")
async def products_list(callback: CallbackQuery, state: FSMContext):
    """Показать список товаров: формируемый список сверху, inline-клавиатура версий iPhone ниже."""
    await state.update_data(products_back="products_list")
    products, total = await get_products_api(status_filter="active", limit=5000)
    
    # Фильтруем новые и custom-товары (они должны быть только в "Список новых")
    products = _filter_used_products_only(products)
    total = len(products)
    if not products:
        text = "📦 Список товаров пуст."
        await safe_edit_message(callback.message, text, reply_markup=await products_menu_markup())
        await callback.answer("Список товаров пуст")
        return
    
    # Группируем товары по моделям для inline-клавиатуры (iPhone X (1), iPhone 11 (2), ...)
    grouped_products = group_products_by_model(products)
    
    # Формируемый список сверху (полный список товаров с ценами и ссылками)
    text = f"📦 Список товаров ({total}):\n\n"
    text += format_full_products_list(products)
    # Лимит Telegram 4096 символов на сообщение — при превышении разбиваем по границам строк
    max_len = 4090
    if len(text) > max_len:
        parts = []
        rest = text
        while rest:
            if len(rest) <= max_len:
                parts.append(rest)
                break
            chunk = rest[:max_len]
            last_nl = chunk.rfind("\n")
            if last_nl > 100:
                parts.append(rest[: last_nl + 1])
                rest = rest[last_nl + 1 :]
            else:
                parts.append(chunk)
                rest = rest[max_len:]
        # Клавиатуру вешаем на последнее сообщение, чтобы пользователь не искал её в потоке
        keyboard = get_iphone_versions_keyboard(grouped_products)
        await safe_edit_message(
            callback.message,
            parts[0],
            reply_markup=None,
            parse_mode="HTML",
            disable_link_preview=True
        )
        # Отправляем продолжения без цитирования первого сообщения — иначе вверху показывается превью/ссылка на него
        chat_id = callback.message.chat.id
        send_opts = {"parse_mode": "HTML", "link_preview_options": LinkPreviewOptions(is_disabled=True)}
        for extra in parts[1:-1]:
            await callback.bot.send_message(chat_id=chat_id, text=extra, **send_opts)
        await callback.bot.send_message(
            chat_id=chat_id,
            text=parts[-1],
            reply_markup=keyboard,
            **send_opts
        )
    else:
        await safe_edit_message(
            callback.message,
            text,
            reply_markup=get_iphone_versions_keyboard(grouped_products),
            parse_mode="HTML",
            disable_link_preview=True
        )
    await callback.answer()


@router.callback_query(F.data.startswith("iphone_version_"))
async def iphone_version_models(callback: CallbackQuery, state: FSMContext):
    """Показать модели конкретной версии iPhone."""
    try:
        # Извлекаем версию из callback_data
        version = callback.data.replace("iphone_version_", "")
    except (ValueError, IndexError):
        await callback.answer("Ошибка получения версии")
        return

    back_cb = f"iphone_version_{version}"
    await state.update_data(products_back=back_cb)

    # Получаем все активные товары (увеличенный лимит для полного списка)
    all_products, total = await get_products_api(status_filter="active", limit=5000)
    
    # Фильтруем новые и custom-товары (они должны быть только в "Список новых")
    all_products = _filter_used_products_only(all_products)
    
    if not all_products:
        await callback.answer("Товары не найдены")
        return
    
    # Группируем товары по моделям
    grouped_products = group_products_by_model(all_products)
    
    # Спецветка: «Другие» — это плоский список товаров, не разбитых по моделям iPhone.
    # Показываем список напрямую, как для конкретной модели, чтобы пользователь мог
    # взаимодействовать с каждым товаром.
    if version == "Другие":
        other_products = grouped_products.get("Другие", [])
        if not other_products:
            await callback.answer("Товары не найдены")
            return

        text = f"📦 Другие ({len(other_products)})\n\n"
        text += format_full_products_list(other_products)

        await safe_edit_message(
            callback.message,
            text,
            reply_markup=get_iphone_model_products_keyboard(other_products, "Другие", version=None),
            parse_mode="HTML",
            disable_link_preview=True,
        )
        await callback.answer()
        return

    # Получаем модели для выбранной версии
    from app.utils.iphone_parser import get_models_for_version
    version_models = get_models_for_version(version, grouped_products)
    
    if not version_models:
        await callback.answer(f"Модели версии {version} не найдены")
        return
    
    # Собираем все товары выбранной версии и формируем список сверху
    version_products = []
    for model_products in version_models.values():
        version_products.extend(model_products)
    
    # Формируем текст: список товаров версии сверху
    if version == "SE":
        text = f"📱 iPhone SE ({len(version_products)})\n\n"
    elif version == "Air":
        text = f"📱 iPhone Air ({len(version_products)})\n\n"
    elif version == "Другие":
        text = f"📦 Другие товары ({len(version_products)})\n\n"
    else:
        text = f"📱 iPhone {version} ({len(version_products)})\n\n"
    text += format_full_products_list(version_products)
    
    # Показываем модели версии
    await safe_edit_message(
        callback.message,
        text,
        reply_markup=get_iphone_models_keyboard(version_models, version),
        parse_mode="HTML",
        disable_link_preview=True
    )
    await callback.answer()


@router.callback_query(F.data.startswith("iphone_model_"))
async def iphone_model_products(callback: CallbackQuery, state: FSMContext):
    """Показать товары конкретной модели iPhone."""
    try:
        # Извлекаем название модели из callback_data
        # Формат: iphone_model_{model_name} или iphone_model_{model_name}_page_{page}
        data_parts = callback.data.replace("iphone_model_", "").split("_page_")
        model = data_parts[0]
        page = int(data_parts[1]) if len(data_parts) > 1 else 0
    except (ValueError, IndexError):
        await callback.answer("Ошибка получения модели")
        return

    back_cb = f"iphone_model_{model}"
    await state.update_data(products_back=back_cb)

    # Получаем все активные товары (увеличенный лимит для полного списка)
    all_products, total = await get_products_api(status_filter="active", limit=5000)
    
    # Фильтруем новые и custom-товары (они должны быть только в "Список новых")
    all_products = _filter_used_products_only(all_products)
    
    if not all_products:
        await callback.answer("Товары не найдены")
        return
    
    # Группируем товары по моделям
    grouped_products = group_products_by_model(all_products)
    
    # Получаем товары для выбранной модели
    model_products = grouped_products.get(model, [])
    
    if not model_products:
        await callback.answer(f"Товары модели {model} не найдены")
        return
    
    # Определяем версию модели для кнопки "Назад"
    from app.utils.iphone_parser import get_main_iphone_versions
    model_lower = model.lower()
    version = None
    
    if "se" in model_lower:
        version = "SE"
    elif "air" in model_lower:
        version = "Air"
    elif model_lower.startswith("iphone x") and not any(v in model_lower for v in ["11", "12", "13", "14", "15", "16", "17"]):
        version = "X"
    else:
        # Извлекаем номер версии из названия модели
        import re
        match = re.search(r'iphone (\d+)', model_lower)
        if match:
            version = match.group(1)
    
    # Формируем текст: список товаров модели сверху
    display_name = get_model_display_name(model)
    text = f"📱 {display_name} ({len(model_products)})\n\n"
    text += format_full_products_list(model_products)
    
    # Показываем товары с пагинацией
    await safe_edit_message(
        callback.message,
        text,
        reply_markup=get_iphone_model_products_keyboard(model_products, model, version=version, page=page),
        parse_mode="HTML",
        disable_link_preview=True
    )
    await callback.answer()


@router.callback_query(F.data.startswith("products_page_"))
async def products_list_page(callback: CallbackQuery):
    """Обработка пагинации списка товаров (старый формат, оставлен для совместимости)."""
    try:
        page = int(callback.data.replace("products_page_", ""))
    except ValueError:
        await callback.answer("Ошибка пагинации")
        return
    
    products, total = await get_products_api(status_filter="active", skip=page * 10, limit=10)
    
    if not products:
        await callback.answer("Нет товаров на этой странице")
        return
    
    text = f"📦 Список товаров ({total}):\n\n"
    await safe_edit_message(
        callback.message,
        text,
        reply_markup=get_product_list_keyboard(products, page=page)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("product_") & ~F.data.startswith("product_unavailable_") & ~F.data.startswith("product_delete_") & ~F.data.startswith("product_restore_") & ~F.data.startswith("product_confirm_") & ~F.data.startswith("product_price_") & ~F.data.startswith("product_avito_") & ~F.data.startswith("product_toggle_report_") & ~F.data.startswith("product_toggle_mark_tg_") & ~F.data.startswith("product_toggle_archive_kind_") & ~F.data.startswith("product_payment_"))
async def product_detail(callback: CallbackQuery, state: FSMContext):
    """Показать детальную информацию о товаре."""
    try:
        product_id = int(callback.data.replace("product_", ""))
    except ValueError:
        await callback.answer("Ошибка получения товара")
        return

    data = await state.get_data()
    back_data = _products_back_from_state(data)
    await state.update_data(products_back=back_data)

    product = await get_product_api(product_id)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    # Формируем текст с информацией о товаре
    status = product.get("status", "active")
    text = await build_product_card_html(product)

    try:
        await safe_edit_message(
            callback.message,
            text,
            reply_markup=get_product_detail_keyboard(product_id, status, back_data=back_data),
            parse_mode="HTML"
        )
    except Exception:
        logger.exception("product_detail edit failed for product_id=%s", product_id)
        await callback.answer("Не удалось показать карточку товара", show_alert=True)
        return
    await callback.answer()


@router.callback_query(F.data.startswith("product_unavailable_"))
async def product_unavailable(callback: CallbackQuery, state: FSMContext):
    """Показать подтверждение для пометки товара как недоступного."""
    try:
        product_id = int(callback.data.replace("product_unavailable_", ""))
    except ValueError:
        await callback.answer("Ошибка")
        return

    await _show_unavailable_confirm(
        callback,
        state,
        product_id,
        report_enabled=False,
        mark_telegram_enabled=True,
    )


@router.callback_query(F.data.startswith("product_toggle_report_"))
async def product_toggle_report(callback: CallbackQuery, state: FSMContext):
    """Переключить отправку отчета."""
    try:
        product_id = int(callback.data.replace("product_toggle_report_", ""))
    except ValueError:
        await callback.answer("Ошибка получения товара", show_alert=True)
        return

    data = await state.get_data()
    if not data.get("product_id"):
        await state.update_data(
            product_id=product_id,
            report_enabled=False,
            mark_telegram_enabled=True,
            archive_kind=ARCHIVE_KIND_SALE,
        )
        data = await state.get_data()

    new_report = not data.get("report_enabled", False)
    await _show_unavailable_confirm(
        callback,
        state,
        product_id,
        report_enabled=new_report,
        mark_telegram_enabled=data.get("mark_telegram_enabled", True),
    )


@router.callback_query(F.data.startswith("product_toggle_mark_tg_"))
async def product_toggle_mark_tg(callback: CallbackQuery, state: FSMContext):
    """Переключить пометку поста в Telegram."""
    try:
        product_id = int(callback.data.replace("product_toggle_mark_tg_", ""))
    except ValueError:
        await callback.answer("Ошибка получения товара", show_alert=True)
        return

    data = await state.get_data()
    if not data.get("product_id"):
        await state.update_data(
            product_id=product_id,
            report_enabled=False,
            mark_telegram_enabled=True,
            archive_kind=ARCHIVE_KIND_SALE,
        )
        data = await state.get_data()

    new_mark_tg = not data.get("mark_telegram_enabled", True)
    await _show_unavailable_confirm(
        callback,
        state,
        product_id,
        report_enabled=data.get("report_enabled", False),
        mark_telegram_enabled=new_mark_tg,
    )


@router.callback_query(F.data.startswith("product_toggle_archive_kind_"))
async def product_toggle_archive_kind_stale(callback: CallbackQuery, state: FSMContext):
    """Старые сообщения с тумблером: показать актуальный экран с двумя кнопками."""
    try:
        product_id = int(callback.data.replace("product_toggle_archive_kind_", ""))
    except ValueError:
        await callback.answer("Ошибка получения товара", show_alert=True)
        return

    data = await state.get_data()
    await _show_unavailable_confirm(
        callback,
        state,
        product_id,
        report_enabled=bool(data.get("report_enabled", False)),
        mark_telegram_enabled=data.get("mark_telegram_enabled", True),
        answer_text="Выберите продажу или перемещение",
    )


@router.callback_query(F.data.startswith("product_restore_"))
async def product_restore(callback: CallbackQuery):
    """Показать подтверждение для восстановления товара."""
    try:
        product_id = int(callback.data.replace("product_restore_", ""))
    except ValueError:
        await callback.answer("Ошибка")
        return
    
    product = await get_product_api(product_id)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    
    text = f"✅ Восстановить товар?\n\n📦 {product.get('name', 'Без названия')}\n\nТовар снова появится в каталоге."
    await safe_edit_message(
        callback.message,
        text,
        reply_markup=get_product_status_confirmation_keyboard(product_id, "restore")
    )
    await callback.answer()


@router.callback_query(F.data.startswith("product_delete_"))
async def product_delete(callback: CallbackQuery):
    """Показать подтверждение для удаления товара."""
    try:
        product_id = int(callback.data.replace("product_delete_", ""))
    except ValueError:
        await callback.answer("Ошибка")
        return
    
    product = await get_product_api(product_id)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    
    text = f"🗑️ Удалить товар?\n\n📦 {product.get('name', 'Без названия')}\n\nТовар будет удален из ВК и базы данных. Это действие нельзя отменить!"
    await safe_edit_message(
        callback.message,
        text,
        reply_markup=get_product_status_confirmation_keyboard(product_id, "delete")
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^product_price_\d+$"))
async def product_price_edit(callback: CallbackQuery, state: FSMContext):
    """Начать редактирование цены товара (только product_price_{id})."""
    tail = callback.data.replace("product_price_", "")
    try:
        product_id = int(tail)
    except ValueError:
        await callback.answer("Ошибка получения товара")
        return
    
    product = await get_product_api(product_id)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    
    current_price = product.get('price', 'не указана')
    text = f"💰 <b>Изменение цены товара</b>\n\n"
    text += f"📦 {product.get('name', 'Без названия')}\n\n"
    text += f"Текущая цена: {current_price}\n\n"
    text += "Введите новую цену (число, можно с пробелами):"
    
    old_rub = price_string_to_int_rub(product.get("price")) or 0
    await state.update_data(product_id=product_id, price_old_rub=old_rub)
    await state.set_state(ProductPriceEdit.waiting_for_price)
    
    await safe_edit_message(
        callback.message,
        text,
        reply_markup=get_product_price_edit_keyboard(product_id),
        parse_mode="HTML"
    )
    await callback.answer()


async def _return_to_used_product_detail(
    callback: CallbackQuery,
    product_id: int,
    back_data: str = "products_list",
) -> None:
    """Вернуться в карточку б/у-товара (после отмены/назад из смены цены)."""
    product = await get_product_api(product_id)
    if not product:
        return

    status = product.get("status", "active")
    text = await build_product_card_html(product)

    await safe_edit_message(
        callback.message,
        text,
        reply_markup=get_product_detail_keyboard(product_id, status, back_data=back_data),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("product_price_back_"))
async def product_price_back(callback: CallbackQuery, state: FSMContext):
    """Назад с экрана ввода цены — в карточку товара без сохранения."""
    try:
        product_id = int(callback.data.replace("product_price_back_", ""))
    except ValueError:
        await callback.answer("Ошибка")
        return

    back_data = await _clear_state_keep_products_back(state)
    await callback.answer()
    await _return_to_used_product_detail(callback, product_id, back_data)


@router.message(ProductPriceEdit.waiting_for_price)
async def process_product_price(message: Message, state: FSMContext):
    """Обработать введенную цену."""
    try:
        price_text = message.text.strip()
        
        # Извлекаем число из текста (убираем пробелы, запятые, буквы)
        import re
        # Убираем все кроме цифр, пробелов, точек и запятых
        price_clean = re.sub(r'[^\d\s.,]', '', price_text)
        # Убираем пробелы
        price_clean = price_clean.replace(' ', '').replace(',', '.')
        
        if not price_clean or not price_clean.replace('.', '').isdigit():
            await message.answer("❌ Пожалуйста, введите корректное число для цены.")
            return
        
        # Преобразуем в число и обратно в строку для форматирования
        try:
            price_value = float(price_clean)
            if price_value <= 0:
                await message.answer("❌ Цена должна быть больше нуля.")
                return
        except ValueError:
            await message.answer("❌ Пожалуйста, введите корректное число для цены.")
            return
        
        # Получаем product_id из состояния
        data = await state.get_data()
        product_id = data.get('product_id')
        
        if not product_id:
            await message.answer("❌ Ошибка: не найден ID товара.")
            await state.clear()
            return
        
        # Форматируем цену (добавляем ₽ если его нет)
        formatted_price = price_text
        if '₽' not in formatted_price and 'руб' not in formatted_price.lower():
            formatted_price = f"{price_text}₽"
        
        old_price_rub = int(data.get("price_old_rub") or 0)
        new_rub = int(price_value)
        price_change = analyze_price_change(old_price_rub, new_rub) if old_price_rub else None

        if price_change and price_change.needs_confirm:
            product = await get_product_api(product_id)
            product_name = (product or {}).get("name", "Без названия")
            await state.update_data(pending_formatted_price=formatted_price)
            await state.set_state(ProductPriceEdit.waiting_for_confirm)
            await message.answer(
                format_price_change_confirm_prompt(product_name, price_change),
                parse_mode="HTML",
                reply_markup=get_price_change_confirm_keyboard(
                    product_id, callback_prefix="product_price"
                ),
            )
            return

        pending = None
        try:
            await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        except Exception:
            pass

        summary, updated_product = await execute_product_price_update(
            product_id, formatted_price, old_price_rub,
            bot=message.bot,
            chat_id=message.chat.id,
        )

        if not summary:
            await message.answer("❌ Ошибка при обновлении цены. Попробуйте еще раз.")
        else:
            await message.answer(summary, parse_mode="HTML")

            if updated_product:
                back_data = _products_back_from_state(data)
                await _after_product_price_updated(
                    message, product_id, updated_product, back_data=back_data
                )

        await _clear_state_keep_products_back(state)
    except Exception as e:
        logger.error(f"Error processing product price: {str(e)}")
        await message.answer("❌ Произошла ошибка при обработке цены.")
        await _clear_state_keep_products_back(state)


async def _after_product_price_updated(
    message: Message,
    product_id: int,
    updated_product: dict,
    back_data: str = "products_list",
) -> None:
    """Карточка товара и обновление списка б/у в канале после смены цены."""
    status = updated_product.get("status", "active")
    text = await build_product_card_html(updated_product)
    from app.bot.keyboards.product_keyboard import get_product_detail_keyboard

    await message.answer(
        text,
        reply_markup=get_product_detail_keyboard(product_id, status, back_data=back_data),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("product_price_confirm_"))
async def product_price_confirm(callback: CallbackQuery, state: FSMContext):
    """Подтверждение сильного изменения цены (б/у товары)."""
    try:
        product_id = int(callback.data.replace("product_price_confirm_", ""))
    except ValueError:
        await callback.answer("Ошибка")
        return

    data = await state.get_data()
    if data.get("product_id") != product_id:
        await callback.answer("Сессия устарела. Начните изменение цены заново.", show_alert=True)
        return

    formatted_price = data.get("pending_formatted_price")
    old_price_rub = int(data.get("price_old_rub") or 0)
    if not formatted_price:
        await callback.answer("Нет сохранённой цены", show_alert=True)
        await state.clear()
        return

    await callback.answer()
    try:
        await callback.message.bot.send_chat_action(callback.message.chat.id, ChatAction.TYPING)
    except Exception:
        pass

    summary, updated_product = await execute_product_price_update(
        product_id, formatted_price, old_price_rub,
        bot=callback.message.bot,
        chat_id=callback.message.chat.id,
    )

    back_data = await _clear_state_keep_products_back(state)
    if not summary:
        await callback.message.answer("❌ Ошибка при обновлении цены. Попробуйте еще раз.")
        return

    await callback.message.answer(summary, parse_mode="HTML")
    if updated_product:
        await _after_product_price_updated(
            callback.message, product_id, updated_product, back_data=back_data
        )


@router.callback_query(F.data.startswith("product_price_cancel_"))
async def product_price_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена сильного изменения цены — возврат в карточку товара."""
    try:
        product_id = int(callback.data.replace("product_price_cancel_", ""))
    except ValueError:
        await callback.answer("Ошибка")
        return

    back_data = await _clear_state_keep_products_back(state)
    await callback.answer("Изменение отменено")
    await _return_to_used_product_detail(callback, product_id, back_data)


def _avito_paste_prompt_html(product: dict) -> str:
    cur = product.get("avito_url") or product.get("avito_item_id") or "не привязано"
    return (
        f"🛒 <b>Привязка Авито</b>\n\n"
        f"📦 {html.escape(product.get('name', 'Без названия'))}\n\n"
        f"Текущее: {html.escape(str(cur))}\n\n"
        "Отправьте <b>ссылку на объявление</b> или только <b>числовой id</b> (цифры из URL).\n"
        "Лучше всего: откройте объявление в <b>браузере</b> и скопируйте адрес "
        "(в конце часто <code>…_1234567890</code> или сегмент <code>/1234567890</code>).\n"
        "Ссылка «Поделиться» из приложения подойдёт, если в тексте есть этот id; "
        "короткая ссылка без цифр — не сработает."
    )


def _avito_paste_keyboard(product_id: int, *, in_queue: bool, back_data: str) -> InlineKeyboardMarkup:
    rows = []
    if in_queue:
        rows.append(
            [InlineKeyboardButton(text="Пропустить", callback_data=f"avm_skip_{product_id}")]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back_data)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_avito_match_html(
    product: dict,
    candidates: list,
    *,
    queue_index: Optional[int] = None,
    queue_total: Optional[int] = None,
    api_error: Optional[str] = None,
) -> str:
    lines = ["🛒 <b>Привязка Авито</b>"]
    if queue_total:
        pos = (queue_index or 0) + 1
        lines.append(f"<i>{pos} из {queue_total}</i>")
    lines.append("")
    lines.append(f"📦 {html.escape(product.get('name') or 'Без названия')}")
    if product.get("price"):
        lines.append(f"💵 {html.escape(str(product['price']))}")
    if api_error:
        lines.append("")
        lines.append(html.escape(api_error))
    elif not candidates:
        lines.append("")
        lines.append("Среди свободных объявлений кабинета не нашлось совпадения по модели, памяти и цене.")
        lines.append("Вставьте ссылку вручную или пропустите.")
    elif len(candidates) == 1:
        cand = candidates[0]
        lines.append("")
        lines.append("Похоже на это объявление:")
        lines.append(_format_candidate_line(cand))
        lines.append("")
        lines.append("Нажмите «Это оно», если верно.")
    else:
        lines.append("")
        lines.append("Несколько похожих объявлений — выберите нужное:")
        for i, cand in enumerate(candidates, 1):
            lines.append(f"{i}. {_format_candidate_line(cand)}")
    return "\n".join(lines)


def _format_candidate_line(cand: dict) -> str:
    title = html.escape(str(cand.get("title") or "объявление"))
    price = cand.get("price_rub")
    price_bit = f"{price}₽ · " if price else ""
    url = cand.get("url") or f"https://www.avito.ru/{cand.get('item_id')}"
    href = html.escape(str(url), quote=True)
    return f'{price_bit}<a href="{href}">{title}</a>'


def _listings_to_cand_dicts(listings) -> list[dict]:
    return [
        {
            "item_id": item.item_id,
            "title": item.title,
            "price_rub": item.price_rub,
            "url": item.url,
        }
        for item in listings
    ]


async def _load_avito_match_pool():
    from app.db.database import SessionLocal, run_db
    from app.db.product_queries import fetch_linked_avito_item_ids
    from app.integrations.avito import actions as avito_actions
    from app.services.avito_listing_match import listings_from_api_rows

    rows = await avito_actions.fetch_active_listings()
    listings = listings_from_api_rows(rows)

    def _occupied():
        db = SessionLocal()
        try:
            return fetch_linked_avito_item_ids(db)
        finally:
            db.close()

    occupied = await run_db(_occupied)
    return listings, occupied


async def _show_avito_match_for_product(
    target_message,
    state: FSMContext,
    product: dict,
    *,
    in_queue: bool,
    back_data: str,
    queue_index: Optional[int] = None,
    queue_total: Optional[int] = None,
) -> None:
    from app.services.avito_listing_match import match_product_to_listings

    product_id = int(product["id"])
    api_error = None
    candidates: list[dict] = []
    already = str(product.get("avito_item_id") or "").strip()
    if already:
        await _show_avito_paste(
            target_message,
            state,
            product,
            in_queue=in_queue,
            back_data=back_data,
        )
        return
    try:
        listings, occupied = await _load_avito_match_pool()
        matched = match_product_to_listings(
            product, listings, occupied_item_ids=occupied
        )
        candidates = _listings_to_cand_dicts(matched)
    except Exception as exc:
        logger.exception("Avito match listings failed product_id=%s", product_id)
        api_error = "Не удалось получить список объявлений Авито. Можно вставить ссылку вручную."
        _ = exc

    await state.update_data(
        product_id=product_id,
        avito_match_in_queue=in_queue,
        avito_match_back=back_data,
    )
    if not candidates:
        await _show_avito_paste(
            target_message,
            state,
            product,
            in_queue=in_queue,
            back_data=back_data,
            extra_html=(
                html.escape(api_error) + "\n\n"
                if api_error
                else "Среди свободных объявлений не нашлось совпадения.\n\n"
            ),
        )
        return

    await state.set_state(None)
    text = _format_avito_match_html(
        product,
        candidates,
        queue_index=queue_index,
        queue_total=queue_total,
    )
    await safe_edit_message(
        target_message,
        text,
        reply_markup=get_avito_match_keyboard(
            product_id,
            candidates,
            in_queue=in_queue,
            back_data=back_data,
        ),
        parse_mode="HTML",
        disable_link_preview=True,
    )


async def _show_avito_paste(
    target_message,
    state: FSMContext,
    product: dict,
    *,
    in_queue: bool,
    back_data: str,
    extra_html: str = "",
) -> None:
    product_id = int(product["id"])
    await state.update_data(
        product_id=product_id,
        avito_match_in_queue=in_queue,
        avito_match_back=back_data,
    )
    await state.set_state(ProductAvitoLinkEdit.waiting_for_avito_ref)
    text = extra_html + _avito_paste_prompt_html(product)
    await safe_edit_message(
        target_message,
        text,
        reply_markup=_avito_paste_keyboard(
            product_id, in_queue=in_queue, back_data=back_data
        ),
        parse_mode="HTML",
    )


async def _advance_avito_match_queue(target_message, state: FSMContext) -> bool:
    """Показать следующий товар очереди. False — очередь кончилась."""
    data = await state.get_data()
    ids = list(data.get("avito_match_ids") or [])
    index = int(data.get("avito_match_index") or 0) + 1
    while index < len(ids):
        product = await get_product_api(int(ids[index]))
        if product and not str(product.get("avito_item_id") or "").strip():
            await state.update_data(avito_match_index=index)
            await _show_avito_match_for_product(
                target_message,
                state,
                product,
                in_queue=True,
                back_data="products_menu",
                queue_index=index,
                queue_total=len(ids),
            )
            return True
        index += 1
    return False


async def _finish_avito_match_queue(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(avito_match_ids=None, avito_match_index=None, avito_match_in_queue=False)
    await state.set_state(None)
    await safe_edit_message(
        callback.message,
        "📦 Управление товарами\n\nВыберите действие:",
        reply_markup=await products_menu_markup(),
    )


@router.callback_query(F.data == "avito_match_queue")
async def avito_match_queue_start(callback: CallbackQuery, state: FSMContext):
    from app.db.database import SessionLocal, run_db
    from app.db.product_queries import fetch_unlinked_used_avito_products

    def _load():
        db = SessionLocal()
        try:
            return fetch_unlinked_used_avito_products(db)
        finally:
            db.close()

    try:
        rows = await run_db(_load)
    except Exception:
        logger.exception("Failed to list unlinked used Avito products")
        await callback.answer("Не удалось загрузить список", show_alert=True)
        return
    await callback.answer()
    ids = [int(r["id"]) for r in rows if r.get("id")]
    if not ids:
        await safe_edit_message(
            callback.message,
            "📦 Управление товарами\n\nНет б/у без ссылки Авито.",
            reply_markup=await products_menu_markup(),
        )
        return
    await state.update_data(
        avito_match_ids=ids,
        avito_match_index=0,
        avito_match_in_queue=True,
        avito_match_back="products_menu",
        products_back="products_menu",
    )
    product = await get_product_api(ids[0])
    if not product:
        await safe_edit_message(
            callback.message,
            "📦 Управление товарами\n\nВыберите действие:",
            reply_markup=await products_menu_markup(),
        )
        return
    await _show_avito_match_for_product(
        callback.message,
        state,
        product,
        in_queue=True,
        back_data="products_menu",
        queue_index=0,
        queue_total=len(ids),
    )


@router.callback_query(F.data.startswith("product_avito_link_"))
async def product_avito_link_start(callback: CallbackQuery, state: FSMContext):
    try:
        product_id = int(callback.data.replace("product_avito_link_", ""))
    except ValueError:
        await callback.answer("Ошибка")
        return
    product = await get_product_api(product_id)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    back_data = _products_back_from_state(await state.get_data())
    await state.update_data(
        avito_match_ids=None,
        avito_match_index=None,
        avito_match_in_queue=False,
        products_back=back_data,
        avito_match_back=f"product_{product_id}",
    )
    await callback.answer()
    await _show_avito_match_for_product(
        callback.message,
        state,
        product,
        in_queue=False,
        back_data=f"product_{product_id}",
    )


@router.callback_query(F.data.startswith("avm_ok_"))
async def avito_match_confirm(callback: CallbackQuery, state: FSMContext):
    try:
        rest = callback.data[len("avm_ok_") :]
        product_id_s, item_id_s = rest.split("_", 1)
        product_id = int(product_id_s)
        item_id = int(item_id_s)
    except ValueError:
        await callback.answer("Ошибка")
        return
    result = await update_product_avito_link_api(product_id, str(item_id))
    if not result:
        await callback.answer("Не удалось сохранить", show_alert=True)
        return
    await callback.answer("Привязано")
    data = await state.get_data()
    in_queue = bool(data.get("avito_match_in_queue"))
    if in_queue:
        moved = await _advance_avito_match_queue(callback.message, state)
        if not moved:
            await _finish_avito_match_queue(callback, state)
        return
    back_data = data.get("avito_match_back") or _products_back_from_state(data)
    await state.set_state(None)
    await _return_to_used_product_detail(callback, product_id, back_data)


@router.callback_query(F.data.startswith("avm_none_"))
async def avito_match_none(callback: CallbackQuery, state: FSMContext):
    try:
        product_id = int(callback.data.replace("avm_none_", ""))
    except ValueError:
        await callback.answer("Ошибка")
        return
    product = await get_product_api(product_id)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    data = await state.get_data()
    in_queue = bool(data.get("avito_match_in_queue"))
    back_data = data.get("avito_match_back") or (
        "products_menu" if in_queue else _products_back_from_state(data)
    )
    await callback.answer()
    await _show_avito_paste(
        callback.message,
        state,
        product,
        in_queue=in_queue,
        back_data=back_data,
    )


@router.callback_query(F.data.startswith("avm_paste_"))
async def avito_match_paste(callback: CallbackQuery, state: FSMContext):
    try:
        product_id = int(callback.data.replace("avm_paste_", ""))
    except ValueError:
        await callback.answer("Ошибка")
        return
    product = await get_product_api(product_id)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    data = await state.get_data()
    in_queue = bool(data.get("avito_match_in_queue"))
    back_data = data.get("avito_match_back") or (
        "products_menu" if in_queue else _products_back_from_state(data)
    )
    await callback.answer()
    await _show_avito_paste(
        callback.message,
        state,
        product,
        in_queue=in_queue,
        back_data=back_data,
    )


@router.callback_query(F.data.startswith("avm_skip_"))
async def avito_match_skip(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Пропущено")
    data = await state.get_data()
    if not data.get("avito_match_in_queue"):
        product_id = data.get("product_id")
        back_data = data.get("avito_match_back") or _products_back_from_state(data)
        await state.set_state(None)
        if product_id:
            await _return_to_used_product_detail(callback, int(product_id), back_data)
        return
    moved = await _advance_avito_match_queue(callback.message, state)
    if not moved:
        await _finish_avito_match_queue(callback, state)


@router.message(ProductAvitoLinkEdit.waiting_for_avito_ref)
async def product_avito_link_process(message: Message, state: FSMContext):
    data = await state.get_data()
    product_id = data.get("product_id")
    if not product_id:
        await state.clear()
        await message.answer("❌ Сессия устарела.")
        return
    ref = (message.text or "").strip()
    if not ref:
        await message.answer("❌ Введите ссылку или id.")
        return
    result = await update_product_avito_link_api(product_id, ref)
    if not result:
        await message.answer(
            "❌ Не удалось распознать объявление. Пришлите полный URL из браузера "
            "или только id (обычно 9–10 цифр в конце адреса). "
            "Если из приложения пришла короткая ссылка без цифр — откройте объявление в браузере и скопируйте урл."
        )
        return
    await message.answer("✅ Объявление Авито привязано.")
    in_queue = bool(data.get("avito_match_in_queue"))
    if in_queue:
        moved = await _advance_avito_match_queue(message, state)
        if not moved:
            await state.update_data(
                avito_match_ids=None, avito_match_index=None, avito_match_in_queue=False
            )
            await state.set_state(None)
            await message.answer(
                "📦 Управление товарами\n\nВыберите действие:",
                reply_markup=await products_menu_markup(),
            )
        return
    back_data = await _clear_state_keep_products_back(state)
    status = result.get("status", "active")
    text = f"📦 <b>{result.get('name', 'Без названия')}</b>\n\n"
    if result.get("price"):
        text += f"💵 Цена: {result['price']}\n"
    if result.get("avito_url"):
        text += f"\n🛒 <a href='{result['avito_url']}'>Авито</a>"
    if result.get("vk_product_link"):
        text += f"\n🔗 <a href='{result['vk_product_link']}'>ВК</a>"
    await message.answer(
        text,
        reply_markup=get_product_detail_keyboard(product_id, status, back_data=back_data),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("product_confirm_"))
async def product_confirm_action(callback: CallbackQuery, state: FSMContext):
    """Подтверждение действия с товаром."""
    try:
        parts = callback.data.replace("product_confirm_", "").split("_")
        action = parts[0]
        product_id = int(parts[1])
        # Извлекаем флаги переключателей (если есть)
        report_enabled = len(parts) > 2 and parts[2] == "1"
        mark_telegram_enabled = len(parts) > 3 and parts[3] == "1"
        archive_kind = (
            ARCHIVE_KIND_TRANSFER
            if len(parts) > 4 and parts[4] == "1"
            else ARCHIVE_KIND_SALE
        )
    except (ValueError, IndexError):
        await callback.answer("Ошибка")
        return
    
    product = await get_product_api(product_id)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    sdata = await state.get_data()
    back_data = _products_back_from_state(sdata)
    transfer_skip_report = False
    if archive_kind == ARCHIVE_KIND_TRANSFER:
        transfer_skip_report = report_enabled
        report_enabled = False

    if action == "unavailable":
        # Если включен отчет, показываем выбор способа оплаты
        if report_enabled:
            await state.update_data(
                product_id=product_id,
                report_enabled=True,
                mark_telegram_enabled=mark_telegram_enabled,
                archive_kind=ARCHIVE_KIND_SALE,
            )
            await state.set_state(ProductUnavailableOptions.waiting_for_payment_method)
            
            text = f"💰 Выберите способ оплаты:\n\n📦 {product.get('name', 'Без названия')}"
            from app.bot.keyboards.product_keyboard import get_payment_method_keyboard
            price_str = product.get('price', '')
            await safe_edit_message(
                callback.message,
                text,
                reply_markup=get_payment_method_keyboard(product_id, price_str)
            )
            await callback.answer()
            return
        
        # Если отчет не включен, сразу помечаем как недоступный
        await process_product_unavailable(
            product_id,
            None,
            mark_telegram_enabled,
            callback,
            state,
            archive_kind=archive_kind,
            answer_text="В отчёт не уйдёт" if transfer_skip_report else None,
        )
        return
    
    elif action == "restore":
        result = await update_product_status_api(product_id, "active")
        if not result:
            await callback.answer("❌ Ошибка восстановления", show_alert=True)
            return
        updated_product = result.get("product") or await get_product_api(product_id)
        if updated_product and updated_product.get("telegram_link"):
            await remove_telegram_post_unavailable(updated_product.get("telegram_link"))
        resolved_max_link = resolve_product_max_link(updated_product)
        if resolved_max_link:
            await remove_max_post_unavailable(resolved_max_link)
        if updated_product and resolve_product_instagram_media_id(updated_product):
            await remove_instagram_post_unavailable(updated_product)
        if updated_product and resolve_product_vk_post_id(updated_product):
            await remove_vk_post_unavailable(updated_product)

        await callback.answer("✅ Товар восстановлен")
        updated_product = await get_product_api(product_id)
        if updated_product:
            status = updated_product.get("status", "active")
            text = await build_product_card_html(updated_product)
            await safe_edit_message(
                callback.message,
                text,
                reply_markup=get_product_detail_keyboard(
                    product_id, status, back_data=back_data
                ),
                parse_mode="HTML",
            )
        try:
            from app.bot.utils.used_products_lists import refresh_used_products_catalogs

            await refresh_used_products_catalogs(callback.bot)
        except Exception as upd_err:
            logger.warning("Failed to update used products list in channel: %s", upd_err)
    
    elif action == "delete":
        result = await delete_product_api(product_id)
        if result:
            await callback.answer("✅ Товар удален")
            text = "✅ Товар успешно удален."
            await safe_edit_message(
                callback.message,
                text,
                reply_markup=await products_menu_markup()
            )
            try:
                from app.bot.utils.used_products_lists import refresh_used_products_catalogs
                await refresh_used_products_catalogs(callback.bot)
            except Exception as upd_err:
                logger.warning("Failed to update used products list in channel: %s", upd_err)
        else:
            await callback.answer("❌ Ошибка при удалении", show_alert=True)


SEARCH_PER_PAGE = 10
STALE_LIST_MAX_LEN = 4090


async def _fetch_stale_price_data(
    sort_mode: str = STALE_SORT_PRICE,
) -> tuple[list[dict], int]:
    from app.db.database import run_db
    from app.services.product_ops_service import fetch_stale_price_list

    return await run_db(fetch_stale_price_list, STALE_BADGE_MIN_DAYS, sort_mode=sort_mode)


async def _fetch_stale_price_detail(product_id: int) -> tuple[Optional[dict], list[dict]]:
    from app.db.database import run_db
    from app.services.product_ops_service import fetch_stale_price_detail

    return await run_db(fetch_stale_price_detail, product_id)


async def _send_long_html_message(message, text: str, reply_markup, *, bot, chat_id: int):
    """Отправить длинный HTML-текст с клавиатурой на последнем фрагменте."""
    send_opts = {"parse_mode": "HTML", "link_preview_options": LinkPreviewOptions(is_disabled=True)}
    if len(text) <= STALE_LIST_MAX_LEN:
        await safe_edit_message(
            message,
            text,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_link_preview=True,
        )
        return
    parts = []
    rest = text
    while rest:
        if len(rest) <= STALE_LIST_MAX_LEN:
            parts.append(rest)
            break
        chunk = rest[:STALE_LIST_MAX_LEN]
        last_nl = chunk.rfind("\n")
        if last_nl > 100:
            parts.append(rest[: last_nl + 1])
            rest = rest[last_nl + 1 :]
        else:
            parts.append(chunk)
            rest = rest[STALE_LIST_MAX_LEN:]
    await safe_edit_message(
        message,
        parts[0],
        reply_markup=None,
        parse_mode="HTML",
        disable_link_preview=True,
    )
    for extra in parts[1:-1]:
        await bot.send_message(chat_id=chat_id, text=extra, **send_opts)
    await bot.send_message(
        chat_id=chat_id,
        text=parts[-1],
        reply_markup=reply_markup,
        **send_opts,
    )


async def _render_price_stale_list(
    message,
    state: FSMContext,
    *,
    page: int = 0,
    sort_mode: Optional[str] = None,
    bot=None,
    chat_id: Optional[int] = None,
) -> None:
    """Экран «Застой по цене»: текстовый рейтинг + пагинированные кнопки."""
    data = await state.get_data()
    if sort_mode is None:
        sort_mode = data.get("stale_sort_mode") or STALE_SORT_PRICE
    if sort_mode not in (STALE_SORT_PRICE, STALE_SORT_SALE):
        sort_mode = STALE_SORT_PRICE

    products, badge_count = await _fetch_stale_price_data(sort_mode)
    await state.update_data(
        products_back="price_stale_list",
        stale_page=page,
        stale_sort_mode=sort_mode,
    )

    if not products:
        await safe_edit_message(
            message,
            "🕰 <b>Застой по цене (б/у)</b>\n\nНет активных б/у товаров.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад в архив", callback_data="products_archive")]
                ]
            ),
            parse_mode="HTML",
        )
        return

    per_page = 10
    last_page = max(0, (len(products) - 1) // per_page)
    page = min(max(0, page), last_page)
    await state.update_data(stale_page=page)

    text = format_stale_list_text(
        products, badge_count, STALE_BADGE_MIN_DAYS, sort_mode=sort_mode
    )
    keyboard = get_stale_price_list_keyboard(products, page=page, sort_mode=sort_mode)
    _bot = bot or message.bot
    _chat_id = chat_id or message.chat.id
    await _send_long_html_message(message, text, keyboard, bot=_bot, chat_id=_chat_id)


async def _render_product_search(
    message,
    state: FSMContext,
    *,
    act_page: Optional[int] = None,
    arc_page: Optional[int] = None,
    archive_expanded: Optional[bool] = None,
):
    """Отрисовать результаты поиска товаров.

    Источник истины — сохранённый в FSM запрос (search_query). Каждый раз
    выдача пересобирается заново: приоритетно показываются товары в наличии
    (active, только б/у-ветка), архив (unavailable) — под сворачиваемой
    кнопкой со счётчиком. Пагинация active и архива независима.
    """
    data = await state.get_data()
    query = (data.get("search_query") or "").strip()
    if not query:
        await safe_edit_message(
            message,
            "🔍 Введите название товара для поиска:",
            reply_markup=await products_menu_markup(),
        )
        return

    if act_page is None:
        act_page = data.get("search_act_page", 0)
    if arc_page is None:
        arc_page = data.get("search_arc_page", 0)
    if archive_expanded is None:
        archive_expanded = data.get("search_arc_expanded", False)

    # В наличии: только б/у-ветка, без новых и custom-товаров
    active, _ = await get_products_api(status_filter="active", search=query, limit=1000)
    active = _filter_used_products_only(active)
    # Архив: снятые с продажи (помечены «Товар недоступен»)
    archive, _ = await get_products_api(status_filter="unavailable", search=query, limit=1000)

    if not active and not archive:
        await safe_edit_message(
            message,
            f"🔍 По запросу «{query}» ничего не найдено.",
            reply_markup=await products_menu_markup(),
        )
        await state.update_data(products_back="products_list")
        return

    # Нормализуем номера страниц под фактическое число позиций
    def _clamp_page(page: int, total_items: int) -> int:
        if total_items <= 0:
            return 0
        last_page = (total_items - 1) // SEARCH_PER_PAGE
        return min(max(0, page), last_page)

    act_page = _clamp_page(act_page, len(active))
    arc_page = _clamp_page(arc_page, len(archive))

    await state.update_data(
        search_act_page=act_page,
        search_arc_page=arc_page,
        search_arc_expanded=archive_expanded,
        products_back="psearch_back",
    )

    text = f"🔍 Результаты поиска: «{query}»\n\n"
    if active:
        text += f"📦 В наличии: {len(active)}"
    else:
        text += "📦 В наличии: ничего не найдено"
    if archive:
        text += f"\n📁 В архиве: {len(archive)}"
        if not active:
            text += "\n\nНажмите «📁 В архиве», чтобы посмотреть архивные позиции."

    keyboard = get_search_results_keyboard(
        active,
        archive,
        act_page=act_page,
        arc_page=arc_page,
        per_page=SEARCH_PER_PAGE,
        archive_expanded=archive_expanded,
    )
    await safe_edit_message(message, text, reply_markup=keyboard)


@router.callback_query(F.data == "products_search")
async def products_search_start(callback: CallbackQuery, state: FSMContext):
    """Начать поиск товаров."""
    text = "🔍 Введите название товара для поиска:"
    await safe_edit_message(callback.message, text)
    await state.update_data(
        search_query=None,
        search_act_page=0,
        search_arc_page=0,
        search_arc_expanded=False,
    )
    await state.set_state(ProductSearch.waiting_for_query)
    await callback.answer()


@router.message(ProductSearch.waiting_for_query)
async def products_search_process(message: Message, state: FSMContext):
    """Обработать запрос поиска товаров."""
    query = (message.text or "").strip()

    if not query:
        await message.answer("Пожалуйста, введите название товара.")
        return

    # Сохраняем контекст поиска и выходим из состояния ввода, НЕ затирая данные
    await state.set_state(None)
    await state.update_data(
        search_query=query,
        search_act_page=0,
        search_arc_page=0,
        search_arc_expanded=False,
    )

    sent = await message.answer("🔍 Ищу товары…")
    await _render_product_search(sent, state)


@router.callback_query(F.data.startswith("psearch_act_"))
async def products_search_active_page(callback: CallbackQuery, state: FSMContext):
    """Пагинация результатов поиска «в наличии»."""
    try:
        page = int(callback.data.replace("psearch_act_", ""))
    except ValueError:
        await callback.answer("Ошибка пагинации")
        return
    await _render_product_search(callback.message, state, act_page=page)
    await callback.answer()


@router.callback_query(F.data.startswith("psearch_arc_"))
async def products_search_archive_page(callback: CallbackQuery, state: FSMContext):
    """Раскрытие/пагинация архивного блока в результатах поиска."""
    try:
        page = int(callback.data.replace("psearch_arc_", ""))
    except ValueError:
        await callback.answer("Ошибка пагинации")
        return
    await _render_product_search(callback.message, state, arc_page=page, archive_expanded=True)
    await callback.answer()


@router.callback_query(F.data == "psearch_collapse")
async def products_search_collapse_archive(callback: CallbackQuery, state: FSMContext):
    """Свернуть архивный блок в результатах поиска."""
    await _render_product_search(callback.message, state, arc_page=0, archive_expanded=False)
    await callback.answer()


@router.callback_query(F.data == "psearch_back")
async def products_search_back(callback: CallbackQuery, state: FSMContext):
    """Вернуться к результатам поиска (например, из карточки товара)."""
    await _render_product_search(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == "products_archive")
async def products_archive(callback: CallbackQuery, state: FSMContext, year=None, month=None, day=None):
    """Показать архив товаров с навигацией по датам."""
    await show_archived_products(callback.message, year=year, month=month, day=day, state=state)
    await callback.answer()


async def show_archived_products(message, year=None, month=None, day=None, state: Optional[FSMContext] = None):
    """Показать архив товаров с группировкой по датам архивации."""
    import asyncio
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from datetime import datetime

    from app.services.evening_report_service import (
        ARCHIVE_DB_TIMEOUT_SEC,
        get_report_text_by_date,
        get_saved_report_days_for_month,
        get_saved_report_months_for_year,
        get_saved_report_years,
    )

    products, total = await get_all_products_api(status_filter="unavailable")
    
    stale_badge_count = 0
    if year is None:
        try:
            _, stale_badge_count = await _fetch_stale_price_data()
        except Exception as ex:
            logger.warning("stale badge count failed: %s", ex)

    if not products:
        if year is None:
            buttons = [
                [ikb("📊 Вечерний отчет", "evening_report_start")],
                [ikb(stale_button_label(stale_badge_count), "price_stale_list")],
                [InlineKeyboardButton(text="⬅️ Назад в меню товаров", callback_data="products_menu")],
            ]
            await safe_edit_message(
                message,
                "📁 Архив товаров пуст.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            )
            return
        text = "📁 Архив товаров пуст."
        await safe_edit_message(message, text, reply_markup=await products_menu_markup())
        return
    
    # Группируем товары по дате архивации
    today = datetime.now().date()
    products_by_date = {}
    products_today = []
    
    for product in products:
        try:
            archived_at_str = product.get("archived_at")
            if not archived_at_str:
                # Если нет даты архивации, используем updated_at
                archived_at_str = product.get("updated_at")
                if not archived_at_str:
                    continue
            
            archived_at = datetime.fromisoformat(archived_at_str.replace("Z", "+00:00"))
            archive_date = archived_at.date()
            archive_year = archive_date.year
            archive_month = archive_date.month
            archive_day = archive_date.day
        except Exception as e:
            logger.error(f"Error processing product {product.get('id')}: {str(e)}")
            continue
        
        # Проверяем соответствие фильтру
        if year is not None and archive_year != year:
            continue
        if month is not None and archive_month != month:
            continue
        if day is not None and archive_day != day:
            continue
        
        # Отделяем сегодняшние товары
        if archive_date == today:
            products_today.append(product)
            continue
        
        # Группируем по году, месяцу, дню
        if archive_year not in products_by_date:
            products_by_date[archive_year] = {}
        if archive_month not in products_by_date[archive_year]:
            products_by_date[archive_year][archive_month] = {}
        if archive_day not in products_by_date[archive_year][archive_month]:
            products_by_date[archive_year][archive_month][archive_day] = []
        
        products_by_date[archive_year][archive_month][archive_day].append(product)

    report_years: set[int] = set()
    report_months: set[int] = set()
    report_days: set[int] = set()
    try:
        if year is None:
            report_years = await asyncio.wait_for(
                asyncio.to_thread(get_saved_report_years),
                timeout=ARCHIVE_DB_TIMEOUT_SEC,
            )
        elif month is None:
            report_months = await asyncio.wait_for(
                asyncio.to_thread(get_saved_report_months_for_year, year),
                timeout=ARCHIVE_DB_TIMEOUT_SEC,
            )
        elif day is None:
            report_days = await asyncio.wait_for(
                asyncio.to_thread(get_saved_report_days_for_month, year, month),
                timeout=ARCHIVE_DB_TIMEOUT_SEC,
            )
    except asyncio.TimeoutError:
        logger.warning(
            "evening_report calendar load timed out (year=%s month=%s day=%s)",
            year,
            month,
            day,
        )
    
    # Создаем кнопки и текст в зависимости от уровня навигации
    buttons = []
    
    if year is None:
        # Корневой уровень - показываем сегодняшние товары и годы
        response_text = "📁 Архив товаров:\n\n"
        
        # Показываем сегодняшние товары
        if products_today:
            response_text += f"📅 Сегодня ({today.strftime('%d.%m.%Y')}):\n\n"
            for i, product in enumerate(products_today, 1):
                product_name = _archive_product_title(product)
                response_text += f"{i}. {product_name}\n"
                buttons.append([InlineKeyboardButton(
                    text=f"{i}. {product_name[:30]}{'...' if len(product_name) > 30 else ''}",
                    callback_data=f"product_{product.get('id')}"
                )])
            
            response_text += "\n📂 Архив по годам:\n\n"
        
        # Добавляем кнопки годов (товары ∪ отчёты)
        years = sorted(set(products_by_date.keys()) | report_years, reverse=True)
        for year_val in years:
            year_count = sum(
                len(products_by_date.get(year_val, {}).get(m, {}).get(d, []))
                for m in products_by_date.get(year_val, {})
                for d in products_by_date.get(year_val, {}).get(m, {})
            )
            buttons.append([InlineKeyboardButton(
                text=f"📅 {year_val} ({year_count} товаров)",
                callback_data=f"products_archive_year_{year_val}"
            )])
    
    elif month is None:
        # Уровень года - показываем месяцы
        response_text = f"📁 Архив товаров за {year} год:\n\n"
        
        months = sorted(set(products_by_date.get(year, {}).keys()) | report_months, reverse=True)
        for month_val in months:
            month_count = sum(
                len(products_by_date.get(year, {}).get(month_val, {}).get(d, []))
                for d in products_by_date.get(year, {}).get(month_val, {})
            )
            month_names = {
                1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
                5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
                9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
            }
            month_name = month_names.get(month_val, str(month_val))
            buttons.append([InlineKeyboardButton(
                text=f"📅 {month_name} ({month_count} товаров)",
                callback_data=f"products_archive_month_{year}_{month_val}"
            )])
        
        buttons.append([InlineKeyboardButton(
            text="⬅️ Назад к годам",
            callback_data="products_archive"
        )])
    
    elif day is None:
        # Уровень месяца - показываем дни
        month_names = {
            1: "Января", 2: "Февраля", 3: "Марта", 4: "Апреля",
            5: "Мая", 6: "Июня", 7: "Июля", 8: "Августа",
            9: "Сентября", 10: "Октября", 11: "Ноября", 12: "Декабря"
        }
        month_name = month_names.get(month, str(month))
        response_text = f"📁 Архив товаров за {month_name} {year} года:\n\n"
        
        days = sorted(set(products_by_date.get(year, {}).get(month, {}).keys()) | report_days, reverse=True)
        for day_val in days:
            day_count = len(products_by_date.get(year, {}).get(month, {}).get(day_val, []))
            day_label = f"📅 {day_val} {month_name} ({day_count} товаров)"
            if day_val in report_days:
                day_label += " ●"
            buttons.append([InlineKeyboardButton(
                text=day_label,
                callback_data=f"products_archive_day_{year}_{month}_{day_val}"
            )])
        
        buttons.append([InlineKeyboardButton(
            text="⬅️ Назад к месяцам",
            callback_data=f"products_archive_year_{year}"
        )])
    
    else:
        # Уровень дня - товары (если есть) + вечерний отчёт
        from datetime import date as date_cls

        from app.bot.keyboards.evening_report_keyboard import evening_report_date_callback

        month_names = {
            1: "Января", 2: "Февраля", 3: "Марта", 4: "Апреля",
            5: "Мая", 6: "Июня", 7: "Июля", 8: "Августа",
            9: "Сентября", 10: "Октября", 11: "Ноября", 12: "Декабря"
        }
        month_name = month_names.get(month, str(month))
        response_text = f"📁 Архив товаров за {day} {month_name} {year} года:\n\n"

        report_date = date_cls(year, month, day)
        try:
            report_text_saved = await asyncio.wait_for(
                asyncio.to_thread(get_report_text_by_date, report_date),
                timeout=ARCHIVE_DB_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            report_text_saved = None
            logger.warning("evening_report text load timed out for %s", report_date)
        er_cb = evening_report_date_callback(year, month, day)
        if report_text_saved:
            response_text += f"📊 Вечерний отчёт:\n{report_text_saved}\n\n"
            buttons.append([ikb("📊 Открыть отчёт", er_cb)])
        else:
            buttons.append([ikb("📊 Создать отчёт за этот день", er_cb)])

        day_products = products_by_date.get(year, {}).get(month, {}).get(day, [])
        for i, product in enumerate(day_products, 1):
            product_name = _archive_product_title(product)
            response_text += f"{i}. {product_name}\n"
            buttons.append([InlineKeyboardButton(
                text=f"{i}. {product_name[:30]}{'...' if len(product_name) > 30 else ''}",
                callback_data=f"product_{product.get('id')}"
            )])
        
        buttons.append([InlineKeyboardButton(
            text="⬅️ Назад к дням",
            callback_data=f"products_archive_month_{year}_{month}"
        )])
    
    # Кнопка "Вечерний отчет" (только на корневом уровне архива)
    if year is None:
        buttons.append([ikb("📊 Вечерний отчет", "evening_report_start")])
        buttons.append([ikb(stale_button_label(stale_badge_count), "price_stale_list")])
    
    # Кнопка назад в меню товаров
    buttons.append([InlineKeyboardButton(
        text="⬅️ Назад в меню товаров",
        callback_data="products_menu"
    )])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    if state is not None:
        await state.update_data(
            products_back=_archive_products_back_data(year, month, day)
        )
    await safe_edit_message(message, response_text, reply_markup=keyboard)


@router.callback_query(F.data == "price_stale_list")
async def price_stale_list(callback: CallbackQuery, state: FSMContext):
    """Список застоя по цене (б/у): текст + пагинированные кнопки."""
    data = await state.get_data()
    page = int(data.get("stale_page") or 0)
    sort_mode = data.get("stale_sort_mode") or STALE_SORT_PRICE
    await _render_price_stale_list(
        callback.message,
        state,
        page=page,
        sort_mode=sort_mode,
        bot=callback.bot,
        chat_id=callback.message.chat.id,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("price_stale_page_"))
async def price_stale_page(callback: CallbackQuery, state: FSMContext):
    """Пагинация списка застоя."""
    try:
        page = int(callback.data.replace("price_stale_page_", ""))
    except ValueError:
        await callback.answer("Ошибка")
        return
    data = await state.get_data()
    sort_mode = data.get("stale_sort_mode") or STALE_SORT_PRICE
    await _render_price_stale_list(
        callback.message,
        state,
        page=page,
        sort_mode=sort_mode,
        bot=callback.bot,
        chat_id=callback.message.chat.id,
    )
    await callback.answer()


@router.callback_query(F.data == "price_stale_sort_price")
async def price_stale_sort_price(callback: CallbackQuery, state: FSMContext):
    """Сортировка застоя: по давности смены цены."""
    await _render_price_stale_list(
        callback.message,
        state,
        page=0,
        sort_mode=STALE_SORT_PRICE,
        bot=callback.bot,
        chat_id=callback.message.chat.id,
    )
    await callback.answer()


@router.callback_query(F.data == "price_stale_sort_sale")
async def price_stale_sort_sale(callback: CallbackQuery, state: FSMContext):
    """Сортировка застоя: по давности в продаже."""
    await _render_price_stale_list(
        callback.message,
        state,
        page=0,
        sort_mode=STALE_SORT_SALE,
        bot=callback.bot,
        chat_id=callback.message.chat.id,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("price_stale_item_"))
async def price_stale_item(callback: CallbackQuery, state: FSMContext):
    """История цен одного застоявшегося товара."""
    try:
        product_id = int(callback.data.replace("price_stale_item_", ""))
    except ValueError:
        await callback.answer("Ошибка")
        return

    product, history = await _fetch_stale_price_detail(product_id)
    if not product:
        await callback.answer("Товар не найден или не активен", show_alert=True)
        return

    await state.update_data(products_back="price_stale_list")
    text = format_stale_detail_text(product, history)
    await safe_edit_message(
        callback.message,
        text,
        reply_markup=get_stale_price_detail_keyboard(product_id),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("products_archive_year_"))
async def products_archive_year(callback: CallbackQuery, state: FSMContext):
    """Показать месяцы выбранного года."""
    try:
        year = int(callback.data.replace("products_archive_year_", ""))
    except ValueError:
        await callback.answer("Ошибка")
        return
    await show_archived_products(callback.message, year=year, state=state)
    await callback.answer()


@router.callback_query(F.data.startswith("products_archive_month_"))
async def products_archive_month(callback: CallbackQuery, state: FSMContext):
    """Показать дни выбранного месяца."""
    try:
        parts = callback.data.replace("products_archive_month_", "").split("_")
        year = int(parts[0])
        month = int(parts[1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка")
        return
    await show_archived_products(callback.message, year=year, month=month, state=state)
    await callback.answer()


@router.callback_query(F.data.startswith("products_archive_day_"))
async def products_archive_day(callback: CallbackQuery, state: FSMContext):
    """Показать товары выбранного дня."""
    try:
        parts = callback.data.replace("products_archive_day_", "").split("_")
        year = int(parts[0])
        month = int(parts[1])
        day = int(parts[2])
    except (ValueError, IndexError):
        await callback.answer("Ошибка")
        return
    await show_archived_products(callback.message, year=year, month=month, day=day, state=state)
    await callback.answer()


async def process_product_unavailable(
    product_id: int,
    payment_method: Optional[str],
    mark_telegram_enabled: bool,
    callback: CallbackQuery,
    state: FSMContext,
    archive_kind: str = ARCHIVE_KIND_SALE,
    answer_text: Optional[str] = None,
):
    """Пометить товар недоступным: БД сразу, площадки — в очередь синхронизации."""
    from app.services.price_sync_service import (
        format_unavailable_saved_immediate_message,
        get_price_sync_service,
        is_new_product_branch,
        is_used_product_branch,
    )

    try:
        await callback.bot.send_chat_action(callback.message.chat.id, ChatAction.TYPING)
    except Exception:
        pass

    product = await get_product_api(product_id)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        await state.clear()
        return

    kind = normalize_archive_kind(archive_kind)
    if kind == ARCHIVE_KIND_TRANSFER:
        payment_method = None

    if payment_method:
        import asyncio
        import math

        def _save_payment_method():
            from app.db.database import SessionLocal
            from app.api.models.product import Product

            db = SessionLocal()
            try:
                db_product = db.query(Product).filter(Product.id == product_id).first()
                if not db_product:
                    return
                db_product.payment_method = payment_method
                price_str = product.get("price", "")
                price_clean = re.sub(r"[^\d.,]", "", price_str).replace(",", ".")
                try:
                    base_price = float(price_clean)
                    if payment_method == "card":
                        new_price = base_price * 1.05
                        new_price = math.ceil(new_price / 10) * 10
                        final_price = f"{int(new_price)}₽💳"
                    elif payment_method == "cash":
                        final_price = f"{price_str}💰"
                    elif payment_method == "credit":
                        final_price = f"{price_str}🏦"
                    else:
                        final_price = price_str
                    db_product.final_price = final_price
                    db.commit()
                except (ValueError, TypeError):
                    pass
            finally:
                db.close()

        await asyncio.to_thread(_save_payment_method)

    result = await update_product_status_api(
        product_id,
        "unavailable",
        sync_platforms=False,
        archive_kind=kind,
    )
    if not result:
        await callback.answer("❌ Ошибка при обновлении статуса", show_alert=True)
        await state.clear()
        return

    updated_product = result.get("product") or product

    if payment_method:
        await send_vk_report(updated_product, payment_method)

    service = get_price_sync_service()
    await service.enqueue_unavailable_sync(
        callback.bot,
        chat_id=callback.message.chat.id,
        product_id=product_id,
        product=updated_product,
        mark_telegram_enabled=mark_telegram_enabled,
        refresh_used_list=is_used_product_branch(updated_product),
        refresh_availability_list=is_new_product_branch(updated_product),
    )

    try:
        await callback.message.answer(
            format_unavailable_saved_immediate_message(),
            parse_mode="HTML",
        )
    except Exception as ex:
        logger.warning("Could not send unavailable immediate summary: %s", ex)

    sdata = await state.get_data()
    back_data = _products_back_from_state(sdata)

    refreshed = await get_product_api(product_id) or updated_product
    if refreshed:
        status = refreshed.get("status", "unavailable")
        text = await build_product_card_html(refreshed)
        await safe_edit_message(
            callback.message,
            text,
            reply_markup=get_product_detail_keyboard(
                product_id, status, back_data=back_data
            ),
            parse_mode="HTML",
        )

    await callback.answer(answer_text or "✅ Помечен недоступным")
    await _clear_state_keep_products_back(state)


@router.callback_query(F.data.startswith("product_payment_"))
async def product_payment_method(callback: CallbackQuery, state: FSMContext):
    """Обработать выбор способа оплаты."""
    try:
        parts = callback.data.replace("product_payment_", "").split("_")
        payment_method = parts[0]  # cash, card, credit
        product_id = int(parts[1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка")
        return
    
    data = await state.get_data()
    mark_telegram_enabled = data.get("mark_telegram_enabled", True)

    await process_product_unavailable(
        product_id,
        payment_method,
        mark_telegram_enabled,
        callback,
        state,
        archive_kind=ARCHIVE_KIND_SALE,
    )


async def send_vk_report(product: dict, payment_method: str):
    """Отправить отчет о продаже в ВК пользователям."""
    try:
        from app.utils.vk_client import community_token
        from app.services.settings_service import get_settings_service
        import vk_api
        import re
        import math

        token = community_token()
        if not token:
            logger.warning("VK community token not configured, skipping report")
            return
        VK_REPORT_USER_IDS = get_settings_service().get_vk_report_user_ids()
        if not VK_REPORT_USER_IDS:
            logger.warning("VK_REPORT_USER_IDS not configured, skipping report")
            return
        
        product_name = product.get('name', 'Без названия')
        price_str = product.get('price', '')
        
        # Извлекаем число из цены
        price_clean = re.sub(r'[^\d.,]', '', price_str)
        price_clean = price_clean.replace(',', '.')
        
        try:
            base_price = float(price_clean)
        except (ValueError, TypeError):
            logger.error(f"Invalid price format: {price_str}")
            return
        
        # Формируем сообщение в зависимости от способа оплаты
        if payment_method == "cash":
            message = f"{product_name} - {price_str}💰"
        elif payment_method == "card":
            # Добавляем 5% и округляем в большую сторону до десятков
            new_price = base_price * 1.05
            # Округляем в большую сторону до десятков
            new_price = math.ceil(new_price / 10) * 10
            message = f"{product_name} - {int(new_price)}₽💳"
        elif payment_method == "credit":
            message = f"{product_name} - {price_str}🏦"
        else:
            logger.error(f"Unknown payment method: {payment_method}")
            return
        
        # Отправляем сообщение каждому пользователю
        vk_session = vk_api.VkApi(token=token)
        vk = vk_session.get_api()
        
        for user_id in VK_REPORT_USER_IDS:
            try:
                vk.messages.send(
                    user_id=user_id,
                    message=message,
                    random_id=0
                )
                logger.info(f"Report sent to VK user {user_id}: {message}")
            except Exception as e:
                logger.error(f"Error sending report to VK user {user_id}: {str(e)}")
    
    except Exception as e:
        logger.error(f"Error sending VK report: {str(e)}")


async def get_telegram_message_text(chat_id: str, message_id: int) -> tuple[Optional[str], bool]:
    """
    Получить текущий текст/caption сообщения из Telegram API.
    Returns: (text_or_caption, has_media)
    """
    try:
        from app.config.settings import TELEGRAM_BOT_TOKEN
        import aiohttp
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        
        # Используем прямой вызов API для получения сообщения
        async with aiohttp.ClientSession() as session:
            # Получаем сообщение напрямую через forwardMessage или через getChat
            # Но лучше использовать прямой вызов getChatMember или другой метод
            # На самом деле, правильнее использовать прямой вызов API для получения сообщения
            get_message_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/forwardMessage"
            
            # Альтернативный способ - использовать прямой вызов через getChat
            # Но самый простой способ - получить сообщение через bot.get_chat и затем получить сообщение
            # Но в aiogram нет прямого метода для получения сообщения по ID
            
            # Используем прямой вызов API Telegram
            async with aiohttp.ClientSession() as http_session:
                # Пробуем получить сообщение через прямой вызов (но это не работает напрямую)
                # Вместо этого используем aiogram Bot для получения сообщения
                from aiogram import Bot
                bot = Bot(token=TELEGRAM_BOT_TOKEN)
                try:
                    # Пробуем получить сообщение через forwardMessage (но это не работает)
                    # Правильный способ - использовать bot.get_chat и затем получить сообщение
                    # Но в aiogram нет метода для получения сообщения по ID
                    
                    # Используем прямой вызов API через aiohttp
                    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getChat"
                    # Но getChat не возвращает сообщения
                    
                    # Правильный способ - использовать webhook или получить сообщение через updates
                    # Но проще всего - использовать прямой вызов API для получения сообщения
                    # К сожалению, Telegram API не предоставляет прямой метод для получения сообщения по ID
                    # Поэтому мы будем использовать сохраненный текст из БД, но с сохранением форматирования
                    
                    return None, False
                finally:
                    await bot.session.close()
    except Exception as e:
        logger.error(f"Error getting Telegram message text: {str(e)}")
        return None, False


async def update_telegram_post_price(telegram_link: str, old_price: str, new_price: str) -> bool:
    """Обновить цену в Telegram сообщении, сохраняя форматирование. True при успешном edit."""
    try:
        from app.config.settings import TELEGRAM_BOT_TOKEN
        from app.services.settings_service import get_settings_service
        TELEGRAM_CHANNEL_ID = get_settings_service().get_telegram_channel_id()
        from aiogram import Bot
        from aiogram.enums import ParseMode
        from app.utils.text_formatter import format_for_telegram
        import re

        match = re.search(r"/(\d+)$", telegram_link)
        if not match:
            logger.error("Could not extract message_id from link: %s", telegram_link)
            return False

        message_id = int(match.group(1))
        logger.info("Updating price in Telegram message %s", message_id)

        chat_id = TELEGRAM_CHANNEL_ID

        import asyncio

        post = await asyncio.to_thread(_fetch_post_by_telegram_link, telegram_link)
        if not post:
            logger.warning("Post with telegram_link %s not found in database", telegram_link)
            return False

        original_text = post.get("text") or ""

        new_price_clean = re.sub(r"[^\d.,]", "", new_price).replace(",", ".").replace(" ", "")
        try:
            new_price_value = float(new_price_clean)
            formatted_price_value = f"{int(new_price_value):,}".replace(",", " ")
        except (ValueError, TypeError):
            formatted_price_value = new_price_clean

        price_patterns = [
            (r"(Цена:?\s*)(\d+[\s\.,]?\d*)\s*(?:₽|руб|р\.?|RUB)", f"Цена: {formatted_price_value}₽"),
            (r"(\d+[\s\.,]?\d*)\s*(?:₽|руб|р\.?|RUB)", f"{formatted_price_value}₽"),
        ]

        updated_text = original_text
        replaced = False
        for pattern, replacement in price_patterns:
            if re.search(pattern, original_text, re.IGNORECASE):
                updated_text = re.sub(pattern, replacement, original_text, count=1, flags=re.IGNORECASE)
                replaced = True
                logger.info("Price replaced in original text using pattern: %s", pattern)
                break

        if not replaced:
            logger.warning("Could not find price pattern in text to replace")
            return False

        formatted_text = format_for_telegram(updated_text, signature_enabled=True)

        from app.bot.utils.telegram_edit import (
            edit_message_caption_safe,
            edit_message_text_safe,
        )

        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        try:
            has_media = bool(post.get("photos") or post.get("videos"))
            if has_media:
                logger.info("Editing caption for media message %s to update price", message_id)
                ok = await edit_message_caption_safe(
                    bot,
                    chat_id=chat_id,
                    message_id=message_id,
                    caption=formatted_text,
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
                if ok:
                    logger.info("Telegram post %s price updated successfully", message_id)
                    return True
                ok = await edit_message_caption_safe(
                    bot,
                    chat_id=chat_id,
                    message_id=message_id,
                    caption=formatted_text,
                )
                if ok:
                    logger.info("Telegram post %s price updated (without parse_mode)", message_id)
                return ok
            logger.info("Editing text for text message %s to update price", message_id)
            ok = await edit_message_text_safe(
                bot,
                chat_id=chat_id,
                message_id=message_id,
                text=formatted_text,
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            if ok:
                logger.info("Telegram post %s price updated successfully", message_id)
                return True
            ok = await edit_message_text_safe(
                bot,
                chat_id=chat_id,
                message_id=message_id,
                text=formatted_text,
            )
            if ok:
                logger.info("Telegram post %s price updated (without parse_mode)", message_id)
            return ok
        finally:
            await bot.session.close()

    except Exception as e:
        logger.error("Error updating Telegram post price: %s", e, exc_info=True)
        return False


async def remove_telegram_post_unavailable(telegram_link: str):
    """Убрать пометку #неактуально из Telegram поста, сохраняя форматирование."""
    try:
        from app.config.settings import TELEGRAM_BOT_TOKEN
        from app.services.settings_service import get_settings_service
        TELEGRAM_CHANNEL_ID = get_settings_service().get_telegram_channel_id()
        from aiogram import Bot
        from aiogram.enums import ParseMode
        from app.utils.text_formatter import format_for_telegram, format_for_telegram_plain
        import re
        import asyncio
        
        match = re.search(r'/(\d+)$', telegram_link)
        if not match:
            logger.error(f"Could not extract message_id from link: {telegram_link}")
            return
        
        message_id = int(match.group(1))
        logger.info(f"Removing #неактуально from Telegram message {message_id}")
        chat_id = TELEGRAM_CHANNEL_ID
        
        def _parse_retry_seconds(err: Exception) -> int:
            s = str(err)
            m = re.search(r'[Rr]etry in (\d+) seconds', s) or re.search(r'retry after (\d+)', s)
            return int(m.group(1)) if m else 0

        if True:
            post = await asyncio.to_thread(_fetch_post_by_telegram_link, telegram_link)
            if not post:
                logger.warning(f"Post with telegram_link {telegram_link} not found in database")
                return
            
            original_text = post.get("text") or ""
            text_without_unavailable = original_text
            if text_without_unavailable.startswith("#неактуально"):
                text_without_unavailable = text_without_unavailable.replace("#неактуально", "", 1).strip()
                text_without_unavailable = re.sub(r'^\n+', '', text_without_unavailable)
            elif text_without_unavailable.startswith("\\#неактуально"):
                text_without_unavailable = text_without_unavailable.replace("\\#неактуально", "", 1).strip()
                text_without_unavailable = re.sub(r'^\n+', '', text_without_unavailable)
            
            formatted_text = format_for_telegram(text_without_unavailable, signature_enabled=True)
            plain_text = format_for_telegram_plain(text_without_unavailable, signature_enabled=True)
            
            async def _edit_caption_retry():
                err = None
                for attempt in range(2):
                    if attempt > 0 and err is not None:
                        sec = _parse_retry_seconds(err)
                        if sec > 0:
                            logger.info(f"Flood control: waiting {sec}s before retry (remove #неактуально) message {message_id}")
                            await asyncio.sleep(sec)
                    try:
                        await bot.edit_message_caption(
                            chat_id=chat_id,
                            message_id=message_id,
                            caption=formatted_text,
                            parse_mode=ParseMode.MARKDOWN_V2
                        )
                        return True
                    except Exception as e:
                        err = e
                        if attempt == 0 and _parse_retry_seconds(e) == 0:
                            raise
                raise err
            
            async def _edit_text_retry():
                err = None
                for attempt in range(2):
                    if attempt > 0 and err is not None:
                        sec = _parse_retry_seconds(err)
                        if sec > 0:
                            logger.info(f"Flood control: waiting {sec}s before retry (remove #неактуально) message {message_id}")
                            await asyncio.sleep(sec)
                    try:
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=formatted_text,
                            parse_mode=ParseMode.MARKDOWN_V2
                        )
                        return True
                    except Exception as e:
                        err = e
                        if attempt == 0 and _parse_retry_seconds(e) == 0:
                            raise
                raise err
            
            bot = Bot(token=TELEGRAM_BOT_TOKEN)
            try:
                has_media = bool(post.get("photos") or post.get("videos"))
                if has_media:
                    logger.info(f"Editing caption for media message {message_id} to remove #неактуально")
                    try:
                        await _edit_caption_retry()
                        logger.info(f"Telegram post {message_id} #неактуально removed successfully")
                    except Exception as e:
                        logger.error(f"Error editing Telegram message caption {message_id}: {str(e)}")
                        sec = _parse_retry_seconds(e)
                        if sec > 0:
                            logger.info(f"Waiting {sec}s before fallback (remove #неактуально) message {message_id}")
                            await asyncio.sleep(sec)
                        e2 = None
                        for fallback_attempt in range(2):
                            if fallback_attempt > 0 and e2 is not None:
                                sec2 = _parse_retry_seconds(e2)
                                wait_sec = max(sec2, 6) + 2
                                await asyncio.sleep(wait_sec)
                            try:
                                await bot.edit_message_caption(
                                    chat_id=chat_id,
                                    message_id=message_id,
                                    caption=plain_text
                                )
                                logger.info(f"Telegram post {message_id} #неактуально removed (without parse_mode)")
                                break
                            except Exception as e2:
                                logger.error(f"Error editing caption (without parse_mode) {message_id}: {str(e2)}")
                                if fallback_attempt == 1 or _parse_retry_seconds(e2) == 0:
                                    break
                else:
                    logger.info(f"Editing text for text message {message_id} to remove #неактуально")
                    try:
                        await _edit_text_retry()
                        logger.info(f"Telegram post {message_id} #неактуально removed successfully")
                    except Exception as e:
                        logger.error(f"Error editing Telegram message text {message_id}: {str(e)}")
                        sec = _parse_retry_seconds(e)
                        if sec > 0:
                            await asyncio.sleep(sec)
                        e2 = None
                        for fallback_attempt in range(2):
                            if fallback_attempt > 0 and e2 is not None:
                                wait_sec = max(_parse_retry_seconds(e2), 6) + 2
                                await asyncio.sleep(wait_sec)
                            try:
                                await bot.edit_message_text(
                                    chat_id=chat_id,
                                    message_id=message_id,
                                    text=plain_text
                                )
                                logger.info(f"Telegram post {message_id} #неактуально removed (without parse_mode)")
                                break
                            except Exception as e2:
                                logger.error(f"Error editing text (without parse_mode) {message_id}: {str(e2)}")
                                if fallback_attempt == 1 or _parse_retry_seconds(e2) == 0:
                                    break
            finally:
                await bot.session.close()

    except Exception as e:
        logger.error(f"Error removing #неактуально from Telegram post: {str(e)}", exc_info=True)


async def mark_telegram_post_unavailable(telegram_link: str) -> bool:
    """Отредактировать пост в Telegram, добавив пометку #неактуально с сохранением форматирования. Возвращает успех."""
    try:
        from app.config.settings import TELEGRAM_BOT_TOKEN
        from app.services.settings_service import get_settings_service
        TELEGRAM_CHANNEL_ID = get_settings_service().get_telegram_channel_id()
        from aiogram import Bot
        from aiogram.enums import ParseMode
        import re
        
        # Извлекаем message_id из ссылки
        match = re.search(r'/(\d+)$', telegram_link)
        if not match:
            logger.error(f"Could not extract message_id from link: {telegram_link}")
            return False
        
        message_id = int(match.group(1))
        logger.info(f"Editing Telegram message {message_id} from link: {telegram_link}")
        chat_id = TELEGRAM_CHANNEL_ID
        logger.info(f"Using chat_id: {chat_id}")
        
        # Получаем пост из БД (в отдельном потоке, чтобы не блокировать event loop)
        import asyncio as _asyncio

        post = await _asyncio.to_thread(_fetch_post_by_telegram_link, telegram_link)
        if not post:
            logger.warning(f"Post with telegram_link {telegram_link} not found in database")
            return False

        # Получаем исходный текст поста (без форматирования для Telegram)
        original_text = post.get("text") or ""

        if original_text.startswith("#неактуально") or original_text.startswith("\\#неактуально"):
            logger.info(f"Message {message_id} already marked as unavailable")
            return True

        text_with_unavailable = f"#неактуально\n\n{original_text}"

        from app.utils.text_formatter import format_for_telegram, format_for_telegram_plain
        import asyncio
        formatted_text = format_for_telegram(text_with_unavailable, signature_enabled=True)
        plain_text = format_for_telegram_plain(text_with_unavailable, signature_enabled=True)
        has_media = bool(post.get("photos") or post.get("videos"))
        logger.info(f"New text length: {len(formatted_text)}, post has media: {has_media}")

        def _parse_retry_seconds(err: Exception) -> int:
            s = str(err)
            m = re.search(r'[Rr]etry in (\d+) seconds', s) or re.search(r'retry after (\d+)', s)
            return int(m.group(1)) if m else 0

        async def _edit_caption_with_retry():
            err = None
            for attempt in range(2):
                if attempt > 0 and err is not None:
                    sec = _parse_retry_seconds(err)
                    if sec > 0:
                        logger.info(f"Flood control: waiting {sec}s before retry for message {message_id}")
                        await asyncio.sleep(sec)
                try:
                    await bot.edit_message_caption(
                        chat_id=chat_id,
                        message_id=message_id,
                        caption=formatted_text,
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                    return True
                except Exception as e:
                    err = e
                    if attempt == 0 and _parse_retry_seconds(e) == 0:
                        raise
            raise err

        async def _edit_text_with_retry():
            err = None
            for attempt in range(2):
                if attempt > 0 and err is not None:
                    sec = _parse_retry_seconds(err)
                    if sec > 0:
                        logger.info(f"Flood control: waiting {sec}s before retry for message {message_id}")
                        await asyncio.sleep(sec)
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=formatted_text,
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                    return True
                except Exception as e:
                    err = e
                    if attempt == 0 and _parse_retry_seconds(e) == 0:
                        raise
            raise err

        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        edit_ok = False
        try:
            if has_media:
                logger.info(f"Editing caption for media message {message_id}")
                try:
                    await _edit_caption_with_retry()
                    logger.info(f"Telegram post {message_id} caption marked as unavailable successfully")
                    edit_ok = True
                except Exception as e:
                    logger.error(f"Error editing Telegram message caption {message_id}: {str(e)}")
                    sec = _parse_retry_seconds(e)
                    if sec > 0:
                        logger.info(f"Waiting {sec}s before fallback edit for message {message_id}")
                        await asyncio.sleep(sec)
                    e2: Optional[Exception] = None
                    for fallback_attempt in range(2):
                        if fallback_attempt > 0:
                            sec2 = _parse_retry_seconds(e2) if e2 is not None else 0
                            wait_sec = max(sec2, 6) + 2
                            logger.info(f"Waiting {wait_sec}s before fallback retry for message {message_id}")
                            await asyncio.sleep(wait_sec)
                        try:
                            await bot.edit_message_caption(
                                chat_id=chat_id,
                                message_id=message_id,
                                caption=plain_text
                            )
                            logger.info(f"Telegram post {message_id} caption marked as unavailable (without parse_mode)")
                            edit_ok = True
                            break
                        except Exception as e2:
                            logger.error(f"Error editing Telegram message caption {message_id} (without parse_mode): {str(e2)}")
                            if fallback_attempt == 1 or _parse_retry_seconds(e2) == 0:
                                break
            else:
                logger.info(f"Editing text for text message {message_id}")
                try:
                    await _edit_text_with_retry()
                    logger.info(f"Telegram post {message_id} marked as unavailable successfully")
                    edit_ok = True
                except Exception as e:
                    logger.error(f"Error editing Telegram message text {message_id}: {str(e)}")
                    sec = _parse_retry_seconds(e)
                    if sec > 0:
                        logger.info(f"Waiting {sec}s before fallback edit for message {message_id}")
                        await asyncio.sleep(sec)
                    e2: Optional[Exception] = None
                    for fallback_attempt in range(2):
                        if fallback_attempt > 0 and e2 is not None:
                            sec2 = _parse_retry_seconds(e2)
                            wait_sec = max(sec2, 6) + 2
                            logger.info(f"Waiting {wait_sec}s before fallback text retry for message {message_id}")
                            await asyncio.sleep(wait_sec)
                        try:
                            await bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=message_id,
                                text=plain_text
                            )
                            logger.info(f"Telegram post {message_id} marked as unavailable (without parse_mode)")
                            edit_ok = True
                            break
                        except Exception as e2:
                            logger.error(f"Error editing Telegram message text {message_id} (without parse_mode): {str(e2)}")
                            if fallback_attempt == 1 or _parse_retry_seconds(e2) == 0:
                                break
        finally:
            await bot.session.close()
        return edit_ok

    except Exception as e:
        logger.error(f"Error marking Telegram post as unavailable: {str(e)}", exc_info=True)
        return False


def _fetch_post_by_max_link(max_link: str) -> Optional[dict]:
    """Поля поста по max_link (raw SQL, для вызова через to_thread)."""
    if not max_link:
        return None
    try:
        from sqlalchemy import text
        from app.db.database import SessionLocal

        with SessionLocal() as db:
            row = (
                db.execute(
                    text(
                        "SELECT id, text, photos, videos FROM posts "
                        "WHERE max_link = :link LIMIT 1"
                    ),
                    {"link": max_link},
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None
    except Exception:
        logger.exception("Failed to fetch post by max_link")
        return None


def _extract_max_message_id(max_link: str) -> Optional[str]:
    match = re.search(r"/([^/]+)$", max_link or "")
    if not match:
        return None
    return match.group(1)


async def update_max_post_price(max_link: str, old_price: str, new_price: str) -> bool:
    try:
        import asyncio

        from app.services.settings_service import get_settings_service
        MAX_CHANNEL_ID = get_settings_service().get_max_channel_id()
        from app.integrations.max.client import create_max_api_client
        from app.utils.text_formatter import format_for_max, format_for_max_plain

        message_id = _extract_max_message_id(max_link)
        if not message_id:
            logger.error("Could not extract max message_id from link: %s", max_link)
            return False

        post = await asyncio.to_thread(_fetch_post_by_max_link, max_link)
        if not post:
            logger.warning("Post with max_link %s not found in DB", max_link)
            return False

        original_text = post.get("text") or ""
        new_price_clean = re.sub(r"[^\d.,]", "", new_price).replace(",", ".").replace(" ", "")
        try:
            new_price_value = float(new_price_clean)
            formatted_price_value = f"{int(new_price_value):,}".replace(",", " ")
        except (ValueError, TypeError):
            formatted_price_value = new_price_clean

        price_patterns = [
            (r"(Цена:?\s*)(\d+[\s\.,]?\d*)\s*(?:₽|руб|р\.?|RUB)", f"Цена: {formatted_price_value}₽"),
            (r"(\d+[\s\.,]?\d*)\s*(?:₽|руб|р\.?|RUB)", f"{formatted_price_value}₽"),
        ]
        updated_text = original_text
        replaced = False
        for pattern, replacement in price_patterns:
            if re.search(pattern, original_text, re.IGNORECASE):
                updated_text = re.sub(pattern, replacement, original_text, count=1, flags=re.IGNORECASE)
                replaced = True
                break
        if not replaced:
            logger.warning("Could not find price pattern in Max post text")
            return False

        client = create_max_api_client()
        formatted_text = format_for_max(updated_text, signature_enabled=True)
        plain_text = format_for_max_plain(updated_text, signature_enabled=True)
        if post.get("photos") or post.get("videos"):
            try:
                await client.edit_message_caption(
                    chat_id=MAX_CHANNEL_ID,
                    message_id=message_id,
                    caption=formatted_text,
                    parse_mode="MarkdownV2",
                )
                return True
            except Exception:
                await client.edit_message_caption(
                    chat_id=MAX_CHANNEL_ID,
                    message_id=message_id,
                    caption=plain_text,
                )
                return True
        try:
            await client.edit_message_text(
                chat_id=MAX_CHANNEL_ID,
                message_id=message_id,
                text=formatted_text,
                parse_mode="MarkdownV2",
            )
            return True
        except Exception:
            await client.edit_message_text(
                chat_id=MAX_CHANNEL_ID,
                message_id=message_id,
                text=plain_text,
            )
            return True
    except Exception as exc:
        logger.error("Error updating Max post price: %s", exc, exc_info=True)
        return False


async def remove_max_post_unavailable(max_link: str):
    try:
        import asyncio

        from app.services.settings_service import get_settings_service
        MAX_CHANNEL_ID = get_settings_service().get_max_channel_id()
        from app.integrations.max.client import create_max_api_client
        from app.utils.text_formatter import format_for_max, format_for_max_plain

        message_id = _extract_max_message_id(max_link)
        if not message_id:
            logger.error("Could not extract max message_id from link: %s", max_link)
            return

        post = await asyncio.to_thread(_fetch_post_by_max_link, max_link)
        if not post:
            return
        original_text = post.get("text") or ""
        text_without_unavailable = original_text
        if text_without_unavailable.startswith("#неактуально"):
            text_without_unavailable = text_without_unavailable.replace("#неактуально", "", 1).strip()
        elif text_without_unavailable.startswith("\\#неактуально"):
            text_without_unavailable = text_without_unavailable.replace("\\#неактуально", "", 1).strip()

        client = create_max_api_client()
        formatted_text = format_for_max(text_without_unavailable, signature_enabled=True)
        plain_text = format_for_max_plain(text_without_unavailable, signature_enabled=True)
        if post.get("photos") or post.get("videos"):
            try:
                await client.edit_message_caption(MAX_CHANNEL_ID, message_id, formatted_text, parse_mode="MarkdownV2")
            except Exception:
                await client.edit_message_caption(MAX_CHANNEL_ID, message_id, plain_text)
        else:
            try:
                await client.edit_message_text(MAX_CHANNEL_ID, message_id, formatted_text, parse_mode="MarkdownV2")
            except Exception:
                await client.edit_message_text(MAX_CHANNEL_ID, message_id, plain_text)
    except Exception as exc:
        logger.error("Error removing #неактуально in Max post: %s", exc, exc_info=True)


async def mark_max_post_unavailable(max_link: str) -> bool:
    try:
        import asyncio

        from app.services.settings_service import get_settings_service
        MAX_CHANNEL_ID = get_settings_service().get_max_channel_id()
        from app.integrations.max.client import create_max_api_client
        from app.utils.text_formatter import format_for_max, format_for_max_plain

        message_id = _extract_max_message_id(max_link)
        if not message_id:
            logger.error("Could not extract max message_id from link: %s", max_link)
            return False

        post = await asyncio.to_thread(_fetch_post_by_max_link, max_link)
        if not post:
            return False
        original_text = post.get("text") or ""
        if original_text.startswith("#неактуально") or original_text.startswith("\\#неактуально"):
            return True
        text_with_unavailable = f"#неактуально\n\n{original_text}"

        client = create_max_api_client()
        formatted_text = format_for_max(text_with_unavailable, signature_enabled=True)
        plain_text = format_for_max_plain(text_with_unavailable, signature_enabled=True)
        if post.get("photos") or post.get("videos"):
            try:
                await client.edit_message_caption(MAX_CHANNEL_ID, message_id, formatted_text, parse_mode="MarkdownV2")
            except Exception:
                await client.edit_message_caption(MAX_CHANNEL_ID, message_id, plain_text)
        else:
            try:
                await client.edit_message_text(MAX_CHANNEL_ID, message_id, formatted_text, parse_mode="MarkdownV2")
            except Exception:
                await client.edit_message_text(MAX_CHANNEL_ID, message_id, plain_text)
        return True
    except Exception as exc:
        logger.error("Error marking Max post unavailable: %s", exc, exc_info=True)
        return False


async def mark_instagram_post_unavailable(product: dict) -> bool:
    """Оставить комментарий #неактуально под постом Instagram (Graph API)."""
    try:
        from app.workers.instagram.graph_client import InstagramGraphClient, UNAVAILABLE_COMMENT

        media_id = resolve_product_instagram_media_id(product)
        if not media_id:
            logger.warning(
                "Instagram unavailable: no media_id for product_id=%s",
                product.get("id"),
            )
            return False

        client = InstagramGraphClient()
        if not client.enabled:
            logger.error("Instagram Graph API not configured for commenting")
            return False

        if await client.has_unavailable_comment(media_id):
            logger.info("Instagram media %s already has unavailable comment", media_id)
            return True

        ok = await client.post_comment(media_id, UNAVAILABLE_COMMENT)
        if ok:
            logger.info(
                "Instagram comment posted for product_id=%s media_id=%s",
                product.get("id"),
                media_id,
            )
            return True

        # Graph API иногда отвечает 400 (spam filter), хотя комментарий уже в ленте.
        if await client.has_unavailable_comment(media_id):
            logger.info(
                "Instagram media %s has #неактуально after post_comment failure (idempotent ok)",
                media_id,
            )
            return True

        return False
    except Exception as exc:
        logger.error("Error marking Instagram post unavailable: %s", exc, exc_info=True)
        return False


async def remove_instagram_post_unavailable(product: dict) -> bool:
    """Удалить комментарий #неактуально под постом Instagram при восстановлении товара."""
    try:
        from app.workers.instagram.graph_client import InstagramGraphClient

        media_id = resolve_product_instagram_media_id(product)
        if not media_id:
            logger.info(
                "Instagram restore: no media_id for product_id=%s, skip",
                product.get("id"),
            )
            return True

        client = InstagramGraphClient()
        if not client.enabled:
            logger.error("Instagram Graph API not configured for comment delete")
            return False

        comment_ids = await client.find_unavailable_comment_ids(media_id)
        if not comment_ids:
            logger.info("Instagram media %s: no #неактуально comment to remove", media_id)
            return True

        all_ok = True
        for comment_id in comment_ids:
            deleted = await client.delete_comment(comment_id)
            if deleted:
                logger.info(
                    "Instagram deleted comment %s on media %s (product_id=%s)",
                    comment_id,
                    media_id,
                    product.get("id"),
                )
            else:
                all_ok = False
        return all_ok
    except Exception as exc:
        logger.error("Error removing Instagram unavailable comment: %s", exc, exc_info=True)
        return False


def resolve_product_vk_post_id(product: Optional[dict]) -> Optional[str]:
    """vk_post_id товара (формат "{owner_id}_{post_id}")."""
    if not product:
        return None
    vk_post_id = product.get("vk_post_id")
    if vk_post_id:
        return str(vk_post_id)
    return None


async def mark_vk_post_unavailable(product: dict) -> bool:
    """Оставить комментарий «неактуально» от лица группы под постом в ленте VK.

    Только если включён переключатель «Товары ВК» и пост опубликован в ленте.
    """
    try:
        import asyncio

        from app.services.settings_service import get_settings_service
        from app.workers.vk.wall_comments import VKWallCommentClient, parse_vk_post_id

        if not get_settings_service().is_vk_market_publish_allowed():
            logger.info(
                "VK unavailable comment skipped (Товары ВК выключены) product_id=%s",
                product.get("id"),
            )
            return False

        parsed = parse_vk_post_id(resolve_product_vk_post_id(product))
        if not parsed:
            logger.info(
                "VK unavailable: no vk_post_id for product_id=%s, skip",
                product.get("id"),
            )
            return False

        owner_id, post_id = parsed
        client = VKWallCommentClient()
        return await asyncio.to_thread(
            client.create_unavailable_comment, owner_id, post_id
        )
    except Exception as exc:
        logger.error("Error marking VK post unavailable: %s", exc, exc_info=True)
        return False


async def remove_vk_post_unavailable(product: dict) -> bool:
    """Удалить комментарий «неактуально» от лица группы под постом ленты VK при восстановлении."""
    try:
        import asyncio

        from app.services.settings_service import get_settings_service
        from app.workers.vk.wall_comments import VKWallCommentClient, parse_vk_post_id

        if not get_settings_service().is_vk_market_publish_allowed():
            logger.info(
                "VK restore comment cleanup skipped (Товары ВК выключены) product_id=%s",
                product.get("id"),
            )
            return False

        parsed = parse_vk_post_id(resolve_product_vk_post_id(product))
        if not parsed:
            logger.info(
                "VK restore: no vk_post_id for product_id=%s, skip",
                product.get("id"),
            )
            return True

        owner_id, post_id = parsed
        client = VKWallCommentClient()
        return await asyncio.to_thread(
            client.remove_unavailable_comments, owner_id, post_id
        )
    except Exception as exc:
        logger.error("Error removing VK post unavailable comment: %s", exc, exc_info=True)
        return False

