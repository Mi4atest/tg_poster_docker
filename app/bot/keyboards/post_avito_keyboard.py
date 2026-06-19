"""Подписи и текст блока «параметры только для Авито» при создании поста."""
from __future__ import annotations

import html

from aiogram.types import InlineKeyboardMarkup

from app.bot.utils.button_styles import ikb
from app.integrations.avito.condition_maps import cycle_avito_level, clamp_avito_level

# Уровни 1–3 (без «не указано»).
AVITO_SCREEN_LABELS = ("Без дефектов", "1-2 мелкие царапины", "Много царапин")
AVITO_BODY_LABELS = ("Без дефектов", "Мелкие царапины", "Глубокие царапины")


def _label_for_level(level: int, labels: tuple) -> str:
    idx = clamp_avito_level(level) - 1
    return labels[idx]


def format_avito_text_step_block(screen_level: int, body_level: int) -> str:
    sl = clamp_avito_level(screen_level)
    bl = clamp_avito_level(body_level)
    return (
        "🛒 <b>Параметры для Авито</b>\n\n"
        f"Экран: <b>{html.escape(_label_for_level(sl, AVITO_SCREEN_LABELS))}</b>\n"
        f"Корпус: <b>{html.escape(_label_for_level(bl, AVITO_BODY_LABELS))}</b>\n\n"
        "Нажмите кнопки ниже, чтобы переключить состояние.\n"
        "<i>Учитывается в фиде автозагрузки Авито; на ВК, Telegram, Max и др. не передаётся.</i>"
    )


def format_post_creation_text_prompt(
    status_hint: str,
    screen_level: int,
    body_level: int,
    *,
    avito_enabled: bool = True,
) -> str:
    """Текст экрана «отправьте текст» с подсказкой по площадкам и блоком Авито."""
    base = (
        "📝 Отправьте текст для нового поста.\n\n"
        "После этого вы сможете добавить фотографии и видео.\n\n"
        f"{status_hint}"
    )
    if not avito_enabled:
        return base
    block = format_avito_text_step_block(screen_level, body_level)
    return f"{base}\n\n{block}"


def format_avito_publish_block(screen_level: int, body_level: int) -> str:
    return (
        "🛒 <b>Публикация в Авито</b>\n\n"
        "Укажите состояние экрана и корпуса (обязательно для автозагрузки):\n\n"
        f"Экран: <b>{html.escape(_label_for_level(screen_level, AVITO_SCREEN_LABELS))}</b>\n"
        f"Корпус: <b>{html.escape(_label_for_level(body_level, AVITO_BODY_LABELS))}</b>"
    )


def get_avito_publish_keyboard(
    screen_level: int,
    body_level: int,
    *,
    cancel_callback: str = "back_to_post",
) -> InlineKeyboardMarkup:
    sl = clamp_avito_level(screen_level)
    bl = clamp_avito_level(body_level)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                ikb(
                    f"📱 {_label_for_level(sl, AVITO_SCREEN_LABELS)}"[:64],
                    "avito_pub_scr",
                ),
                ikb(
                    f"📦 {_label_for_level(bl, AVITO_BODY_LABELS)}"[:64],
                    "avito_pub_bod",
                ),
            ],
            [ikb("✅ В очередь Авито", "avito_pub_go")],
            [ikb("⬅️ Отмена", cancel_callback, style="danger")],
        ]
    )


def next_screen_level(current: int) -> int:
    return cycle_avito_level(current)


def next_body_level(current: int) -> int:
    return cycle_avito_level(current)
