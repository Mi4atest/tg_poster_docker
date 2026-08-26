from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from app.scheduler.queue_ui import queue_item_display_name
from app.bot.utils.button_styles import ikb


def get_queue_menu_keyboard(stats: dict, *, global_pause: bool = False) -> InlineKeyboardMarkup:
    """Создать клавиатуру главного меню очереди."""
    pause_btn = (
        InlineKeyboardButton(
            text="▶️ Возобновить все",
            callback_data="queue_resume_global",
        )
        if global_pause
        else InlineKeyboardButton(
            text="⏸ Пауза всех",
            callback_data="queue_pause_global",
        )
    )
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
        [pause_btn],
        [
            InlineKeyboardButton(
                text="🏠 Вернуться в главное меню",
                callback_data="back_to_main"
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _platform_pause_button(platform: str, *, platform_paused: bool) -> InlineKeyboardButton:
    if platform_paused:
        return InlineKeyboardButton(
            text="▶️ Возобновить платформу",
            callback_data=f"queue_resume_platform_{platform}",
        )
    return InlineKeyboardButton(
        text="⏸ Пауза платформы",
        callback_data=f"queue_pause_platform_{platform}",
    )


def get_avito_platform_keyboard(
    *,
    can_upload: bool,
    has_work: bool,
    platform_paused: bool = False,
    global_pause: bool = False,
) -> InlineKeyboardMarkup:
    """Экран очереди Авито: ручная отправка файла и пауза платформы."""
    buttons = []
    paused = global_pause or platform_paused
    if has_work and can_upload and not paused:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="📤 Отправить файл на Авито",
                    callback_data="queue_avito_upload_feed",
                )
            ]
        )
    buttons.append([_platform_pause_button("avito", platform_paused=platform_paused)])
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


def get_platform_queue_keyboard(
    platform: str,
    queue_items: list,
    *,
    platform_paused: bool = False,
) -> InlineKeyboardMarkup:
    """Создать клавиатуру очереди для платформы."""
    buttons = []

    for item in queue_items[:10]:
        post_name = queue_item_display_name(item)
        status_icon = "⏸" if item.status == "paused" else "⏳" if item.status == "pending" else "🔄"
        buttons.append([
            InlineKeyboardButton(
                text=f"{status_icon} {post_name[:30]}",
                callback_data=f"queue_post_{item.id}"
            )
        ])

    buttons.append([_platform_pause_button(platform, platform_paused=platform_paused)])

    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Назад к очереди",
            callback_data="queue_menu"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_post_queue_actions_keyboard(queue_item_id: int, post_id: str, platform: str) -> InlineKeyboardMarkup:
    """Создать клавиатуру действий с постом в очереди."""
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
                "❌ Убрать из этой очереди",
                f"queue_cancel_post_{queue_item_id}",
            )
        ],
        [
            ikb("⬅️ Назад", f"queue_platform_{platform}")
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)
