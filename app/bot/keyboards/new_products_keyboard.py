"""
Клавиатуры для навигации по новым товарам (из подборок ВК).
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import List, Optional, Dict, Callable

from app.bot.utils.button_styles import ikb
from app.utils.color_emoji import replace_color_with_emoji
from app.services import menu_constructor_service as mcs_paths


# Маппинг категорий на названия подборок ВК и эмодзи
NEW_CATEGORIES = [
    ("Airpods", "Airpods", "🎧"),
    ("Apple Watch", "Apple Watch", "⌚"),
    ("iPad", "iPad", "📱"),
    ("iPhone", "iPhone новые", "📱"),
]

# Навигация «память» после модели: pipe, чтобы не путать iPhone 13 и 13 Pro (старый new_iphone_var_13_13_128).
NEW_IPHONE_VAR_PIPE = "new_iphone_var|"


def format_new_iphone_var_nav(version: str, model_key: str, memory_token: str) -> str:
    """callback для шага памяти и «назад» с экрана товаров 17: memory_token как в клавиатуре (128, 1tb, …)."""
    return f"{NEW_IPHONE_VAR_PIPE}{version}|{model_key}|{memory_token}"


def parse_new_iphone_var_nav(callback_data: str) -> tuple[str, str, str]:
    """(version, model_key, memory_key в lower) из new_iphone_var|… или legacy new_iphone_var_…"""
    if callback_data.startswith(NEW_IPHONE_VAR_PIPE):
        body = callback_data[len(NEW_IPHONE_VAR_PIPE) :]
        try:
            version, model_key, mem = body.split("|", 2)
        except ValueError:
            return "", "", ""
        return (version or "", model_key or "", (mem or "").lower())
    if callback_data.startswith("new_iphone_var_"):
        rest = callback_data.replace("new_iphone_var_", "", 1)
        parts = rest.split("_")
        if not parts:
            return "", "", ""
        version = parts[0] if parts else ""
        model_key = "_".join(parts[1:-1]) if len(parts) > 2 else (parts[1] if len(parts) > 1 else "")
        memory_key = (parts[-1] or "").lower()
        return version, model_key, memory_key
    return "", "", ""


def get_new_products_categories_keyboard(counts: Dict[str, int]) -> InlineKeyboardMarkup:
    """
    Клавиатура категорий новых товаров.
    counts: {"Airpods": 6, "Apple Watch": 18, "iPad": 6, "iPhone": 53}
    """
    buttons = []
    for key, _collection, emoji in NEW_CATEGORIES:
        c = counts.get(key, 0)
        label = "iPhone" if key == "iPhone" else key
        buttons.append([
            InlineKeyboardButton(
                text=f"{emoji}{label} ({c})",
                callback_data=f"new_cat_{key.replace(' ', '_')}"
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data="products_menu")
    ])
    buttons.append([
        InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_new_iphone_versions_keyboard(
    version_counts: Dict[str, int],
    back_data: str = "new_products_menu",
    label_resolver: Optional[Callable[[str, str], str]] = None,
) -> InlineKeyboardMarkup:
    """
    Клавиатура версий iPhone для новых товаров (12, 13, 14, 15, 16, 17).
    version_counts: {"12": 0, "13": 3, "14": 5, "15": 6, "16": 10, "17": 18}
    label_resolver: (path, default_label) -> подпись из настроек конструктора.
    """
    buttons = []
    for v in ["12", "13", "14", "15", "16", "17"]:
        c = version_counts.get(v, 0)
        if c <= 0:
            continue
        pth = f"root>cat>iPhone>ver>{v}"
        disp = label_resolver(pth, f"iPhone {v}") if label_resolver else f"iPhone {v}"
        buttons.append([
            InlineKeyboardButton(
                text=f"📱 {disp} ({c})",
                callback_data=f"new_iphone_ver_{v}"
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data=back_data)
    ])
    buttons.append([
        InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_new_iphone_models_keyboard(
    model_counts: Dict[str, int],
    version: str,
    back_data: str = "new_cat_iPhone",
    label_resolver: Optional[Callable[[str, str], str]] = None,
) -> InlineKeyboardMarkup:
    """
    Клавиатура моделей версии. Использует только модели из model_counts (не жестко заданный список).
    model_counts: {"13": 2, "13 Pro": 1} для версии 13, {"Air": 4, "17": 8} для версии 17
    """
    buttons = []
    # Порядок строк = порядок ключей в model_counts (вызывающий код задаёт порядок слотов версии).
    for m in model_counts.keys():
        c = model_counts.get(m, 0)
        key = m.replace(" ", "_").lower()
        pth = f"root>cat>iPhone>ver>{version}>md>{key}"
        disp = label_resolver(pth, m) if label_resolver else m
        buttons.append([
            InlineKeyboardButton(
                text=f"{disp} ({c})",
                callback_data=f"new_iphone_mod_{version}_{key}"
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data=back_data)
    ])
    buttons.append([
        InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_new_iphone_variants_keyboard(
    variant_counts: Dict[str, int],
    version: str,
    model_key: str,
    back_data: str,
    label_resolver: Optional[Callable[[str, str], str]] = None,
) -> InlineKeyboardMarkup:
    """
    Клавиатура вариантов по памяти: только существующие (128Gb, 256Gb, 512Gb, 1Tb).
    variant_counts: {"256": 8, "512": 2} — только ключи с count > 0.
    """
    buttons = []
    order = ["64", "128", "256", "512", "1Tb"]
    for mem in order:
        c = variant_counts.get(mem, 0)
        if c <= 0:
            continue
        mem_safe = mem.replace("Tb", "tb")
        base_l = f"1Tb" if mem == "1Tb" else f"{mem}Gb"
        pth = f"root>cat>iPhone>ver>{version}>md>{model_key}>mem>{mem_safe.lower()}"
        disp = label_resolver(pth, base_l) if label_resolver else base_l
        label = f"{disp} ({c})"
        buttons.append([
            InlineKeyboardButton(
                text=label,
                callback_data=format_new_iphone_var_nav(version, model_key, mem_safe),
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data=back_data)
    ])
    buttons.append([
        InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_new_iphone_storage_keyboard(
    storage_counts: Dict[str, int],
    version: str,
    model_key: str,
    memory_key: str,
    back_data: str,
    label_resolver: Optional[Callable[[str, str], str]] = None,
) -> InlineKeyboardMarkup:
    """
    Клавиатура типов хранилища: esim, 1+1, 2sim.
    storage_counts: {"esim": 4, "1+1": 4, "2sim": 0}
    """
    buttons = []
    for stor, label in [("esim", "esim"), ("1+1", "1+1"), ("2sim", "2sim")]:
        c = storage_counts.get(stor, 0)
        if c <= 0:
            continue
        stor_safe = stor.replace("+", "p")
        pth = f"root>cat>iPhone>ver>{version}>md>{model_key}>mem>{memory_key}>stor>{stor_safe}"
        disp = label_resolver(pth, label) if label_resolver else label
        buttons.append([
            InlineKeyboardButton(
                text=f"{disp} ({c})",
                callback_data=f"new_iphone_stor_{version}_{model_key}_{memory_key}_{stor_safe}"
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data=back_data)
    ])
    buttons.append([
        InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_new_iphone_products_keyboard(
    products: List[dict],
    version: str,
    model_key: str,
    memory_key: str,
    storage_key: str,
    back_data: str,
    prefix: str = "new_product",
    short_labels: Optional[Dict[int, str]] = None
) -> InlineKeyboardMarkup:
    """
    Клавиатура товаров. short_labels: {product_id: "17 256Gb ⚪️ eSim"} для коротких подписей (iPhone 17).
    """
    buttons = []
    for p in products:
        pid = p.get("id")
        if short_labels and pid is not None and pid in short_labels:
            label = short_labels[pid]
        else:
            label = replace_color_with_emoji(p.get("name", "Без названия"))
            if len(label) > 35:
                label = label[:32] + "..."
        buttons.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"{prefix}_{pid}"
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад к списку", callback_data=back_data)
    ])
    buttons.append([
        InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_airpods_models_keyboard(
    model_counts: Dict[str, int],
    back_data: str = "new_products_menu",
    label_resolver: Optional[Callable[[str, str], str]] = None,
) -> InlineKeyboardMarkup:
    """
    Клавиатура моделей AirPods.
    model_counts: {"AirPods 3": 2, "AirPods 3 Magsafe": 1, "AirPods 4": 1, ...}
    """
    buttons = []
    # Порядок моделей AirPods
    order = ["AirPods 3", "AirPods 3 Magsafe", "AirPods 4", "AirPods 4 ANC", "AirPods Pro 2", "AirPods Pro 3"]
    for model in order:
        c = model_counts.get(model, 0)
        if c <= 0:
            continue
        model_key = model.replace(" ", "_").lower().replace("airpods", "airpods")
        mk = mcs_paths.AIRPODS_KEY.get(model, model_key)
        pth = f"root>cat>Airpods>md>{mk}"
        disp = label_resolver(pth, model) if label_resolver else model
        buttons.append([
            InlineKeyboardButton(
                text=f"{disp} ({c})",
                callback_data=f"new_airpods_{model_key}"
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data=back_data)
    ])
    buttons.append([
        InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_apple_watch_categories_keyboard(
    category_counts: Dict[str, int],
    back_data: str = "new_products_menu",
    label_resolver: Optional[Callable[[str, str], str]] = None,
) -> InlineKeyboardMarkup:
    """
    Клавиатура категорий Apple Watch: SE 2, SE 3, 11.
    category_counts: {"SE 2": 6, "SE 3": 8, "11": 4}
    """
    buttons = []
    order = ["SE 2", "SE 3", "11"]
    for cat in order:
        c = category_counts.get(cat, 0)
        if c <= 0:
            continue
        cat_key = cat.replace(" ", "_").lower()
        ck = mcs_paths.WATCH_KEY[cat]
        pth = f"root>cat>Apple Watch>wc>{ck}"
        base = f"AW {cat}"
        disp = label_resolver(pth, base) if label_resolver else base
        buttons.append([
            InlineKeyboardButton(
                text=f"{disp} ({c})",
                callback_data=f"new_watch_cat_{cat_key}"
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data=back_data)
    ])
    buttons.append([
        InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_apple_watch_sizes_keyboard(
    size_counts: Dict[str, int],
    category: str,
    back_data: str
) -> InlineKeyboardMarkup:
    """
    Клавиатура размеров Apple Watch: 40mm, 44mm.
    size_counts: {"40mm": 3, "44mm": 3}
    """
    buttons = []
    order = ["40mm", "44mm"]
    for size in order:
        c = size_counts.get(size, 0)
        if c <= 0:
            continue
        buttons.append([
            InlineKeyboardButton(
                text=f"AW {category} {size} ({c})",
                callback_data=f"new_watch_size_{category.replace(' ', '_').lower()}_{size}"
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data=back_data)
    ])
    buttons.append([
        InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_ipad_models_keyboard(
    model_counts: Dict[str, int],
    back_data: str = "new_products_menu",
    label_resolver: Optional[Callable[[str, str], str]] = None,
) -> InlineKeyboardMarkup:
    """
    Клавиатура моделей iPad: iPad 11, iPad Air.
    model_counts: {"iPad 11": 2, "iPad Air": 4}
    """
    buttons = []
    order = ["iPad 11", "iPad Air"]
    for model in order:
        c = model_counts.get(model, 0)
        if c <= 0:
            continue
        model_key = model.replace(" ", "_").lower()
        mk = mcs_paths.IPAD_KEY[model]
        pth = f"root>cat>iPad>md>{mk}"
        disp = label_resolver(pth, model) if label_resolver else model
        buttons.append([
            InlineKeyboardButton(
                text=f"{disp} ({c})",
                callback_data=f"new_ipad_{model_key}"
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data=back_data)
    ])
    buttons.append([
        InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_new_product_detail_keyboard(
    product_id: int,
    status: str = "active",
    availability_status: Optional[str] = None,
    back_data: str = "new_products_menu"
) -> InlineKeyboardMarkup:
    """
    Клавиатура деталей нового товара:
    - Продажа
    - Товар недоступен
    - Изменить цену
    - 🟢 В наличии / 🔴 На заказ (переключатель)
    - Назад к списку
    """
    buttons = []
    if status == "active":
        buttons.append([
            InlineKeyboardButton(text="💰 Продажа", callback_data=f"new_product_sell_{product_id}")
        ])
        buttons.append([
            ikb(
                "🚫 Товар недоступен",
                f"new_product_unavail_{product_id}",
                style="danger",
            )
        ])
    buttons.append([
        ikb(
            "💰 Изменить цену",
            f"new_product_price_{product_id}",
            style="primary",
        )
    ])
    buttons.append([
        InlineKeyboardButton(text="🛒 Авито (ссылка / id)", callback_data=f"new_product_avito_{product_id}")
    ])
    avail_text = "🟢 В наличии" if availability_status == "available" else "🔴 На заказ"
    buttons.append([
        InlineKeyboardButton(text=avail_text, callback_data=f"new_product_toggle_avail_{product_id}")
    ])
    buttons.append([
        InlineKeyboardButton(
            text="📝 Описание ценника",
            callback_data=f"new_product_tag_desc_{product_id}",
        )
    ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад к списку", callback_data=back_data)
    ])
    buttons.append([
        InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_new_product_tag_desc_keyboard(product_id: int, back_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=f"new_product_tag_back_{product_id}",
                )
            ],
        ]
    )


def get_new_product_price_edit_keyboard(product_id: int) -> InlineKeyboardMarkup:
    """Клавиатура экрана ввода новой цены (новые товары)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=f"new_product_price_back_{product_id}",
                )
            ],
        ]
    )


def get_payment_method_keyboard_new_product(
    product_id: int,
    price_str: Optional[str] = None
) -> InlineKeyboardMarkup:
    """Клавиатура выбора способа оплаты для «Продажа» (нал/карта/кредит)."""
    import re
    import math
    cash_price = price_str or "0₽"
    card_price = "0₽"
    credit_price = price_str or "0₽"
    if price_str:
        price_clean = re.sub(r"[^\d.,]", "", price_str).replace(",", ".")
        try:
            base = float(price_clean)
            card_val = math.ceil(base * 1.05 / 10) * 10
            card_price = f"{int(card_val)}₽"
        except (ValueError, TypeError):
            pass
    buttons = [
        [InlineKeyboardButton(text=f"Нал 💰 {cash_price}", callback_data=f"new_pay_cash_{product_id}")],
        [InlineKeyboardButton(text=f"Карта 💳 {card_price}", callback_data=f"new_pay_card_{product_id}")],
        [InlineKeyboardButton(text=f"Кредит 🏦 {credit_price}", callback_data=f"new_pay_credit_{product_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"new_product_{product_id}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)
