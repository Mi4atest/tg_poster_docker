"""
Редактор inline-меню «Список новых» (Настройки → Меню новые).
"""
import logging
from html import escape as html_escape
from typing import Any, Dict, List, Optional

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)

from app.bot.handlers.new_products_management import safe_edit_message
from app.bot.keyboards.menu_constructor_keyboard import (
    get_constructor_delete_confirm_keyboard,
    get_constructor_input_cancel_keyboard,
    get_constructor_manage_keyboard,
    get_constructor_node_keyboard,
    get_constructor_product_card_keyboard,
)
from app.db.database import SessionLocal
from app.services import menu_constructor_service as mcs
from app.utils.product_label import button_label_for_product

logger = logging.getLogger(__name__)

router = Router()



class MenuConstructorState(StatesGroup):
    waiting_for_button_label = State()
    waiting_for_product_link = State()
    waiting_for_product_name = State()
    waiting_for_product_price = State()
    waiting_for_product_short_label = State()
    waiting_for_product_tag_subtitle = State()
    waiting_for_product_tag_description = State()
    waiting_for_hardcoded_label = State()
    waiting_for_custom_button_rename = State()
    waiting_for_edit_product_name = State()
    waiting_for_edit_product_label = State()
    waiting_for_edit_product_subtitle = State()
    waiting_for_edit_product_description = State()


def _set_constructor_input_mode(bot, user_id: int, enabled: bool) -> None:
    if hasattr(bot, "user_data"):
        bot.user_data.setdefault(user_id, {})["in_menu_constructor_mode"] = enabled


def _editor_message_html(db, path: str, nodes: List[mcs.MenuNode]) -> str:
    return mcs.format_constructor_editor_message_html(path, nodes, db)


def _product_card_html(product: Dict[str, Any]) -> str:
    pid = int(product.get("id") or 0)
    name = (product.get("name") or "").strip() or "—"
    dl = (product.get("display_label") or "").strip() or "—"
    sub = (product.get("price_tag_subtitle") or "").strip() or "—"
    desc = (product.get("price_tag_description") or "").strip()
    desc_show = desc if desc else "из шаблона"
    if len(desc_show) > 200:
        desc_show = desc_show[:197] + "…"
    cn = (product.get("collection_name") or "").strip()
    kind = "свой" if cn == "custom" else "стандартный"
    return (
        f"📝 <b>Товар #{pid}</b> <i>({html_escape(kind)})</i>\n\n"
        f"<b>Название для ценника:</b>\n{html_escape(name)}\n\n"
        f"<b>Подпись кнопки:</b>\n{html_escape(dl)}\n\n"
        f"<b>Подзаголовок:</b> {html_escape(sub)}\n"
        f"<b>Описание ценника:</b>\n{html_escape(desc_show)}"
    )


def _product_manage_rows(db, path: str, *, limit: int = 12, label_max: int = 36) -> list:
    """Строки 📝 / 🗑 для товаров узла (🗑 только у custom)."""
    rows = []
    for p in mcs.list_editable_products_at_node(db, path, limit=limit):
        pid = p.get("id")
        if pid is None:
            continue
        nm = button_label_for_product(p).replace("\n", " ")[:label_max]
        row = [InlineKeyboardButton(text=f"📝 {nm}", callback_data=f"mc_edtag_{int(pid)}")]
        if mcs.is_constructor_deletable_product(p):
            row.append(InlineKeyboardButton(text="🗑", callback_data=f"mc_rmp_{int(pid)}"))
        rows.append(row)
    return rows


def _manage_products_hint(prod_rows: list, path: str) -> str:
    if not prod_rows:
        return ""
    if path.startswith("custom:") or not mcs.is_hardcoded_leaf_with_products(path):
        return "\n\n<b>Товары на этом узле</b> — 🗑 удалить свой товар из базы."
    return (
        "\n\n<b>Товары на этом узле</b> — 📝 править название/подпись/ценник.\n"
        "🗑 только у своих товаров."
    )


async def _show_product_card(
    target_message,
    state: FSMContext,
    product_id: int,
    *,
    edit: bool = True,
) -> bool:
    db = SessionLocal()
    try:
        product = mcs.get_custom_product_for_edit(db, product_id)
    finally:
        db.close()
    if not product:
        return False
    await state.update_data(mc_edit_product_id=product_id)
    await state.set_state(None)
    text = _product_card_html(product)
    markup = get_constructor_product_card_keyboard(product_id)
    if edit:
        await safe_edit_message(
            target_message,
            text,
            reply_markup=markup,
            parse_mode="HTML",
            disable_link_preview=True,
        )
    else:
        await target_message.answer(
            text,
            reply_markup=markup,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    return True


async def _reopen_manage_screen(callback: CallbackQuery, state: FSMContext) -> bool:
    """Вернуться к экрану ⚙️ текущего узла по данным в state."""
    data = await state.get_data()
    path = data.get("mc_manage_path")
    kind = data.get("mc_manage_kind")
    custom_id = data.get("mc_manage_custom_id")
    if not path or not kind:
        return False
    label = path
    refs = await _get_refs(state)
    for r in refs:
        if r.get("path") == path:
            label = r.get("label") or label
            break
    hidden = False
    for r in refs:
        if r.get("path") == path:
            hidden = bool(r.get("hidden", False))
            break
    node = mcs.MenuNode(
        path=path,
        label=label,
        kind=kind,
        count=0,
        hidden=hidden,
        custom_id=custom_id,
    )
    can_hide = kind == "hardcoded"
    can_del = kind == "custom" and custom_id
    can_rename = kind == "hardcoded" and not str(path).startswith("custom:")
    db = SessionLocal()
    try:
        human = mcs.human_constructor_breadcrumb(path, db)
        prod_rows = _product_manage_rows(db, path, limit=8, label_max=36)
        # обновить label из БД для custom
        if kind == "custom" and custom_id:
            from app.api.models.new_menu_button import NewMenuButton

            btn = db.query(NewMenuButton).filter(NewMenuButton.id == int(custom_id)).first()
            if btn:
                label = btn.label
                node = mcs.MenuNode(
                    path=path,
                    label=label,
                    kind=kind,
                    count=0,
                    hidden=hidden,
                    custom_id=custom_id,
                )
    finally:
        db.close()
    body = (
        f"⚙️ Управление: <b>{html_escape(label)}</b>\n"
        f"📍 {html_escape(human)}\n<code>{html_escape(path)}</code>"
    )
    body += _manage_products_hint(prod_rows, path)
    await safe_edit_message(
        callback.message,
        body,
        reply_markup=get_constructor_manage_keyboard(
            node,
            can_hide=can_hide,
            is_hidden=hidden,
            can_delete_custom=bool(can_del),
            can_rename_hardcoded=can_rename,
            product_delete_rows=prod_rows,
        ),
        parse_mode="HTML",
        disable_link_preview=True,
    )
    return True


def _insert_unlink_product_rows(markup: InlineKeyboardMarkup, extra_rows: list) -> InlineKeyboardMarkup:
    """Строки отвязки товаров — перед блоком ➕/📥/⬅️/🏠/⚙️ (последние 5 рядов)."""
    rows = list(markup.inline_keyboard)
    if len(rows) < 5 or not extra_rows:
        return markup
    return InlineKeyboardMarkup(inline_keyboard=rows[:-5] + extra_rows + rows[-5:])


def _editor_markup_with_products(db, path: str, nodes: List[mcs.MenuNode]) -> InlineKeyboardMarkup:
    kb = get_constructor_node_keyboard(nodes, editor=True)
    extra = _product_manage_rows(db, path, limit=12, label_max=28)
    return _insert_unlink_product_rows(kb, extra)


async def _save_refs(state: FSMContext, nodes: List[mcs.MenuNode]) -> None:
    refs = [
        {
            "path": n.path,
            "kind": n.kind,
            "custom_id": n.custom_id,
            "label": n.label,
            "hidden": n.hidden,
        }
        for n in nodes
    ]
    await state.update_data(mc_refs=refs)


async def _get_refs(state: FSMContext) -> List[Dict[str, Any]]:
    data = await state.get_data()
    return list(data.get("mc_refs") or [])


async def _current_path(state: FSMContext) -> str:
    data = await state.get_data()
    stack = list(data.get("mc_nav_stack") or ["root"])
    return stack[-1]


async def _set_stack(state: FSMContext, stack: List[str]) -> None:
    await state.update_data(mc_nav_stack=stack)


async def _render(callback: CallbackQuery, state: FSMContext, path: Optional[str] = None):
    db = SessionLocal()
    try:
        if path is not None:
            data = await state.get_data()
            stack = list(data.get("mc_nav_stack") or ["root"])
            if stack and stack[-1] != path:
                stack.append(path)
            await _set_stack(state, stack)
        cur = await _current_path(state)
        nodes = mcs.get_merged_menu_nodes(db, cur, editor=True)
        await _save_refs(state, nodes)
        title = _editor_message_html(db, cur, nodes)
        await safe_edit_message(
            callback.message,
            title,
            reply_markup=_editor_markup_with_products(db, cur, nodes),
            parse_mode="HTML",
            disable_link_preview=True,
        )
    finally:
        db.close()
    await callback.answer()


@router.callback_query(F.data == "mc_open")
async def mc_open(callback: CallbackQuery, state: FSMContext):
    await state.update_data(
        mc_nav_stack=["root"],
        mc_manage_path=None,
        mc_manage_kind=None,
        mc_manage_custom_id=None,
        mc_edit_product_id=None,
    )
    await _render(callback, state, path="root")


@router.callback_query(F.data.startswith("mc_s_"))
async def mc_select(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data.replace("mc_s_", ""))
    refs = await _get_refs(state)
    if idx < 0 or idx >= len(refs):
        await callback.answer("Устарело, откройте редактор заново", show_alert=True)
        return
    path = refs[idx]["path"]
    data = await state.get_data()
    stack = list(data.get("mc_nav_stack") or ["root"])
    stack.append(path)
    await _set_stack(state, stack)
    await state.update_data(mc_manage_path=None, mc_manage_kind=None, mc_manage_custom_id=None)
    db = SessionLocal()
    try:
        nodes = mcs.get_merged_menu_nodes(db, path, editor=True)
        await _save_refs(state, nodes)
        title = _editor_message_html(db, path, nodes)
        await safe_edit_message(
            callback.message,
            title,
            reply_markup=_editor_markup_with_products(db, path, nodes),
            parse_mode="HTML",
            disable_link_preview=True,
        )
    finally:
        db.close()
    await callback.answer()


@router.callback_query(F.data == "mc_back")
async def mc_back(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    stack = list(data.get("mc_nav_stack") or ["root"])
    if len(stack) <= 1:
        await callback.answer("Уже в корне")
        return
    stack.pop()
    await _set_stack(state, stack)
    await state.update_data(mc_manage_path=None, mc_manage_kind=None, mc_manage_custom_id=None)
    db = SessionLocal()
    try:
        cur = stack[-1]
        nodes = mcs.get_merged_menu_nodes(db, cur, editor=True)
        await _save_refs(state, nodes)
        title = _editor_message_html(db, cur, nodes)
        await safe_edit_message(
            callback.message,
            title,
            reply_markup=_editor_markup_with_products(db, cur, nodes),
            parse_mode="HTML",
            disable_link_preview=True,
        )
    finally:
        db.close()
    await callback.answer()


@router.callback_query(F.data.startswith("mc_g_"))
async def mc_manage(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data.replace("mc_g_", ""))
    refs = await _get_refs(state)
    if idx < 0 or idx >= len(refs):
        await callback.answer("Устарело", show_alert=True)
        return
    r = refs[idx]
    path = r["path"]
    kind = r["kind"]
    hidden = r.get("hidden", False)
    node = mcs.MenuNode(
        path=path,
        label=r["label"],
        kind=kind,
        count=0,
        hidden=hidden,
        custom_id=r.get("custom_id"),
    )
    await state.update_data(mc_manage_path=path, mc_manage_kind=kind, mc_manage_custom_id=r.get("custom_id"))
    can_hide = kind == "hardcoded"
    can_del = kind == "custom" and r.get("custom_id")
    can_rename = kind == "hardcoded" and not path.startswith("custom:")
    db = SessionLocal()
    try:
        human = mcs.human_constructor_breadcrumb(path, db)
        prod_rows = _product_manage_rows(db, path, limit=8, label_max=36)
    finally:
        db.close()
    body = (
        f"⚙️ Управление: <b>{html_escape(r['label'])}</b>\n"
        f"📍 {html_escape(human)}\n<code>{html_escape(path)}</code>"
    )
    body += _manage_products_hint(prod_rows, path)
    await safe_edit_message(
        callback.message,
        body,
        reply_markup=get_constructor_manage_keyboard(
            node,
            can_hide=can_hide,
            is_hidden=hidden,
            can_delete_custom=bool(can_del),
            can_rename_hardcoded=can_rename,
            product_delete_rows=prod_rows,
        ),
        parse_mode="HTML",
        disable_link_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data == "mc_manage_cancel")
async def mc_manage_cancel(callback: CallbackQuery, state: FSMContext):
    await state.update_data(
        mc_manage_path=None,
        mc_manage_kind=None,
        mc_manage_custom_id=None,
        mc_edit_product_id=None,
    )
    db = SessionLocal()
    try:
        cur = await _current_path(state)
        nodes = mcs.get_merged_menu_nodes(db, cur, editor=True)
        await _save_refs(state, nodes)
        await safe_edit_message(
            callback.message,
            _editor_message_html(db, cur, nodes),
            reply_markup=_editor_markup_with_products(db, cur, nodes),
            parse_mode="HTML",
            disable_link_preview=True,
        )
    finally:
        db.close()
    await callback.answer()


@router.callback_query(F.data == "mc_toggle_hide")
async def mc_toggle_hide(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    path = data.get("mc_manage_path")
    if not path or path.startswith("custom:"):
        await callback.answer("Для своих кнопок используйте «Удалить»", show_alert=True)
        return
    mcs.toggle_hidden(path)
    await mc_manage_cancel(callback, state)


@router.callback_query(F.data == "mc_del_confirm")
async def mc_del_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cid = data.get("mc_manage_custom_id")
    if not cid:
        await callback.answer("Нет кнопки", show_alert=True)
        return
    db = SessionLocal()
    try:
        n_btn, n_prod = mcs.count_delete_preview(db, int(cid))
        await state.update_data(mc_del_target_id=int(cid))
        await safe_edit_message(
            callback.message,
            "🗑 <b>Удаление пользовательской кнопки</b>\n\n"
            f"Будет удалено кнопок (с поддеревом): <b>{n_btn}</b>\n"
            f"Товаров сейчас привязано: <b>{n_prod}</b> — они будут <b>удалены из базы</b>.\n\n"
            "Продолжить?",
            reply_markup=get_constructor_delete_confirm_keyboard(),
            parse_mode="HTML",
            disable_link_preview=True,
        )
    finally:
        db.close()
    await callback.answer()


@router.callback_query(F.data == "mc_del_no")
async def mc_del_no(callback: CallbackQuery, state: FSMContext):
    await state.update_data(mc_del_target_id=None)
    await mc_manage_cancel(callback, state)


@router.callback_query(F.data == "mc_del_yes")
async def mc_del_yes(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cid = data.get("mc_del_target_id")
    if not cid:
        await callback.answer("Ошибка", show_alert=True)
        return
    db = SessionLocal()
    try:
        mcs.delete_custom_button_cascade(db, int(cid))
    finally:
        db.close()
    await state.update_data(mc_del_target_id=None, mc_manage_path=None, mc_manage_custom_id=None)
    await callback.answer("✅ Удалено")
    db = SessionLocal()
    try:
        cur = await _current_path(state)
        nodes = mcs.get_merged_menu_nodes(db, cur, editor=True)
        await _save_refs(state, nodes)
        await safe_edit_message(
            callback.message,
            _editor_message_html(db, cur, nodes),
            reply_markup=_editor_markup_with_products(db, cur, nodes),
            parse_mode="HTML",
            disable_link_preview=True,
        )
    finally:
        db.close()


@router.callback_query(F.data == "mc_add_btn")
async def mc_add_btn(callback: CallbackQuery, state: FSMContext):
    cur = await _current_path(state)
    if callback.from_user:
        _set_constructor_input_mode(callback.bot, callback.from_user.id, True)
    await state.set_state(MenuConstructorState.waiting_for_button_label)
    await state.update_data(mc_input_parent=cur)
    await safe_edit_message(
        callback.message,
        "➕ <b>Новая кнопка</b>\n\nВведите текст кнопки (как увидят в «Список новых»):",
        reply_markup=get_constructor_input_cancel_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "mc_add_prod")
async def mc_add_prod(callback: CallbackQuery, state: FSMContext):
    cur = await _current_path(state)
    if callback.from_user:
        _set_constructor_input_mode(callback.bot, callback.from_user.id, True)
    await state.set_state(MenuConstructorState.waiting_for_product_link)
    await state.update_data(mc_prod_parent=cur)
    await safe_edit_message(
        callback.message,
        "📥 <b>Добавить товар</b>\n\n"
        "Вставьте ссылку на товар ВК. Пример:\n"
        "<code>vk.ru/market-129808251?w=product-129808251_11608836</code>",
        reply_markup=get_constructor_input_cancel_keyboard(),
        parse_mode="HTML",
        disable_link_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data == "mc_cancel_input")
async def mc_cancel_input(callback: CallbackQuery, state: FSMContext):
    if callback.from_user:
        _set_constructor_input_mode(callback.bot, callback.from_user.id, False)
    data = await state.get_data()
    edit_pid = data.get("mc_edit_product_id")
    await state.set_state(None)
    if edit_pid:
        ok = await _show_product_card(callback.message, state, int(edit_pid), edit=True)
        if ok:
            await callback.answer()
            return
    db = SessionLocal()
    try:
        cur = await _current_path(state)
        nodes = mcs.get_merged_menu_nodes(db, cur, editor=True)
        await _save_refs(state, nodes)
        await safe_edit_message(
            callback.message,
            _editor_message_html(db, cur, nodes),
            reply_markup=_editor_markup_with_products(db, cur, nodes),
            parse_mode="HTML",
            disable_link_preview=True,
        )
    finally:
        db.close()
    await callback.answer()


@router.callback_query(F.data == "mc_btn_rename")
async def mc_btn_rename(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cid = data.get("mc_manage_custom_id")
    if not cid:
        await callback.answer("Нет кнопки", show_alert=True)
        return
    db = SessionLocal()
    try:
        from app.api.models.new_menu_button import NewMenuButton

        btn = db.query(NewMenuButton).filter(NewMenuButton.id == int(cid)).first()
        cur_label = (btn.label if btn else "") or ""
    finally:
        db.close()
    if callback.from_user:
        _set_constructor_input_mode(callback.bot, callback.from_user.id, True)
    await state.set_state(MenuConstructorState.waiting_for_custom_button_rename)
    await state.update_data(mc_rename_button_id=int(cid), mc_edit_product_id=None)
    await safe_edit_message(
        callback.message,
        "✏️ <b>Переименовать кнопку</b>\n\n"
        f"Текущая подпись: <b>{html_escape(cur_label)}</b>\n\n"
        "Введите новый текст кнопки (как в «Список новых»):",
        reply_markup=get_constructor_input_cancel_keyboard(),
        parse_mode="HTML",
        disable_link_preview=True,
    )
    await callback.answer()


@router.message(MenuConstructorState.waiting_for_custom_button_rename)
async def mc_btn_rename_apply(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    data = await state.get_data()
    cid = data.get("mc_rename_button_id") or data.get("mc_manage_custom_id")
    if message.from_user:
        _set_constructor_input_mode(message.bot, message.from_user.id, False)
    await state.set_state(None)
    if not cid:
        await message.answer("❌ Нет кнопки")
        return
    db = SessionLocal()
    try:
        try:
            ok = mcs.rename_custom_button(db, int(cid), raw)
        except ValueError as e:
            await message.answer(f"❌ {e}")
            return
        if not ok:
            await message.answer("❌ Не удалось переименовать")
            return
        cur = await _current_path(state)
        nodes = mcs.get_merged_menu_nodes(db, cur, editor=True)
        await _save_refs(state, nodes)
        # обновить label в manage state
        for n in nodes:
            if n.custom_id == int(cid):
                await state.update_data(mc_manage_path=n.path, mc_manage_kind=n.kind, mc_manage_custom_id=n.custom_id)
                break
        title = _editor_message_html(db, cur, nodes)
        mk = _editor_markup_with_products(db, cur, nodes)
    finally:
        db.close()
    await message.answer(
        title,
        reply_markup=mk,
        parse_mode="HTML",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
    await message.answer("✅ Кнопка переименована.")


@router.callback_query(F.data == "mc_lbl_edit")
async def mc_lbl_edit(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    path = data.get("mc_manage_path")
    if not path or path.startswith("custom:"):
        await callback.answer("Только для стандартных узлов", show_alert=True)
        return
    await state.update_data(mc_lbl_path=path, mc_edit_product_id=None)
    await state.set_state(MenuConstructorState.waiting_for_hardcoded_label)
    if callback.from_user:
        _set_constructor_input_mode(callback.bot, callback.from_user.id, True)
    await safe_edit_message(
        callback.message,
        "✏️ <b>Подпись в «Список новых»</b>\n\n"
        "Введите текст для кнопки/узла вместо стандартного.\n"
        "Отправьте <code>-</code> или <code>сброс</code> — вернуть подпись по умолчанию.",
        reply_markup=get_constructor_input_cancel_keyboard(),
        parse_mode="HTML",
        disable_link_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mc_rmp_"))
async def mc_remove_product(callback: CallbackQuery, state: FSMContext):
    try:
        pid = int(callback.data.replace("mc_rmp_", ""))
    except ValueError:
        await callback.answer("Ошибка", show_alert=True)
        return
    db = SessionLocal()
    try:
        ok = mcs.delete_custom_product(db, pid)
    finally:
        db.close()
    if not ok:
        await callback.answer("Не удалось удалить (не свой товар или не найден)", show_alert=True)
        return
    await callback.answer("✅ Товар удалён")
    db = SessionLocal()
    try:
        cur = await _current_path(state)
        nodes = mcs.get_merged_menu_nodes(db, cur, editor=True)
        await _save_refs(state, nodes)
        await safe_edit_message(
            callback.message,
            _editor_message_html(db, cur, nodes),
            reply_markup=_editor_markup_with_products(db, cur, nodes),
            parse_mode="HTML",
            disable_link_preview=True,
        )
    finally:
        db.close()


@router.message(MenuConstructorState.waiting_for_hardcoded_label)
async def mc_lbl_apply(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    data = await state.get_data()
    path = data.get("mc_lbl_path") or data.get("mc_manage_path")
    stack = list(data.get("mc_nav_stack") or ["root"])
    cur = stack[-1] if stack else "root"
    await state.set_state(None)
    await state.update_data(mc_lbl_path=None, mc_manage_path=None, mc_manage_custom_id=None, mc_manage_kind=None)
    if message.from_user:
        _set_constructor_input_mode(message.bot, message.from_user.id, False)
    if path and not str(path).startswith("custom:"):
        if raw.lower() in ("-", "сброс", "сбросить"):
            mcs.set_label_override(str(path), "")
        else:
            mcs.set_label_override(str(path), raw[:128])
    db = SessionLocal()
    try:
        nodes = mcs.get_merged_menu_nodes(db, cur, editor=True)
        await _save_refs(state, nodes)
        title = _editor_message_html(db, cur, nodes)
        mk = _editor_markup_with_products(db, cur, nodes)
    finally:
        db.close()
    await message.answer(
        title,
        reply_markup=mk,
        parse_mode="HTML",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
    await message.answer("✅ Подпись сохранена. Сообщение выше — актуальный редактор.")


@router.message(MenuConstructorState.waiting_for_button_label)
async def mc_save_button_label(message: Message, state: FSMContext):
    data = await state.get_data()
    parent = data.get("mc_input_parent", "root")
    label = (message.text or "").strip()
    if not label:
        await message.answer("❌ Пустой текст")
        return
    db = SessionLocal()
    try:
        uid = message.from_user.id if message.from_user else 0
        mcs.add_custom_button(db, parent, label, uid)
    except ValueError as e:
        await message.answer(f"❌ {e}")
        return
    finally:
        db.close()
    if message.from_user:
        _set_constructor_input_mode(message.bot, message.from_user.id, False)
    await state.set_state(None)
    await message.answer("✅ Кнопка добавлена.")
    # refresh: send new message with keyboard — user may continue in chat
    db = SessionLocal()
    try:
        cur = await _current_path(state)
        nodes = mcs.get_merged_menu_nodes(db, cur, editor=True)
        await _save_refs(state, nodes)
        await message.answer(
            _editor_message_html(db, cur, nodes),
            reply_markup=_editor_markup_with_products(db, cur, nodes),
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    finally:
        db.close()


@router.message(MenuConstructorState.waiting_for_product_link)
async def mc_save_product_link(message: Message, state: FSMContext):
    link = (message.text or "").strip()
    if "product-" not in link and "market" not in link.lower():
        await message.answer("❌ Похоже, это не ссылка на товар ВК.")
        return
    await state.update_data(mc_prod_link=link)
    await state.set_state(MenuConstructorState.waiting_for_product_name)
    await message.answer(
        "Введите <b>название товара</b> (как в карточке):",
        parse_mode="HTML",
        reply_markup=get_constructor_input_cancel_keyboard(),
    )


@router.message(MenuConstructorState.waiting_for_product_name)
async def mc_save_product_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        await message.answer("❌ Пустое название")
        return
    await state.update_data(mc_prod_name=name)
    await state.set_state(MenuConstructorState.waiting_for_product_price)
    await message.answer(
        "Введите <b>цену</b> целым числом в рублях, например: <code>89900</code>",
        parse_mode="HTML",
        reply_markup=get_constructor_input_cancel_keyboard(),
    )


@router.message(MenuConstructorState.waiting_for_product_price)
async def mc_save_product_price(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("❌ Нужно целое число (руб.)")
        return
    await state.update_data(mc_prod_price=raw)
    await state.set_state(MenuConstructorState.waiting_for_product_short_label)
    await message.answer(
        "Введите <b>короткую подпись</b> для кнопки (одна строка), как в «Список новых»:\n"
        "Например: <code>MacBook Air M2 256 ⚫️</code> или <code>AirPods 4 ANC</code>\n\n"
        "Отправьте <code>-</code> — подставить автоматически из названия.",
        parse_mode="HTML",
        reply_markup=get_constructor_input_cancel_keyboard(),
    )


@router.message(MenuConstructorState.waiting_for_product_short_label)
async def mc_save_product_short_label(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    display_label = None if raw.lower() in ("-", "пропустить", "авто") else raw[:128]
    await state.update_data(mc_prod_display_label=display_label)
    await state.set_state(MenuConstructorState.waiting_for_product_tag_subtitle)
    await message.answer(
        "Введите <b>подзаголовок ценника</b> (например: <code>не_активирован</code>).\n"
        "Отправьте <code>-</code> — пропустить.",
        parse_mode="HTML",
        reply_markup=get_constructor_input_cancel_keyboard(),
    )


@router.message(MenuConstructorState.waiting_for_product_tag_subtitle)
async def mc_save_product_tag_subtitle(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    subtitle = None if raw in ("-", "пропустить") else raw[:64]
    await state.update_data(mc_prod_tag_subtitle=subtitle)
    await state.set_state(MenuConstructorState.waiting_for_product_tag_description)
    await message.answer(
        "Введите <b>описание для ценника</b> (несколько строк текста).\n"
        "Отправьте <code>-</code> — использовать шаблон из настроек.",
        parse_mode="HTML",
        reply_markup=get_constructor_input_cancel_keyboard(),
    )


@router.message(MenuConstructorState.waiting_for_product_tag_description)
async def mc_save_product_tag_description(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    description = None if raw in ("-", "пропустить") else raw[:512]
    data = await state.get_data()
    parent = data.get("mc_prod_parent", "root")
    link = data.get("mc_prod_link", "")
    name = data.get("mc_prod_name", "")
    price = data.get("mc_prod_price", "")
    display_label = data.get("mc_prod_display_label")
    subtitle = data.get("mc_prod_tag_subtitle")
    await state.set_state(None)
    await state.update_data(
        mc_prod_link=None,
        mc_prod_name=None,
        mc_prod_parent=None,
        mc_prod_price=None,
        mc_prod_display_label=None,
        mc_prod_tag_subtitle=None,
    )
    db = SessionLocal()
    try:
        uid = message.from_user.id if message.from_user else 0
        mcs.attach_custom_product(
            db,
            parent,
            link,
            name,
            price,
            uid,
            display_label=display_label,
            price_tag_subtitle=subtitle,
            price_tag_description=description,
        )
    except ValueError as e:
        if hasattr(message.bot, "user_data"):
            message.bot.user_data.setdefault(message.from_user.id, {})["in_menu_constructor_mode"] = False
        await message.answer(f"❌ {e}")
        return
    except Exception as e:
        logger.exception("attach product: %s", e)
        if hasattr(message.bot, "user_data"):
            message.bot.user_data.setdefault(message.from_user.id, {})["in_menu_constructor_mode"] = False
        await message.answer("❌ Ошибка сохранения.")
        return
    finally:
        db.close()
    if hasattr(message.bot, "user_data"):
        message.bot.user_data.setdefault(message.from_user.id, {})["in_menu_constructor_mode"] = False
    await message.answer("✅ Товар добавлен.")
    db = SessionLocal()
    try:
        cur = await _current_path(state)
        nodes = mcs.get_merged_menu_nodes(db, cur, editor=True)
        await _save_refs(state, nodes)
        await message.answer(
            _editor_message_html(db, cur, nodes),
            reply_markup=_editor_markup_with_products(db, cur, nodes),
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    finally:
        db.close()


@router.callback_query(F.data.startswith("mc_edtag_"))
async def mc_edit_product_card(callback: CallbackQuery, state: FSMContext):
    try:
        pid = int(callback.data.replace("mc_edtag_", ""))
    except ValueError:
        await callback.answer("Ошибка", show_alert=True)
        return
    ok = await _show_product_card(callback.message, state, pid, edit=True)
    if not ok:
        await callback.answer("Товар не найден", show_alert=True)
        return
    await callback.answer()


@router.callback_query(F.data == "mc_pf_back")
async def mc_product_card_back(callback: CallbackQuery, state: FSMContext):
    await state.update_data(mc_edit_product_id=None)
    if await _reopen_manage_screen(callback, state):
        await callback.answer()
        return
    await mc_manage_cancel(callback, state)


async def _start_product_field_edit(
    callback: CallbackQuery,
    state: FSMContext,
    product_id: int,
    fsm_state: State,
    prompt_html: str,
) -> None:
    if callback.from_user:
        _set_constructor_input_mode(callback.bot, callback.from_user.id, True)
    await state.update_data(mc_edit_product_id=product_id)
    await state.set_state(fsm_state)
    await safe_edit_message(
        callback.message,
        prompt_html,
        reply_markup=get_constructor_input_cancel_keyboard(),
        parse_mode="HTML",
        disable_link_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mc_pf_name_"))
async def mc_pf_name(callback: CallbackQuery, state: FSMContext):
    try:
        pid = int(callback.data.replace("mc_pf_name_", ""))
    except ValueError:
        await callback.answer("Ошибка", show_alert=True)
        return
    db = SessionLocal()
    try:
        product = mcs.get_custom_product_for_edit(db, pid)
    finally:
        db.close()
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    cur = (product.get("name") or "").strip()
    cn = (product.get("collection_name") or "").strip()
    warn = ""
    if cn and cn != "custom":
        warn = (
            "\n\n⚠️ Для стандартного товара название также участвует в разборе меню "
            "(модель/память/цвет). Меняйте аккуратно."
        )
    await _start_product_field_edit(
        callback,
        state,
        pid,
        MenuConstructorState.waiting_for_edit_product_name,
        "🏷 <b>Название для ценника</b>\n\n"
        f"Текущее: <b>{html_escape(cur or '—')}</b>\n\n"
        "Введите новое название (как на PDF-ценнике):"
        f"{warn}",
    )


@router.callback_query(F.data.startswith("mc_pf_label_"))
async def mc_pf_label(callback: CallbackQuery, state: FSMContext):
    try:
        pid = int(callback.data.replace("mc_pf_label_", ""))
    except ValueError:
        await callback.answer("Ошибка", show_alert=True)
        return
    db = SessionLocal()
    try:
        product = mcs.get_custom_product_for_edit(db, pid)
    finally:
        db.close()
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    cur = (product.get("display_label") or "").strip()
    await _start_product_field_edit(
        callback,
        state,
        pid,
        MenuConstructorState.waiting_for_edit_product_label,
        "🔘 <b>Подпись кнопки</b>\n\n"
        f"Текущая: <b>{html_escape(cur or '—')}</b>\n\n"
        "Введите короткую подпись для «Список новых».\n"
        "Отправьте <code>-</code> — подставить автоматически из названия.",
    )


@router.callback_query(F.data.startswith("mc_pf_sub_"))
async def mc_pf_sub(callback: CallbackQuery, state: FSMContext):
    try:
        pid = int(callback.data.replace("mc_pf_sub_", ""))
    except ValueError:
        await callback.answer("Ошибка", show_alert=True)
        return
    db = SessionLocal()
    try:
        product = mcs.get_custom_product_for_edit(db, pid)
    finally:
        db.close()
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    cur = (product.get("price_tag_subtitle") or "").strip()
    await _start_product_field_edit(
        callback,
        state,
        pid,
        MenuConstructorState.waiting_for_edit_product_subtitle,
        "📄 <b>Подзаголовок ценника</b>\n\n"
        f"Текущий: <b>{html_escape(cur or '—')}</b>\n\n"
        "Введите подзаголовок (например: <code>не_активирован</code>).\n"
        "Отправьте <code>-</code> чтобы очистить.",
    )


@router.callback_query(F.data.startswith("mc_pf_desc_"))
async def mc_pf_desc(callback: CallbackQuery, state: FSMContext):
    try:
        pid = int(callback.data.replace("mc_pf_desc_", ""))
    except ValueError:
        await callback.answer("Ошибка", show_alert=True)
        return
    db = SessionLocal()
    try:
        product = mcs.get_custom_product_for_edit(db, pid)
    finally:
        db.close()
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    cur = (product.get("price_tag_description") or "").strip()
    preview = cur if cur else "из шаблона"
    if len(preview) > 200:
        preview = preview[:197] + "…"
    await _start_product_field_edit(
        callback,
        state,
        pid,
        MenuConstructorState.waiting_for_edit_product_description,
        "📝 <b>Описание ценника</b>\n\n"
        f"Текущее:\n{html_escape(preview)}\n\n"
        "Введите описание для ценника.\n"
        "Отправьте <code>-</code> — очистить (будет шаблон из настроек).",
    )


@router.message(MenuConstructorState.waiting_for_edit_product_name)
async def mc_save_edit_product_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    data = await state.get_data()
    pid = int(data.get("mc_edit_product_id") or 0)
    if message.from_user:
        _set_constructor_input_mode(message.bot, message.from_user.id, False)
    if not name:
        await message.answer("❌ Пустое название")
        return
    db = SessionLocal()
    try:
        try:
            product = mcs.update_constructor_product_fields(db, pid, name=name)
        except ValueError as e:
            await message.answer(f"❌ {e}")
            return
    finally:
        db.close()
    if not product:
        await message.answer("❌ Не удалось сохранить")
        return
    await message.answer("✅ Название сохранено.")
    await _show_product_card(message, state, pid, edit=False)


@router.message(MenuConstructorState.waiting_for_edit_product_label)
async def mc_save_edit_product_label(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    data = await state.get_data()
    pid = int(data.get("mc_edit_product_id") or 0)
    if message.from_user:
        _set_constructor_input_mode(message.bot, message.from_user.id, False)
    auto = raw.lower() in ("-", "авто", "пропустить")
    db = SessionLocal()
    try:
        product = mcs.update_constructor_product_fields(
            db,
            pid,
            display_label=None if auto else raw,
            auto_display_label=auto,
        )
    finally:
        db.close()
    if not product:
        await message.answer("❌ Не удалось сохранить")
        return
    await message.answer("✅ Подпись кнопки сохранена.")
    await _show_product_card(message, state, pid, edit=False)


@router.message(MenuConstructorState.waiting_for_edit_product_subtitle)
async def mc_save_edit_product_subtitle(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    data = await state.get_data()
    pid = int(data.get("mc_edit_product_id") or 0)
    if message.from_user:
        _set_constructor_input_mode(message.bot, message.from_user.id, False)
    clear = raw == "-"
    db = SessionLocal()
    try:
        product = mcs.update_constructor_product_fields(
            db,
            pid,
            price_tag_subtitle=None if clear else raw[:64],
            clear_subtitle=clear,
        )
    finally:
        db.close()
    if not product:
        await message.answer("❌ Не удалось сохранить")
        return
    await message.answer("✅ Подзаголовок сохранён.")
    await _show_product_card(message, state, pid, edit=False)


@router.message(MenuConstructorState.waiting_for_edit_product_description)
async def mc_save_edit_product_description(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    data = await state.get_data()
    pid = int(data.get("mc_edit_product_id") or 0)
    if message.from_user:
        _set_constructor_input_mode(message.bot, message.from_user.id, False)
    clear = raw == "-"
    db = SessionLocal()
    try:
        product = mcs.update_constructor_product_fields(
            db,
            pid,
            price_tag_description=None if clear else raw[:512],
            clear_description=clear,
        )
    finally:
        db.close()
    if not product:
        await message.answer("❌ Не удалось сохранить")
        return
    await message.answer("✅ Описание ценника сохранено.")
    await _show_product_card(message, state, pid, edit=False)