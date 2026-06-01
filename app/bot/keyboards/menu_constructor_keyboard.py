"""Inline-клавиатуры редактора «Меню новые»."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.services.menu_constructor_service import MenuNode


def _label_with_count(node: MenuNode, editor: bool) -> str:
    prefix = ""
    if editor and node.hidden and node.kind == "hardcoded":
        prefix = "🚫 "
    if node.emoji:
        return f"{prefix}{node.emoji}{node.label} ({node.count})"
    return f"{prefix}{node.label} ({node.count})"


def get_constructor_node_keyboard(
    nodes: list[MenuNode],
    editor: bool = True,
) -> InlineKeyboardMarkup:
    """Строки: [подпись (count)] [⚙️]. Внизу: добавить кнопку, добавить товар, назад, главная."""
    buttons = []
    for i, node in enumerate(nodes):
        label = _label_with_count(node, editor)
        if len(label) > 64:
            label = label[:61] + "..."
        row = [
            InlineKeyboardButton(
                text=label,
                callback_data=f"mc_s_{i:02d}",
            ),
            InlineKeyboardButton(
                text="⚙️",
                callback_data=f"mc_g_{i:02d}",
            ),
        ]
        buttons.append(row)
    buttons.append(
        [InlineKeyboardButton(text="➕ добавить кнопку", callback_data="mc_add_btn")]
    )
    buttons.append(
        [InlineKeyboardButton(text="📥 Добавить товар", callback_data="mc_add_prod")]
    )
    buttons.append(
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="mc_back")]
    )
    buttons.append(
        [InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")]
    )
    buttons.append(
        [InlineKeyboardButton(text="⚙️ К настройкам", callback_data="settings_root")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_constructor_manage_keyboard(
    node: MenuNode,
    can_hide: bool,
    is_hidden: bool,
    can_delete_custom: bool,
    can_rename_hardcoded: bool = False,
) -> InlineKeyboardMarkup:
    rows = []
    if can_hide:
        txt = "👁 Показать в меню" if is_hidden else "🚫 Скрыть из меню"
        rows.append([InlineKeyboardButton(text=txt, callback_data="mc_toggle_hide")])
    if can_rename_hardcoded:
        rows.append(
            [InlineKeyboardButton(text="✏️ Своя подпись в «Список новых»…", callback_data="mc_lbl_edit")]
        )
    if can_delete_custom:
        rows.append(
            [InlineKeyboardButton(text="🗑 Удалить кнопку…", callback_data="mc_del_confirm")]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Отмена", callback_data="mc_manage_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_constructor_delete_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, удалить", callback_data="mc_del_yes"),
                InlineKeyboardButton(text="❌ Нет", callback_data="mc_del_no"),
            ],
        ]
    )


def get_constructor_input_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена ввода", callback_data="mc_cancel_input")],
        ]
    )
