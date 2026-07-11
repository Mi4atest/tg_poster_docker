"""Клавиатуры для пакетного обновления цен."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def get_bulk_price_start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="bulk_price_cancel")],
        ]
    )


def get_bulk_price_preview_keyboard(
    ready_count: int,
    *,
    critical_count: int = 0,
    mismatch_count: int = 0,
) -> InlineKeyboardMarkup:
    rows = []
    if ready_count > 0:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"✅ Применить готовые ({ready_count})",
                    callback_data="bulk_price_apply",
                )
            ]
        )
    if mismatch_count > 0:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"⚠️ Проверить расхождения ({mismatch_count})",
                    callback_data="bulk_price_mismatch_start",
                )
            ]
        )
    if critical_count > 0:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🚨 Крупное изменение ({critical_count})",
                    callback_data="bulk_price_critical_start",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="bulk_price_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_bulk_price_critical_keyboard(product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, изменить",
                    callback_data=f"bulk_price_confirm_{product_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Пропустить",
                    callback_data=f"bulk_price_skip_{product_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏹ Остановить пакет",
                    callback_data="bulk_price_stop",
                )
            ],
        ]
    )


def get_bulk_price_mismatch_keyboard(product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, поставить",
                    callback_data=f"bulk_mismatch_confirm_{product_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Пропустить",
                    callback_data=f"bulk_mismatch_skip_{product_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏹ Остановить пакет",
                    callback_data="bulk_price_stop",
                )
            ],
        ]
    )


def get_bulk_price_continue_keyboard(action: str, count: int) -> InlineKeyboardMarkup:
    """Кнопка продолжить оставшийся поток после применения готовых."""
    labels = {
        "mismatch": f"⚠️ Проверить расхождения ({count})",
        "critical": f"🚨 Крупное изменение ({count})",
    }
    callbacks = {
        "mismatch": "bulk_price_mismatch_start",
        "critical": "bulk_price_critical_start",
    }
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=labels[action],
                    callback_data=callbacks[action],
                )
            ],
            [InlineKeyboardButton(text="🏁 Завершить", callback_data="bulk_price_finish")],
        ]
    )


def get_bulk_price_done_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🆕 Список новых", callback_data="new_products_menu")],
            [InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")],
        ]
    )
