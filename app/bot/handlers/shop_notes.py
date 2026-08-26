"""Напоминалки на главном экране: добавить и снять."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards.shop_notes_keyboard import (
    get_note_add_prompt_keyboard,
    get_note_category_keyboard,
    get_note_done_picker_keyboard,
)
from app.bot.utils.main_menu import show_home
from app.db.database import run_db
from app.services.shop_notes_service import (
    MAX_ACTIVE_NOTES,
    NoteLimitError,
    count_active_notes,
    create_note,
    list_active_notes,
    mark_note_done,
)

logger = logging.getLogger(__name__)
router = Router()


class ShopNoteCreate(StatesGroup):
    waiting_for_text = State()
    waiting_for_category = State()


@router.callback_query(F.data == "note_add")
async def note_add_start(callback: CallbackQuery, state: FSMContext):
    try:
        n = await run_db(count_active_notes)
    except Exception:
        logger.exception("note_add count failed")
        await callback.answer("Не удалось открыть заметки", show_alert=True)
        return
    if n >= MAX_ACTIVE_NOTES:
        await callback.answer(
            f"Сначала снимите одну — максимум {MAX_ACTIVE_NOTES} напоминаний.",
            show_alert=True,
        )
        return
    await state.set_state(ShopNoteCreate.waiting_for_text)
    await callback.message.edit_text(
        "📌 Напиши напоминание",
        reply_markup=get_note_add_prompt_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "note_add_cancel")
async def note_add_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_home(callback.message, callback.bot, edit=True)
    await callback.answer()


@router.message(ShopNoteCreate.waiting_for_text, F.text)
async def note_add_text(message: Message, state: FSMContext):
    body = (message.text or "").strip()
    if not body:
        await message.answer("Напиши текст напоминания.")
        return
    await state.update_data(note_body=body)
    await state.set_state(ShopNoteCreate.waiting_for_category)
    await message.answer(
        "Метка (необязательно):",
        reply_markup=get_note_category_keyboard(),
    )


@router.callback_query(ShopNoteCreate.waiting_for_category, F.data.startswith("note_cat_"))
async def note_add_category(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    body = (data.get("note_body") or "").strip()
    await state.clear()
    if not body:
        await callback.answer("Нет текста", show_alert=True)
        await show_home(callback.message, callback.bot, edit=True)
        return
    raw = callback.data.replace("note_cat_", "", 1)
    category = None if raw == "none" else raw
    try:
        await run_db(create_note, body, category)
    except NoteLimitError:
        await callback.answer(
            f"Сначала снимите одну — максимум {MAX_ACTIVE_NOTES} напоминаний.",
            show_alert=True,
        )
        await show_home(callback.message, callback.bot, edit=True)
        return
    except Exception:
        logger.exception("create_note failed")
        await callback.answer("Не удалось сохранить", show_alert=True)
        await show_home(callback.message, callback.bot, edit=True)
        return
    await show_home(callback.message, callback.bot, edit=True)
    await callback.answer("Добавлено")


@router.callback_query(F.data == "note_done")
async def note_done_entry(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        notes = await run_db(list_active_notes)
    except Exception:
        logger.exception("note_done list failed")
        await callback.answer("Не удалось загрузить заметки", show_alert=True)
        return
    if not notes:
        await callback.answer("Нет активных напоминаний")
        await show_home(callback.message, callback.bot, edit=True)
        return
    if len(notes) == 1:
        await run_db(mark_note_done, int(notes[0]["id"]))
        await show_home(callback.message, callback.bot, edit=True)
        await callback.answer("Готово")
        return
    await callback.message.edit_text(
        "Что уже не актуально?",
        reply_markup=get_note_done_picker_keyboard(notes),
    )
    await callback.answer()


@router.callback_query(F.data == "note_done_cancel")
async def note_done_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_home(callback.message, callback.bot, edit=True)
    await callback.answer()


@router.callback_query(
    F.data.startswith("note_done_") & ~F.data.in_({"note_done_cancel"})
)
async def note_done_one(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    raw = callback.data.replace("note_done_", "", 1)
    try:
        note_id = int(raw)
    except ValueError:
        await callback.answer("Ошибка")
        return
    try:
        await run_db(mark_note_done, note_id)
    except Exception:
        logger.exception("mark_note_done failed")
        await callback.answer("Не удалось снять", show_alert=True)
        return
    await show_home(callback.message, callback.bot, edit=True)
    await callback.answer("Готово")
