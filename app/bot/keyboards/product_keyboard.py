from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import List, Optional, Dict
from app.utils.color_emoji import replace_color_with_emoji
from app.bot.utils.button_styles import ikb


def get_products_menu_keyboard(
    *,
    avito_market_enabled: Optional[bool] = None,
) -> InlineKeyboardMarkup:
    """Создает клавиатуру главного меню товаров."""
    if avito_market_enabled is None:
        from app.services.settings_service import get_settings_service

        avito_market_enabled = get_settings_service().is_avito_market_enabled()
    buttons = [
        [InlineKeyboardButton(text="📦 Список б/у товаров", callback_data="products_list")],
        [InlineKeyboardButton(text="🆕 Список новых", callback_data="new_products_menu")],
        [InlineKeyboardButton(text="🔍 Поиск товара", callback_data="products_search")],
    ]
    if avito_market_enabled:
        buttons.append(
            [InlineKeyboardButton(text="📊 Оценка рынка Avito", callback_data="avito_market_start")]
        )
    buttons.extend(
        [
            [InlineKeyboardButton(text="📁 Архив товаров", callback_data="products_archive")],
            [InlineKeyboardButton(text="🔄 Обновление постов", callback_data="sync_telegram_links")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_product_list_keyboard(
    products: List[dict],
    page: int = 0,
    per_page: int = 10
) -> InlineKeyboardMarkup:
    """Создает клавиатуру для списка товаров с пагинацией."""
    buttons = []
    
    # Кнопки для товаров на текущей странице
    start_idx = page * per_page
    end_idx = min(start_idx + per_page, len(products))
    
    for i in range(start_idx, end_idx):
        product = products[i]
        product_name = product.get('name', 'Без названия')
        # Преобразуем названия цветов в эмодзи для inline клавиатуры
        product_name = replace_color_with_emoji(product_name)
        # Обрезаем название, если слишком длинное
        if len(product_name) > 40:
            product_name = product_name[:37] + "..."
        buttons.append([
            InlineKeyboardButton(
                text=f"📦 {product_name}",
                callback_data=f"product_{product['id']}"
            )
        ])
    
    # Кнопки пагинации
    nav_buttons = []
    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"products_page_{page-1}")
        )
    if end_idx < len(products):
        nav_buttons.append(
            InlineKeyboardButton(text="Вперед ➡️", callback_data=f"products_page_{page+1}")
        )
    
    if nav_buttons:
        buttons.append(nav_buttons)
    
    # Кнопки управления
    buttons.append([
        InlineKeyboardButton(text="🔍 Поиск", callback_data="products_search"),
        InlineKeyboardButton(text="⬅️ Назад", callback_data="products_menu")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _product_button_row(product: dict, prefix_emoji: str) -> List[InlineKeyboardButton]:
    """Одна кнопка-строка товара для списков поиска."""
    product_name = product.get('name', 'Без названия')
    product_name = replace_color_with_emoji(product_name)
    if len(product_name) > 40:
        product_name = product_name[:37] + "..."
    return [
        InlineKeyboardButton(
            text=f"{prefix_emoji} {product_name}",
            callback_data=f"product_{product['id']}"
        )
    ]


def get_search_results_keyboard(
    active_products: List[dict],
    archive_products: List[dict],
    *,
    act_page: int = 0,
    arc_page: int = 0,
    per_page: int = 10,
    archive_expanded: bool = False,
) -> InlineKeyboardMarkup:
    """Клавиатура результатов поиска товаров.

    Приоритет — товары в наличии (active, б/у-ветка) со своей пагинацией.
    Архив (unavailable) скрыт под сворачиваемой кнопкой со счётчиком и
    раскрывается по требованию, со своей независимой пагинацией.
    """
    buttons: List[List[InlineKeyboardButton]] = []

    # --- Товары в наличии ---
    a_start = act_page * per_page
    a_end = min(a_start + per_page, len(active_products))
    for i in range(a_start, a_end):
        buttons.append(_product_button_row(active_products[i], "📦"))

    nav_buttons = []
    if act_page > 0:
        nav_buttons.append(
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"psearch_act_{act_page-1}")
        )
    if a_end < len(active_products):
        nav_buttons.append(
            InlineKeyboardButton(text="Вперед ➡️", callback_data=f"psearch_act_{act_page+1}")
        )
    if nav_buttons:
        buttons.append(nav_buttons)

    # --- Архив (по требованию) ---
    if archive_products:
        if not archive_expanded:
            buttons.append([
                InlineKeyboardButton(
                    text=f"📁 В архиве ({len(archive_products)})",
                    callback_data="psearch_arc_0",
                )
            ])
        else:
            buttons.append([
                InlineKeyboardButton(
                    text=f"🔼 Свернуть архив ({len(archive_products)})",
                    callback_data="psearch_collapse",
                )
            ])
            ar_start = arc_page * per_page
            ar_end = min(ar_start + per_page, len(archive_products))
            for i in range(ar_start, ar_end):
                buttons.append(_product_button_row(archive_products[i], "🗄️"))

            arc_nav = []
            if arc_page > 0:
                arc_nav.append(
                    InlineKeyboardButton(text="⬅️ Архив назад", callback_data=f"psearch_arc_{arc_page-1}")
                )
            if ar_end < len(archive_products):
                arc_nav.append(
                    InlineKeyboardButton(text="Архив вперед ➡️", callback_data=f"psearch_arc_{arc_page+1}")
                )
            if arc_nav:
                buttons.append(arc_nav)

    # --- Управление ---
    buttons.append([
        InlineKeyboardButton(text="🔍 Новый поиск", callback_data="products_search"),
        InlineKeyboardButton(text="⬅️ Назад", callback_data="products_menu"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_product_detail_keyboard(
    product_id: int,
    status: str = "active",
    back_data: str = "products_list",
) -> InlineKeyboardMarkup:
    """Создает клавиатуру для детальной информации о товаре."""
    buttons = []
    
    # Кнопки изменения статуса
    if status == "active":
        buttons.append([
            ikb(
                "🚫 Товар недоступен",
                f"product_unavailable_{product_id}",
                style="danger",
            )
        ])
    elif status == "unavailable":
        buttons.append([
            ikb(
                "✅ Восстановить товар",
                f"product_restore_{product_id}",
                style="success",
            )
        ])
    
    # Кнопка удаления
    buttons.append([
        InlineKeyboardButton(
            text="🗑️ Удалить товар",
            callback_data=f"product_delete_{product_id}"
        )
    ])
    
    # Кнопка изменения цены
    buttons.append([
        ikb(
            "💰 Изменить цену",
            f"product_price_{product_id}",
            style="primary",
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            text="🛒 Авито (ссылка / id)",
            callback_data=f"product_avito_link_{product_id}"
        )
    ])

    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад к списку", callback_data=back_data)
    ])
    buttons.append([
        InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_product_price_edit_keyboard(product_id: int) -> InlineKeyboardMarkup:
    """Клавиатура экрана ввода новой цены (б/у товары)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=f"product_price_back_{product_id}",
                )
            ],
        ]
    )


def get_product_status_confirmation_keyboard(
    product_id: int,
    action: str,  # "unavailable", "delete", "restore"
    report_enabled: bool = False,
    mark_telegram_enabled: bool = True,
) -> InlineKeyboardMarkup:
    """Создает клавиатуру подтверждения изменения статуса товара."""
    buttons = []

    if action == "unavailable":
        report_text = "🟢 Отчет Иван/Саша" if report_enabled else "🔴 Отчет Иван/Саша"
        buttons.append([
            InlineKeyboardButton(
                text=report_text,
                callback_data=f"product_toggle_report_{product_id}"
            )
        ])

        mark_tg_text = "🟢 Пометить ТГ/IG/Max" if mark_telegram_enabled else "🔴 Пометить ТГ/IG/Max"
        buttons.append([
            InlineKeyboardButton(
                text=mark_tg_text,
                callback_data=f"product_toggle_mark_tg_{product_id}"
            )
        ])

        flags = f"{int(report_enabled)}_{int(mark_telegram_enabled)}"
        sale_cb = f"product_confirm_{action}_{product_id}_{flags}_0"
        transfer_cb = f"product_confirm_{action}_{product_id}_{flags}_1"
        buttons.append([
            ikb("💰 Продажа", sale_cb, style="success"),
            ikb("📦 Перемещение", transfer_cb, style="danger"),
        ])
        buttons.append([ikb("❌ Отмена", f"product_{product_id}")])
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    confirm_style = "danger" if action == "delete" else "success"
    confirm_cb = (
        f"product_confirm_{action}_{product_id}_"
        f"{int(report_enabled)}_{int(mark_telegram_enabled)}"
    )
    buttons.append([ikb("✅ Подтвердить", confirm_cb, style=confirm_style)])
    buttons.append([ikb("❌ Отмена", f"product_{product_id}")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_payment_method_keyboard(product_id: int, price_str: str = None) -> InlineKeyboardMarkup:
    """Создает клавиатуру для выбора способа оплаты."""
    import re
    import math
    
    # Вычисляем суммы для каждого способа оплаты
    cash_price = price_str or "0₽"
    card_price = "0₽"
    credit_price = price_str or "0₽"
    
    if price_str:
        # Извлекаем число из цены
        price_clean = re.sub(r'[^\d.,]', '', price_str)
        price_clean = price_clean.replace(',', '.')
        
        try:
            base_price = float(price_clean)
            # Для карты добавляем 5% и округляем в большую сторону до десятков
            card_price_value = base_price * 1.05
            card_price_value = math.ceil(card_price_value / 10) * 10
            card_price = f"{int(card_price_value)}₽"
        except (ValueError, TypeError):
            pass
    
    buttons = [
        [
            InlineKeyboardButton(
                text=f"Нал 💰 {cash_price}",
                callback_data=f"product_payment_cash_{product_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"Карта 💳 {card_price}",
                callback_data=f"product_payment_card_{product_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"Кредит 🏦 {credit_price}",
                callback_data=f"product_payment_credit_{product_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=f"product_{product_id}"
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_category_selection_keyboard() -> InlineKeyboardMarkup:
    """Создает клавиатуру для выбора категории товара."""
    buttons = [
        [InlineKeyboardButton(text="📱 Смартфоны", callback_data="category_смартфоны")],
        [InlineKeyboardButton(text="📱 Планшеты", callback_data="category_планшеты")],
        [InlineKeyboardButton(text="💻 Ноутбуки", callback_data="category_ноутбуки")],
        [InlineKeyboardButton(text="⌚ Часы", callback_data="category_часы")],
        [InlineKeyboardButton(text="🎧 Наушники", callback_data="category_наушники")],
        [InlineKeyboardButton(text="🖥️ Компьютеры", callback_data="category_компьютеры")],
        [InlineKeyboardButton(text="⏭️ Пропустить", callback_data="category_skip")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_collection_selection_keyboard(category: Optional[str] = None) -> InlineKeyboardMarkup:
    """Создает клавиатуру для выбора подборки товара."""
    buttons = []
    
    if category and "iphone" in category.lower() or category == "смартфоны":
        buttons.append([
            InlineKeyboardButton(text="📱 iPhone б/у", callback_data="collection_iPhone б/у")
        ])
        buttons.append([
            InlineKeyboardButton(text="📱 iPhone новые", callback_data="collection_iPhone новые")
        ])
    else:
        buttons.append([
            InlineKeyboardButton(text="📦 Б/у", callback_data="collection_б/у")
        ])
        buttons.append([
            InlineKeyboardButton(text="✨ Новые", callback_data="collection_новые")
        ])
    
    buttons.append([
        InlineKeyboardButton(text="⏭️ Пропустить", callback_data="collection_skip")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_iphone_versions_keyboard(grouped_products: Dict[str, List[dict]]) -> InlineKeyboardMarkup:
    """
    Создает клавиатуру с основными версиями iPhone и количеством товаров в каждой версии.
    
    Args:
        grouped_products: Словарь {model_name: [products]}
        
    Returns:
        InlineKeyboardMarkup с кнопками основных версий
    """
    from app.utils.iphone_parser import group_by_main_version, get_main_iphone_versions
    
    buttons = []
    
    # Группируем по основным версиям
    version_groups = group_by_main_version(grouped_products)
    
    # Порядок версий для отображения
    version_order = get_main_iphone_versions()
    
    # Создаем кнопки для каждой версии
    for version in version_order:
        if version in version_groups:
            models = version_groups[version]
            # Подсчитываем общее количество товаров в версии
            total_count = sum(len(products) for products in models.values())
            
            # Формируем текст кнопки с количеством
            if version == "SE":
                button_text = f"📱 iPhone SE ({total_count})"
            elif version == "Air":
                button_text = f"📱 iPhone Air ({total_count})"
            else:
                button_text = f"📱 iPhone {version} ({total_count})"
            
            buttons.append([
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=f"iphone_version_{version}"
                )
            ])
    
    # Добавляем "Другие" если есть
    if "Другие" in version_groups:
        models = version_groups["Другие"]
        total_count = sum(len(products) for products in models.values())
        buttons.append([
            InlineKeyboardButton(
                text=f"📦 Другие ({total_count})",
                callback_data="iphone_version_Другие"
            )
        ])
    
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data="products_menu")
    ])
    buttons.append([
        InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_iphone_models_keyboard(version_models: Dict[str, List[dict]], version: str) -> InlineKeyboardMarkup:
    """
    Создает клавиатуру с моделями конкретной версии iPhone.
    
    Args:
        version_models: Словарь {model_name: [products]} для конкретной версии
        version: Название версии (например, "13", "14", "15")
        
    Returns:
        InlineKeyboardMarkup с кнопками моделей версии
    """
    from app.utils.iphone_parser import sort_models_for_display, get_model_display_name
    
    buttons = []
    
    # Сортируем модели для отображения
    sorted_models = sort_models_for_display(list(version_models.keys()))
    
    # Создаем кнопки для каждой модели
    for model in sorted_models:
        products = version_models[model]
        count = len(products)
        display_name = get_model_display_name(model)
        # Преобразуем названия цветов в эмодзи для inline клавиатуры
        display_name = replace_color_with_emoji(display_name)
        
        # Формируем текст кнопки с количеством
        button_text = f"📱 {display_name} ({count})"
        
        buttons.append([
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"iphone_model_{model}"
            )
        ])
    
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад к версиям", callback_data="products_list")
    ])
    buttons.append([
        InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_iphone_model_products_keyboard(
    products: List[dict],
    model: str,
    version: Optional[str] = None,
    page: int = 0,
    per_page: int = 10
) -> InlineKeyboardMarkup:
    """
    Создает клавиатуру для списка товаров конкретной модели iPhone.
    
    Args:
        products: Список товаров модели
        model: Название модели
        version: Версия iPhone (для кнопки "Назад")
        page: Номер страницы
        per_page: Товаров на странице
        
    Returns:
        InlineKeyboardMarkup с кнопками товаров
    """
    buttons = []
    
    # Кнопки для товаров на текущей странице
    start_idx = page * per_page
    end_idx = min(start_idx + per_page, len(products))
    
    for i in range(start_idx, end_idx):
        product = products[i]
        product_name = product.get('name', 'Без названия')
        # Преобразуем названия цветов в эмодзи для inline клавиатуры
        product_name = replace_color_with_emoji(product_name)
        # Обрезаем название, если слишком длинное
        if len(product_name) > 40:
            product_name = product_name[:37] + "..."
        buttons.append([
            InlineKeyboardButton(
                text=f"📦 {product_name}",
                callback_data=f"product_{product['id']}"
            )
        ])
    
    # Кнопки пагинации
    nav_buttons = []
    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"iphone_model_{model}_page_{page-1}")
        )
    if end_idx < len(products):
        nav_buttons.append(
            InlineKeyboardButton(text="Вперед ➡️", callback_data=f"iphone_model_{model}_page_{page+1}")
        )
    
    if nav_buttons:
        buttons.append(nav_buttons)
    
    # Кнопка назад к версии или моделям
    if version:
        buttons.append([
            InlineKeyboardButton(text="⬅️ Назад к версии", callback_data=f"iphone_version_{version}")
        ])
    else:
        buttons.append([
            InlineKeyboardButton(text="⬅️ Назад к моделям", callback_data="products_list")
        ])
    buttons.append([
        InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_full_products_list_keyboard(
    products: List[dict],
    exclude_product_id: Optional[int] = None,
    max_buttons: int = 95
) -> InlineKeyboardMarkup:
    """
    Создает inline-клавиатуру для полного списка товаров.
    
    Args:
        products: Список всех товаров
        exclude_product_id: ID товара, который нужно исключить (опционально)
        max_buttons: Максимальное количество кнопок (Telegram ограничивает до 100)
        
    Returns:
        InlineKeyboardMarkup с кнопками товаров
    """
    buttons = []
    
    # Фильтруем товары, если нужно исключить один
    filtered_products = products
    if exclude_product_id is not None:
        filtered_products = [p for p in products if p.get('id') != exclude_product_id]
    
    # Ограничиваем количество кнопок
    display_products = filtered_products[:max_buttons]
    
    # Создаем кнопки для каждого товара
    for product in display_products:
        product_name = product.get('name', 'Без названия')
        # Преобразуем названия цветов в эмодзи для inline клавиатуры
        product_name = replace_color_with_emoji(product_name)
        # Обрезаем название, если слишком длинное
        if len(product_name) > 40:
            product_name = product_name[:37] + "..."
        buttons.append([
            InlineKeyboardButton(
                text=f"📦 {product_name}",
                callback_data=f"product_{product['id']}"
            )
        ])
    
    # Кнопка "Назад" в меню товаров
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data="products_menu")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


STALE_PRICE_PER_PAGE = 10


def get_stale_price_list_keyboard(
    products: List[dict],
    page: int = 0,
    per_page: int = STALE_PRICE_PER_PAGE,
    *,
    sort_mode: str = "price",
) -> InlineKeyboardMarkup:
    """Пагинированные кнопки застоявшихся б/у-товаров.

    Цифра на кнопке совпадает со списком выше:
    По цене — дни без смены (+↺); По продаже — дни с публикации в TG.
    """
    from app.utils.stale_price_utils import STALE_SORT_PRICE, STALE_SORT_SALE
    from app.utils.stale_price_utils import days_in_sale, days_without_price_change

    buttons = []
    start_idx = page * per_page
    end_idx = min(start_idx + per_page, len(products))

    for i in range(start_idx, end_idx):
        product = products[i]
        if sort_mode == STALE_SORT_SALE:
            days = days_in_sale(product)
            repriced = ""
        else:
            days = days_without_price_change(
                product.get("price_changed_at") or product.get("created_at")
            )
            repriced = "↺" if product.get("price_repriced") else ""
        label = product.get("name", "Без названия")
        label = replace_color_with_emoji(label)
        if len(label) > 32:
            label = label[:29] + "..."
        btn_text = f"{label} · {days}д.{repriced}"
        buttons.append([
            InlineKeyboardButton(
                text=btn_text,
                callback_data=f"price_stale_item_{product['id']}",
            )
        ])

    price_label = "🕰 По цене ✓" if sort_mode == STALE_SORT_PRICE else "🕰 По цене"
    sale_label = "📅 В продаже ✓" if sort_mode == STALE_SORT_SALE else "📅 В продаже"
    buttons.append([
        InlineKeyboardButton(text=price_label, callback_data="price_stale_sort_price"),
        InlineKeyboardButton(text=sale_label, callback_data="price_stale_sort_sale"),
    ])

    nav_buttons = []
    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton(text="⬅️", callback_data=f"price_stale_page_{page - 1}")
        )
    if end_idx < len(products):
        nav_buttons.append(
            InlineKeyboardButton(text="➡️", callback_data=f"price_stale_page_{page + 1}")
        )
    if nav_buttons:
        buttons.append(nav_buttons)

    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад в архив", callback_data="products_archive"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_stale_price_detail_keyboard(product_id: int) -> InlineKeyboardMarkup:
    """Экран истории цен одного товара."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📦 Открыть карточку товара",
                    callback_data=f"product_{product_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к застою",
                    callback_data="price_stale_list",
                )
            ],
        ]
    )