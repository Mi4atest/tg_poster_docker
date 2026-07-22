"""
Обработчики для управления новыми товарами (из подборок ВК).
"""
from __future__ import annotations

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, LinkPreviewOptions, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ChatAction
import logging
import re
from html import escape
from typing import Optional, Dict, List, Any

from app.bot.keyboards.product_keyboard import get_products_menu_keyboard
from app.bot.keyboards.new_products_keyboard import (
    NEW_CATEGORIES,
    format_new_iphone_var_nav,
    parse_new_iphone_var_nav,
    get_new_iphone_versions_keyboard,
    get_new_iphone_models_keyboard,
    get_new_iphone_variants_keyboard,
    get_new_iphone_storage_keyboard,
    get_new_iphone_products_keyboard,
    get_new_product_detail_keyboard,
    get_new_product_price_edit_keyboard,
    get_new_product_tag_desc_keyboard,
    get_payment_method_keyboard_new_product,
    get_airpods_models_keyboard,
    get_apple_watch_categories_keyboard,
    get_apple_watch_sizes_keyboard,
    get_ipad_models_keyboard,
)
from app.utils.iphone_parser import (
    parse_iphone_model,
    get_model_display_name,
    get_iphone_version_from_model,
    parse_iphone_memory,
    parse_iphone_storage_type,
    parse_iphone_color_key,
    get_short_model_key_for_new,
)
from app.db.database import SessionLocal, run_db
from app.api.models.product import Product
from app.services import menu_constructor_service as mcs
from app.utils.product_label import availability_line_for_product, button_label_for_product

from app.bot.handlers.product_management import (
    update_product_avito_link_api,
    resolve_product_max_link,
    update_product_status_api,
    execute_product_price_update,
    get_price_change_confirm_keyboard,
    get_product_api,
)
from app.utils.price_change import (
    analyze_price_change,
    format_price_change_confirm_prompt,
    price_string_to_int_rub,
)
logger = logging.getLogger(__name__)

router = Router()

# Значения collection_name для "новых" товаров
NEW_COLLECTION_VALUES = {"iPhone новые", "Airpods", "Apple Watch", "iPad"}

# Порядок моделей по версии (как в конструкторе меню) — для клавиатур и слияния счётчиков с custom.
IPHONE_VERSION_MODEL_ORDER: Dict[str, List[str]] = {
    "12": ["12", "12 mini", "12 Pro", "12 Pro Max"],
    "13": ["13", "13 mini", "13 Pro", "13 Pro Max"],
    "14": ["14", "14 Plus", "14 Pro", "14 Pro Max"],
    "15": ["15", "15 Plus", "15 Pro", "15 Pro Max"],
    "16": ["16", "16E", "16 Plus", "16 Pro", "16 Pro Max"],
    "17": ["Air", "17", "17E", "17 Pro", "17 Pro Max"],
}



async def safe_edit_message(message, text, reply_markup=None, parse_mode=None, disable_link_preview=False):
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
        raise
    except Exception as e:
        logger.error("Error editing message: %s", e)
        kwargs = {"reply_markup": reply_markup}
        if parse_mode:
            kwargs["parse_mode"] = parse_mode
        if disable_link_preview:
            kwargs["link_preview_options"] = LinkPreviewOptions(is_disabled=True)
        return await message.reply(text, **kwargs)


def _fetch_products_sync(limit: int = 5000):
    """Все товары прямым SQL (для вызова внутри run_db)."""
    from app.services.product_ops_service import fetch_products_list

    return fetch_products_list(limit=limit)


async def get_products_api(limit: int = 5000):
    """Получить все товары (прямой SQL в отдельном потоке, без HTTP к API)."""
    try:
        return await run_db(_fetch_products_sync, limit)
    except Exception as e:
        logger.error("Error getting products: %s", e)
        return [], 0


def _fetch_available_products_for_menu() -> List[dict]:
    """Доступные новые и custom-товары для корневого меню (raw SQL, без ORM)."""
    from sqlalchemy import text

    sql = text(
        """
        SELECT id, name, display_label, price, vk_product_link,
               availability_status, collection_name, custom_button_id
        FROM products
        WHERE availability_status = 'available'
          AND (
            collection_name = ANY(:cols)
            OR (collection_name = 'custom' AND custom_button_id IS NOT NULL)
          )
        ORDER BY id DESC
        LIMIT 500
        """
    )
    with SessionLocal() as db:
        rows = db.execute(sql, {"cols": list(NEW_COLLECTION_VALUES)}).mappings().all()
    return [dict(r) for r in rows]


def _filter_new_products(items: List[dict], collection_value: Optional[str] = None) -> List[dict]:
    lst = [p for p in items if (p.get("collection_name") or "").strip() in NEW_COLLECTION_VALUES]
    if collection_value:
        lst = [p for p in lst if (p.get("collection_name") or "").strip() == collection_value]
    return lst


def _normalize_price_display(price_str: Optional[str]) -> str:
    """Цена для компактного UI бота: целые рубли без разделителей тысяч."""
    if not price_str:
        return "—"
    s = str(price_str).strip()
    num = price_string_to_int_rub(s)
    if num is None:
        return s
    return f"{num}₽"


def _price_sort_value(row: dict) -> int:
    num = price_string_to_int_rub(row.get("price"))
    if num is not None:
        return num
    s = _normalize_price_display(row.get("price"))
    m = re.search(r"\d+", s or "")
    return int(m.group()) if m else 10**9


def _sort_products_by_price(products: List[dict]) -> List[dict]:
    """Сортировка листа меню: по цене, при равенстве — по id."""
    return sorted(
        products,
        key=lambda p: (_price_sort_value(p), int(p.get("id") or 0)),
    )


def _iphone_model_sort_order(short: str) -> float:
    """Порядок модели внутри версии iPhone."""
    mk = (short or "").lower()
    if "promax" in mk or "pro_max" in mk:
        return 4
    if "pro" in mk:
        return 3
    if "plus" in mk:
        return 2
    if "air" in mk:
        return 0.5
    if "16e" in mk or "16_e" in mk or "17e" in mk or "17_e" in mk:
        return 1
    if "mini" in mk:
        return 0.3
    return 0


def _iphone_storage_sort_order(name: str, version_num: int) -> int:
    """Порядок типа сим-карты: esim → 1+1 → 2sim (как в клавиатуре меню)."""
    st = parse_iphone_storage_type(name)
    if st == "esim":
        return 0
    if st == "1+1":
        return 1
    if st == "2sim":
        return 2
    if version_num == 17:
        return 1
    return 0


def _iphone_color_sort_order(color_emoji: Optional[str]) -> int:
    color_order_map = {
        "🟣": 1, "🟢": 2, "🔵": 3, "⚪️": 4, "⚫️": 5,
        "🟠": 6, "🟡": 7, "🌸": 8, "🔴": 9, "⭐": 10,
    }
    if color_emoji and color_emoji in color_order_map:
        return color_order_map[color_emoji]
    return 99


def _sort_iphone_products(products: List[dict]) -> List[dict]:
    """
    Сортирует товары iPhone по дереву меню, затем по цене внутри группы.
    Порядок: версия → модель → память → тип сим (17) → цена → цвет.
    """
    def sort_key(p: dict) -> tuple:
        name = p.get("name", "")
        model = parse_iphone_model(name)
        ver = get_iphone_version_from_model(model) if model else "99"
        short = get_short_model_key_for_new(model or "")
        mem = parse_iphone_memory(name)
        color_emoji = parse_iphone_color_key(name)

        version_num = int(ver) if ver.isdigit() else (17 if ver == "17" else 99)
        model_order = _iphone_model_sort_order(short)

        mem_num = 0
        if mem:
            if mem.lower() == "1tb":
                mem_num = 1024
            else:
                try:
                    mem_num = int(mem)
                except (ValueError, TypeError):
                    mem_num = 0

        storage_order = _iphone_storage_sort_order(name, version_num)
        color_order = _iphone_color_sort_order(color_emoji)
        pid = int(p.get("id") or 0)

        return (
            version_num,
            model_order,
            mem_num,
            storage_order,
            _price_sort_value(p),
            color_order,
            pid,
        )

    return sorted(products, key=sort_key)


def _iphone_version_counts(products: List[dict]) -> Dict[str, int]:
    counts = {}
    for v in ["12", "13", "14", "15", "16", "17"]:
        counts[v] = 0
    for p in products:
        name = p.get("name", "")
        model = parse_iphone_model(name)
        ver = get_iphone_version_from_model(model) if model else None
        if ver and ver in counts:
            counts[ver] += 1
        elif ver == "X" or ver == "SE":
            continue
        # Fallback для 12/14: по вхождению в названии
        elif not ver and name:
            name_lower = name.lower()
            if "iphone 12" in name_lower:
                counts["12"] = counts.get("12", 0) + 1
            elif "iphone 14" in name_lower:
                counts["14"] = counts.get("14", 0) + 1
    return counts


def _iphone_model_counts(products: List[dict], version: str) -> Dict[str, int]:
    order = IPHONE_VERSION_MODEL_ORDER.get(version, [])
    
    counts = {}
    for p in products:
        name = p.get("name", "")
        model = parse_iphone_model(name)
        ver = get_iphone_version_from_model(model) if model else None
        if ver != version:
            continue
        disp = get_model_display_name(model) if model else None
        if disp:
            counts[disp] = counts.get(disp, 0) + 1
    
    # Возвращаем только модели, которые есть в order и в counts
    return {k: counts.get(k, 0) for k in order if k in counts}


def _iphone_memory_counts(products: List[dict], version: str, model_key: str) -> Dict[str, int]:
    """Считает объёмы памяти по фактическим данным (64, 128, 256, 512, 1Tb)."""
    counts: Dict[str, int] = {}
    order = ["64", "128", "256", "512", "1Tb"]
    for p in products:
        name = p.get("name", "")
        model = parse_iphone_model(name)
        ver = get_iphone_version_from_model(model) if model else None
        short = get_short_model_key_for_new(model or "")
        if ver != version or short != model_key:
            continue
        mem = parse_iphone_memory(name)
        if mem:
            counts[mem] = counts.get(mem, 0) + 1
    return {k: counts.get(k, 0) for k in order if counts.get(k, 0) > 0}


def _iphone_storage_counts(products: List[dict], version: str, model_key: str, memory_key: str) -> Dict[str, int]:
    counts = {"esim": 0, "1+1": 0, "2sim": 0}
    memory_norm = "1Tb" if (memory_key or "").lower() == "1tb" else memory_key
    for p in products:
        name = p.get("name", "")
        model = parse_iphone_model(name)
        ver = get_iphone_version_from_model(model) if model else None
        short = get_short_model_key_for_new(model or "")
        mem = parse_iphone_memory(name)
        if ver != version or short != model_key:
            continue
        if memory_norm == "1Tb" and mem != "1Tb":
            continue
        if memory_norm != "1Tb" and (mem or "") != memory_norm:
            continue
        st = parse_iphone_storage_type(name)
        if st in counts:
            counts[st] += 1
        # iPhone 17 (базовый, Pro, Pro Max): товары без esim/2sim в названии считаем как 1+1
        elif version == "17" and st is None:
            mk = (model_key or "").lower()
            if mk == "17" or "17_pro" in mk or "17_promax" in mk:
                counts["1+1"] += 1
    return counts


def _iphone_model_display_label(version: str, model_key: str) -> str:
    """Возвращает подпись модели для списка и кнопок: 16 Plus, 13 Pro, 13 Pro Max, 16, 16E, Air (порядок: promax -> pro -> plus -> mini)."""
    mk = (model_key or "").lower()
    if "air" in mk:
        return "Air"
    if "pro_max" in mk or "promax" in mk:
        return f"{version} Pro Max"
    if "pro" in mk:
        return f"{version} Pro"
    if "plus" in mk:
        return f"{version} Plus"
    if "mini" in mk:
        return f"{version} mini"
    if "16e" in mk or "16_e" in mk:
        return "16E"
    if "17e" in mk or "17_e" in mk:
        return "17E"
    return version


def _custom_button_labels_map(products: List[dict]) -> Dict[int, str]:
    """Подписи пользовательских кнопок для custom-товаров (custom_button_id -> label)."""
    ids = {int(p["custom_button_id"]) for p in products if p.get("custom_button_id") is not None}
    if not ids:
        return {}
    out: Dict[int, str] = {}
    with SessionLocal() as db:
        for bid in ids:
            lab = mcs.get_custom_button_label(db, bid)
            if lab:
                out[bid] = lab
    return out


def _short_line_iphone(
    p: dict,
    version: str,
    model_key: str,
    memory_key: str,
    storage_key: Optional[str] = None,
    custom_labels: Optional[Dict[int, str]] = None,
) -> str:
    """Короткая строка для списка: 13 Pro 128Gb 🔵 - 39500₽ ВК или 17 Pro 256Gb ⚪️ eSim - 96900₽ ВК."""
    from app.utils.color_emoji import replace_color_with_emoji
    mem_display = "1Tb" if (memory_key or "").lower() == "1tb" else f"{memory_key}Gb"
    name = p.get("name", "")
    color_emoji = parse_iphone_color_key(name)
    if not color_emoji:
        replaced = replace_color_with_emoji(name)
        for em in ("🟣", "🟢", "🔵", "⚪️", "⚫️", "🟠", "🟡", "🌸", "🔴", "⭐"):
            if em in replaced:
                color_emoji = em
                break
        color_emoji = color_emoji or "⚫️"
    price = _normalize_price_display(p.get("price"))
    vk = p.get("vk_product_link", "")
    model_label = _iphone_model_display_label(version, model_key)
    bid = p.get("custom_button_id")
    used_custom_label = False
    if bid is not None and custom_labels:
        cl = custom_labels.get(int(bid))
        if cl:
            model_label = cl
            used_custom_label = True
    mk = (model_key or "").lower()
    # Для версии 17 показываем тип сим-карты для всех моделей (17, 17 Pro, 17 Pro Max, Air)
    if used_custom_label:
        # Для custom-товаров подпись уже может содержать память/цвет/sim, не дублируем части.
        line = f"{model_label} - {price}"
    elif version == "17":
        if storage_key:
            stor_norm = storage_key.replace("p", "+") if "p" in storage_key else storage_key
            stor_label = "eSim" if stor_norm == "esim" else "(1+1)" if stor_norm == "1+1" else "2sim"
        else:
            # Если storage_key не передан, парсим из названия товара
            st = parse_iphone_storage_type(name)
            stor_label = "eSim" if st == "esim" else "(1+1)" if (st == "1+1" or st is None) else "2sim"
        line = f"{model_label} {mem_display} {color_emoji} {stor_label} - {price}"
    else:
        line = f"{model_label} {mem_display} {color_emoji} - {price}"
    if vk:
        line += f' <a href="{vk}">ВК</a>'
    return line


def _short_line_iphone_by_product(
    p: dict, custom_labels: Optional[Dict[int, str]] = None
) -> str:
    """Короткая строка для одного товара по названию: 13 Pro 128Gb 🔵 - 39500₽ ВК или 17 Pro 256Gb ⚪️ eSim - 96900₽ ВК."""
    name = p.get("name", "")
    model = parse_iphone_model(name)
    ver = get_iphone_version_from_model(model) if model else "17"
    short = get_short_model_key_for_new(model or "")
    mem = parse_iphone_memory(name)
    st = parse_iphone_storage_type(name)
    mem_display = (mem + "Gb") if mem and mem != "1Tb" else ("1Tb" if mem == "1Tb" else "")
    color_emoji = parse_iphone_color_key(name)
    if not color_emoji:
        from app.utils.color_emoji import replace_color_with_emoji
        replaced = replace_color_with_emoji(name)
        for em in ("🟣", "🟢", "🔵", "⚪️", "⚫️", "🟠", "🟡", "🌸", "🔴", "⭐"):
            if em in replaced:
                color_emoji = em
                break
        color_emoji = color_emoji or "⚫️"
    price = _normalize_price_display(p.get("price"))
    vk = p.get("vk_product_link", "")
    model_label = _iphone_model_display_label(ver, short)
    bid = p.get("custom_button_id")
    used_custom_label = False
    if bid is not None and custom_labels:
        cl = custom_labels.get(int(bid))
        if cl:
            model_label = cl
            used_custom_label = True
    # Для версии 17 показываем тип сим-карты для всех моделей (17, 17 Pro, 17 Pro Max, Air)
    if used_custom_label:
        # Для custom-кнопок текст уже финальный, иначе получаем дубли вида "128Gb 🔵 128Gb 🔵".
        line = f"{model_label} - {price}"
    elif ver == "17" and mem_display:
        stor_label = "eSim" if st == "esim" else "(1+1)" if (st == "1+1" or st is None) else "2sim"
        line = f"{model_label} {mem_display} {color_emoji} {stor_label} - {price}"
    else:
        line = f"{model_label} {mem_display} {color_emoji} - {price}"
    if vk:
        line += f' <a href="{vk}">ВК</a>'
    return line


def _format_new_iphone_products_text(
    products: List[dict],
    version: Optional[str] = None,
    model_key: Optional[str] = None,
    memory_key: Optional[str] = None,
    storage_key: Optional[str] = None,
) -> str:
    """Форматирует список товаров iPhone (единый слой подписей product_label)."""
    del version, model_key, memory_key, storage_key
    if not products:
        return "Товары не найдены"
    lines: List[str] = []
    for p in _sort_iphone_products(products):
        nm = escape(button_label_for_product(p))
        price = _normalize_price_display(p.get("price"))
        vk = p.get("vk_product_link", "")
        if vk:
            lines.append(f"{nm} - {price} <a href=\"{vk}\">ВК</a>")
        else:
            lines.append(f"{nm} - {price}")
    return "\n".join(lines)


def _short_label_iphone_12_16(
    p: dict,
    version: str,
    model_key: str,
    memory_key: str,
    custom_labels: Optional[Dict[int, str]] = None,
) -> str:
    """Короткая подпись для кнопки 12–16: 16 Plus 128Gb 🔵 или подпись custom-кнопки целиком (без дубля памяти/цвета)."""
    from app.utils.color_emoji import replace_color_with_emoji
    mem_display = "1Tb" if (memory_key or "").lower() == "1tb" else f"{memory_key}Gb"
    name = p.get("name", "")
    color_emoji = parse_iphone_color_key(name)
    if not color_emoji:
        replaced = replace_color_with_emoji(name)
        for em in ("🟣", "🟢", "🔵", "⚪️", "⚫️", "🟠", "🟡", "🌸", "🔴", "⭐"):
            if em in replaced:
                color_emoji = em
                break
        color_emoji = color_emoji or "⚫️"
    model_label = _iphone_model_display_label(version, model_key)
    bid = p.get("custom_button_id")
    used_custom_label = False
    if bid is not None and custom_labels:
        cl = custom_labels.get(int(bid))
        if cl:
            model_label = cl
            used_custom_label = True
    if used_custom_label:
        return model_label
    return f"{model_label} {mem_display} {color_emoji}"


def _short_label_iphone_product(
    p: dict,
    version: str,
    model_key: str,
    memory_key: str,
    storage_key: str,
    custom_labels: Optional[Dict[int, str]] = None,
) -> str:
    """Короткая подпись для кнопки: 17 256Gb ⚪️ eSim или подпись custom-кнопки целиком (без дубля памяти/sim)."""
    from app.utils.color_emoji import replace_color_with_emoji
    mem_display = "1Tb" if (memory_key or "").lower() == "1tb" else f"{memory_key}Gb"
    stor_norm = storage_key.replace("p", "+") if "p" in storage_key else storage_key
    stor_label = "eSim" if stor_norm == "esim" else "(1+1)" if stor_norm == "1+1" else "2sim"
    name = p.get("name", "")
    color_emoji = parse_iphone_color_key(name)
    if not color_emoji:
        replaced = replace_color_with_emoji(name)
        for em in ("🟣", "🟢", "🔵", "⚪️", "⚫️", "🟠", "🟡", "🌸", "🔴", "⭐"):
            if em in replaced:
                color_emoji = em
                break
        color_emoji = color_emoji or "⚫️"
    model_label = _iphone_model_display_label(version, model_key)
    bid = p.get("custom_button_id")
    used_custom_label = False
    if bid is not None and custom_labels:
        cl = custom_labels.get(int(bid))
        if cl:
            model_label = cl
            used_custom_label = True
    if used_custom_label:
        return model_label
    return f"{model_label} {mem_display} {color_emoji} {stor_label}"


def _iphone_products_for_storage(
    products: List[dict], version: str, model_key: str, memory_key: str, storage_key: str
) -> List[dict]:
    storage_norm = storage_key.replace("p", "+") if "p" in storage_key else storage_key
    memory_norm = "1Tb" if memory_key == "1tb" else memory_key
    out = []
    for p in products:
        name = p.get("name", "")
        model = parse_iphone_model(name)
        ver = get_iphone_version_from_model(model) if model else None
        short = get_short_model_key_for_new(model or "")
        mem = parse_iphone_memory(name)
        st = parse_iphone_storage_type(name)
        if ver != version or short != model_key:
            continue
        if memory_norm == "1Tb" and mem != "1Tb":
            continue
        if memory_norm != "1Tb" and (mem or "") != memory_norm:
            continue
        if storage_norm == "1+1":
            if st == "1+1":
                out.append(p)
            elif version == "17" and st is None:
                mk = (model_key or "").lower()
                if mk == "17" or "17_pro" in mk or "17_promax" in mk:
                    out.append(p)
        elif st == storage_norm:
            out.append(p)
    return out


class NewProductPriceEdit(StatesGroup):
    waiting_for_price = State()
    waiting_for_confirm = State()


class NewProductAvitoLink(StatesGroup):
    waiting_for_ref = State()


class NewProductTagDesc(StatesGroup):
    waiting_for_subtitle = State()
    waiting_for_description = State()


def _merge_custom_into_markup(markup, db, parent_path: str):
    """Вставляет пользовательские кнопки перед последними двумя рядами (Назад / Главная)."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    extras = mcs.get_custom_extra_entries(db, parent_path)
    if not extras:
        return markup
    rows = list(markup.inline_keyboard)
    if len(rows) < 2:
        return markup
    ext = [
        [InlineKeyboardButton(text=e["text"], callback_data=e["callback"])]
        for e in extras
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows[:-2] + ext + rows[-2:])


def _nav_text_with_products(title: str, products: List[dict], prompt: str) -> str:
    """Заголовок + текстовый список товаров + призыв к навигации (без product-кнопок)."""
    text = f"🆕 {title}\n\n"
    if products:
        text += _format_new_iphone_products_text(products)
        text += "\n\n"
    text += prompt
    return text


def _iphone_nav_text_with_products(title: str, products: List[dict], prompt: str) -> str:
    return _nav_text_with_products(title, products, prompt)


async def _send_html_nav_message(callback: CallbackQuery, text: str, keyboard) -> None:
    """Редактирует сообщение; при длинном тексте разбивает на части, клавиатуру — в последнем."""
    max_len = 4090
    if len(text) <= max_len:
        await safe_edit_message(
            callback.message,
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
            disable_link_preview=True,
        )
        return
    parts: List[str] = []
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
    await safe_edit_message(
        callback.message,
        parts[0],
        reply_markup=None,
        parse_mode="HTML",
        disable_link_preview=True,
    )
    chat_id = callback.message.chat.id
    send_opts = {"parse_mode": "HTML", "link_preview_options": LinkPreviewOptions(is_disabled=True)}
    for extra in parts[1:-1]:
        await callback.bot.send_message(chat_id=chat_id, text=extra, **send_opts)
    await callback.bot.send_message(chat_id=chat_id, text=parts[-1], reply_markup=keyboard, **send_opts)


def _build_root_new_products_keyboard(db) -> "InlineKeyboardMarkup":
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    nodes = mcs.get_merged_menu_nodes(db, "root", editor=False)
    path_to_cb = {
        "root>cat>Airpods": "new_cat_Airpods",
        "root>cat>Apple Watch": "new_cat_Apple_Watch",
        "root>cat>iPad": "new_cat_iPad",
        "root>cat>iPhone": "new_cat_iPhone",
    }
    rows = []
    for n in nodes:
        if n.count <= 0:
            continue
        if n.kind == "custom" and n.custom_id is not None:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{n.label} ({n.count})",
                        callback_data=f"new_custom_{n.custom_id}",
                    )
                ]
            )
            continue
        cb = path_to_cb.get(n.path)
        if not cb:
            continue
        em = n.emoji or ""
        lab = n.label
        if n.path.endswith("iPhone"):
            lab = "iPhone"
        txt = f"{em}{lab} ({n.count})" if em else f"{lab} ({n.count})"
        rows.append([InlineKeyboardButton(text=txt, callback_data=cb)])
    rows.append(
        [InlineKeyboardButton(text="⚡ Пакетное обновление цен", callback_data="bulk_price_start")]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="📄 Прайс A4 (PDF)",
                callback_data="iphone_print_price_pdf",
            ),
            InlineKeyboardButton(
                text="🏷️ Ценники A4 (PDF)",
                callback_data="price_tags_select",
            ),
        ]
    )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="products_menu")])
    rows.append(
        [InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "new_products_menu")
async def new_products_menu(callback: CallbackQuery):
    """Главное меню категорий новых товаров."""
    try:
        await callback.answer()
    except Exception:
        pass
    try:
        def _build():
            text = "🆕 Список новых товаров\n\n"
            all_available = _fetch_available_products_for_menu()
            if all_available:
                text += "🟢 В наличии:\n\n"
                text += _format_available_lines(all_available)
                text += "\n\n"
            text += "Выберите категорию:"
            with SessionLocal() as db:
                kb = _build_root_new_products_keyboard(db)
            return text, kb

        text, kb = await run_db(_build)
    except Exception as e:
        await callback.answer("Ошибка открытия меню", show_alert=True)
        return
    max_len = 4090
    try:
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

            await safe_edit_message(
                callback.message,
                parts[0],
                reply_markup=None,
                parse_mode="HTML",
                disable_link_preview=True,
            )
            chat_id = callback.message.chat.id
            send_opts = {"parse_mode": "HTML", "link_preview_options": LinkPreviewOptions(is_disabled=True)}
            for extra in parts[1:-1]:
                await callback.bot.send_message(chat_id=chat_id, text=extra, **send_opts)
            await callback.bot.send_message(chat_id=chat_id, text=parts[-1], reply_markup=kb, **send_opts)
        else:
            await safe_edit_message(
                callback.message,
                text,
                reply_markup=kb,
                parse_mode="HTML",
                disable_link_preview=True,
            )
    except Exception as e:
        await callback.answer("Ошибка отображения меню", show_alert=True)
        return


@router.callback_query(F.data == "iphone_print_price_pdf")
async def iphone_print_price_pdf(callback: CallbackQuery):
    """Собрать и отправить PDF-прайс iPhone для печати A4."""
    try:
        await callback.answer()
    except Exception:
        pass
    try:
        await callback.message.bot.send_chat_action(
            callback.message.chat.id, ChatAction.UPLOAD_DOCUMENT
        )
    except Exception:
        pass

    def _build_pdf():
        from app.utils.iphone_print_pdf import build_iphone_price_pdf_bytes

        return build_iphone_price_pdf_bytes()

    try:
        pdf_bytes = await run_db(_build_pdf)
    except FileNotFoundError as e:
        await callback.message.answer(f"Не удалось создать PDF: {e}")
        return
    except Exception:
        logging.exception("iphone_print_price_pdf failed")
        await callback.message.answer("Ошибка при формировании PDF-прайса.")
        return

    from datetime import date

    filename = f"iphone_prices_{date.today().isoformat()}.pdf"
    doc = BufferedInputFile(pdf_bytes, filename=filename)
    await callback.message.answer_document(
        document=doc,
        caption="📄 Актуальные цены на Новые iPhone (A4)",
    )


async def _show_new_product_card(
    callback: CallbackQuery,
    state: FSMContext,
    product_id: int,
    back_data: str,
) -> bool:
    """Открыть карточку нового товара; вернуть успех."""
    await state.update_data(new_products_back=back_data)
    product = await get_product_api(product_id)
    if not product:
        return False
    price_display = _normalize_price_display(product.get("price"))
    text = f"📦 <b>{product.get('name', 'Без названия')}</b>\n\n"
    text += f"💵 Цена: {price_display}\n"
    text += f"📁 Подборка: {product.get('collection_name', '—')}\n"
    av = product.get("availability_status")
    text += f"Наличие: {'🟢 В наличии' if av == 'available' else '🔴 На заказ' if av == 'on_order' else '—'}\n"
    pt_sub = (product.get("price_tag_subtitle") or "").strip()
    pt_desc = (product.get("price_tag_description") or "").strip()
    if pt_sub or pt_desc:
        text += "\n🏷️ <b>Ценник:</b>\n"
        if pt_sub:
            text += f"Подзаголовок: {escape(pt_sub)}\n"
        if pt_desc:
            preview = pt_desc if len(pt_desc) <= 120 else pt_desc[:117] + "…"
            text += f"Описание: {escape(preview)}\n"
    if product.get("vk_product_link"):
        text += f"\n🔗 <a href=\"{product['vk_product_link']}\">Ссылка на товар в ВК</a>"
    if product.get("avito_url"):
        text += f"\n🛒 <a href=\"{product['avito_url']}\">Ссылка на Авито</a>"
    await safe_edit_message(
        callback.message,
        text,
        reply_markup=get_new_product_detail_keyboard(
            product_id,
            status=product.get("status", "active"),
            availability_status=av,
            back_data=back_data,
        ),
        parse_mode="HTML",
        disable_link_preview=True,
    )
    return True


@router.callback_query(F.data.startswith("new_custom_"))
async def new_custom_branch(callback: CallbackQuery, state: FSMContext):
    """Пользовательская ветка меню «Список новых»."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    try:
        bid = int(callback.data.replace("new_custom_", ""))
    except ValueError:
        await callback.answer("Ошибка", show_alert=True)
        return

    def _build():
        with SessionLocal() as db:
            btn = mcs.get_new_menu_button(db, bid)
            if not btn:
                return None
            path = mcs.custom_node_path(bid)
            nodes = mcs.get_merged_menu_nodes(db, path, editor=False)
            prods = mcs.list_products_for_custom_leaf(db, bid)
            prods = _sort_products_by_price(prods)
            back_cb = mcs.back_callback_for_custom_parent(btn.parent_path)
            if not nodes and len(prods) == 1:
                return {"kind": "card", "product_id": prods[0]["id"], "back": back_cb}
            title = f"🆕 <b>{escape(btn.label)}</b>\n\n"
            lines = []
            for p in prods:
                price = _normalize_price_display(p.get("price"))
                vk = p.get("vk_product_link", "")
                nm_safe = escape(_compact_label_for_product(p))
                if vk:
                    lines.append(f"{nm_safe} — {price} <a href=\"{vk}\">ВК</a>")
                else:
                    lines.append(f"{nm_safe} — {price}")
            if lines:
                title += "\n".join(lines) + "\n\n"
            available_custom = _available_only(prods)
            if available_custom:
                title += "🟢 В наличии:\n\n"
                title += _format_available_lines(available_custom) + "\n\n"
            if nodes:
                title += "Подменю:"
            elif not prods:
                title += "Пока пусто. Добавьте подкнопки или товары в Настройках → Меню новые."
            b_rows = []
            for n in nodes:
                if n.kind == "custom" and n.custom_id is not None:
                    b_rows.append(
                        [
                            InlineKeyboardButton(
                                text=f"{n.label} ({n.count})",
                                callback_data=f"new_custom_{n.custom_id}",
                            )
                        ]
                    )
            for p in prods:
                lbl = _compact_label_for_product(p)
                if len(lbl) > 40:
                    lbl = lbl[:37] + "..."
                b_rows.append(
                    [InlineKeyboardButton(text=lbl, callback_data=f"new_product_{p['id']}")]
                )
            b_rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb)])
            b_rows.append(
                [InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")]
            )
            return {
                "kind": "screen",
                "title": title,
                "kb": InlineKeyboardMarkup(inline_keyboard=b_rows),
            }

    data = await run_db(_build)
    if data is None:
        await callback.answer("Кнопка не найдена", show_alert=True)
        return
    if data["kind"] == "card":
        if await _show_new_product_card(callback, state, data["product_id"], data["back"]):
            await callback.answer()
            return
        # Карточка не открылась — падаем в общий экран нельзя, просто сообщаем
        await callback.answer("Товар не найден", show_alert=True)
        return
    await state.update_data(new_products_back=f"new_custom_{bid}")
    await safe_edit_message(
        callback.message,
        data["title"],
        reply_markup=data["kb"],
        parse_mode="HTML",
        disable_link_preview=True,
    )
    await callback.answer()


def _parse_airpods_model(name: str) -> Optional[str]:
    """Парсит модель AirPods из названия (Magsafe — если есть '3' и 'magsafe', даже с другими словами между ними)."""
    name_lower = name.lower()
    if "airpods pro 3" in name_lower or "pro 3" in name_lower:
        return "AirPods Pro 3"
    if "airpods pro 2" in name_lower or "pro 2" in name_lower:
        return "AirPods Pro 2"
    if "airpods 4 anc" in name_lower or "4 anc" in name_lower:
        return "AirPods 4 ANC"
    if "airpods 4" in name_lower:
        return "AirPods 4"
    # AirPods 3 Magsafe: "AirPods 3 с зарядным кейсом Magsafe" — проверяем наличие 3 и magsafe
    if "airpods 3" in name_lower and "magsafe" in name_lower:
        return "AirPods 3 Magsafe"
    if "airpods 3" in name_lower:
        return "AirPods 3"
    return None


def _parse_apple_watch_category(name: str) -> Optional[str]:
    """Парсит категорию Apple Watch из названия."""
    name_lower = name.lower()
    if "se 3" in name_lower or "se3" in name_lower:
        return "SE 3"
    if "se 2" in name_lower or "se2" in name_lower:
        return "SE 2"
    if "11" in name_lower and ("watch" in name_lower or "aw" in name_lower):
        return "11"
    return None


def _parse_apple_watch_size(name: str) -> Optional[str]:
    """Парсит размер Apple Watch из названия (40/41/42/44/45/46mm)."""
    name_lower = name.lower()
    if "46mm" in name_lower or "46 mm" in name_lower:
        return "46mm"
    if "45mm" in name_lower or "45 mm" in name_lower:
        return "45mm"
    if "44mm" in name_lower or "44 mm" in name_lower:
        return "44mm"
    if "41mm" in name_lower or "41 mm" in name_lower:
        return "41mm"
    if "42mm" in name_lower or "42 mm" in name_lower:
        return "42mm"
    if "40mm" in name_lower or "40 mm" in name_lower:
        return "40mm"
    return None


def _parse_apple_watch_color_emoji(name: str) -> str:
    """Возвращает эмодзи цвета Apple Watch: Silver=⚪️, Starlight=⭐, Space Gray=🔘, Midnight=⚫️, Rose Gold=🌸."""
    from app.utils.color_emoji import replace_color_with_emoji
    name_lower = name.lower()
    if "starlight" in name_lower:
        return "⭐"
    if "silver" in name_lower:
        return "⚪️"
    if "space gray" in name_lower or "space grey" in name_lower:
        return "🔘"
    if "midnight" in name_lower:
        return "⚫️"
    if "rose gold" in name_lower:
        return "🌸"
    # Fallback: try replace_color_with_emoji and take first emoji
    replaced = replace_color_with_emoji(name)
    for emoji in ("⭐", "⚪️", "🔘", "⚫️", "🌸", "🔵", "🟣"):
        if emoji in replaced:
            return emoji
    return "⚫️"


def _parse_ipad_model(name: str) -> Optional[str]:
    """Парсит модель iPad из названия."""
    name_lower = name.lower()
    if "ipad air" in name_lower:
        return "iPad Air"
    if "ipad 11" in name_lower or "11" in name_lower:
        return "iPad 11"
    return None


def _format_airpods_list(products: List[dict]) -> str:
    """Форматирует список AirPods в простой формат (цены нормализуем для отображения)."""
    model_map = {}
    for p in products:
        model = _parse_airpods_model(p.get("name", ""))
        if model:
            if model not in model_map:
                model_map[model] = []
            model_map[model].append(p)
    
    lines = []
    order = ["AirPods 3", "AirPods 3 Magsafe", "AirPods 4", "AirPods 4 ANC", "AirPods Pro 2", "AirPods Pro 3"]
    for model in order:
        if model in model_map:
            for p in model_map[model]:
                price = _normalize_price_display(p.get("price"))
                vk_link = p.get("vk_product_link", "")
                if vk_link:
                    lines.append(f"{model} - {price} <a href=\"{vk_link}\">ВК</a>")
                else:
                    lines.append(f"{model} - {price}")
    
    return "\n".join(lines) if lines else "Товары не найдены"


def _available_only(products: List[dict]) -> List[dict]:
    return [p for p in products if p.get("availability_status") == "available"]


def _group_for_product(
    p: dict,
    custom_parent_by_button: Optional[Dict[int, str]] = None,
) -> str:
    name_low = (p.get("name") or "").lower()
    collection = (p.get("collection_name") or "").strip()
    if collection == "Airpods":
        return "Airpods"
    if collection == "Apple Watch":
        return "Apple Watch"
    if collection == "iPad":
        return "iPad"
    if collection == "iPhone новые":
        return "iPhone новые"
    # custom/fallback: определяем группу по пути родителя custom-кнопки.
    bid = p.get("custom_button_id")
    if bid and custom_parent_by_button:
        parent = custom_parent_by_button.get(int(bid), "")
        if parent.startswith("root>cat>Airpods"):
            return "Airpods"
        if parent.startswith("root>cat>Apple Watch"):
            return "Apple Watch"
        if parent.startswith("root>cat>iPad"):
            return "iPad"
        if parent.startswith("root>cat>iPhone"):
            return "iPhone новые"
    # Дополнительная эвристика для custom-товаров:
    # иногда parent_path может не попасть в ожидаемый префикс.
    if "airpods" in name_low:
        return "Airpods"
    if "watch" in name_low:
        return "Apple Watch"
    if "ipad" in name_low:
        return "iPad"
    if "iphone" in name_low:
        return "iPhone новые"
    return "custom"


def _compact_label_for_product(
    p: dict,
    custom_label_by_button: Optional[Dict[int, str]] = None,
) -> str:
    row = dict(p)
    bid = row.get("custom_button_id")
    if bid and custom_label_by_button:
        cl = custom_label_by_button.get(int(bid))
        if cl:
            row["custom_button_label"] = cl
    return button_label_for_product(row)


def _available_sort_key(
    p: dict,
    custom_parent_by_button: Optional[Dict[int, str]] = None,
) -> tuple:
    group = _group_for_product(p, custom_parent_by_button)
    group_order = {"Airpods": 0, "Apple Watch": 1, "iPad": 2, "iPhone новые": 3, "custom": 4}
    g = group_order.get(group, 99)

    if group == "Airpods":
        model = _parse_airpods_model(p.get("name", "")) or ""
        model_order = {
            "AirPods 3": 0,
            "AirPods 3 Magsafe": 1,
            "AirPods 4": 2,
            "AirPods 4 ANC": 3,
            "AirPods Pro 2": 4,
            "AirPods Pro 3": 5,
        }.get(model, 99)
        return (g, model_order, _price_sort_value(p), p.get("id", 0))

    if group == "Apple Watch":
        cat = _parse_apple_watch_category(p.get("name", "")) or ""
        cat_order = {"SE 2": 0, "SE 3": 1, "11": 2}.get(cat, 99)
        size = _parse_apple_watch_size(p.get("name", "")) or ""
        size_num = int(re.sub(r"[^\d]", "", size) or "0")
        return (g, cat_order, size_num, _price_sort_value(p), p.get("id", 0))

    if group == "iPad":
        model = _parse_ipad_model(p.get("name", "")) or ""
        model_order = {"iPad 11": 0, "iPad Air": 1}.get(model, 99)
        gen = _parse_ipad_air_generation(p.get("name", "")) or ""
        gen_order = {"M3": 0, "M4": 1}.get(gen, 99)
        return (g, model_order, gen_order, _price_sort_value(p), p.get("id", 0))

    if group == "iPhone новые":
        sorted_ids = [x.get("id") for x in _sort_iphone_products([p])]
        rank = sorted_ids.index(p.get("id")) if p.get("id") in sorted_ids else 0
        return (g, rank, _price_sort_value(p), p.get("id", 0))

    return (g, _price_sort_value(p), p.get("id", 0))


def _format_available_lines(products: List[dict]) -> str:
    custom_label_by_button: Dict[int, str] = {}
    custom_parent_by_button: Dict[int, str] = {}
    try:
        custom_ids = {
            int(p.get("custom_button_id"))
            for p in products
            if p.get("custom_button_id")
        }
        if custom_ids:
            with SessionLocal() as db:
                for bid in custom_ids:
                    lbl = mcs.get_custom_button_label(db, bid)
                    if lbl:
                        custom_label_by_button[bid] = lbl
                    btn = mcs.get_new_menu_button(db, bid)
                    if btn:
                        custom_parent_by_button[bid] = btn.parent_path or ""
    except Exception:
        pass

    iphone_items = [p for p in products if _group_for_product(p, custom_parent_by_button) == "iPhone новые"]
    iphone_sorted_ids: Dict[int, int] = {}
    if iphone_items:
        sorted_iph = _sort_iphone_products(iphone_items)
        iphone_sorted_ids = {int(x.get("id")): idx for idx, x in enumerate(sorted_iph)}

    def _key(p: dict) -> tuple:
        group = _group_for_product(p, custom_parent_by_button)
        group_order = {"Airpods": 0, "Apple Watch": 1, "iPad": 2, "iPhone новые": 3, "custom": 4}
        g = group_order.get(group, 99)
        pid = int(p.get("id") or 0)
        if group == "iPhone новые":
            return (g, iphone_sorted_ids.get(pid, 9999), pid)
        return _available_sort_key(p, custom_parent_by_button)

    products = sorted(products, key=_key)


    lines: List[str] = []
    for p in products:
        compact = _compact_label_for_product(p, custom_label_by_button)
        nm = escape(compact)
        price = _normalize_price_display(p.get("price"))
        vk_link = p.get("vk_product_link", "")
        if vk_link:
            lines.append(f"{nm} - {price} <a href=\"{vk_link}\">ВК</a>")
        else:
            lines.append(f"{nm} - {price}")
    return "\n".join(lines) if lines else "Нет позиций в наличии"


def _custom_label_map_for_products(products: List[dict]) -> Dict[int, str]:
    out: Dict[int, str] = {}
    try:
        ids = {int(p.get("custom_button_id")) for p in products if p.get("custom_button_id")}
        if not ids:
            return out
        with SessionLocal() as db:
            for bid in ids:
                lbl = mcs.get_custom_button_label(db, bid)
                if lbl:
                    out[bid] = lbl
    except Exception:
        return out
    return out


@router.callback_query(F.data.startswith("new_cat_"))
async def new_products_category(callback: CallbackQuery, state: FSMContext):
    """Выбор категории: iPhone -> версии; Airpods/Apple Watch/iPad -> подменю."""
    cat = callback.data.replace("new_cat_", "").replace("_", " ")
    
    if cat.lower() == "iphone":
        # Сразу гасим "часики": дальше только сборка экрана в фоне, alert-ошибок нет
        try:
            await callback.answer()
        except Exception:
            pass
        await state.update_data(new_products_back="new_cat_iPhone")

        def _build_iphone():
            items, _ = _fetch_products_sync(limit=5000)
            iphone_new = _filter_new_products(items, "iPhone новые")
            with SessionLocal() as db:
                iphone_custom = mcs.list_custom_products_for_path(db, "root>cat>iPhone")

            available_products = [
                p
                for p in (iphone_new + iphone_custom)
                if p.get("availability_status") == "available"
            ]

            v_counts = _iphone_version_counts(iphone_new)
            with SessionLocal() as db:
                all_items = mcs.load_new_products_dicts(db)
                for v in ["12", "13", "14", "15", "16", "17"]:
                    pth = f"root>cat>iPhone>ver>{v}"
                    v_counts[v] = mcs.total_count_for_path(db, pth, all_items)
                keyboard = _merge_custom_into_markup(
                    get_new_iphone_versions_keyboard(
                        v_counts,
                        back_data="new_products_menu",
                        label_resolver=mcs.effective_hardcoded_label,
                    ),
                    db,
                    "root>cat>iPhone",
                )

            text = "🆕 iPhone (новые)\n\n"
            if available_products:
                text += "🟢 В наличии:\n\n"
                text += _format_new_iphone_products_text(_sort_iphone_products(available_products))
                text += "\n\n"
            text += "Выберите версию:"
            return text, keyboard

        text, keyboard = await run_db(_build_iphone)
        await _send_html_nav_message(callback, text, keyboard)
        return
    
    collection_map = {
        "Airpods": "root>cat>Airpods",
        "Apple Watch": "root>cat>Apple Watch",
        "iPad": "root>cat>iPad",
    }
    cat_path = collection_map.get(cat)
    if not cat_path:
        await callback.answer("Категория не найдена")
        return

    def _build_category():
        with SessionLocal() as db:
            all_items = mcs.load_new_products_dicts(db)
            category_products_all = mcs.collect_products_for_path(db, cat_path, all_items)
            cat_total = mcs.total_count_for_path(db, cat_path, all_items)

        if cat_total <= 0:
            with SessionLocal() as db:
                kb = _build_root_new_products_keyboard(db)
            return "empty", f"🆕 Категория «{cat}»\n\nТовары не найдены.", kb

        if cat.lower() == "airpods":
            from app.bot.keyboards.new_products_keyboard import get_airpods_models_keyboard

            with SessionLocal() as db:
                model_counts: Dict[str, int] = {}
                for model in mcs.AIRPODS_ORDER:
                    mk = mcs.AIRPODS_KEY[model]
                    pth = f"{cat_path}>md>{mk}"
                    tc = mcs.total_count_for_path(db, pth, all_items)
                    if tc > 0:
                        model_counts[model] = tc
                kb = _merge_custom_into_markup(
                    get_airpods_models_keyboard(
                        model_counts,
                        back_data="new_products_menu",
                        label_resolver=mcs.effective_hardcoded_label,
                    ),
                    db,
                    cat_path,
                )
            text = _nav_text_with_products("AirPods", category_products_all, "Выберите модель:")
            return "nav", text, kb

        if cat.lower() == "apple watch":
            from app.bot.keyboards.new_products_keyboard import get_apple_watch_categories_keyboard

            with SessionLocal() as db:
                category_counts: Dict[str, int] = {}
                for wc in mcs.WATCH_CATS:
                    ck = mcs.WATCH_KEY[wc]
                    pth = f"{cat_path}>wc>{ck}"
                    tc = mcs.total_count_for_path(db, pth, all_items)
                    if tc > 0:
                        category_counts[wc] = tc
                kb = _merge_custom_into_markup(
                    get_apple_watch_categories_keyboard(
                        category_counts,
                        back_data="new_products_menu",
                        label_resolver=mcs.effective_hardcoded_label,
                    ),
                    db,
                    cat_path,
                )
            text = _nav_text_with_products(
                f"Apple Watch ({cat_total})", category_products_all, "Выберите категорию:"
            )
            return "nav", text, kb

        if cat.lower() == "ipad":
            from app.bot.keyboards.new_products_keyboard import get_ipad_models_keyboard

            with SessionLocal() as db:
                model_counts: Dict[str, int] = {}
                for model in mcs.IPAD_ORDER:
                    mk = mcs.IPAD_KEY[model]
                    pth = f"{cat_path}>md>{mk}"
                    tc = mcs.total_count_for_path(db, pth, all_items)
                    if tc > 0:
                        model_counts[model] = tc
                kb = _merge_custom_into_markup(
                    get_ipad_models_keyboard(
                        model_counts,
                        back_data="new_products_menu",
                        label_resolver=mcs.effective_hardcoded_label,
                    ),
                    db,
                    cat_path,
                )
            text = _nav_text_with_products(
                f"iPad ({cat_total})", category_products_all, "Выберите модель:"
            )
            return "nav", text, kb

        return None

    result = await run_db(_build_category)
    if result is None:
        await callback.answer("Категория не найдена")
        return
    kind, text, kb = result
    if kind == "empty":
        await safe_edit_message(callback.message, text, reply_markup=kb)
    else:
        await _send_html_nav_message(callback, text, kb)
    await callback.answer()


@router.callback_query(F.data.startswith("new_iphone_ver_"))
async def new_iphone_versions(callback: CallbackQuery, state: FSMContext):
    """Версия iPhone -> модели."""
    version = callback.data.replace("new_iphone_ver_", "")
    await state.update_data(new_products_back=f"new_iphone_ver_{version}")

    def _build():
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        items, _ = _fetch_products_sync(limit=5000)
        iphone_new = _filter_new_products(items, "iPhone новые")

        parent_path = f"root>cat>iPhone>ver>{version}"
        m_counts: Dict[str, int] = {}
        with SessionLocal() as db:
            all_items = mcs.load_new_products_dicts(db)
            for display in IPHONE_VERSION_MODEL_ORDER.get(version, []):
                mk = display.replace(" ", "_").lower()
                pth = f"{parent_path}>md>{mk}"
                tc = mcs.total_count_for_path(db, pth, all_items)
                if tc > 0:
                    m_counts[display] = tc
            version_products = mcs.collect_products_for_path(db, parent_path, all_items)

        text = _iphone_nav_text_with_products(
            f"iPhone {version}", version_products, "Выберите модель:"
        )

        # Flat fallback только для 12/14, когда нет ни одного стандартного слота модели.
        if not m_counts and version in ("12", "14"):
            version_products = []
            for p in iphone_new:
                name = p.get("name", "")
                model = parse_iphone_model(name)
                ver = get_iphone_version_from_model(model) if model else None
                if ver == version:
                    version_products.append(p)
                elif (name or "").lower().count(f"iphone {version}") > 0:
                    version_products.append(p)
            with SessionLocal() as db:
                version_products.extend(mcs.list_custom_products_for_path(db, parent_path))
            if version_products:
                version_products = _sort_iphone_products(version_products)
                text = f"🆕 iPhone {version} ({len(version_products)}):\n\n"
                text += _format_new_iphone_products_text(version_products)
                buttons = []
                for p in version_products:
                    lbl = _short_line_iphone_by_product(p).split(" - ")[0]
                    if len(lbl) > 40:
                        lbl = lbl[:37] + "..."
                    buttons.append([
                        InlineKeyboardButton(text=lbl, callback_data=f"new_product_{p['id']}")
                    ])
                with SessionLocal() as db:
                    extras = mcs.get_custom_extra_entries(db, parent_path)
                    for e in extras:
                        buttons.append(
                            [InlineKeyboardButton(text=e["text"], callback_data=e["callback"])]
                        )
                buttons.append([
                    InlineKeyboardButton(text="⬅️ Назад", callback_data="new_cat_iPhone")
                ])
                buttons.append([
                    InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")
                ])
                return text, InlineKeyboardMarkup(inline_keyboard=buttons)

        with SessionLocal() as db:
            kb = _merge_custom_into_markup(
                get_new_iphone_models_keyboard(
                    m_counts,
                    version,
                    back_data="new_cat_iPhone",
                    label_resolver=mcs.effective_hardcoded_label,
                ),
                db,
                parent_path,
            )
        return text, kb

    text, kb = await run_db(_build)
    await _send_html_nav_message(callback, text, kb)
    await callback.answer()


@router.callback_query(F.data.startswith("new_airpods_"))
async def new_airpods_model(callback: CallbackQuery, state: FSMContext):
    """Выбор модели AirPods -> список товаров этой модели."""
    await state.update_data(new_products_back="new_cat_Airpods")
    # Ключ в callback_data с подчёркиваниями: new_airpods_airpods_3 -> airpods_3
    model_key = callback.data.replace("new_airpods_", "")
    
    # Маппинг ключей на названия моделей (ключи с подчёркиваниями)
    model_map = {
        "airpods_3": "AirPods 3",
        "airpods_3_magsafe": "AirPods 3 Magsafe",
        "airpods_4": "AirPods 4",
        "airpods_4_anc": "AirPods 4 ANC",
        "airpods_pro_2": "AirPods Pro 2",
        "airpods_pro_3": "AirPods Pro 3",
    }
    model_name = model_map.get(model_key.lower())
    
    if not model_name:
        await callback.answer("Модель не найдена")
        return
    
    ap_path = f"root>cat>Airpods>md>{model_key}"

    def _build():
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        with SessionLocal() as db:
            all_items = mcs.load_new_products_dicts(db)
            model_products = mcs.collect_products_for_path(db, ap_path, all_items)
        model_products = _sort_products_by_price(model_products)

        if not model_products:
            return None
        if len(model_products) == 1:
            return {"kind": "card", "product_id": model_products[0]["id"]}

        # Несколько товаров: список с ценой сверху, кнопки без цены
        text = f"🆕 {model_name}\n\n"
        lines = []
        buttons = []
        custom_map = _custom_label_map_for_products(model_products)
        for p in model_products:
            price = _normalize_price_display(p.get("price"))
            vk_link = p.get("vk_product_link", "")
            label = _compact_label_for_product(p, custom_map)
            if vk_link:
                lines.append(f"{label} - {price} <a href=\"{vk_link}\">ВК</a>")
            else:
                lines.append(f"{label} - {price}")
            buttons.append([
                InlineKeyboardButton(text=label, callback_data=f"new_product_{p['id']}")
            ])
        text += "\n".join(lines) if lines else "Товары не найдены"
        text += "\n\nВыберите позицию:"
        has_custom_in_list = any(bool(p.get("custom_button_id")) for p in model_products)
        with SessionLocal() as db:
            extras = mcs.get_custom_extra_entries(db, ap_path)
            if not has_custom_in_list:
                for e in extras:
                    buttons.append(
                        [InlineKeyboardButton(text=e["text"], callback_data=e["callback"])]
                    )
        buttons.append([
            InlineKeyboardButton(text="⬅️ Назад", callback_data="new_cat_Airpods")
        ])
        buttons.append([
            InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")
        ])
        return {
            "kind": "screen",
            "text": text,
            "kb": InlineKeyboardMarkup(inline_keyboard=buttons),
        }

    data = await run_db(_build)
    if data is None:
        await callback.answer("Товары не найдены", show_alert=True)
        return
    if data["kind"] == "card":
        # Один товар — сразу открываем карточку редактирования
        if not await _show_new_product_card(
            callback, state, data["product_id"], "new_cat_Airpods"
        ):
            await callback.answer("Товар не найден", show_alert=True)
            return
        await callback.answer()
        return
    await state.update_data(new_products_back=f"new_airpods_{model_key}")
    await safe_edit_message(
        callback.message,
        data["text"],
        reply_markup=data["kb"],
        parse_mode="HTML",
        disable_link_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("new_watch_cat_"))
async def new_apple_watch_category(callback: CallbackQuery, state: FSMContext):
    """Выбор категории Apple Watch -> кнопки по товарам (размер + цвет), каждая открывает карточку."""
    cat_key = callback.data.replace("new_watch_cat_", "")
    cat_map = {"se_2": "SE 2", "se_3": "SE 3", "11": "11"}
    category = cat_map.get(cat_key)
    
    if not category:
        await callback.answer("Категория не найдена")
        return
    
    await state.update_data(new_products_back=f"new_watch_cat_{cat_key}")
    
    wc_path = f"root>cat>Apple Watch>wc>{cat_key}"

    def _build():
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        with SessionLocal() as db:
            all_items = mcs.load_new_products_dicts(db)
            category_products = mcs.collect_products_for_path(db, wc_path, all_items)
        category_products = _sort_products_by_price(category_products)

        if not category_products:
            return None

        # Текст сверху: список с ценами и ссылкой ВК
        lines = []
        buttons = []
        custom_label_map = _custom_label_map_for_products(category_products)
        for p in category_products:
            label = _compact_label_for_product(p, custom_label_map)
            price = _normalize_price_display(p.get("price"))
            vk_link = p.get("vk_product_link", "")
            if vk_link:
                lines.append(f"{label} - {price} <a href=\"{vk_link}\">ВК</a>")
            else:
                lines.append(f"{label} - {price}")
            buttons.append([
                InlineKeyboardButton(text=label, callback_data=f"new_product_{p['id']}")
            ])

        text = f"🆕 Apple Watch {category} ({len(category_products)})\n\n"
        text += "\n".join(lines) if lines else "Товары не найдены"
        text += "\n\nВыберите позицию:"
        with SessionLocal() as db:
            extras = mcs.get_custom_extra_entries(db, wc_path)
            for e in extras:
                buttons.append(
                    [InlineKeyboardButton(text=e["text"], callback_data=e["callback"])]
                )
        buttons.append([
            InlineKeyboardButton(text="⬅️ Назад", callback_data="new_cat_Apple_Watch")
        ])
        buttons.append([
            InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")
        ])
        return text, InlineKeyboardMarkup(inline_keyboard=buttons)

    result = await run_db(_build)
    if result is None:
        await callback.answer("Товары не найдены", show_alert=True)
        return
    text, keyboard = result
    await safe_edit_message(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
        disable_link_preview=True,
    )
    await callback.answer()


def _parse_ipad_color_emoji(name: str) -> str:
    """Эмодзи цвета для iPad: Rose Gold=🌸, Blue=🔵, Yellow=🟡, White=⚪️, Space Gray=🔘, Starlight=⭐, Purple=🟣."""
    from app.utils.color_emoji import replace_color_with_emoji
    name_lower = name.lower()
    if "rose gold" in name_lower or "pink" in name_lower:
        return "🌸"
    if "blue" in name_lower:
        return "🔵"
    if "yellow" in name_lower or "gold" in name_lower:
        return "🟡"
    if "white" in name_lower:
        return "⚪️"
    if "space gray" in name_lower or "space grey" in name_lower:
        return "🔘"
    if "starlight" in name_lower:
        return "⭐"
    if "purple" in name_lower or "lavender" in name_lower:
        return "🟣"
    replaced = replace_color_with_emoji(name)
    for emoji in ("🌸", "🔵", "🟡", "⚪️", "🔘", "⭐", "🟣"):
        if emoji in replaced:
            return emoji
    return "⚫️"


def _parse_ipad_air_generation(name: str) -> Optional[str]:
    """Возвращает поколение чипа для iPad Air по названию: M3/M4."""
    low = (name or "").lower()
    if "ipad air" not in low:
        return None
    if "m4" in low:
        return "M4"
    if "m3" in low:
        return "M3"
    return None


@router.callback_query(F.data.startswith("new_ipad_"))
async def new_ipad_model(callback: CallbackQuery, state: FSMContext):
    """Выбор модели iPad -> список с ценами и кнопки по цвету (карточка товара)."""
    # Ключ с подчёркиваниями: new_ipad_ipad_11 -> ipad_11
    model_key = callback.data.replace("new_ipad_", "")
    
    model_map = {
        "ipad_11": "iPad 11",
        "ipad_air": "iPad Air",
    }
    model_name = model_map.get(model_key.lower())
    
    if not model_name:
        await callback.answer("Модель не найдена")
        return
    
    await state.update_data(new_products_back=f"new_ipad_{model_key}")
    
    ipad_path = f"root>cat>iPad>md>{model_key}"

    def _build():
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        with SessionLocal() as db:
            all_items = mcs.load_new_products_dicts(db)
            model_products = mcs.collect_products_for_path(db, ipad_path, all_items)
        model_products = _sort_products_by_price(model_products)

        if not model_products:
            return None

        # Текст сверху: модель - цена ВК; кнопки: "iPad 11 🌸", "iPad 11 🔵" и т.д.
        lines = []
        buttons = []
        custom_label_map = _custom_label_map_for_products(model_products)
        for p in model_products:
            label = _compact_label_for_product(p, custom_label_map)
            price = _normalize_price_display(p.get("price"))
            vk_link = p.get("vk_product_link", "")
            if vk_link:
                lines.append(f"{label} - {price} <a href=\"{vk_link}\">ВК</a>")
            else:
                lines.append(f"{label} - {price}")
            buttons.append([
                InlineKeyboardButton(text=label, callback_data=f"new_product_{p['id']}")
            ])

        text = f"🆕 {model_name} ({len(model_products)}):\n\n"
        text += "\n".join(lines) if lines else "Товары не найдены"
        text += "\n\nВыберите позицию:"
        has_custom_in_list = any(bool(p.get("custom_button_id")) for p in model_products)
        with SessionLocal() as db:
            extras = mcs.get_custom_extra_entries(db, ipad_path)
            if not has_custom_in_list:
                for e in extras:
                    buttons.append(
                        [InlineKeyboardButton(text=e["text"], callback_data=e["callback"])]
                    )
        buttons.append([
            InlineKeyboardButton(text="⬅️ Назад", callback_data="new_cat_iPad")
        ])
        buttons.append([
            InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")
        ])
        return text, InlineKeyboardMarkup(inline_keyboard=buttons)

    result = await run_db(_build)
    if result is None:
        await callback.answer("Товары не найдены", show_alert=True)
        return
    text, keyboard = result
    await safe_edit_message(
        callback.message,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
        disable_link_preview=True,
    )
    await callback.answer()


def _iphone_products_for_model(products: List[dict], version: str, model_key: str) -> List[dict]:
    """Товары для версии и модели (все объёмы памяти)."""
    out = []
    for p in products:
        name = p.get("name", "")
        model = parse_iphone_model(name)
        ver = get_iphone_version_from_model(model) if model else None
        short = get_short_model_key_for_new(model or "")
        if ver != version or short != model_key:
            continue
        out.append(p)
    return out


def _iphone_products_for_memory(products: List[dict], version: str, model_key: str, memory_key: str) -> List[dict]:
    """Товары для версии, модели и объёма памяти (все типы сим)."""
    memory_norm = "1Tb" if (memory_key or "").lower() == "1tb" else memory_key
    out = []
    for p in products:
        name = p.get("name", "")
        model = parse_iphone_model(name)
        ver = get_iphone_version_from_model(model) if model else None
        short = get_short_model_key_for_new(model or "")
        mem = parse_iphone_memory(name)
        if ver != version or short != model_key:
            continue
        if memory_norm == "1Tb" and mem != "1Tb":
            continue
        if memory_norm != "1Tb" and (mem or "") != memory_norm:
            continue
        out.append(p)
    return out


@router.callback_query(F.data.startswith("new_iphone_mod_"))
async def new_iphone_models(callback: CallbackQuery, state: FSMContext):
    """Модель -> варианты по памяти (256/512/1Tb)."""
    rest = callback.data.replace("new_iphone_mod_", "")
    parts = rest.split("_")
    version = parts[0] if parts else ""
    model_key = "_".join(parts[1:]) if len(parts) > 1 else ""
    await state.update_data(new_products_back=f"new_iphone_mod_{version}_{model_key}")

    def _build():
        items, _ = _fetch_products_sync(limit=5000)
        iphone_new = _filter_new_products(items, "iPhone новые")
        var_counts = _iphone_memory_counts(iphone_new, version, model_key)
        parent_path = f"root>cat>iPhone>ver>{version}>md>{model_key}"
        with SessionLocal() as db:
            all_items = mcs.load_new_products_dicts(db)
            for mem in ["64", "128", "256", "512", "1Tb"]:
                mem_key = mem.lower()
                pth = f"{parent_path}>mem>{mem_key}"
                tc = mcs.total_count_for_path(db, pth, all_items)
                if tc > 0:
                    var_counts[mem] = tc
                else:
                    var_counts.pop(mem, None)
            model_products = mcs.collect_products_for_path(db, parent_path, all_items)
        model_display = _iphone_model_display_label(version, model_key)
        text = _iphone_nav_text_with_products(
            f"iPhone {model_display}", model_products, "Выберите объём памяти:"
        )
        with SessionLocal() as db:
            kb = _merge_custom_into_markup(
                get_new_iphone_variants_keyboard(
                    var_counts,
                    version,
                    model_key,
                    back_data=f"new_iphone_ver_{version}",
                    label_resolver=mcs.effective_hardcoded_label,
                ),
                db,
                parent_path,
            )
        return text, kb

    text, kb = await run_db(_build)
    await _send_html_nav_message(callback, text, kb)
    await callback.answer()


@router.callback_query(F.data.startswith("new_iphone_var"))
async def new_iphone_variants(callback: CallbackQuery, state: FSMContext):
    """Память -> для 12–16 сразу список товаров; для 17 — тип хранилища (esim, 1+1, 2sim)."""
    version, model_key, memory_key = parse_new_iphone_var_nav(callback.data)
    if not version or not memory_key:
        await callback.answer("Ошибка навигации", show_alert=True)
        return
    await state.update_data(
        new_products_back=format_new_iphone_var_nav(version, model_key, memory_key)
    )

    def _build():
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        items, _ = _fetch_products_sync(limit=5000)
        iphone_new = _filter_new_products(items, "iPhone новые")
        mem_path = f"root>cat>iPhone>ver>{version}>md>{model_key}>mem>{memory_key}"
        with SessionLocal() as db:
            all_items = mcs.load_new_products_dicts(db)
            mem_products = mcs.collect_products_for_path(db, mem_path, all_items)
        mem_products = _sort_iphone_products(mem_products)
        mem_display = "1Tb" if memory_key == "1tb" else f"{memory_key}Gb"

        # iPhone 12–16: после выбора памяти сразу список товаров (без шага esim/1+1/2sim)
        if version in ("12", "13", "14", "15", "16"):
            text = f"🆕 iPhone {version} {model_key.replace('_', ' ')} {mem_display}\n\n"
            if mem_products:
                text += _format_new_iphone_products_text(mem_products, version, model_key, memory_key, None)
            else:
                text += "Товары не найдены"
            text += "\n\nВыберите позицию:"
            buttons = []
            for p in mem_products:
                lbl = button_label_for_product(p)
                buttons.append([
                    InlineKeyboardButton(text=lbl, callback_data=f"new_product_{p['id']}")
                ])
            has_custom_in_mem_products = any(bool(p.get("custom_button_id")) for p in mem_products)
            with SessionLocal() as db:
                extras = mcs.get_custom_extra_entries(db, mem_path)
                # Если custom-товар уже есть в списке кнопок, не дублируем его через extras.
                if not has_custom_in_mem_products:
                    for e in extras:
                        buttons.append(
                            [InlineKeyboardButton(text=e["text"], callback_data=e["callback"])]
                        )
            buttons.append([
                InlineKeyboardButton(text="⬅️ Назад", callback_data=f"new_iphone_mod_{version}_{model_key}")
            ])
            buttons.append([
                InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")
            ])
            return text, InlineKeyboardMarkup(inline_keyboard=buttons)

        # iPhone 17: показываем выбор типа сим-карты
        stor_counts = _iphone_storage_counts(iphone_new, version, model_key, memory_key)
        with SessionLocal() as db:
            all_items = mcs.load_new_products_dicts(db)
            for stor in list(stor_counts.keys()):
                stor_key = stor.replace("+", "p")
                pth = f"root>cat>iPhone>ver>{version}>md>{model_key}>mem>{memory_key}>stor>{stor_key}"
                stor_counts[stor] = mcs.total_count_for_path(db, pth, all_items)
        model_display = _iphone_model_display_label(version, model_key)
        text = _iphone_nav_text_with_products(
            f"iPhone {model_display} {mem_display}",
            mem_products,
            "Выберите тип сим-карты:",
        )
        with SessionLocal() as db:
            kb = _merge_custom_into_markup(
                get_new_iphone_storage_keyboard(
                    stor_counts,
                    version,
                    model_key,
                    memory_key,
                    back_data=f"new_iphone_mod_{version}_{model_key}",
                    label_resolver=mcs.effective_hardcoded_label,
                ),
                db,
                mem_path,
            )
        return text, kb

    text, kb = await run_db(_build)
    await _send_html_nav_message(callback, text, kb)
    await callback.answer()


@router.callback_query(F.data.startswith("new_iphone_stor_"))
async def new_iphone_storage(callback: CallbackQuery, state: FSMContext):
    """Тип хранилища -> список товаров (по цветам), короткие подписи для 17/Pro/Pro Max."""
    await state.update_data(new_products_back=callback.data)
    rest = callback.data.replace("new_iphone_stor_", "")
    parts = rest.split("_")
    if len(parts) < 4:
        await callback.answer("Ошибка навигации", show_alert=True)
        return
    version = parts[0]
    model_key = "_".join(parts[1:-2]) if len(parts) > 3 else parts[1]
    memory_key = parts[-2] if len(parts) >= 2 else ""
    storage_key = parts[-1] if parts else ""

    def _build():
        items, _ = _fetch_products_sync(limit=5000)
        iphone_new = _filter_new_products(items, "iPhone новые")
        plist = _iphone_products_for_storage(iphone_new, version, model_key, memory_key, storage_key)
        stor_path = f"root>cat>iPhone>ver>{version}>md>{model_key}>mem>{memory_key}>stor>{storage_key}"
        with SessionLocal() as db:
            plist.extend(mcs.list_products_at_hardcoded_leaf(db, stor_path))
        plist = _sort_iphone_products(plist)
        text = "🆕 Товары:\n\n"
        if plist:
            text += _format_new_iphone_products_text(plist, version, model_key, memory_key, storage_key)
        else:
            text += "Товары не найдены"
        text += "\n\nВыберите позицию:"

        back_data = format_new_iphone_var_nav(version, model_key, memory_key)
        short_labels = {p["id"]: button_label_for_product(p) for p in plist}

        has_custom_in_plist = any(bool(p.get("custom_button_id")) for p in plist)
        with SessionLocal() as db:
            base_kb = get_new_iphone_products_keyboard(
                plist,
                version, model_key, memory_key, storage_key,
                back_data=back_data,
                prefix="new_product",
                short_labels=short_labels,
            )
            # Если custom-товар уже добавлен в список позиций, не дублируем его extra-кнопкой.
            if has_custom_in_plist:
                kb = base_kb
            else:
                kb = _merge_custom_into_markup(base_kb, db, stor_path)
        return text, kb

    text, kb = await run_db(_build)
    await safe_edit_message(
        callback.message,
        text,
        reply_markup=kb,
        parse_mode="HTML",
        disable_link_preview=True,
    )
    await callback.answer()


@router.callback_query(
    F.data.startswith("new_product_")
    & ~F.data.startswith("new_product_sell_")
    & ~F.data.startswith("new_product_unavail_")
    & ~F.data.startswith("new_product_price_")
    & ~F.data.startswith("new_product_avito_")
    & ~F.data.startswith("new_product_toggle_avail_")
    & ~F.data.startswith("new_pay_")
)
async def new_product_detail(callback: CallbackQuery, state: FSMContext):
    """Детали нового товара."""
    tail = callback.data[len("new_product_") :]
    if not tail.isdigit():
        await callback.answer("Ошибка", show_alert=True)
        return
    product_id = int(tail)
    try:
        await callback.answer()
    except Exception:
        pass
    data = await state.get_data()
    back_data = data.get("new_products_back", "new_products_menu")
    if not await _show_new_product_card(callback, state, product_id, back_data):
        await callback.answer("Товар не найден", show_alert=True)
        return


@router.callback_query(F.data.startswith("new_product_sell_"))
async def new_product_sell(callback: CallbackQuery):
    """Продажа: показать выбор способа оплаты (нал/карта/кредит)."""
    pid = callback.data.replace("new_product_sell_", "")
    try:
        product_id = int(pid)
    except ValueError:
        await callback.answer("Ошибка")
        return
    product = await get_product_api(product_id)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    price_display = _normalize_price_display(product.get("price"))
    text = f"💰 Выберите способ оплаты:\n\n📦 {product.get('name', 'Без названия')}"
    await safe_edit_message(
        callback.message,
        text,
        reply_markup=get_payment_method_keyboard_new_product(product_id, price_display),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("new_pay_"))
async def new_product_payment(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора оплаты: отправить отчёт в ВК."""
    rest = callback.data.replace("new_pay_", "")
    parts = rest.split("_")
    if len(parts) < 2:
        await callback.answer("Ошибка")
        return
    method, product_id = parts[0], int(parts[1])
    from app.bot.handlers.product_management import send_vk_report
    product = await get_product_api(product_id)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    await send_vk_report(product, method)
    await callback.answer("✅ Отчет отправлен")
    sdata = await state.get_data()
    back_data = sdata.get("new_products_back", "new_products_menu")
    text = f"📦 <b>{product.get('name', 'Без названия')}</b>\n\n✅ Отчет о продаже отправлен."
    await safe_edit_message(
        callback.message,
        text,
        reply_markup=get_new_product_detail_keyboard(
            product_id,
            status=product.get("status", "active"),
            availability_status=product.get("availability_status"),
            back_data=back_data,
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("new_product_unavail_"))
async def new_product_unavailable(callback: CallbackQuery, state: FSMContext):
    """Товар недоступен: БД сразу, площадки — в очередь синхронизации."""
    from app.services.price_sync_service import (
        format_unavailable_saved_immediate_message,
        get_price_sync_service,
        is_new_product_branch,
        is_used_product_branch,
    )

    pid = callback.data.replace("new_product_unavail_", "")
    try:
        product_id = int(pid)
    except ValueError:
        await callback.answer("Ошибка")
        return

    try:
        await callback.bot.send_chat_action(callback.message.chat.id, ChatAction.TYPING)
    except Exception:
        pass

    result = await update_product_status_api(product_id, "unavailable", sync_platforms=False)
    if not result:
        await callback.answer("Ошибка обновления статуса", show_alert=True)
        return

    updated_product = result.get("product") or {}
    service = get_price_sync_service()
    await service.enqueue_unavailable_sync(
        callback.bot,
        chat_id=callback.message.chat.id,
        product_id=product_id,
        product=updated_product,
        mark_telegram_enabled=False,
        refresh_used_list=is_used_product_branch(updated_product),
        refresh_availability_list=is_new_product_branch(updated_product),
    )

    try:
        await callback.message.answer(
            format_unavailable_saved_immediate_message(),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("Could not send unavailable immediate summary: %s", e)

    product = updated_product if updated_product.get("id") else await get_product_api(product_id)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    await callback.answer("✅ Товар помечен как недоступный")
    sdata = await state.get_data()
    back_data = sdata.get("new_products_back", "new_products_menu")
    text = f"📦 <b>{product.get('name', 'Без названия')}</b>\n\n🚫 Товар недоступен (скрыт в ВК и на других площадках по привязке)."
    await safe_edit_message(
        callback.message,
        text,
        reply_markup=get_new_product_detail_keyboard(
            product_id,
            status="unavailable",
            availability_status=product.get("availability_status"),
            back_data=back_data,
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data.regexp(r"^new_product_price_\d+$"))
async def new_product_price_start(callback: CallbackQuery, state: FSMContext):
    """Начать ввод новой цены (только new_product_price_{id}, не confirm/cancel)."""
    pid = callback.data.replace("new_product_price_", "")
    try:
        product_id = int(pid)
    except ValueError:
        await callback.answer("Ошибка")
        return
    product = await get_product_api(product_id)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    old_rub = price_string_to_int_rub(product.get("price")) or 0
    await state.update_data(new_product_price_id=product_id, price_old_rub=old_rub)
    await state.set_state(NewProductPriceEdit.waiting_for_price)
    price_display = _normalize_price_display(product.get("price"))
    text = f"💰 <b>Изменение цены</b>\n\n📦 {product.get('name', 'Без названия')}\n\nТекущая цена: {price_display}\n\nВведите новую цену (число):"
    await safe_edit_message(
        callback.message,
        text,
        reply_markup=get_new_product_price_edit_keyboard(product_id),
        parse_mode="HTML",
    )
    await callback.answer()


async def _after_new_product_price_updated(
    message: Message,
    product_id: int,
    updated_product: dict,
    back_data: str,
) -> None:
    product = updated_product if updated_product.get("id") else await get_product_api(product_id)
    if not product:
        return
    price_display = _normalize_price_display(product.get("price"))
    text = f"📦 <b>{product.get('name', 'Без названия')}</b>\n\n💵 Цена: {price_display}"
    await message.answer(
        text,
        reply_markup=get_new_product_detail_keyboard(
            product_id,
            status=product.get("status", "active"),
            availability_status=product.get("availability_status"),
            back_data=back_data,
        ),
        parse_mode="HTML",
    )


@router.message(NewProductPriceEdit.waiting_for_price)
async def new_product_price_apply(message: Message, state: FSMContext):
    """Применить введённую цену."""
    data = await state.get_data()
    product_id = data.get("new_product_price_id")
    back_data = data.get("new_products_back", "new_products_menu")
    if not product_id:
        await state.clear()
        await message.answer("Ошибка: товар не выбран.")
        return
    raw = (message.text or "").strip()
    price_clean = re.sub(r"[^\d\s.,]", "", raw).replace(" ", "").replace(",", ".")
    if not price_clean or not price_clean.replace(".", "").isdigit():
        await message.answer("❌ Введите корректное число.")
        return
    try:
        val = float(price_clean)
        if val <= 0:
            await message.answer("❌ Цена должна быть больше нуля.")
            return
    except ValueError:
        await message.answer("❌ Введите корректное число.")
        return
    price_str = raw if "₽" in raw or "руб" in raw.lower() else f"{raw}₽"
    old_price_rub = int(data.get("price_old_rub") or 0)
    new_rub = int(val)
    price_change = analyze_price_change(old_price_rub, new_rub) if old_price_rub else None

    if price_change and price_change.needs_confirm:
        product = await get_product_api(product_id)
        product_name = (product or {}).get("name", "Без названия")
        await state.update_data(pending_formatted_price=price_str, new_products_back=back_data)
        await state.set_state(NewProductPriceEdit.waiting_for_confirm)
        await message.answer(
            format_price_change_confirm_prompt(product_name, price_change),
            parse_mode="HTML",
            reply_markup=get_price_change_confirm_keyboard(
                product_id, callback_prefix="new_product_price"
            ),
        )
        return

    try:
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    except Exception:
        pass

    summary, updated_product = await execute_product_price_update(
        product_id, price_str, old_price_rub,
        bot=message.bot,
        chat_id=message.chat.id,
    )

    await state.clear()
    if not summary:
        await message.answer("❌ Ошибка обновления цены.")
        return

    await message.answer(summary, parse_mode="HTML")
    if updated_product:
        await _after_new_product_price_updated(
            message, product_id, updated_product, back_data
        )


@router.callback_query(F.data.startswith("new_product_price_confirm_"))
async def new_product_price_confirm(callback: CallbackQuery, state: FSMContext):
    """Подтверждение сильного изменения цены (новые товары)."""
    try:
        product_id = int(callback.data.replace("new_product_price_confirm_", ""))
    except ValueError:
        await callback.answer("Ошибка")
        return

    data = await state.get_data()
    if data.get("new_product_price_id") != product_id:
        await callback.answer("Сессия устарела. Начните изменение цены заново.", show_alert=True)
        return

    formatted_price = data.get("pending_formatted_price")
    old_price_rub = int(data.get("price_old_rub") or 0)
    back_data = data.get("new_products_back", "new_products_menu")
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

    await state.clear()
    if not summary:
        await callback.message.answer("❌ Ошибка обновления цены.")
        return

    await callback.message.answer(summary, parse_mode="HTML")
    if updated_product:
        await _after_new_product_price_updated(
            callback.message, product_id, updated_product, back_data
        )


async def _return_to_new_product_detail(
    callback: CallbackQuery,
    product_id: int,
    back_data: str,
) -> None:
    """Вернуться в карточку нового товара (после отмены/назад из смены цены)."""
    product = await get_product_api(product_id)
    if not product:
        return

    price_display = _normalize_price_display(product.get("price"))
    text = f"📦 <b>{product.get('name', 'Без названия')}</b>\n\n💵 Цена: {price_display}"
    await safe_edit_message(
        callback.message,
        text,
        reply_markup=get_new_product_detail_keyboard(
            product_id,
            status=product.get("status", "active"),
            availability_status=product.get("availability_status"),
            back_data=back_data,
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("new_product_price_back_"))
async def new_product_price_back(callback: CallbackQuery, state: FSMContext):
    """Назад с экрана ввода цены — в карточку товара без сохранения."""
    try:
        product_id = int(callback.data.replace("new_product_price_back_", ""))
    except ValueError:
        await callback.answer("Ошибка")
        return

    data = await state.get_data()
    back_data = data.get("new_products_back", "new_products_menu")
    await state.clear()
    await callback.answer()
    await _return_to_new_product_detail(callback, product_id, back_data)


@router.callback_query(F.data.startswith("new_product_price_cancel_"))
async def new_product_price_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена сильного изменения цены — возврат в карточку товара."""
    try:
        product_id = int(callback.data.replace("new_product_price_cancel_", ""))
    except ValueError:
        await callback.answer("Ошибка")
        return

    data = await state.get_data()
    back_data = data.get("new_products_back", "new_products_menu")
    await state.clear()
    await callback.answer("Изменение отменено")
    await _return_to_new_product_detail(callback, product_id, back_data)


@router.callback_query(F.data.startswith("new_product_avito_"))
async def new_product_avito_start(callback: CallbackQuery, state: FSMContext):
    pid = callback.data.replace("new_product_avito_", "")
    try:
        product_id = int(pid)
    except ValueError:
        await callback.answer("Ошибка")
        return
    product = await get_product_api(product_id)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    sdata = await state.get_data()
    back_data = sdata.get("new_products_back", "new_products_menu")
    cur = product.get("avito_url") or product.get("avito_item_id") or "не привязано"
    text = (
        f"🛒 <b>Привязка Авито</b>\n\n"
        f"📦 {escape(product.get('name', 'Без названия'))}\n\n"
        f"Текущее: {escape(str(cur))}\n\n"
        "Отправьте <b>ссылку на объявление</b> или только <b>числовой id</b> (цифры из URL).\n"
        "Лучше всего: откройте объявление в <b>браузере</b> и скопируйте адрес "
        "(в конце часто <code>…_1234567890</code> или сегмент <code>/1234567890</code>).\n"
        "Ссылка «Поделиться» из приложения подойдёт, если в тексте есть этот id; "
        "короткая ссылка без цифр — не сработает."
    )
    await state.update_data(new_product_avito_id=product_id, new_products_back=back_data)
    await state.set_state(NewProductAvitoLink.waiting_for_ref)
    await safe_edit_message(callback.message, text, parse_mode="HTML")
    await callback.answer()


@router.message(NewProductAvitoLink.waiting_for_ref)
async def new_product_avito_apply(message: Message, state: FSMContext):
    data = await state.get_data()
    product_id = data.get("new_product_avito_id")
    back_data = data.get("new_products_back", "new_products_menu")
    await state.clear()
    if not product_id:
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
    product = await get_product_api(product_id)
    if product:
        price_display = _normalize_price_display(product.get("price"))
        text = f"📦 <b>{product.get('name', 'Без названия')}</b>\n\n💵 Цена: {price_display}\n"
        text += f"📁 Подборка: {product.get('collection_name', '—')}\n"
        av = product.get("availability_status")
        text += f"Наличие: {'🟢 В наличии' if av == 'available' else '🔴 На заказ' if av == 'on_order' else '—'}\n"
        if product.get("vk_product_link"):
            text += f"\n🔗 <a href=\"{product['vk_product_link']}\">Ссылка на товар в ВК</a>"
        if product.get("avito_url"):
            text += f"\n🛒 <a href=\"{product['avito_url']}\">Ссылка на Авито</a>"
        await message.answer(
            text,
            reply_markup=get_new_product_detail_keyboard(
                product_id,
                status=product.get("status", "active"),
                availability_status=av,
                back_data=back_data,
            ),
            parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("new_product_toggle_avail_"))
async def new_product_toggle_availability(callback: CallbackQuery, state: FSMContext):
    """Переключить наличие: available <-> on_order."""
    pid = callback.data.replace("new_product_toggle_avail_", "")
    try:
        product_id = int(pid)
    except ValueError:
        await callback.answer("Ошибка")
        return
    product = await get_product_api(product_id)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    cur = product.get("availability_status")
    next_val = "on_order" if cur == "available" else "available"
    try:
        from app.services.product_ops_service import set_product_availability

        updated = await run_db(set_product_availability, product_id, next_val)
        if not updated:
            await callback.answer("Ошибка обновления наличия", show_alert=True)
            return
    except Exception as e:
        logger.error("Error updating availability: %s", e)
        await callback.answer("Ошибка обновления наличия", show_alert=True)
        return
    try:
        from app.bot.utils.channel_updater import update_availability_message
        await update_availability_message(callback.bot)
    except Exception as e:
        logger.warning("Could not update channel availability message: %s", e)
    product = await get_product_api(product_id)
    if not product:
        await callback.answer("Ошибка")
        return
    sdata = await state.get_data()
    back_data = sdata.get("new_products_back", "new_products_menu")
    lbl = "🟢 В наличии" if next_val == "available" else "🔴 На заказ"
    await callback.answer(f"✅ {lbl}")
    price_display = _normalize_price_display(product.get("price"))
    text = f"📦 <b>{product.get('name', 'Без названия')}</b>\n\n💵 Цена: {price_display}\nНаличие: {lbl}"
    await safe_edit_message(
        callback.message,
        text,
        reply_markup=get_new_product_detail_keyboard(
            product_id,
            status=product.get("status", "active"),
            availability_status=product.get("availability_status"),
            back_data=back_data,
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("new_product_tag_desc_"))
async def new_product_tag_desc_start(callback: CallbackQuery, state: FSMContext):
    try:
        product_id = int(callback.data.replace("new_product_tag_desc_", ""))
    except ValueError:
        await callback.answer("Ошибка", show_alert=True)
        return
    product = await get_product_api(product_id)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    sdata = await state.get_data()
    back_data = sdata.get("new_products_back", "new_products_menu")
    await state.update_data(
        new_product_tag_id=product_id,
        new_products_back=back_data,
    )
    await state.set_state(NewProductTagDesc.waiting_for_subtitle)
    cur_sub = (product.get("price_tag_subtitle") or "").strip()
    hint = f"\n\nТекущий: {escape(cur_sub)}" if cur_sub else ""
    await safe_edit_message(
        callback.message,
        f"📝 <b>Описание ценника</b> — товар #{product_id}{hint}\n\n"
        "Введите <b>подзаголовок</b> (например: <code>не_активирован</code>).\n"
        "Отправьте <code>-</code> чтобы очистить.",
        reply_markup=get_new_product_tag_desc_keyboard(product_id, back_data),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("new_product_tag_back_"))
async def new_product_tag_desc_back(callback: CallbackQuery, state: FSMContext):
    try:
        product_id = int(callback.data.replace("new_product_tag_back_", ""))
    except ValueError:
        await callback.answer("Ошибка", show_alert=True)
        return
    sdata = await state.get_data()
    back_data = sdata.get("new_products_back", "new_products_menu")
    await state.clear()
    ok = await _show_new_product_card(callback, state, product_id, back_data)
    if ok:
        await callback.answer()
    else:
        await callback.answer("Товар не найден", show_alert=True)


@router.message(NewProductTagDesc.waiting_for_subtitle)
async def new_product_tag_subtitle(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    subtitle = "" if raw == "-" else raw[:64]
    await state.update_data(new_product_tag_subtitle=subtitle)
    await state.set_state(NewProductTagDesc.waiting_for_description)
    await message.answer(
        "Введите <b>описание для ценника</b>.\n"
        "Отправьте <code>-</code> чтобы очистить (будет использован шаблон из настроек).",
        parse_mode="HTML",
    )


@router.message(NewProductTagDesc.waiting_for_description)
async def new_product_tag_description_save(message: Message, state: FSMContext):
    from app.db.database import SessionLocal
    from app.db.product_queries import update_product_price_tag_fields
    from app.services.menu_constructor_service import invalidate_new_products_cache

    raw = (message.text or "").strip()
    data = await state.get_data()
    product_id = int(data.get("new_product_tag_id") or 0)
    subtitle = data.get("new_product_tag_subtitle")
    description = "" if raw == "-" else raw[:512]
    back_data = data.get("new_products_back", "new_products_menu")

    def _save():
        with SessionLocal() as db:
            update_product_price_tag_fields(
                db,
                product_id,
                price_tag_subtitle=subtitle if subtitle is not None else None,
                clear_subtitle=subtitle == "",
                price_tag_description=description if description is not None else None,
                clear_description=description == "",
            )
        invalidate_new_products_cache()

    try:
        await run_db(_save)
    except Exception:
        logger.exception("new_product_tag_description save")
        await message.answer("❌ Ошибка сохранения.")
        return

    await state.clear()
    await message.answer("✅ Описание ценника сохранено.")
    product = await get_product_api(product_id)
    if product:
        price_display = _normalize_price_display(product.get("price"))
        av = product.get("availability_status")
        text = f"📦 <b>{product.get('name', 'Без названия')}</b>\n\n💵 Цена: {price_display}\n"
        text += f"Наличие: {'🟢 В наличии' if av == 'available' else '🔴 На заказ' if av == 'on_order' else '—'}\n"
        pt_sub = (product.get("price_tag_subtitle") or "").strip()
        pt_desc = (product.get("price_tag_description") or "").strip()
        if pt_sub or pt_desc:
            text += "\n🏷️ <b>Ценник:</b>\n"
            if pt_sub:
                text += f"Подзаголовок: {escape(pt_sub)}\n"
            if pt_desc:
                preview = pt_desc if len(pt_desc) <= 120 else pt_desc[:117] + "…"
                text += f"Описание: {escape(preview)}\n"
        await message.answer(
            text,
            reply_markup=get_new_product_detail_keyboard(
                product_id,
                status=product.get("status", "active"),
                availability_status=av,
                back_data=back_data,
            ),
            parse_mode="HTML",
        )
