"""Evening report: panel-based editing and daily persistence."""
from __future__ import annotations

import asyncio
import logging
from datetime import date

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards.evening_report_keyboard import (
    get_evening_report_copy_keyboard,
    get_evening_report_extra_kind_keyboard,
    get_evening_report_extra_name_keyboard,
)
from app.bot.utils.evening_report_flow import (
    build_report_text,
    calc_final_cash,
    draft_from_data,
    draft_snapshot,
    parse_money,
    parse_nf,
    refresh_report_panel,
    show_extra_manage_panel,
    show_field_prompt,
    show_report_panel,
    sync_draft_to_state,
)
from app.services.evening_report_service import load_or_create_draft, save_report

logger = logging.getLogger(__name__)
router = Router()


class EveningReport(StatesGroup):
    panel = State()
    waiting_for_field = State()
    waiting_for_nf = State()
    waiting_for_extra_name = State()
    waiting_for_extra_amount = State()


async def _init_report_state(state: FSMContext, for_date: date | None = None) -> None:
    report_date = for_date or date.today()

    def _load():
        return load_or_create_draft(report_date)

    draft = await asyncio.to_thread(_load)
    saved = draft_snapshot(draft)
    await state.update_data(
        **sync_draft_to_state(draft),
        er_saved_snapshot=saved,
        er_current_field=None,
        er_extra_kind=None,
        er_extra_name=None,
    )


@router.callback_query(F.data == "evening_report_start")
async def evening_report_start(callback: CallbackQuery, state: FSMContext):
    await _init_report_state(state)
    await state.set_state(EveningReport.panel)
    await show_report_panel(callback, state)
    await callback.answer()


@router.callback_query(StateFilter(EveningReport), F.data == "er_back_panel")
async def er_back_panel(callback: CallbackQuery, state: FSMContext):
    await state.set_state(EveningReport.panel)
    await show_report_panel(callback, state)
    await callback.answer()


@router.callback_query(StateFilter(EveningReport), F.data.startswith("er_field_"))
async def er_field_select(callback: CallbackQuery, state: FSMContext):
    field_key = callback.data.removeprefix("er_field_")
    await state.update_data(er_current_field=field_key)

    if field_key == "nf":
        await state.set_state(EveningReport.waiting_for_nf)
    else:
        await state.set_state(EveningReport.waiting_for_field)

    await show_field_prompt(callback, state, field_key)
    await callback.answer()


@router.callback_query(StateFilter(EveningReport), F.data.startswith("er_skip_"))
async def er_field_skip(callback: CallbackQuery, state: FSMContext):
    field_key = callback.data.removeprefix("er_skip_")
    update: dict = {}

    if field_key == "notes":
        update["er_notes_text"] = None
    elif field_key == "nf":
        update["er_nf_primary"] = None
        update["er_nf_secondary"] = None
    else:
        update[f"er_{field_key}"] = None

    await state.update_data(**update)
    await state.set_state(EveningReport.panel)
    await show_report_panel(callback, state, hint="Поле очищено.")
    await callback.answer()


@router.message(EveningReport.waiting_for_field, F.text)
async def er_receive_field(message: Message, state: FSMContext):
    data = await state.get_data()
    field_key = data.get("er_current_field")

    if field_key == "notes":
        await state.update_data(er_notes_text=message.text.strip() or None)
    else:
        try:
            value = parse_money(message.text)
        except ValueError:
            await message.reply("❌ Пожалуйста, введите число.")
            return
        await state.update_data(**{f"er_{field_key}": value})

    await state.set_state(EveningReport.panel)
    data = await state.get_data()
    if data.get("er_panel_message_id"):
        await refresh_report_panel(message.bot, message.chat.id, state, hint="✅ Значение обновлено.")
    else:
        await show_report_panel(message, state, hint="✅ Значение обновлено.")


@router.message(EveningReport.waiting_for_nf, F.text)
async def er_receive_nf(message: Message, state: FSMContext):
    try:
        primary, secondary = parse_nf(message.text)
    except ValueError:
        await message.reply("❌ Введите два числа, например: 297000 3600 или 297000 (3600)")
        return

    await state.update_data(er_nf_primary=primary, er_nf_secondary=secondary)
    await state.set_state(EveningReport.panel)
    data = await state.get_data()
    if data.get("er_panel_message_id"):
        await refresh_report_panel(message.bot, message.chat.id, state, hint="✅ НФ обновлено.")
    else:
        await show_report_panel(message, state, hint="✅ НФ обновлено.")


@router.callback_query(StateFilter(EveningReport), F.data == "er_extra_manage")
async def er_extra_manage(callback: CallbackQuery, state: FSMContext):
    await show_extra_manage_panel(callback, state)
    await callback.answer()


@router.callback_query(StateFilter(EveningReport), F.data == "er_extra_add")
async def er_extra_add(callback: CallbackQuery, state: FSMContext):
    from aiogram.types import InlineKeyboardMarkup

    await callback.message.edit_text(
        "Выберите тип:",
        reply_markup=get_evening_report_extra_kind_keyboard(),
    )
    await callback.answer()


@router.callback_query(StateFilter(EveningReport), F.data.startswith("er_extra_kind_"))
async def er_extra_kind(callback: CallbackQuery, state: FSMContext):
    kind = callback.data.removeprefix("er_extra_kind_")
    await state.update_data(er_extra_kind=kind)
    await state.set_state(EveningReport.waiting_for_extra_name)
    await callback.message.edit_text(
        f"Введите название ({'расход' if kind == 'expense' else 'приход'}):",
        reply_markup=get_evening_report_extra_name_keyboard(),
    )
    await callback.answer()


@router.message(EveningReport.waiting_for_extra_name, F.text)
async def er_receive_extra_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if not name:
        await message.reply("❌ Название не может быть пустым.")
        return
    await state.update_data(er_extra_name=name)
    await state.set_state(EveningReport.waiting_for_extra_amount)
    await message.reply(f"Введите сумму для «{name}»:")


@router.message(EveningReport.waiting_for_extra_amount, F.text)
async def er_receive_extra_amount(message: Message, state: FSMContext):
    try:
        amount = parse_money(message.text)
    except ValueError:
        await message.reply("❌ Пожалуйста, введите число.")
        return

    data = await state.get_data()
    extra_items = list(data.get("er_extra_items") or [])
    extra_items.append(
        {
            "name": data.get("er_extra_name", "—"),
            "amount": amount,
            "kind": data.get("er_extra_kind", "expense"),
        }
    )
    await state.update_data(er_extra_items=extra_items, er_extra_name=None, er_extra_kind=None)
    await state.set_state(EveningReport.panel)
    if data.get("er_panel_message_id"):
        await refresh_report_panel(message.bot, message.chat.id, state, hint="✅ Строка добавлена.")
    else:
        await show_report_panel(message, state, hint="✅ Строка добавлена.")


@router.callback_query(StateFilter(EveningReport), F.data.startswith("er_del_extra_"))
async def er_del_extra(callback: CallbackQuery, state: FSMContext):
    index = int(callback.data.removeprefix("er_del_extra_"))
    data = await state.get_data()
    extra_items = list(data.get("er_extra_items") or [])
    if 0 <= index < len(extra_items):
        extra_items.pop(index)
        await state.update_data(er_extra_items=extra_items)
    await show_extra_manage_panel(callback, state)
    await callback.answer("Удалено")


@router.callback_query(StateFilter(EveningReport), F.data == "evening_report_save")
async def evening_report_save(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    draft = draft_from_data(data)

    if draft.get("morning_cash") is None or draft.get("day_cash") is None:
        await callback.answer("Укажите «Касса на утро» и «За день»", show_alert=True)
        return

    report_text = build_report_text(draft)
    final_cash = calc_final_cash(draft)

    def _save():
        record = save_report(draft, report_text=report_text, final_cash=final_cash)
        return record.id

    try:
        report_id = await asyncio.to_thread(_save)
        await state.update_data(
            er_report_id=report_id,
            er_saved_snapshot=draft_snapshot(draft),
        )
        await show_report_panel(callback, state, hint="✅ Отчёт сохранён.")
        await callback.answer("Сохранено")
    except Exception as e:
        logger.error("Error saving evening report: %s", e)
        await callback.answer("❌ Ошибка сохранения", show_alert=True)


@router.callback_query(StateFilter(EveningReport), F.data == "evening_report_copy")
async def evening_report_copy(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    draft = draft_from_data(data)
    report_text = build_report_text(draft)
    if not report_text:
        await callback.answer("Отчёт пуст — нечего копировать", show_alert=True)
        return
    await callback.message.reply(
        report_text,
        reply_markup=get_evening_report_copy_keyboard(),
    )
    await callback.answer("Текст отправлен отдельным сообщением")


@router.callback_query(F.data == "evening_report_delete_copy")
async def evening_report_delete_copy(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer()


@router.callback_query(StateFilter(EveningReport), F.data == "evening_report_cancel")
async def evening_report_cancel(callback: CallbackQuery, state: FSMContext):
    from app.bot.handlers.product_management import show_archived_products

    await state.clear()
    await show_archived_products(callback.message, state=state)
    await callback.answer()
