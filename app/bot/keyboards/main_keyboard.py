from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def get_main_keyboard() -> InlineKeyboardMarkup:
    """Create the main keyboard for the bot."""
    buttons = [
        [InlineKeyboardButton(text="🆕 Создать пост", callback_data="create_post")],
        [InlineKeyboardButton(text="⏳ Отложенные посты", callback_data="pending_posts")],
        [InlineKeyboardButton(text="📁 Архив", callback_data="archive_posts")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_post_actions_keyboard(from_archive=False) -> InlineKeyboardMarkup:
    """Create the keyboard for post actions.

    Args:
        from_archive: Whether the post is being viewed from the archive
    """
    buttons = [
        # Social media buttons for posts
        [InlineKeyboardButton(text="📤 Отправить во все соцсети", callback_data="publish_all")],
        [
            InlineKeyboardButton(text="📱 в ВК", callback_data="publish_vk"),
            InlineKeyboardButton(text="📢 в ТГ", callback_data="publish_telegram"),
            InlineKeyboardButton(text="📸 в IG", callback_data="publish_instagram")
        ],
        # Social media buttons for stories (только кнопка меню, без подкнопок)
        [InlineKeyboardButton(text="📱 Сторис", callback_data="stories_menu")],
        # Edit and delete buttons
        [
            InlineKeyboardButton(text="✏️ Редактировать", callback_data="edit"),
            InlineKeyboardButton(text="🗑 Удалить пост", callback_data="delete")
        ],
        # Back button - different depending on context
        [InlineKeyboardButton(
            text="⬅️ Назад к архиву" if from_archive else "⬅️ Назад к постам",
            callback_data="back_to_archive" if from_archive else "back_to_posts"
        )]
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_skip_back_keyboard() -> InlineKeyboardMarkup:
    """Create a keyboard with skip and back buttons."""
    buttons = [
        [
            InlineKeyboardButton(text="⏭️ Далее", callback_data="skip"),
            InlineKeyboardButton(text="⬅️ Назад", callback_data="back")
        ]
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)

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
            InlineKeyboardButton(text="✅ Да", callback_data="confirm_yes"),
            InlineKeyboardButton(text="❌ Нет", callback_data="confirm_no")
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

def get_publish_interval_keyboard() -> InlineKeyboardMarkup:
    """Create the keyboard for choosing a bulk-publish interval."""
    buttons = [
        [InlineKeyboardButton(text="🚀 Сразу", callback_data="publish_immediately")],
        [
            InlineKeyboardButton(text="10 мин", callback_data="publish_interval_10"),
            InlineKeyboardButton(text="15 мин", callback_data="publish_interval_15"),
        ],
        [
            InlineKeyboardButton(text="30 мин", callback_data="publish_interval_30"),
            InlineKeyboardButton(text="45 мин", callback_data="publish_interval_45"),
        ],
        [InlineKeyboardButton(text="✏️ Свой интервал", callback_data="publish_custom_interval")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_publish_all")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_custom_interval_keyboard() -> InlineKeyboardMarkup:
    """Create the keyboard shown while entering a custom publish interval."""
    buttons = [
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_custom_interval")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)
