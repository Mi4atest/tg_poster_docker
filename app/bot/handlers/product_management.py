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
from typing import Optional

from app.bot.keyboards.product_keyboard import (
    get_products_menu_keyboard,
    get_product_list_keyboard,
    get_search_results_keyboard,
    get_product_detail_keyboard,
    get_product_price_edit_keyboard,
    get_product_status_confirmation_keyboard,
    get_iphone_versions_keyboard,
    get_iphone_models_keyboard,
    get_iphone_model_products_keyboard
)
from app.utils.iphone_parser import group_products_by_model, get_model_display_name
from app.bot.utils.product_list_formatter import format_full_products_list
from app.config.settings import API_HOST, API_PORT, MAX_SHARE_FALLBACK_PREFIX
from app.utils.price_change import (
    PriceChangeInfo,
    analyze_price_change,
    format_price_change_confirm_prompt,
    format_price_change_html_lines,
    price_string_to_int_rub,
)
from app.bot.utils.button_styles import ikb
logger = logging.getLogger(__name__)

router = Router()


def _products_back_from_state(data: dict) -> str:
    return data.get("products_back") or "products_list"


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


class ProductSearch(StatesGroup):
    waiting_for_query = State()

class ProductPriceEdit(StatesGroup):
    waiting_for_price = State()
    waiting_for_confirm = State()


class ProductAvitoLinkEdit(StatesGroup):
    waiting_for_avito_ref = State()


class ProductUnavailableOptions(StatesGroup):
    waiting_for_payment_method = State()

class EveningReport(StatesGroup):
    waiting_for_morning_cash = State()
    waiting_for_day_cash = State()
    waiting_for_bn = State()
    waiting_for_new_advance = State()
    waiting_for_old_advance = State()
    waiting_for_surrendered = State()
    waiting_for_buybacks = State()
    waiting_for_wholesale = State()
    waiting_for_credit = State()
    waiting_for_nf = State()
    waiting_for_additional_expenses = State()
    waiting_for_expense_name = State()
    waiting_for_expense_amount = State()
    waiting_for_final_confirmation = State()


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
    """Получить товары из API."""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"http://{API_HOST}:{API_PORT}/api/products/"
            params = {"skip": skip, "limit": limit}
            
            if status_filter:
                params["status_filter"] = status_filter
            if search:
                params["search"] = search
            
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    items = data.get("items", [])
                    total = data.get("total", 0)
                    return items, total
                else:
                    logger.error(f"Failed to get products: {response.status}")
                    return [], 0
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
    """Получить товар по ID из API."""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"http://{API_HOST}:{API_PORT}/api/products/{product_id}"
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logger.error(f"Failed to get product: {response.status}")
                    return None
    except Exception as e:
        logger.error(f"Error getting product: {str(e)}")
        return None


def resolve_product_max_link(product: Optional[dict]) -> Optional[str]:
    """Вернуть max_link товара, при отсутствии — попробовать взять из связанного Post."""
    if not product:
        return None
    direct_link = (product.get("max_link") or "").strip()
    if direct_link:
        return direct_link
    post_id = product.get("post_id")
    if not post_id:
        return None
    try:
        from app.db.database import SessionLocal
        from app.api.models.post import Post

        db = SessionLocal()
        try:
            post = db.query(Post).filter(Post.id == post_id).first()
            resolved = (post.max_link or "").strip() if post else ""
            return resolved or None
        finally:
            db.close()
    except Exception:
        logger.exception("Failed to resolve product max_link from post_id=%s", post_id)
        return None


def resolve_product_instagram_media_id(product: Optional[dict]) -> Optional[str]:
    """Вернуть instagram_media_id товара, при отсутствии — из связанного Post."""
    if not product:
        return None
    direct_id = (product.get("instagram_media_id") or "").strip()
    if direct_id:
        return direct_id
    post_id = product.get("post_id")
    if not post_id:
        return None
    try:
        from app.db.database import SessionLocal
        from app.api.models.post import Post

        db = SessionLocal()
        try:
            post = db.query(Post).filter(Post.id == post_id).first()
            resolved = (post.instagram_media_id or "").strip() if post else ""
            return resolved or None
        finally:
            db.close()
    except Exception:
        logger.exception("Failed to resolve product instagram_media_id from post_id=%s", post_id)
        return None


def resolve_product_instagram_link(product: Optional[dict]) -> Optional[str]:
    """Вернуть instagram_link товара, при отсутствии — из связанного Post."""
    if not product:
        return None
    direct_link = (product.get("instagram_link") or "").strip()
    if direct_link:
        return direct_link
    post_id = product.get("post_id")
    if not post_id:
        return None
    try:
        from app.db.database import SessionLocal
        from app.api.models.post import Post

        db = SessionLocal()
        try:
            post = db.query(Post).filter(Post.id == post_id).first()
            resolved = (post.instagram_link or "").strip() if post else ""
            return resolved or None
        finally:
            db.close()
    except Exception:
        logger.exception("Failed to resolve product instagram_link from post_id=%s", post_id)
        return None


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


async def update_product_status_api(product_id: int, status: str, *, sync_platforms: bool = True):
    """Обновить статус товара через API. Ответ: {product, status_sync} или None."""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"http://{API_HOST}:{API_PORT}/api/products/{product_id}/status"
            data = {"status": status, "sync_platforms": sync_platforms}
            async with session.put(url, json=data) as response:
                if response.status == 200:
                    return await response.json()
                error_text = await response.text()
                logger.error("Failed to update product status: %s, %s", response.status, error_text)
                return None
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
    """Удалить товар через API."""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"http://{API_HOST}:{API_PORT}/api/products/{product_id}"
            async with session.delete(url) as response:
                if response.status == 200:
                    return True
                else:
                    error_text = await response.text()
                    logger.error(f"Failed to delete product: {response.status}, {error_text}")
                    return False
    except Exception as e:
        logger.error(f"Error deleting product: {str(e)}")
        return False


async def update_product_price_api(product_id: int, price: str, *, sync_platforms: bool = True):
    """Обновить цену товара через API. Ответ: {product, price_sync} или None при ошибке."""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"http://{API_HOST}:{API_PORT}/api/products/{product_id}/price"
            data = {"price": price, "sync_platforms": sync_platforms}
            async with session.put(url, json=data) as response:
                if response.status == 200:
                    return await response.json()
                error_text = await response.text()
                logger.error("Failed to update product price: %s - %s", response.status, error_text)
                return None
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
    """Привязать объявление Авито к товару."""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"http://{API_HOST}:{API_PORT}/api/products/{product_id}/avito_link"
            async with session.put(url, json={"avito_link_or_id": avito_link_or_id}) as response:
                if response.status == 200:
                    return await response.json()
                error_text = await response.text()
                logger.error("Failed to update avito link: %s - %s", response.status, error_text)
                return None
    except Exception as e:
        logger.error("Error updating avito link: %s", e)
        return None


# Handlers
@router.callback_query(F.data == "products_menu")
async def products_menu(callback: CallbackQuery):
    """Показать меню товаров."""
    text = "📦 Управление товарами\n\nВыберите действие:"
    await safe_edit_message(callback.message, text, reply_markup=get_products_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "sync_telegram_links")
async def sync_telegram_links(callback: CallbackQuery):
    """Синхронизировать ссылки на посты ТГ: из Post.telegram_link в Product.telegram_link для всех постов с ссылкой."""
    await callback.answer("Проверяю посты…")
    try:
        from app.db.database import SessionLocal
        from app.api.models.post import Post
        from app.api.models.product import Product

        db = SessionLocal()
        try:
            posts_with_link = db.query(Post).filter(
                Post.telegram_link.isnot(None),
                Post.telegram_link != ""
            ).all()
            updated_products = 0
            posts_processed = 0
            for post in posts_with_link:
                products = db.query(Product).filter(Product.post_id == post.id).all()
                for prod in products:
                    if prod.telegram_link != post.telegram_link:
                        prod.telegram_link = post.telegram_link
                        updated_products += 1
                if products:
                    posts_processed += 1
            db.commit()
            text = (
                "🔄 Обновление постов\n\n"
                f"Проверено постов с ссылкой: {len(posts_with_link)}\n"
                f"Постов с товарами: {posts_processed}\n"
                f"Обновлено ссылок у товаров: {updated_products}\n\n"
                "✅ Готово."
            )
        finally:
            db.close()
    except Exception as e:
        logger.exception("sync_telegram_links failed")
        text = f"🔄 Обновление постов\n\n❌ Ошибка: {e}"
    await safe_edit_message(callback.message, text, reply_markup=get_products_menu_keyboard())


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
        await safe_edit_message(callback.message, text, reply_markup=get_products_menu_keyboard())
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


@router.callback_query(F.data.startswith("product_") & ~F.data.startswith("product_unavailable_") & ~F.data.startswith("product_delete_") & ~F.data.startswith("product_restore_") & ~F.data.startswith("product_confirm_") & ~F.data.startswith("product_price_") & ~F.data.startswith("product_avito_") & ~F.data.startswith("product_toggle_report_") & ~F.data.startswith("product_toggle_mark_tg_") & ~F.data.startswith("product_payment_"))
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
    text = f"📦 <b>{product.get('name', 'Без названия')}</b>\n\n"
    
    if product.get('price'):
        text += f"💵 Цена: {product['price']}\n"
    
    if product.get('category_name'):
        text += f"📂 Категория: {product['category_name']}\n"
    
    if product.get('collection_name'):
        text += f"📁 Подборка: {product['collection_name']}\n"
    
    status_emoji = {
        "active": "✅",
        "unavailable": "🚫",
        "deleted": "🗑️"
    }
    status_text = {
        "active": "Активен",
        "unavailable": "Недоступен",
        "deleted": "Удален"
    }
    status = product.get('status', 'active')
    text += f"\n{status_emoji.get(status, '❓')} Статус: {status_text.get(status, status)}\n"
    text += format_product_platform_links_html(product)

    await safe_edit_message(
        callback.message,
        text,
        reply_markup=get_product_detail_keyboard(product_id, status, back_data=back_data),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("product_unavailable_"))
async def product_unavailable(callback: CallbackQuery, state: FSMContext):
    """Показать подтверждение для пометки товара как недоступного."""
    try:
        product_id = int(callback.data.replace("product_unavailable_", ""))
    except ValueError:
        await callback.answer("Ошибка")
        return
    
    product = await get_product_api(product_id)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    
    # Инициализируем состояние с переключателями (по умолчанию «Пометить ТГ/IG/Max» включено)
    await state.update_data(
        product_id=product_id,
        report_enabled=False,
        mark_telegram_enabled=True,
    )
    
    text = f"🚫 Пометить товар как недоступный?\n\n📦 {product.get('name', 'Без названия')}\n\nТовар будет скрыт из каталога, но не удален."
    await safe_edit_message(
        callback.message,
        text,
        reply_markup=get_product_status_confirmation_keyboard(product_id, "unavailable", report_enabled=False, mark_telegram_enabled=True)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("product_toggle_report_"))
async def product_toggle_report(callback: CallbackQuery, state: FSMContext):
    """Переключить отправку отчета."""
    try:
        product_id = int(callback.data.replace("product_toggle_report_", ""))
        logger.info(f"Toggle report for product {product_id}")
    except ValueError as e:
        logger.error(f"Error parsing product_id from callback_data: {callback.data}, error: {str(e)}")
        await callback.answer("Ошибка получения товара", show_alert=True)
        return
    
    # Получаем или инициализируем состояние
    data = await state.get_data()
    logger.info(f"Current state data: {data}")
    
    # Если состояние не инициализировано, инициализируем его
    if not data.get("product_id"):
        logger.info(f"Initializing state for product {product_id}")
        await state.update_data(
            product_id=product_id,
            report_enabled=False,
            mark_telegram_enabled=True,
        )
        data = await state.get_data()
    
    current_report = data.get("report_enabled", False)
    new_report = not current_report
    logger.info(f"Toggling report from {current_report} to {new_report}")
    
    await state.update_data(report_enabled=new_report)
    
    mark_tg = data.get("mark_telegram_enabled", True)
    
    product = await get_product_api(product_id)
    if not product:
        logger.error(f"Product {product_id} not found via API")
        await callback.answer("Товар не найден", show_alert=True)
        return
    
    text = f"🚫 Пометить товар как недоступный?\n\n📦 {product.get('name', 'Без названия')}\n\nТовар будет скрыт из каталога, но не удален."
    await safe_edit_message(
        callback.message,
        text,
        reply_markup=get_product_status_confirmation_keyboard(product_id, "unavailable", report_enabled=new_report, mark_telegram_enabled=mark_tg)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("product_toggle_mark_tg_"))
async def product_toggle_mark_tg(callback: CallbackQuery, state: FSMContext):
    """Переключить пометку поста в Telegram."""
    try:
        product_id = int(callback.data.replace("product_toggle_mark_tg_", ""))
        logger.info(f"Toggle mark TG for product {product_id}")
    except ValueError as e:
        logger.error(f"Error parsing product_id from callback_data: {callback.data}, error: {str(e)}")
        await callback.answer("Ошибка получения товара", show_alert=True)
        return
    
    # Получаем или инициализируем состояние
    data = await state.get_data()
    logger.info(f"Current state data: {data}")
    
    # Если состояние не инициализировано, инициализируем его
    if not data.get("product_id"):
        logger.info(f"Initializing state for product {product_id}")
        await state.update_data(
            product_id=product_id,
            report_enabled=False,
            mark_telegram_enabled=True,
        )
        data = await state.get_data()
    
    current_mark_tg = data.get("mark_telegram_enabled", True)
    new_mark_tg = not current_mark_tg
    logger.info(f"Toggling mark TG from {current_mark_tg} to {new_mark_tg}")
    
    await state.update_data(mark_telegram_enabled=new_mark_tg)
    
    report = data.get("report_enabled", False)
    
    product = await get_product_api(product_id)
    if not product:
        logger.error(f"Product {product_id} not found via API")
        await callback.answer("Товар не найден", show_alert=True)
        return
    
    text = f"🚫 Пометить товар как недоступный?\n\n📦 {product.get('name', 'Без названия')}\n\nТовар будет скрыт из каталога, но не удален."
    await safe_edit_message(
        callback.message,
        text,
        reply_markup=get_product_status_confirmation_keyboard(product_id, "unavailable", report_enabled=report, mark_telegram_enabled=new_mark_tg)
    )
    await callback.answer()


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
    text = f"📦 <b>{product.get('name', 'Без названия')}</b>\n\n"
    if product.get("price"):
        text += f"💵 Цена: {product['price']}\n"
    if product.get("category_name"):
        text += f"📂 Категория: {product['category_name']}\n"
    if product.get("collection_name"):
        text += f"📁 Подборка: {product['collection_name']}\n"
    status_emoji = {"active": "✅", "unavailable": "🚫", "deleted": "🗑️"}
    status_text = {"active": "Активен", "unavailable": "Недоступен", "deleted": "Удален"}
    status_val = product.get("status", "active")
    text += f"\n{status_emoji.get(status_val, '❓')} Статус: {status_text.get(status_val, status_val)}\n"
    text += format_product_platform_links_html(product)

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
    text = f"📦 <b>{updated_product.get('name', 'Без названия')}</b>\n\n"
    if updated_product.get("price"):
        text += f"💵 Цена: {updated_product['price']}\n"
    if updated_product.get("category_name"):
        text += f"📂 Категория: {updated_product['category_name']}\n"
    if updated_product.get("collection_name"):
        text += f"📁 Подборка: {updated_product['collection_name']}\n"
    status_emoji = {"active": "✅", "unavailable": "🚫", "deleted": "🗑️"}
    status_text = {"active": "Активен", "unavailable": "Недоступен", "deleted": "Удален"}
    status_val = updated_product.get("status", "active")
    text += f"\n{status_emoji.get(status_val, '❓')} Статус: {status_text.get(status_val, status_val)}\n"
    text += format_product_platform_links_html(updated_product)
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
    cur = product.get("avito_url") or product.get("avito_item_id") or "не привязано"
    text = (
        f"🛒 <b>Привязка Авито</b>\n\n"
        f"📦 {html.escape(product.get('name', 'Без названия'))}\n\n"
        f"Текущее: {html.escape(str(cur))}\n\n"
        "Отправьте <b>ссылку на объявление</b> или только <b>числовой id</b> (цифры из URL).\n"
        "Лучше всего: откройте объявление в <b>браузере</b> и скопируйте адрес "
        "(в конце часто <code>…_1234567890</code> или сегмент <code>/1234567890</code>).\n"
        "Ссылка «Поделиться» из приложения подойдёт, если в тексте есть этот id; "
        "короткая ссылка без цифр — не сработает."
    )
    await state.update_data(product_id=product_id)
    await state.set_state(ProductAvitoLinkEdit.waiting_for_avito_ref)
    await safe_edit_message(callback.message, text, parse_mode="HTML")
    await callback.answer()


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
    back_data = await _clear_state_keep_products_back(state)
    if not result:
        await message.answer(
            "❌ Не удалось распознать объявление. Пришлите полный URL из браузера "
            "или только id (обычно 9–10 цифр в конце адреса). "
            "Если из приложения пришла короткая ссылка без цифр — откройте объявление в браузере и скопируйте урл."
        )
        return
    await message.answer("✅ Объявление Авито привязано.")
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
    except (ValueError, IndexError):
        await callback.answer("Ошибка")
        return
    
    product = await get_product_api(product_id)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    sdata = await state.get_data()
    back_data = _products_back_from_state(sdata)

    if action == "unavailable":
        # Если включен отчет, показываем выбор способа оплаты
        if report_enabled:
            await state.update_data(
                product_id=product_id,
                report_enabled=True,
                mark_telegram_enabled=mark_telegram_enabled
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
        await process_product_unavailable(product_id, None, mark_telegram_enabled, callback, state)
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
            text = f"📦 <b>{updated_product.get('name', 'Без названия')}</b>\n\n"
            if updated_product.get("price"):
                text += f"💵 Цена: {updated_product['price']}\n"
            if updated_product.get("category_name"):
                text += f"📂 Категория: {updated_product['category_name']}\n"
            if updated_product.get("collection_name"):
                text += f"📁 Подборка: {updated_product['collection_name']}\n"
            status_emoji = {"active": "✅", "unavailable": "🚫", "deleted": "🗑️"}
            status_text = {"active": "Активен", "unavailable": "Недоступен", "deleted": "Удален"}
            text += f"\n{status_emoji.get(status, '❓')} Статус: {status_text.get(status, status)}\n"
            text += format_product_platform_links_html(updated_product)
            await safe_edit_message(
                callback.message,
                text,
                reply_markup=get_product_detail_keyboard(
                    product_id, status, back_data=back_data
                ),
                parse_mode="HTML",
            )
        try:
            from app.bot.utils.used_products_channel_updater import update_used_products_list_in_channel

            await update_used_products_list_in_channel(callback.bot)
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
                reply_markup=get_products_menu_keyboard()
            )
            try:
                from app.bot.utils.used_products_channel_updater import update_used_products_list_in_channel
                await update_used_products_list_in_channel(callback.bot)
            except Exception as upd_err:
                logger.warning("Failed to update used products list in channel: %s", upd_err)
        else:
            await callback.answer("❌ Ошибка при удалении", show_alert=True)


SEARCH_PER_PAGE = 10


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
            reply_markup=get_products_menu_keyboard(),
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
            reply_markup=get_products_menu_keyboard(),
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
async def products_archive(callback: CallbackQuery, year=None, month=None, day=None):
    """Показать архив товаров с навигацией по датам."""
    await show_archived_products(callback.message, year=year, month=month, day=day)
    await callback.answer()


async def show_archived_products(message, year=None, month=None, day=None):
    """Показать архив товаров с группировкой по датам архивации."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from datetime import datetime
    
    products, total = await get_all_products_api(status_filter="unavailable")
    
    if not products:
        text = "📁 Архив товаров пуст."
        await safe_edit_message(message, text, reply_markup=get_products_menu_keyboard())
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
    
    # Создаем кнопки и текст в зависимости от уровня навигации
    buttons = []
    
    if year is None:
        # Корневой уровень - показываем сегодняшние товары и годы
        response_text = "📁 Архив товаров:\n\n"
        
        # Показываем сегодняшние товары
        if products_today:
            response_text += f"📅 Сегодня ({today.strftime('%d.%m.%Y')}):\n\n"
            for i, product in enumerate(products_today, 1):
                product_name = product.get("name", "Без названия")
                response_text += f"{i}. {product_name}\n"
                buttons.append([InlineKeyboardButton(
                    text=f"{i}. {product_name[:30]}{'...' if len(product_name) > 30 else ''}",
                    callback_data=f"product_{product.get('id')}"
                )])
            
            response_text += "\n📂 Архив по годам:\n\n"
        
        # Добавляем кнопки годов
        years = sorted(products_by_date.keys(), reverse=True)
        for year_val in years:
            year_count = sum(
                len(products_by_date[year_val][m][d])
                for m in products_by_date[year_val]
                for d in products_by_date[year_val][m]
            )
            buttons.append([InlineKeyboardButton(
                text=f"📅 {year_val} ({year_count} товаров)",
                callback_data=f"products_archive_year_{year_val}"
            )])
    
    elif month is None:
        # Уровень года - показываем месяцы
        response_text = f"📁 Архив товаров за {year} год:\n\n"
        
        months = sorted(products_by_date[year].keys(), reverse=True)
        for month_val in months:
            month_count = sum(
                len(products_by_date[year][month_val][d])
                for d in products_by_date[year][month_val]
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
        
        days = sorted(products_by_date[year][month].keys(), reverse=True)
        for day_val in days:
            day_count = len(products_by_date[year][month][day_val])
            buttons.append([InlineKeyboardButton(
                text=f"📅 {day_val} {month_name} ({day_count} товаров)",
                callback_data=f"products_archive_day_{year}_{month}_{day_val}"
            )])
        
        buttons.append([InlineKeyboardButton(
            text="⬅️ Назад к месяцам",
            callback_data=f"products_archive_year_{year}"
        )])
    
    else:
        # Уровень дня - показываем товары за этот день
        month_names = {
            1: "Января", 2: "Февраля", 3: "Марта", 4: "Апреля",
            5: "Мая", 6: "Июня", 7: "Июля", 8: "Августа",
            9: "Сентября", 10: "Октября", 11: "Ноября", 12: "Декабря"
        }
        month_name = month_names.get(month, str(month))
        response_text = f"📁 Архив товаров за {day} {month_name} {year} года:\n\n"
        
        day_products = products_by_date[year][month][day]
        for i, product in enumerate(day_products, 1):
            product_name = product.get("name", "Без названия")
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
    
    # Кнопка назад в меню товаров
    buttons.append([InlineKeyboardButton(
        text="⬅️ Назад в меню товаров",
        callback_data="products_menu"
    )])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_message(message, response_text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("products_archive_year_"))
async def products_archive_year(callback: CallbackQuery):
    """Показать месяцы выбранного года."""
    try:
        year = int(callback.data.replace("products_archive_year_", ""))
    except ValueError:
        await callback.answer("Ошибка")
        return
    await show_archived_products(callback.message, year=year)
    await callback.answer()


@router.callback_query(F.data.startswith("products_archive_month_"))
async def products_archive_month(callback: CallbackQuery):
    """Показать дни выбранного месяца."""
    try:
        parts = callback.data.replace("products_archive_month_", "").split("_")
        year = int(parts[0])
        month = int(parts[1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка")
        return
    await show_archived_products(callback.message, year=year, month=month)
    await callback.answer()


@router.callback_query(F.data.startswith("products_archive_day_"))
async def products_archive_day(callback: CallbackQuery):
    """Показать товары выбранного дня."""
    try:
        parts = callback.data.replace("products_archive_day_", "").split("_")
        year = int(parts[0])
        month = int(parts[1])
        day = int(parts[2])
    except (ValueError, IndexError):
        await callback.answer("Ошибка")
        return
    await show_archived_products(callback.message, year=year, month=month, day=day)
    await callback.answer()


async def process_product_unavailable(product_id: int, payment_method: Optional[str], mark_telegram_enabled: bool, callback: CallbackQuery, state: FSMContext):
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

    if payment_method:
        from app.db.database import SessionLocal
        from app.api.models.product import Product
        import math

        db = SessionLocal()
        try:
            db_product = db.query(Product).filter(Product.id == product_id).first()
            if db_product:
                db_product.payment_method = payment_method
                price_str = product.get("price", "")
                price_clean = re.sub(r"[^\d.,]", "", price_str)
                price_clean = price_clean.replace(",", ".")
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

    result = await update_product_status_api(product_id, "unavailable", sync_platforms=False)
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
        text = f"📦 <b>{refreshed.get('name', 'Без названия')}</b>\n\n"
        if refreshed.get("price"):
            text += f"💵 Цена: {refreshed['price']}\n"
        if refreshed.get("category_name"):
            text += f"📂 Категория: {refreshed['category_name']}\n"
        if refreshed.get("collection_name"):
            text += f"📁 Подборка: {refreshed['collection_name']}\n"
        status_emoji = {"active": "✅", "unavailable": "🚫", "deleted": "🗑️"}
        status_text = {"active": "Активен", "unavailable": "Недоступен", "deleted": "Удален"}
        text += f"\n{status_emoji.get(status, '❓')} Статус: {status_text.get(status, status)}\n"
        text += format_product_platform_links_html(refreshed)
        await safe_edit_message(
            callback.message,
            text,
            reply_markup=get_product_detail_keyboard(
                product_id, status, back_data=back_data
            ),
            parse_mode="HTML",
        )

    await callback.answer("✅ Помечен недоступным")
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
    
    # Обрабатываем пометку как недоступный с выбранным способом оплаты
    await process_product_unavailable(product_id, payment_method, mark_telegram_enabled, callback, state)


async def send_vk_report(product: dict, payment_method: str):
    """Отправить отчет о продаже в ВК пользователям."""
    try:
        from app.config.settings import VK_ACCESS_TOKEN
        from app.services.settings_service import get_settings_service
        import vk_api
        import re
        import math

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
        vk_session = vk_api.VkApi(token=VK_ACCESS_TOKEN)
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

        from app.db.database import SessionLocal
        from app.api.models.post import Post

        db = SessionLocal()
        try:
            post = db.query(Post).filter(Post.telegram_link == telegram_link).first()
            if not post:
                logger.warning("Post with telegram_link %s not found in database", telegram_link)
                return False

            original_text = post.text or ""

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
                if post.photos or post.videos:
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
        finally:
            db.close()

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
        
        from app.db.database import SessionLocal
        from app.api.models.post import Post
        
        def _parse_retry_seconds(err: Exception) -> int:
            s = str(err)
            m = re.search(r'[Rr]etry in (\d+) seconds', s) or re.search(r'retry after (\d+)', s)
            return int(m.group(1)) if m else 0
        
        db = SessionLocal()
        try:
            post = db.query(Post).filter(Post.telegram_link == telegram_link).first()
            if not post:
                logger.warning(f"Post with telegram_link {telegram_link} not found in database")
                return
            
            original_text = post.text or ""
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
                if post.photos or post.videos:
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
        finally:
            db.close()
    
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
        
        # Получаем пост из БД
        from app.db.database import SessionLocal
        from app.api.models.post import Post
        
        db = SessionLocal()
        try:
            post = db.query(Post).filter(Post.telegram_link == telegram_link).first()
            if not post:
                logger.warning(f"Post with telegram_link {telegram_link} not found in database")
                return False
            
            # Получаем исходный текст поста (без форматирования для Telegram)
            original_text = post.text or ""
            
            # Если текст уже содержит #неактуально, не добавляем его снова
            if original_text.startswith("#неактуально") or original_text.startswith("\\#неактуально"):
                logger.info(f"Message {message_id} already marked as unavailable")
                return True
            
            # Формируем новый текст с пометкой
            text_with_unavailable = f"#неактуально\n\n{original_text}"
            
            # Применяем форматирование для Telegram (как при публикации)
            from app.utils.text_formatter import format_for_telegram, format_for_telegram_plain
            from aiogram.enums import ParseMode
            import asyncio
            formatted_text = format_for_telegram(text_with_unavailable, signature_enabled=True)
            plain_text = format_for_telegram_plain(text_with_unavailable, signature_enabled=True)
            logger.info(f"New text length: {len(formatted_text)}, post has media: {bool(post.photos or post.videos)}")
            
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
                if post.photos or post.videos:
                    # Сообщение с медиа - редактируем caption
                    logger.info(f"Editing caption for media message {message_id}")
                    try:
                        await _edit_caption_with_retry()
                        logger.info(f"Telegram post {message_id} caption marked as unavailable successfully")
                        edit_ok = True
                    except Exception as e:
                        logger.error(f"Error editing Telegram message caption {message_id}: {str(e)}")
                        # При Flood control ждём перед fallback; при повторном лимите — ждём ещё и пробуем снова
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
                    # Текстовое сообщение - редактируем текст
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
        finally:
            db.close()
    
    except Exception as e:
        logger.error(f"Error marking Telegram post as unavailable: {str(e)}", exc_info=True)
        return False


def _extract_max_message_id(max_link: str) -> Optional[str]:
    match = re.search(r"/([^/]+)$", max_link or "")
    if not match:
        return None
    return match.group(1)


async def update_max_post_price(max_link: str, old_price: str, new_price: str) -> bool:
    try:
        from app.api.models.post import Post
        from app.config.settings import MAX_API_BASE_URL, MAX_BOT_TOKEN
        from app.services.settings_service import get_settings_service
        MAX_CHANNEL_ID = get_settings_service().get_max_channel_id()
        from app.db.database import SessionLocal
        from app.integrations.max.client import MaxApiClient
        from app.utils.text_formatter import format_for_max, format_for_max_plain

        message_id = _extract_max_message_id(max_link)
        if not message_id:
            logger.error("Could not extract max message_id from link: %s", max_link)
            return False

        db = SessionLocal()
        try:
            post = db.query(Post).filter(Post.max_link == max_link).first()
            if not post:
                logger.warning("Post with max_link %s not found in DB", max_link)
                return False

            original_text = post.text or ""
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

            client = MaxApiClient(MAX_BOT_TOKEN, MAX_API_BASE_URL)
            formatted_text = format_for_max(updated_text, signature_enabled=True)
            plain_text = format_for_max_plain(updated_text, signature_enabled=True)
            if post.photos or post.videos:
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
        finally:
            db.close()
    except Exception as exc:
        logger.error("Error updating Max post price: %s", exc, exc_info=True)
        return False


async def remove_max_post_unavailable(max_link: str):
    try:
        from app.api.models.post import Post
        from app.config.settings import MAX_API_BASE_URL, MAX_BOT_TOKEN
        from app.services.settings_service import get_settings_service
        MAX_CHANNEL_ID = get_settings_service().get_max_channel_id()
        from app.db.database import SessionLocal
        from app.integrations.max.client import MaxApiClient
        from app.utils.text_formatter import format_for_max, format_for_max_plain

        message_id = _extract_max_message_id(max_link)
        if not message_id:
            logger.error("Could not extract max message_id from link: %s", max_link)
            return

        db = SessionLocal()
        try:
            post = db.query(Post).filter(Post.max_link == max_link).first()
            if not post:
                return
            original_text = post.text or ""
            text_without_unavailable = original_text
            if text_without_unavailable.startswith("#неактуально"):
                text_without_unavailable = text_without_unavailable.replace("#неактуально", "", 1).strip()
            elif text_without_unavailable.startswith("\\#неактуально"):
                text_without_unavailable = text_without_unavailable.replace("\\#неактуально", "", 1).strip()

            client = MaxApiClient(MAX_BOT_TOKEN, MAX_API_BASE_URL)
            formatted_text = format_for_max(text_without_unavailable, signature_enabled=True)
            plain_text = format_for_max_plain(text_without_unavailable, signature_enabled=True)
            if post.photos or post.videos:
                try:
                    await client.edit_message_caption(MAX_CHANNEL_ID, message_id, formatted_text, parse_mode="MarkdownV2")
                except Exception:
                    await client.edit_message_caption(MAX_CHANNEL_ID, message_id, plain_text)
            else:
                try:
                    await client.edit_message_text(MAX_CHANNEL_ID, message_id, formatted_text, parse_mode="MarkdownV2")
                except Exception:
                    await client.edit_message_text(MAX_CHANNEL_ID, message_id, plain_text)
        finally:
            db.close()
    except Exception as exc:
        logger.error("Error removing #неактуально in Max post: %s", exc, exc_info=True)


async def mark_max_post_unavailable(max_link: str) -> bool:
    try:
        from app.api.models.post import Post
        from app.config.settings import MAX_API_BASE_URL, MAX_BOT_TOKEN
        from app.services.settings_service import get_settings_service
        MAX_CHANNEL_ID = get_settings_service().get_max_channel_id()
        from app.db.database import SessionLocal
        from app.integrations.max.client import MaxApiClient
        from app.utils.text_formatter import format_for_max, format_for_max_plain

        message_id = _extract_max_message_id(max_link)
        if not message_id:
            logger.error("Could not extract max message_id from link: %s", max_link)
            return False

        db = SessionLocal()
        try:
            post = db.query(Post).filter(Post.max_link == max_link).first()
            if not post:
                return False
            original_text = post.text or ""
            if original_text.startswith("#неактуально") or original_text.startswith("\\#неактуально"):
                return True
            text_with_unavailable = f"#неактуально\n\n{original_text}"

            client = MaxApiClient(MAX_BOT_TOKEN, MAX_API_BASE_URL)
            formatted_text = format_for_max(text_with_unavailable, signature_enabled=True)
            plain_text = format_for_max_plain(text_with_unavailable, signature_enabled=True)
            if post.photos or post.videos:
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
        finally:
            db.close()
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
        return ok
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


def get_iphone_model_year(model_name: str) -> int:
    """Получить год выхода модели iPhone для сортировки."""
    model_lower = model_name.lower()
    
    # Словарь соответствия моделей и годов
    year_map = {
        "iphone x": 2017,
        "iphone xs": 2018,
        "iphone xs max": 2018,
        "iphone xr": 2018,
        "iphone 11": 2019,
        "iphone 11 pro": 2019,
        "iphone 11 pro max": 2019,
        "iphone se": 2020,  # SE 2020
        "iphone 12": 2020,
        "iphone 12 mini": 2020,
        "iphone 12 pro": 2020,
        "iphone 12 pro max": 2020,
        "iphone 13": 2021,
        "iphone 13 mini": 2021,
        "iphone 13 pro": 2021,
        "iphone 13 pro max": 2021,
        "iphone se 2022": 2022,
        "iphone 14": 2022,
        "iphone 14 plus": 2022,
        "iphone 14 pro": 2022,
        "iphone 14 pro max": 2022,
        "iphone 15": 2023,
        "iphone 15 plus": 2023,
        "iphone 15 pro": 2023,
        "iphone 15 pro max": 2023,
        "iphone 16": 2024,
        "iphone 16e": 2024,
        "iphone 16 plus": 2024,
        "iphone 16 pro": 2024,
        "iphone 16 pro max": 2024,
        "iphone 17": 2025,
        "iphone 17e": 2025,
        "iphone 17 pro": 2025,
        "iphone 17 pro max": 2025,
        "iphone air": 2024,
    }
    
    # Проверяем точные совпадения
    for model_key, year in year_map.items():
        if model_key in model_lower:
            return year
    
    # Если не найдено, пытаемся извлечь номер модели
    match = re.search(r'iphone\s+(\d+)', model_lower)
    if match:
        model_num = int(match.group(1))
        # Базовый год для iPhone 12 = 2020
        base_year = 2020
        return base_year + (model_num - 12)
    
    # По умолчанию возвращаем 2020
    return 2020


async def get_today_sold_products() -> list:
    """Получить товары, проданные сегодня (перемещенные в архив сегодня)."""
    from datetime import datetime, timezone, date
    from app.db.database import SessionLocal
    from app.api.models.product import Product
    
    db = SessionLocal()
    try:
        today = date.today()
        
        # Получаем товары, архивированные сегодня
        products = db.query(Product).filter(
            Product.status == "unavailable",
            Product.archived_at.isnot(None)
        ).all()
        
        # Фильтруем по дате архивации (сегодня)
        today_products = []
        for product in products:
            if product.archived_at:
                archived_date = product.archived_at.date()
                if archived_date == today:
                    today_products.append({
                        'id': product.id,
                        'name': product.name,
                        'price': product.price,
                        'payment_method': product.payment_method,
                        'final_price': product.final_price,
                        'archived_at': product.archived_at.isoformat() if product.archived_at else None
                    })
        
        return today_products
    finally:
        db.close()


@router.callback_query(F.data == "evening_report_start")
async def evening_report_start(callback: CallbackQuery, state: FSMContext):
    """Начать создание вечернего отчета."""
    # Получаем товары, проданные сегодня
    today_products = await get_today_sold_products()
    
    if not today_products:
        await callback.answer("Сегодня нет проданных товаров", show_alert=True)
        return
    
    # Инициализируем состояние
    await state.update_data(
        today_products=today_products,
        additional_expenses=[]
    )
    
    text = "📊 Вечерний отчет\n\nВведите касса на утро:"
    await safe_edit_message(callback.message, text)
    await state.set_state(EveningReport.waiting_for_morning_cash)
    await callback.answer()


@router.message(EveningReport.waiting_for_morning_cash)
async def process_morning_cash(message: Message, state: FSMContext):
    """Обработать ввод кассы на утро."""
    try:
        morning_cash = float(message.text.strip().replace(' ', '').replace(',', '.'))
        await state.update_data(morning_cash=morning_cash)
        text = "📊 Вечерний отчет\n\nКасса на утро: {:.0f}₽\n\nВведите за день:".format(morning_cash)
        await message.answer(text)
        await state.set_state(EveningReport.waiting_for_day_cash)
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число.")


@router.message(EveningReport.waiting_for_day_cash)
async def process_day_cash(message: Message, state: FSMContext):
    """Обработать ввод за день."""
    try:
        day_cash = float(message.text.strip().replace(' ', '').replace(',', '.'))
        await state.update_data(day_cash=day_cash)
        data = await state.get_data()
        text = f"📊 Вечерний отчет\n\nКасса на утро: {data['morning_cash']:.0f}₽\nЗа день: {day_cash:.0f}₽\n\nВведите бн:"
        await message.answer(text)
        await state.set_state(EveningReport.waiting_for_bn)
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число.")


@router.message(EveningReport.waiting_for_bn)
async def process_bn(message: Message, state: FSMContext):
    """Обработать ввод бн."""
    try:
        bn = float(message.text.strip().replace(' ', '').replace(',', '.'))
        await state.update_data(bn=bn)
        data = await state.get_data()
        text = f"📊 Вечерний отчет\n\nКасса на утро: {data['morning_cash']:.0f}₽\nЗа день: {data['day_cash']:.0f}₽\nБн: {bn:.0f}₽\n\nВведите новый аванс (или отправьте 'далее' для пропуска):"
        await message.answer(text)
        await state.set_state(EveningReport.waiting_for_new_advance)
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число.")


@router.message(EveningReport.waiting_for_new_advance)
async def process_new_advance(message: Message, state: FSMContext):
    """Обработать ввод нового аванса."""
    text_lower = message.text.strip().lower()
    if text_lower in ['далее', 'пропустить', 'skip']:
        await state.update_data(new_advance=None)
    else:
        try:
            new_advance = float(text_lower.replace(' ', '').replace(',', '.'))
            await state.update_data(new_advance=new_advance)
        except ValueError:
            await message.answer("❌ Пожалуйста, введите число или 'далее' для пропуска.")
            return
    
    data = await state.get_data()
    text = f"📊 Вечерний отчет\n\nКасса на утро: {data['morning_cash']:.0f}₽\nЗа день: {data['day_cash']:.0f}₽\nБн: {data['bn']:.0f}₽\n"
    if data.get('new_advance'):
        text += f"Новый аванс: {data['new_advance']:.0f}₽\n"
    text += "\nВведите старый аванс (или отправьте 'далее' для пропуска):"
    await message.answer(text)
    await state.set_state(EveningReport.waiting_for_old_advance)


@router.message(EveningReport.waiting_for_old_advance)
async def process_old_advance(message: Message, state: FSMContext):
    """Обработать ввод старого аванса."""
    text_lower = message.text.strip().lower()
    if text_lower in ['далее', 'пропустить', 'skip']:
        await state.update_data(old_advance=None)
    else:
        try:
            old_advance = float(text_lower.replace(' ', '').replace(',', '.'))
            await state.update_data(old_advance=old_advance)
        except ValueError:
            await message.answer("❌ Пожалуйста, введите число или 'далее' для пропуска.")
            return
    
    data = await state.get_data()
    text = f"📊 Вечерний отчет\n\nКасса на утро: {data['morning_cash']:.0f}₽\nЗа день: {data['day_cash']:.0f}₽\nБн: {data['bn']:.0f}₽\n"
    if data.get('new_advance'):
        text += f"Новый аванс: {data['new_advance']:.0f}₽\n"
    if data.get('old_advance'):
        text += f"Старый аванс: {data['old_advance']:.0f}₽\n"
    text += "\nВведите сдано (или отправьте 'далее' для пропуска):"
    await message.answer(text)
    await state.set_state(EveningReport.waiting_for_surrendered)


@router.message(EveningReport.waiting_for_surrendered)
async def process_surrendered(message: Message, state: FSMContext):
    """Обработать ввод сдано."""
    text_lower = message.text.strip().lower()
    if text_lower in ['далее', 'пропустить', 'skip']:
        await state.update_data(surrendered=None)
    else:
        try:
            surrendered = float(text_lower.replace(' ', '').replace(',', '.'))
            await state.update_data(surrendered=surrendered)
        except ValueError:
            await message.answer("❌ Пожалуйста, введите число или 'далее' для пропуска.")
            return
    
    data = await state.get_data()
    text = f"📊 Вечерний отчет\n\nКасса на утро: {data['morning_cash']:.0f}₽\nЗа день: {data['day_cash']:.0f}₽\nБн: {data['bn']:.0f}₽\n"
    if data.get('new_advance'):
        text += f"Новый аванс: {data['new_advance']:.0f}₽\n"
    if data.get('old_advance'):
        text += f"Старый аванс: {data['old_advance']:.0f}₽\n"
    if data.get('surrendered'):
        text += f"Сдано: {data['surrendered']:.0f}₽\n"
    text += "\nВведите выкупы (или отправьте 'далее' для пропуска):"
    await message.answer(text)
    await state.set_state(EveningReport.waiting_for_buybacks)


@router.message(EveningReport.waiting_for_buybacks)
async def process_buybacks(message: Message, state: FSMContext):
    """Обработать ввод выкупов."""
    text_lower = message.text.strip().lower()
    if text_lower in ['далее', 'пропустить', 'skip']:
        await state.update_data(buybacks=None)
    else:
        try:
            buybacks = float(text_lower.replace(' ', '').replace(',', '.'))
            await state.update_data(buybacks=buybacks)
        except ValueError:
            await message.answer("❌ Пожалуйста, введите число или 'далее' для пропуска.")
            return
    
    data = await state.get_data()
    text = f"📊 Вечерний отчет\n\nКасса на утро: {data['morning_cash']:.0f}₽\nЗа день: {data['day_cash']:.0f}₽\nБн: {data['bn']:.0f}₽\n"
    if data.get('new_advance'):
        text += f"Новый аванс: {data['new_advance']:.0f}₽\n"
    if data.get('old_advance'):
        text += f"Старый аванс: {data['old_advance']:.0f}₽\n"
    if data.get('surrendered'):
        text += f"Сдано: {data['surrendered']:.0f}₽\n"
    if data.get('buybacks'):
        text += f"Выкупы: {data['buybacks']:.0f}₽\n"
    text += "\nВведите опт (или отправьте 'далее' для пропуска):"
    await message.answer(text)
    await state.set_state(EveningReport.waiting_for_wholesale)


@router.message(EveningReport.waiting_for_wholesale)
async def process_wholesale(message: Message, state: FSMContext):
    """Обработать ввод опт."""
    text_lower = message.text.strip().lower()
    if text_lower in ['далее', 'пропустить', 'skip']:
        await state.update_data(wholesale=None)
    else:
        try:
            wholesale = float(text_lower.replace(' ', '').replace(',', '.'))
            await state.update_data(wholesale=wholesale)
        except ValueError:
            await message.answer("❌ Пожалуйста, введите число или 'далее' для пропуска.")
            return
    
    data = await state.get_data()
    text = f"📊 Вечерний отчет\n\nКасса на утро: {data['morning_cash']:.0f}₽\nЗа день: {data['day_cash']:.0f}₽\nБн: {data['bn']:.0f}₽\n"
    if data.get('new_advance'):
        text += f"Новый аванс: {data['new_advance']:.0f}₽\n"
    if data.get('old_advance'):
        text += f"Старый аванс: {data['old_advance']:.0f}₽\n"
    if data.get('surrendered'):
        text += f"Сдано: {data['surrendered']:.0f}₽\n"
    if data.get('buybacks'):
        text += f"Выкупы: {data['buybacks']:.0f}₽\n"
    if data.get('wholesale'):
        text += f"Опт: {data['wholesale']:.0f}₽\n"
    text += "\nВведите кредит (или отправьте 'далее' для пропуска):"
    await message.answer(text)
    await state.set_state(EveningReport.waiting_for_credit)


@router.message(EveningReport.waiting_for_credit)
async def process_credit(message: Message, state: FSMContext):
    """Обработать ввод кредит."""
    text_lower = message.text.strip().lower()
    if text_lower in ['далее', 'пропустить', 'skip']:
        await state.update_data(credit=None)
    else:
        try:
            credit = float(text_lower.replace(' ', '').replace(',', '.'))
            await state.update_data(credit=credit)
        except ValueError:
            await message.answer("❌ Пожалуйста, введите число или 'далее' для пропуска.")
            return
    
    data = await state.get_data()
    text = f"📊 Вечерний отчет\n\nКасса на утро: {data['morning_cash']:.0f}₽\nЗа день: {data['day_cash']:.0f}₽\nБн: {data['bn']:.0f}₽\n"
    if data.get('new_advance'):
        text += f"Новый аванс: {data['new_advance']:.0f}₽\n"
    if data.get('old_advance'):
        text += f"Старый аванс: {data['old_advance']:.0f}₽\n"
    if data.get('surrendered'):
        text += f"Сдано: {data['surrendered']:.0f}₽\n"
    if data.get('buybacks'):
        text += f"Выкупы: {data['buybacks']:.0f}₽\n"
    if data.get('wholesale'):
        text += f"Опт: {data['wholesale']:.0f}₽\n"
    if data.get('credit'):
        text += f"Кредит: {data['credit']:.0f}₽\n"
    text += "\nВведите нф (формат: число (число в скобках), например 142900 (40230)):"
    await message.answer(text)
    await state.set_state(EveningReport.waiting_for_nf)


@router.message(EveningReport.waiting_for_nf)
async def process_nf(message: Message, state: FSMContext):
    """Обработать ввод нф."""
    nf_text = message.text.strip()
    await state.update_data(nf=nf_text)
    
    data = await state.get_data()
    text = f"📊 Вечерний отчет\n\nКасса на утро: {data['morning_cash']:.0f}₽\nЗа день: {data['day_cash']:.0f}₽\nБн: {data['bn']:.0f}₽\n"
    if data.get('new_advance'):
        text += f"Новый аванс: {data['new_advance']:.0f}₽\n"
    if data.get('old_advance'):
        text += f"Старый аванс: {data['old_advance']:.0f}₽\n"
    if data.get('surrendered'):
        text += f"Сдано: {data['surrendered']:.0f}₽\n"
    if data.get('buybacks'):
        text += f"Выкупы: {data['buybacks']:.0f}₽\n"
    if data.get('wholesale'):
        text += f"Опт: {data['wholesale']:.0f}₽\n"
    if data.get('credit'):
        text += f"Кредит: {data['credit']:.0f}₽\n"
    text += f"Нф: {nf_text}\n\nЕще расходы? (Да/Нет или далее)"
    await message.answer(text)
    await state.set_state(EveningReport.waiting_for_additional_expenses)


@router.message(EveningReport.waiting_for_additional_expenses)
async def process_additional_expenses_question(message: Message, state: FSMContext):
    """Обработать вопрос о дополнительных расходах."""
    text_lower = message.text.strip().lower()
    if text_lower in ['нет', 'нет', 'далее', 'пропустить', 'skip', 'no']:
        # Переходим к расчету и показу отчета
        await show_evening_report(message, state)
    elif text_lower in ['да', 'yes']:
        text = "Введите название расхода (например: тонер, авито доставка, скобы):"
        await message.answer(text)
        await state.set_state(EveningReport.waiting_for_expense_name)
    else:
        await message.answer("❌ Пожалуйста, ответьте 'Да' или 'Нет' (или 'далее').")


@router.message(EveningReport.waiting_for_expense_name)
async def process_expense_name(message: Message, state: FSMContext):
    """Обработать ввод названия расхода."""
    expense_name = message.text.strip()
    await state.update_data(current_expense_name=expense_name)
    text = f"Введите сумму для '{expense_name}':"
    await message.answer(text)
    await state.set_state(EveningReport.waiting_for_expense_amount)


@router.message(EveningReport.waiting_for_expense_amount)
async def process_expense_amount(message: Message, state: FSMContext):
    """Обработать ввод суммы расхода."""
    try:
        expense_amount = float(message.text.strip().replace(' ', '').replace(',', '.'))
        data = await state.get_data()
        expense_name = data.get('current_expense_name', 'Расход')
        
        additional_expenses = data.get('additional_expenses', [])
        additional_expenses.append({'name': expense_name, 'amount': expense_amount})
        await state.update_data(additional_expenses=additional_expenses)
        
        text = f"Расход '{expense_name}' на сумму {expense_amount:.0f}₽ добавлен.\n\nЕще расходы? (Да/Нет или далее)"
        await message.answer(text)
        await state.set_state(EveningReport.waiting_for_additional_expenses)
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число.")


async def show_evening_report(message: Message, state: FSMContext):
    """Показать итоговый вечерний отчет."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from datetime import date
    
    data = await state.get_data()
    today_products = data.get('today_products', [])
    
    # Получаем информацию о продажах из истории (нужно получить из БД или из отчетов)
    # Пока используем товары, проданные сегодня
    # Нужно получить информацию о способе оплаты и цене из истории отправки отчетов
    # Для упрощения, будем использовать цену товара и определять способ оплаты по эмодзи в цене
    
    # Сортируем товары по году выхода модели (от старых к новым)
    def sort_key(product):
        model_name = product.get('name', '')
        # Парсим модель iPhone
        from app.utils.iphone_parser import parse_iphone_model
        model = parse_iphone_model(model_name)
        if model:
            year = get_iphone_model_year(model)
            return year
        return 2020  # По умолчанию
    
    sorted_products = sorted(today_products, key=sort_key)
    
    # Формируем отчет
    report_lines = []
    
    # Добавляем проданные товары
    for i, product in enumerate(sorted_products, 1):
        product_name = product.get('name', 'Без названия')
        # Используем final_price, если есть, иначе используем price
        final_price = product.get('final_price') or product.get('price', '0₽')
        # Если final_price содержит эмодзи, используем его как есть
        # Иначе добавляем эмодзи в зависимости от payment_method
        if not final_price or final_price == '0₽':
            price_str = product.get('price', '0₽')
            payment_method = product.get('payment_method', 'cash')
            if payment_method == 'card':
                # Вычисляем цену с +5%
                price_clean = re.sub(r'[^\d.,]', '', price_str)
                price_clean = price_clean.replace(',', '.')
                try:
                    base_price = float(price_clean)
                    new_price = base_price * 1.05
                    new_price = math.ceil(new_price / 10) * 10
                    final_price = f"{int(new_price)}₽💳"
                except (ValueError, TypeError):
                    final_price = f"{price_str}💳"
            elif payment_method == 'credit':
                final_price = f"{price_str}🏦"
            else:
                final_price = f"{price_str}💰"
        
        report_lines.append(f"{i}. {product_name} - {final_price}")
    
    # Добавляем пустую строку
    report_lines.append("")
    
    # Добавляем финансовые данные
    morning_cash = data.get('morning_cash', 0)
    day_cash = data.get('day_cash', 0)
    bn = data.get('bn', 0)
    new_advance = data.get('new_advance', 0) or 0
    old_advance = data.get('old_advance', 0) or 0
    surrendered = data.get('surrendered', 0) or 0
    buybacks = data.get('buybacks', 0) or 0
    wholesale = data.get('wholesale', 0) or 0
    credit = data.get('credit', 0) or 0
    nf = data.get('nf', '')
    additional_expenses = data.get('additional_expenses', [])
    
    # Рассчитываем итоговую сумму
    total_expenses = sum(exp.get('amount', 0) for exp in additional_expenses)
    final_cash = morning_cash + day_cash - bn - credit + new_advance - surrendered - old_advance - buybacks + wholesale - total_expenses
    
    report_lines.append(f"касса на утро {morning_cash:.0f}")
    report_lines.append(f"за день {day_cash:.0f}")
    if bn:
        report_lines.append(f"бн {bn:.0f}")
    if wholesale:
        report_lines.append(f"опт {wholesale:.0f}")
    if surrendered:
        report_lines.append(f"сдано {surrendered:.0f}")
    if buybacks:
        report_lines.append(f"выкуп {buybacks:.0f}")
    if nf:
        report_lines.append(f"нф {nf}")
    report_lines.append(f"в кассе {final_cash:.0f}")
    
    # Добавляем дополнительные расходы, если есть
    for exp in additional_expenses:
        report_lines.insert(-1, f"{exp['name']} {exp['amount']:.0f}")
    
    report_text = "\n".join(report_lines)
    
    # Показываем отчет с кнопкой подтверждения
    buttons = [
        [ikb("✅ Отправить отчет", "evening_report_send")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="products_archive")]
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    await message.answer(f"📊 Вечерний отчет:\n\n{report_text}", reply_markup=keyboard)
    await state.set_state(EveningReport.waiting_for_final_confirmation)


@router.callback_query(F.data == "evening_report_send")
async def send_evening_report(callback: CallbackQuery, state: FSMContext):
    """Отправить вечерний отчет в ВК."""
    from app.config.settings import VK_ACCESS_TOKEN
    from app.services.settings_service import get_settings_service
    import vk_api

    VK_REPORT_USER_IDS = get_settings_service().get_vk_report_user_ids()
    data = await state.get_data()
    today_products = data.get('today_products', [])
    
    # Формируем отчет (такой же, как в show_evening_report)
    report_lines = []
    
    # Сортируем товары
    def sort_key(product):
        model_name = product.get('name', '')
        from app.utils.iphone_parser import parse_iphone_model
        model = parse_iphone_model(model_name)
        if model:
            year = get_iphone_model_year(model)
            return year
        return 2020
    
    sorted_products = sorted(today_products, key=sort_key)
    
    for i, product in enumerate(sorted_products, 1):
        product_name = product.get('name', 'Без названия')
        # Используем final_price, если есть, иначе используем price
        final_price = product.get('final_price') or product.get('price', '0₽')
        # Если final_price содержит эмодзи, используем его как есть
        # Иначе добавляем эмодзи в зависимости от payment_method
        if not final_price or final_price == '0₽':
            price_str = product.get('price', '0₽')
            payment_method = product.get('payment_method', 'cash')
            if payment_method == 'card':
                # Вычисляем цену с +5%
                price_clean = re.sub(r'[^\d.,]', '', price_str)
                price_clean = price_clean.replace(',', '.')
                try:
                    base_price = float(price_clean)
                    new_price = base_price * 1.05
                    new_price = math.ceil(new_price / 10) * 10
                    final_price = f"{int(new_price)}₽💳"
                except (ValueError, TypeError):
                    final_price = f"{price_str}💳"
            elif payment_method == 'credit':
                final_price = f"{price_str}🏦"
            else:
                final_price = f"{price_str}💰"
        
        report_lines.append(f"{i}. {product_name} - {final_price}")
    
    report_lines.append("")
    
    morning_cash = data.get('morning_cash', 0)
    day_cash = data.get('day_cash', 0)
    bn = data.get('bn', 0)
    new_advance = data.get('new_advance', 0) or 0
    old_advance = data.get('old_advance', 0) or 0
    surrendered = data.get('surrendered', 0) or 0
    buybacks = data.get('buybacks', 0) or 0
    wholesale = data.get('wholesale', 0) or 0
    credit = data.get('credit', 0) or 0
    nf = data.get('nf', '')
    additional_expenses = data.get('additional_expenses', [])
    
    total_expenses = sum(exp.get('amount', 0) for exp in additional_expenses)
    final_cash = morning_cash + day_cash - bn - credit + new_advance - surrendered - old_advance - buybacks + wholesale - total_expenses
    
    report_lines.append(f"касса на утро {morning_cash:.0f}")
    report_lines.append(f"за день {day_cash:.0f}")
    if bn:
        report_lines.append(f"бн {bn:.0f}")
    if wholesale:
        report_lines.append(f"опт {wholesale:.0f}")
    if surrendered:
        report_lines.append(f"сдано {surrendered:.0f}")
    if buybacks:
        report_lines.append(f"выкуп {buybacks:.0f}")
    if nf:
        report_lines.append(f"нф {nf}")
    report_lines.append(f"в кассе {final_cash:.0f}")
    
    for exp in additional_expenses:
        report_lines.insert(-1, f"{exp['name']} {exp['amount']:.0f}")
    
    report_text = "\n".join(report_lines)
    
    # Отправляем отчет в ВК
    if not VK_REPORT_USER_IDS:
        await callback.answer("VK_REPORT_USER_IDS не настроен", show_alert=True)
        return
    
    try:
        vk_session = vk_api.VkApi(token=VK_ACCESS_TOKEN)
        vk = vk_session.get_api()
        
        for user_id in VK_REPORT_USER_IDS:
            try:
                vk.messages.send(
                    user_id=user_id,
                    message=report_text,
                    random_id=0
                )
                logger.info(f"Evening report sent to VK user {user_id}")
            except Exception as e:
                logger.error(f"Error sending evening report to VK user {user_id}: {str(e)}")
        
        await callback.answer("✅ Отчет отправлен")
        await safe_edit_message(callback.message, "✅ Вечерний отчет отправлен!", reply_markup=get_products_menu_keyboard())
        await state.clear()
    except Exception as e:
        logger.error(f"Error sending evening report: {str(e)}")
        await callback.answer("❌ Ошибка при отправке отчета", show_alert=True)

