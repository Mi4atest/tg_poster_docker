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
)
from app.db.database import SessionLocal
from app.services import menu_constructor_service as mcs

logger = logging.getLogger(__name__)

router = Router()



class MenuConstructorState(StatesGroup):
    waiting_for_button_label = State()
    waiting_for_product_link = State()
    waiting_for_product_name = State()
    waiting_for_product_price = State()
    waiting_for_hardcoded_label = State()


def _editor_message_html(db, path: str, nodes: List[mcs.MenuNode]) -> str:
    return mcs.format_constructor_editor_message_html(path, nodes, db)


def _insert_unlink_product_rows(markup: InlineKeyboardMarkup, extra_rows: list) -> InlineKeyboardMarkup:
    """Строки отвязки товаров — перед блоком ➕/📥/⬅️/🏠/⚙️ (последние 5 рядов)."""
    rows = list(markup.inline_keyboard)
    if len(rows) < 5 or not extra_rows:
        return markup
    return InlineKeyboardMarkup(inline_keyboard=rows[:-5] + extra_rows + rows[-5:])


def _editor_markup_with_products(db, path: str, nodes: List[mcs.MenuNode]) -> InlineKeyboardMarkup:
    kb = get_constructor_node_keyboard(nodes, editor=True)
    extra = []
    for p in mcs.list_detachable_custom_products_at_path(db, path):
        pid = p.get("id")
        if pid is None:
            continue
        nm = ((p.get("name") or "?").replace("\n", " "))[:34]
        extra.append([InlineKeyboardButton(text=f"🗑 {nm}", callback_data=f"mc_rmp_{int(pid)}")])
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
    await state.update_data(mc_nav_stack=["root"])
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
    finally:
        db.close()
    await safe_edit_message(
        callback.message,
        f"⚙️ Управление: <b>{html_escape(r['label'])}</b>\n"
        f"📍 {html_escape(human)}\n<code>{html_escape(path)}</code>",
        reply_markup=get_constructor_manage_keyboard(
            node,
            can_hide=can_hide,
            is_hidden=hidden,
            can_delete_custom=bool(can_del),
            can_rename_hardcoded=can_rename,
        ),
        parse_mode="HTML",
        disable_link_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data == "mc_manage_cancel")
async def mc_manage_cancel(callback: CallbackQuery, state: FSMContext):
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
            f"Товаров сейчас привязано: <b>{n_prod}</b> — они будут <b>отвязаны</b> (остаются в базе).\n\n"
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
    if hasattr(callback.bot, "user_data"):
        callback.bot.user_data.setdefault(callback.from_user.id, {})["in_menu_constructor_mode"] = True
    await state.set_state(MenuConstructorState.waiting_for_product_link)
    await state.update_data(mc_prod_parent=cur)
    await safe_edit_message(
        callback.message,
        "📥 <b>Добавить товар</b>\n\n"
        "Вставьте ссылку на товар ВК. Пример:\n"
        "<code>vk.com/market-129808251?w=product-129808251_11608836</code>",
        reply_markup=get_constructor_input_cancel_keyboard(),
        parse_mode="HTML",
        disable_link_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data == "mc_cancel_input")
async def mc_cancel_input(callback: CallbackQuery, state: FSMContext):
    if hasattr(callback.bot, "user_data"):
        callback.bot.user_data.setdefault(callback.from_user.id, {})["in_menu_constructor_mode"] = False
    await state.set_state(None)
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


@router.callback_query(F.data == "mc_lbl_edit")
async def mc_lbl_edit(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    path = data.get("mc_manage_path")
    if not path or path.startswith("custom:"):
        await callback.answer("Только для стандартных узлов", show_alert=True)
        return
    await state.update_data(mc_lbl_path=path)
    await state.set_state(MenuConstructorState.waiting_for_hardcoded_label)
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
        ok = mcs.detach_custom_product(db, pid)
    finally:
        db.close()
    if not ok:
        await callback.answer("Не удалось отвязать (не кастом или не найден)", show_alert=True)
        return
    await callback.answer("✅ Товар отвязан от меню")
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
    data = await state.get_data()
    parent = data.get("mc_prod_parent", "root")
    link = data.get("mc_prod_link", "")
    name = data.get("mc_prod_name", "")
    await state.set_state(None)
    await state.update_data(mc_prod_link=None, mc_prod_name=None, mc_prod_parent=None)
    db = SessionLocal()
    try:
        uid = message.from_user.id if message.from_user else 0
        product = mcs.attach_custom_product(db, parent, link, name, raw, uid)
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
