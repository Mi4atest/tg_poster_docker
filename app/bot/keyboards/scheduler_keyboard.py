from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from app.scheduler.queue_ui import queue_item_display_name
from app.bot.utils.button_styles import ikb


def get_queue_menu_keyboard(stats: dict) -> InlineKeyboardMarkup:
    """Создать клавиатуру главного меню очереди.
    
    Args:
        stats: Словарь со статистикой очереди
    """
    buttons = [
        [
            InlineKeyboardButton(
                text=f"📱 ВК ({stats.get('vk', 0)})",
                callback_data="queue_platform_vk"
            ),
            InlineKeyboardButton(
                text=f"📢 ТГ ({stats.get('telegram', 0)})",
                callback_data="queue_platform_telegram"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"📸 IG ({stats.get('instagram', 0)})",
                callback_data="queue_platform_instagram"
            ),
            InlineKeyboardButton(
                text=f"💬 MAX ({stats.get('max', 0)})",
                callback_data="queue_platform_max"
            ),
            InlineKeyboardButton(
                text=f"🛒 Авито ({stats.get('avito', 0)})",
                callback_data="queue_platform_avito"
            ),
        ],
        [
            InlineKeyboardButton(
                text="⏸ Пауза всех",
                callback_data="queue_pause_global"
            ),
            InlineKeyboardButton(
                text="▶️ Возобновить все",
                callback_data="queue_resume_global"
            )
        ],
        [
            InlineKeyboardButton(
                text="🏠 Вернуться в главное меню",
                callback_data="back_to_main"
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_avito_platform_keyboard(
    *,
    can_upload: bool,
    has_work: bool,
) -> InlineKeyboardMarkup:
    """Экран очереди Авито: ручная отправка файла."""
    buttons = []
    if has_work:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="📤 Отправить файл на Авито",
                    callback_data="queue_avito_upload_feed",
                )
            ]
        )
    buttons.append(
        [
            InlineKeyboardButton(
                text="🔄 Обновить",
                callback_data="queue_platform_avito",
            )
        ]
    )
    buttons.append(
        [InlineKeyboardButton(text="⬅️ Назад к очереди", callback_data="queue_menu")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_platform_queue_keyboard(platform: str, queue_items: list) -> InlineKeyboardMarkup:
    """Создать клавиатуру очереди для платформы.
    
    Args:
        platform: Платформа ("vk", "telegram", "instagram")
        queue_items: Список записей очереди
    """
    buttons = []
    
    # Кнопки для каждого поста в очереди
    for item in queue_items[:10]:  # Показываем максимум 10 постов
        post_name = queue_item_display_name(item)
        status_icon = "⏸" if item.status == "paused" else "⏳" if item.status == "pending" else "🔄"
        buttons.append([
            InlineKeyboardButton(
                text=f"{status_icon} {post_name[:30]}",
                callback_data=f"queue_post_{item.id}"
            )
        ])
    
    # Кнопки управления платформой
    buttons.append([
        InlineKeyboardButton(
            text="⏸ Пауза платформы",
            callback_data=f"queue_pause_platform_{platform}"
        ),
        InlineKeyboardButton(
            text="▶️ Возобновить платформу",
            callback_data=f"queue_resume_platform_{platform}"
        )
    ])
    
    # Кнопка назад
    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Назад к очереди",
            callback_data="queue_menu"
        )
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_post_queue_actions_keyboard(queue_item_id: int, post_id: str, platform: str) -> InlineKeyboardMarkup:
    """Создать клавиатуру действий с постом в очереди.
    
    Args:
        queue_item_id: ID записи очереди
        post_id: ID поста
        platform: Платформа
    """
    buttons = [
        [
            ikb(
                "🚀 Опубликовать",
                f"queue_publish_now_{platform}_{post_id}",
            )
        ],
        [
            ikb("⏸ Пауза", f"queue_pause_post_{queue_item_id}"),
            ikb("▶️ Возобновить", f"queue_resume_post_{queue_item_id}"),
        ],
        [
            ikb(
                "❌ Отменить (в черновики)",
                f"queue_cancel_post_{queue_item_id}",
            )
        ],
        [
            ikb("⬅️ Назад", f"queue_platform_{platform}")
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

