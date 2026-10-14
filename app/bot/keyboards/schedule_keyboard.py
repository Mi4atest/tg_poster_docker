"""Клавиатуры мастера «Запланировать» и списка запланированных."""
from __future__ import annotations

from datetime import timedelta

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.utils.button_styles import ikb
from app.utils.time_msk import format_schedule_msk, msk_now

_WD_SHORT = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
_HOUR_START = 8
_HOUR_END = 22  # включительно
_MINUTE_PRESETS = (0, 15, 30, 45)


def get_schedule_day_keyboard(post_id: str, *, reschedule: bool = False) -> InlineKeyboardMarkup:
    prefix = "sched_rs_" if reschedule else "sched_"
    today = msk_now().date()
    rows: list[list[InlineKeyboardButton]] = [
        [
            ikb("Сегодня", f"{prefix}day_0_{post_id}"),
            ikb("Завтра", f"{prefix}day_1_{post_id}"),
        ],
    ]
    # Ещё 5 дней вперёд (со послезавтра), по 3 в ряд
    more: list[InlineKeyboardButton] = []
    for offset in range(2, 7):
        d = today + timedelta(days=offset)
        label = f"{_WD_SHORT[d.weekday()]} {d.day}"
        more.append(ikb(label, f"{prefix}day_{offset}_{post_id}"))
        if len(more) == 3:
            rows.append(more)
            more = []
    if more:
        rows.append(more)
    rows.append([ikb("✏️ Ввести дату/время", f"{prefix}day_custom_{post_id}")])
    rows.append(
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"{prefix}cancel_{post_id}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_schedule_hour_keyboard(post_id: str, *, reschedule: bool = False) -> InlineKeyboardMarkup:
    """Часы 08–22 в две колонки."""
    prefix = "sched_rs_" if reschedule else "sched_"
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for hour in range(_HOUR_START, _HOUR_END + 1):
        row.append(ikb(f"{hour:02d}:00", f"{prefix}hour_{hour:02d}_{post_id}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([ikb("✏️ Другое время", f"{prefix}time_custom_{post_id}")])
    rows.append(
        [
            InlineKeyboardButton(text="⬅️ День", callback_data=f"{prefix}back_day_{post_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"{prefix}cancel_{post_id}"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_schedule_minute_keyboard(
    post_id: str, hour: int, *, reschedule: bool = False
) -> InlineKeyboardMarkup:
    """Минуты :00 / :15 / :30 / :45 после выбора часа."""
    prefix = "sched_rs_" if reschedule else "sched_"
    rows: list[list[InlineKeyboardButton]] = [
        [
            ikb(f"{hour:02d}:{m:02d}", f"{prefix}min_{m:02d}_{post_id}")
            for m in _MINUTE_PRESETS[:2]
        ],
        [
            ikb(f"{hour:02d}:{m:02d}", f"{prefix}min_{m:02d}_{post_id}")
            for m in _MINUTE_PRESETS[2:]
        ],
    ]
    rows.append([ikb("✏️ Другое время", f"{prefix}time_custom_{post_id}")])
    rows.append(
        [
            InlineKeyboardButton(text="⬅️ Час", callback_data=f"{prefix}back_hour_{post_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"{prefix}cancel_{post_id}"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


# Обратная совместимость: старое имя → экран часов
def get_schedule_time_keyboard(post_id: str, *, reschedule: bool = False) -> InlineKeyboardMarkup:
    return get_schedule_hour_keyboard(post_id, reschedule=reschedule)


def get_schedule_confirm_keyboard(
    post_id: str, *, reschedule: bool = False
) -> InlineKeyboardMarkup:
    prefix = "sched_rs_" if reschedule else "sched_"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ikb("✅ Подтвердить", f"{prefix}confirm_{post_id}", style="success")],
            [
                InlineKeyboardButton(
                    text="✏️ Изменить", callback_data=f"{prefix}back_day_{post_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отмена", callback_data=f"{prefix}cancel_{post_id}"
                ),
            ],
        ]
    )


def get_scheduled_list_keyboard(items: list) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    for item in items[:15]:
        name = (getattr(item, "post_name", None) or f"Пост {item.post_id[:8]}")[:28]
        when = ""
        if getattr(item, "scheduled_at", None):
            when = format_schedule_msk(item.scheduled_at)
        label = f"📅 {name}" if not when else f"📅 {name} · {when}"
        buttons.append(
            [
                InlineKeyboardButton(
                    text=label[:64],
                    callback_data=f"scheduled_post_{item.post_id}",
                )
            ]
        )
    buttons.append(
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_main")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_scheduled_post_actions_keyboard(post_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ikb("✏️ Перенести", f"sched_rs_start_{post_id}")],
            [ikb("📋 В очередь сейчас", f"scheduled_promote_{post_id}")],
            [ikb("❌ Отменить", f"scheduled_cancel_{post_id}", style="danger")],
            [
                InlineKeyboardButton(
                    text="⬅️ К запланированным", callback_data="scheduled_menu"
                )
            ],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_main")],
        ]
    )
