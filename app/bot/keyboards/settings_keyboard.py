from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def get_settings_root_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="📇 Контакты и подписи", callback_data="settings_signatures")],
        [InlineKeyboardButton(text="📣 Каналы публикации", callback_data="settings_channels")],
        [InlineKeyboardButton(text="⏱ Публикация и интервалы", callback_data="settings_intervals")],
        [InlineKeyboardButton(text="🔐 Интеграции и токены", callback_data="settings_integrations")],
        [InlineKeyboardButton(text="🗂 Отчёты и списки", callback_data="settings_reports")],
        [InlineKeyboardButton(text="🏷️ Ценники", callback_data="settings_price_tags")],
        [InlineKeyboardButton(text="💾 Резервное копирование", callback_data="settings_backup")],
        [InlineKeyboardButton(text="🔄 Обновить с GitHub", callback_data="settings_update_project")],
        [InlineKeyboardButton(text="🧩 Меню новые", callback_data="mc_open")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_settings_channels_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="✈️ ID Telegram-канала", callback_data="settings_edit_channel_telegram_channel_id")],
        [InlineKeyboardButton(text="💬 ID канала Max", callback_data="settings_edit_channel_max_channel_id")],
        [InlineKeyboardButton(text="⬅️ Назад к настройкам", callback_data="settings_root")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_settings_reports_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="📊 VK: получатели отчёта", callback_data="settings_edit_report_vk_report_user_ids")],
        [InlineKeyboardButton(text="🆕 ID сообщений «Наличие»", callback_data="settings_edit_report_availability_message_ids")],
        [InlineKeyboardButton(text="♻️ ID сообщений «Список б/у»", callback_data="settings_edit_report_used_products_list_message_ids")],
        [InlineKeyboardButton(text="⬅️ Назад к настройкам", callback_data="settings_root")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_settings_update_keyboard(
    *,
    running: bool = False,
    up_to_date: bool = False,
    has_update: bool = False,
    fetch_ok: bool = True,
) -> InlineKeyboardMarkup:
    buttons = []
    if running:
        buttons.append([
            InlineKeyboardButton(text="🔄 Обновить статус", callback_data="settings_update_project"),
        ])
    else:
        buttons.append([
            InlineKeyboardButton(text="🔄 Проверить снова", callback_data="settings_update_project"),
        ])
        if up_to_date:
            buttons.append([
                InlineKeyboardButton(text="🔄 Обновить всё равно", callback_data="settings_update_project_force"),
            ])
        else:
            buttons.append([
                InlineKeyboardButton(text="✅ Обновить", callback_data="settings_update_project_confirm"),
            ])
    if not running:
        buttons.append([
            InlineKeyboardButton(
                text="🧹 Освободить место",
                callback_data="settings_update_project_prune",
            ),
        ])
    buttons.append([
        InlineKeyboardButton(text="📋 Подробности / лог", callback_data="settings_update_project_status"),
    ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад к настройкам", callback_data="settings_root"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_settings_backup_keyboard(enabled: bool, media: bool) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(
            text=("🟢" if enabled else "🔴") + " Автобэкап (по расписанию)",
            callback_data="settings_backup_toggle_enabled",
        )],
        [InlineKeyboardButton(
            text=("🟢" if media else "🔴") + " Включать медиа в бэкап",
            callback_data="settings_backup_toggle_media",
        )],
        [InlineKeyboardButton(text="🔑 Токен бота бэкапа", callback_data="settings_backup_edit_token")],
        [InlineKeyboardButton(text="💬 Chat ID получателя", callback_data="settings_backup_edit_chat_id")],
        [InlineKeyboardButton(text="🏷 Имя проекта", callback_data="settings_backup_edit_project_name")],
        [InlineKeyboardButton(text="🕒 Время (ЧЧ:ММ)", callback_data="settings_backup_edit_schedule")],
        [InlineKeyboardButton(text="📦 Сделать бэкап сейчас", callback_data="settings_backup_run_now")],
        [InlineKeyboardButton(text="⬅️ Назад к настройкам", callback_data="settings_root")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_settings_signatures_keyboard(
    enabled: bool,
    vk_market_enabled: bool,
    catalog_enabled: bool,
) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(
            text=("🟢" if enabled else "🔴") + " Подпись",
            callback_data="settings_toggle_signature"
        )],
        [InlineKeyboardButton(
            text=("🟢" if vk_market_enabled else "🔴") + " Товары ВК",
            callback_data="settings_toggle_vk_market"
        )],
        [InlineKeyboardButton(
            text=("🟢" if catalog_enabled else "🔴") + " Блок «Каталог б/у» в постах",
            callback_data="settings_toggle_telegram_used_catalog"
        )],
        [InlineKeyboardButton(
            text="🔗 Ссылка каталога б/у (Telegram)",
            callback_data="settings_edit_tg_catalog_url",
        )],
        [InlineKeyboardButton(
            text="🔗 Ссылка каталога б/у (VK)",
            callback_data="settings_edit_vk_catalog_url",
        )],
        [InlineKeyboardButton(text="✏️ Изменить поля подписей", callback_data="settings_edit_signatures_menu")],
        [InlineKeyboardButton(text="⬅️ Назад к настройкам", callback_data="settings_root")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_settings_contacts_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="Telegram username", callback_data="settings_edit_contact_telegram_username")],
        [InlineKeyboardButton(text="Telegram user id", callback_data="settings_edit_contact_telegram_user_id")],
        [InlineKeyboardButton(text="Телефон", callback_data="settings_edit_contact_phone")],
        [InlineKeyboardButton(text="⬅️ Назад к настройкам", callback_data="settings_root")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_settings_signatures_edit_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="📘 VK", callback_data="settings_preview_vk")],
        [InlineKeyboardButton(text="✈️ Telegram", callback_data="settings_preview_telegram")],
        [InlineKeyboardButton(text="📷 Instagram", callback_data="settings_preview_instagram")],
        [InlineKeyboardButton(text="💬 Max", callback_data="settings_preview_max")],
        [InlineKeyboardButton(text="⬅️ Назад к контактам и подписям", callback_data="settings_signatures")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_signature_platform_fields_keyboard(platform: str) -> InlineKeyboardMarkup:
    if platform == "vk":
        buttons = [
            [InlineKeyboardButton(text="Ссылка VK", callback_data="settings_edit_signature_vk")],
            [InlineKeyboardButton(text="VK short Avito", callback_data="settings_edit_signature_vk_short_avito")],
            [InlineKeyboardButton(text="VK short Telegram", callback_data="settings_edit_signature_vk_short_telegram")],
        ]
    elif platform == "telegram":
        buttons = [
            [InlineKeyboardButton(text="Telegram username", callback_data="settings_edit_contact_telegram_username")],
            [InlineKeyboardButton(text="Telegram user id", callback_data="settings_edit_contact_telegram_user_id")],
            [InlineKeyboardButton(text="Ссылка Telegram", callback_data="settings_edit_signature_telegram")],
            [InlineKeyboardButton(text="Ссылка Avito", callback_data="settings_edit_signature_avito")],
            [InlineKeyboardButton(text="Ссылка Instagram", callback_data="settings_edit_signature_instagram")],
            [InlineKeyboardButton(text="Телефон", callback_data="settings_edit_contact_phone")],
            [InlineKeyboardButton(text="Телефон подписи", callback_data="settings_edit_signature_phone")],
        ]
    elif platform == "instagram":
        buttons = [
            [InlineKeyboardButton(text="Ссылка Instagram", callback_data="settings_edit_signature_instagram")],
            [InlineKeyboardButton(text="Ссылка VK", callback_data="settings_edit_signature_vk")],
            [InlineKeyboardButton(text="Ссылка Telegram", callback_data="settings_edit_signature_telegram")],
        ]
    else:  # max
        buttons = [
            [InlineKeyboardButton(text="Telegram username", callback_data="settings_edit_contact_telegram_username")],
            [InlineKeyboardButton(text="Telegram user id", callback_data="settings_edit_contact_telegram_user_id")],
            [InlineKeyboardButton(text="Ссылка VK", callback_data="settings_edit_signature_vk")],
            [InlineKeyboardButton(text="Ссылка Avito", callback_data="settings_edit_signature_avito")],
            [InlineKeyboardButton(text="Ссылка Telegram", callback_data="settings_edit_signature_telegram")],
            [InlineKeyboardButton(text="Ссылка Instagram", callback_data="settings_edit_signature_instagram")],
            [InlineKeyboardButton(text="Телефон", callback_data="settings_edit_contact_phone")],
        ]

    buttons.append([InlineKeyboardButton(text="⬅️ Назад к платформам", callback_data="settings_edit_signatures_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_input_cancel_keyboard(return_callback: str) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=return_callback)],
        [InlineKeyboardButton(text="❌ Отмена ввода", callback_data="settings_cancel_input")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_settings_intervals_keyboard(data: dict) -> InlineKeyboardMarkup:
    enabled = data["publishing"]["enabled"]
    interval = data["publishing"]["interval_minutes"]
    labels = {
        "vk": "VK",
        "telegram": "Telegram",
        "instagram": "Instagram",
        "max": "Max",
        "avito": "Авито",
    }

    buttons = []
    for platform in ("vk", "telegram", "instagram", "max", "avito"):
        icon = "🟢" if enabled.get(platform, True) else "🔴"
        minutes = int(interval.get(platform, 3))
        buttons.append([
            InlineKeyboardButton(
                text=f"{icon} {labels[platform]}: {minutes} мин",
                callback_data=f"settings_platform_{platform}"
            )
        ])

    buttons.append([InlineKeyboardButton(text="⬅️ Назад к настройкам", callback_data="settings_root")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_platform_interval_keyboard(platform: str, enabled: bool) -> InlineKeyboardMarkup:
    labels = {"vk": "VK", "telegram": "Telegram", "instagram": "Instagram", "max": "Max", "avito": "Авито"}
    p_name = labels.get(platform, platform)
    buttons = [
        [InlineKeyboardButton(
            text=(f"🟢 {p_name}: включено" if enabled else f"🔴 {p_name}: выключено"),
            callback_data=f"settings_toggle_platform_{platform}"
        )],
        [
            InlineKeyboardButton(text="3", callback_data=f"settings_interval_{platform}_3"),
            InlineKeyboardButton(text="5", callback_data=f"settings_interval_{platform}_5"),
            InlineKeyboardButton(text="10", callback_data=f"settings_interval_{platform}_10"),
        ],
        [
            InlineKeyboardButton(text="15", callback_data=f"settings_interval_{platform}_15"),
            InlineKeyboardButton(text="30", callback_data=f"settings_interval_{platform}_30"),
            InlineKeyboardButton(text="45", callback_data=f"settings_interval_{platform}_45"),
        ],
        [InlineKeyboardButton(text="✏️ Свой интервал", callback_data=f"settings_interval_custom_{platform}")],
        [InlineKeyboardButton(text="⬅️ Назад к интервалам", callback_data="settings_intervals")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_settings_integrations_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="📘 VK", callback_data="settings_integration_platform_vk")],
        [InlineKeyboardButton(text="✈️ Telegram", callback_data="settings_integration_platform_telegram")],
        [InlineKeyboardButton(text="💬 Max", callback_data="settings_integration_platform_max")],
        [InlineKeyboardButton(text="🛒 Авито", callback_data="settings_integration_platform_avito")],
        [InlineKeyboardButton(text="📷 Instagram", callback_data="settings_integration_platform_instagram")],
        [InlineKeyboardButton(text="⬅️ Назад к настройкам", callback_data="settings_root")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_integration_platform_keyboard(platform: str) -> InlineKeyboardMarkup:
    if platform == "vk":
        buttons = [
            [InlineKeyboardButton(text="Токен VK", callback_data="settings_edit_integration_vk_access_token")],
            [InlineKeyboardButton(text="ID группы VK", callback_data="settings_edit_integration_vk_group_id")],
        ]
    elif platform == "telegram":
        buttons = [
            [InlineKeyboardButton(text="Токен Telegram бота", callback_data="settings_edit_integration_telegram_bot_token")],
            [InlineKeyboardButton(text="ID Telegram канала", callback_data="settings_edit_integration_telegram_channel_id")],
        ]
    elif platform == "max":
        buttons = [
            [InlineKeyboardButton(text="Токен Max", callback_data="settings_edit_integration_max_bot_token")],
            [InlineKeyboardButton(text="ID канала Max", callback_data="settings_edit_integration_max_channel_id")],
            [InlineKeyboardButton(text="Base URL Max API", callback_data="settings_edit_integration_max_api_base_url")],
        ]
    elif platform == "avito":
        buttons = [
            [InlineKeyboardButton(text="Client ID", callback_data="settings_edit_integration_avito_client_id")],
            [InlineKeyboardButton(text="Client Secret", callback_data="settings_edit_integration_avito_client_secret")],
            [InlineKeyboardButton(text="Category ID (Авито)", callback_data="settings_edit_integration_avito_category_id")],
            [InlineKeyboardButton(text="Location ID (опц.)", callback_data="settings_edit_integration_avito_location_id")],
            [InlineKeyboardButton(text="Авто-создание из поста: вкл/выкл", callback_data="settings_avito_toggle_auto_create")],
            [InlineKeyboardButton(text="Вид объявления: own / resale", callback_data="settings_avito_toggle_listing_kind")],
            [InlineKeyboardButton(text="Мультиобъявление: вкл/выкл", callback_data="settings_avito_toggle_multi")],
            [InlineKeyboardButton(text="Доставка: следующий режим", callback_data="settings_avito_cycle_delivery")],
        ]
    elif platform == "instagram":
        buttons = [
            [InlineKeyboardButton(text="Токен Instagram Graph", callback_data="settings_edit_integration_instagram_graph_access_token")],
            [InlineKeyboardButton(text="App ID (Meta)", callback_data="settings_edit_integration_instagram_graph_app_id")],
            [InlineKeyboardButton(text="App Secret (Meta)", callback_data="settings_edit_integration_instagram_graph_app_secret")],
            [InlineKeyboardButton(text="ID Instagram-аккаунта (Graph User ID)", callback_data="settings_edit_integration_instagram_graph_user_id")],
            [InlineKeyboardButton(text="Обменять в long-lived сейчас", callback_data="settings_instagram_exchange_long_lived")],
            [InlineKeyboardButton(text="Логин Instagram (legacy)", callback_data="settings_edit_integration_instagram_username")],
            [InlineKeyboardButton(text="Пароль Instagram (legacy)", callback_data="settings_edit_integration_instagram_password")],
            [InlineKeyboardButton(text="Переключить IG режим (Graph/Legacy)", callback_data="settings_toggle_instagram_mode")],
        ]
    else:
        buttons = [
            [InlineKeyboardButton(text="Токен Instagram Graph", callback_data="settings_edit_integration_instagram_graph_access_token")],
            [InlineKeyboardButton(text="App ID (Meta)", callback_data="settings_edit_integration_instagram_graph_app_id")],
            [InlineKeyboardButton(text="App Secret (Meta)", callback_data="settings_edit_integration_instagram_graph_app_secret")],
            [InlineKeyboardButton(text="ID Instagram-аккаунта (Graph User ID)", callback_data="settings_edit_integration_instagram_graph_user_id")],
            [InlineKeyboardButton(text="Обменять в long-lived сейчас", callback_data="settings_instagram_exchange_long_lived")],
            [InlineKeyboardButton(text="Логин Instagram (legacy)", callback_data="settings_edit_integration_instagram_username")],
            [InlineKeyboardButton(text="Пароль Instagram (legacy)", callback_data="settings_edit_integration_instagram_password")],
            [InlineKeyboardButton(text="Переключить IG режим (Graph/Legacy)", callback_data="settings_toggle_instagram_mode")],
        ]

    buttons.append([InlineKeyboardButton(text="⬅️ Назад к соцсетям", callback_data="settings_integrations")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_settings_price_tags_keyboard(markup_percent: int) -> InlineKeyboardMarkup:
    pct5 = "✅ +5%" if markup_percent == 5 else "+5%"
    pct10 = "✅ +10%" if markup_percent == 10 else "+10%"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=pct5, callback_data="settings_price_tags_pct_5"),
                InlineKeyboardButton(text=pct10, callback_data="settings_price_tags_pct_10"),
            ],
            [
                InlineKeyboardButton(
                    text="📝 Текст по умолчанию (iPhone)",
                    callback_data="settings_price_tags_edit_iphone",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📝 Текст по умолчанию (iPad)",
                    callback_data="settings_price_tags_edit_ipad",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📝 Общий fallback-текст",
                    callback_data="settings_price_tags_edit_footer",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад к настройкам", callback_data="settings_root")],
        ]
    )
