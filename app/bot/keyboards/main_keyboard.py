from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from app.bot.utils.button_styles import ikb
from app.bot.keyboards.post_avito_keyboard import (
    AVITO_BODY_LABELS,
    AVITO_SCREEN_LABELS,
    _label_for_level,
)
from app.integrations.avito.condition_maps import clamp_avito_level


def get_main_keyboard(queue_count: int = 0, drafts_count: int = 0) -> InlineKeyboardMarkup:
    """Create the main keyboard for the bot.
    
    Args:
        queue_count: Количество постов в очереди (для динамического отображения)
        drafts_count: Количество черновиков; кнопка показывается только при count > 0
    """
    buttons = [
        [ikb("🆕 Создать пост", "create_post")],
    ]
    if drafts_count > 0:
        label = f"📝 Черновики ({drafts_count})" if drafts_count else "📝 Черновики"
        buttons.append([ikb(label, "pending_posts")])
    buttons.extend([
        [ikb(
            f"📋 В очереди {queue_count}" if queue_count > 0 else "📋 В очереди",
            "queue_menu",
        )],
        [ikb("📁 Архив", "archive_posts")],
        [ikb("⚙️ Настройки", "open_settings")],
        [ikb("📦 Товары", "products_menu")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_create_post_entry_keyboard(
    vk_market_enabled: bool,
    *,
    avito_enabled: bool = True,
    avito_screen_level: int = 1,
    avito_body_level: int = 1,
    vk_stories_auto_enabled: bool = False,
) -> InlineKeyboardMarkup:
    """Клавиатура шага «текст»: Назад, Товары ВК, Сторис ВК; Экран/Корпус — если Авито вкл."""
    vk_market_icon = "🟢" if vk_market_enabled else "🔴"
    vk_stories_icon = "🟢" if vk_stories_auto_enabled else "🔴"
    row1 = [
        InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main"),
        InlineKeyboardButton(text=f"{vk_market_icon} Товары ВК", callback_data="create_post_toggle_vk_market"),
    ]
    row2 = [
        InlineKeyboardButton(
            text=f"{vk_stories_icon} Сторис ВК",
            callback_data="create_post_toggle_vk_stories",
        ),
    ]
    rows = [row1, row2]
    if avito_enabled:
        sl = clamp_avito_level(avito_screen_level)
        bl = clamp_avito_level(avito_body_level)
        rows.append([
            InlineKeyboardButton(
                text=f"📱 {_label_for_level(sl, AVITO_SCREEN_LABELS)}"[:64],
                callback_data="pc_text_avito_cycle_scr",
            ),
            InlineKeyboardButton(
                text=f"📦 {_label_for_level(bl, AVITO_BODY_LABELS)}"[:64],
                callback_data="pc_text_avito_cycle_bod",
            ),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_post_actions_keyboard(from_archive=False) -> InlineKeyboardMarkup:
    """Create the keyboard for post actions.

    Args:
        from_archive: Whether the post is being viewed from the archive
    """
    buttons = [
        [ikb("📤 Отправить во все соцсети", "publish_all")],
        [
            ikb("📱 в ВК", "publish_vk"),
            ikb("📢 в ТГ", "publish_telegram"),
            ikb("📸 в IG", "publish_instagram"),
        ],
        [
            ikb("💬 в MAX", "publish_max"),
            ikb("🛒 в Авито", "publish_avito"),
        ],
        [ikb("📦 Товары ВК", "publish_vk_product")],
        [ikb("📱 Сторис", "stories_menu")],
        [
            ikb("✏️ Редактировать", "edit"),
            ikb("🗑 Удалить пост", "delete"),
        ],
        [ikb(
            "⬅️ Назад к архиву" if from_archive else "⬅️ Назад к черновикам",
            "back_to_archive" if from_archive else "back_to_posts",
        )],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def post_actions_kb_for_user(user_data: dict | None = None) -> InlineKeyboardMarkup:
    """Клавиатура действий с постом с учётом контекста (архив / черновики)."""
    ud = user_data or {}
    archive_state = ud.get("archive_state") or {}
    from_archive = bool(ud.get("in_archive") or archive_state.get("year") is not None)
    return get_post_actions_keyboard(from_archive=from_archive)

def get_media_management_keyboard() -> InlineKeyboardMarkup:
    """Create a keyboard for managing photos and videos."""
    buttons = [
        [
            InlineKeyboardButton(text="📷 Управление фото", callback_data="manage_photos"),
            InlineKeyboardButton(text="📹 Управление видео", callback_data="manage_videos")
        ],
        [
            InlineKeyboardButton(text="⏭️ Далее", callback_data="skip"),
            InlineKeyboardButton(text="⬅️ Назад", callback_data="back")
        ]
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_photo_management_keyboard(photos) -> InlineKeyboardMarkup:
    """Create a keyboard for managing photos."""
    buttons = []

    # Добавляем кнопки для каждой фотографии, если они есть
    if photos:
        for i, _ in enumerate(photos, 1):
            buttons.append([
                InlineKeyboardButton(text=f"🗑️ Удалить фото #{i}", callback_data=f"delete_photo_{i-1}")
            ])

    # Добавляем кнопки для добавления новых фото и возврата
    buttons.append([
        InlineKeyboardButton(text="➕ Добавить фото", callback_data="add_photos"),
        InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_media_management")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_video_management_keyboard(videos) -> InlineKeyboardMarkup:
    """Create a keyboard for managing videos."""
    buttons = []

    # Добавляем кнопки для каждого видео, если они есть
    if videos:
        for i, _ in enumerate(videos, 1):
            buttons.append([
                InlineKeyboardButton(text=f"🗑️ Удалить видео #{i}", callback_data=f"delete_video_{i-1}")
            ])

    # Добавляем кнопки для добавления новых видео и возврата
    buttons.append([
        InlineKeyboardButton(text="➕ Добавить видео", callback_data="add_videos"),
        InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_media_management")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_confirmation_keyboard() -> InlineKeyboardMarkup:
    """Create a confirmation keyboard with Yes/No buttons."""
    buttons = [
        [
            ikb("✅ Да", "confirm_yes"),
            ikb("❌ Нет", "confirm_no"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_delete_post_confirmation_keyboard() -> InlineKeyboardMarkup:
    """Подтверждение удаления поста."""
    buttons = [
        [
            ikb("✅ Да, удалить", "confirm_delete"),
            ikb("❌ Нет, отмена", "cancel_delete"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_skip_back_keyboard() -> InlineKeyboardMarkup:
    """Create a keyboard with Next and Back buttons."""
    buttons = [
        [
            InlineKeyboardButton(text="⏭️ Далее", callback_data="skip"),
            InlineKeyboardButton(text="⬅️ Назад", callback_data="back")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_text_only_confirm_keyboard() -> InlineKeyboardMarkup:
    """Confirm creating a post without photos/videos."""
    buttons = [
        [ikb("✅ Да, только текст", "pc_text_only_yes")],
        [ikb("⬅️ Нет, добавлю фото", "pc_text_only_no")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_publish_all_pending_confirmation_keyboard() -> InlineKeyboardMarkup:
    """Подтверждение массового добавления черновиков в очередь."""
    buttons = [
        [ikb("✅ Добавить в очередь", "confirm_publish_all_pending")],
        [ikb("❌ Отмена", "cancel_publish_all_pending")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)
