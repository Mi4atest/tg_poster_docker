"""Evening report panel: text formatting, draft state, UI refresh."""
from __future__ import annotations

import html
import re
from copy import deepcopy
from datetime import date, datetime
from typing import Any, Optional, Union

from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards.evening_report_keyboard import (
    get_evening_report_copy_keyboard,
    get_evening_report_extra_manage_keyboard,
    get_evening_report_field_prompt_keyboard,
    get_evening_report_panel_keyboard,
)

MONEY_FIELD_LABELS: dict[str, str] = {
    "morning_cash": "Касса на утро",
    "day_cash": "За день",
    "bn": "БН",
    "new_advance": "Новый аванс",
    "old_advance": "Старый аванс",
    "surrendered": "Сдано",
    "buybacks": "Выкупы",
    "wholesale": "Опт",
    "credit": "Кредит",
}

MONEY_FIELD_LINES: dict[str, str] = {
    "morning_cash": "касса на утро",
    "day_cash": "за день",
    "bn": "бн",
    "new_advance": "новый аванс",
    "old_advance": "старый аванс",
    "surrendered": "сдано",
    "buybacks": "выкупы",
    "wholesale": "опт",
    "credit": "кредит",
}

MONTH_NAMES = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}


def _escape_md(text: str) -> str:
    for ch in ("\\", "_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


def parse_money(text: str) -> float:
    cleaned = text.strip().replace(" ", "").replace(",", ".")
    return float(cleaned)


def parse_nf(text: str) -> tuple[float, float]:
    """Парсит «297000 3600» или «297000 (3600)»."""
    raw = text.strip()
    paren = re.match(r"^([\d\s.,]+)\s*\(([\d\s.,]+)\)\s*$", raw)
    if paren:
        return parse_money(paren.group(1)), parse_money(paren.group(2))
    parts = raw.split()
    if len(parts) != 2:
        raise ValueError("expected two numbers")
    return parse_money(parts[0]), parse_money(parts[1])


def draft_from_data(data: dict) -> dict[str, Any]:
    return {
        "report_id": data.get("er_report_id"),
        "report_date": data.get("er_report_date"),
        "notes_text": data.get("er_notes_text"),
        "morning_cash": data.get("er_morning_cash"),
        "day_cash": data.get("er_day_cash"),
        "bn": data.get("er_bn"),
        "new_advance": data.get("er_new_advance"),
        "old_advance": data.get("er_old_advance"),
        "surrendered": data.get("er_surrendered"),
        "buybacks": data.get("er_buybacks"),
        "wholesale": data.get("er_wholesale"),
        "credit": data.get("er_credit"),
        "nf_primary": data.get("er_nf_primary"),
        "nf_secondary": data.get("er_nf_secondary"),
        "extra_items": list(data.get("er_extra_items") or []),
    }


def draft_snapshot(draft: dict[str, Any]) -> dict[str, Any]:
    snap = deepcopy(draft)
    snap.pop("report_id", None)
    return snap


def has_unsaved_changes(data: dict) -> bool:
    current = draft_snapshot(draft_from_data(data))
    saved = data.get("er_saved_snapshot") or {}
    return current != saved


def calc_final_cash(draft: dict[str, Any]) -> float:
    def _v(key: str) -> float:
        val = draft.get(key)
        return float(val) if val is not None else 0.0

    expenses = sum(
        float(item.get("amount", 0))
        for item in draft.get("extra_items") or []
        if item.get("kind") == "expense"
    )
    incomes = sum(
        float(item.get("amount", 0))
        for item in draft.get("extra_items") or []
        if item.get("kind") == "income"
    )

    return (
        _v("morning_cash")
        + _v("day_cash")
        - _v("bn")
        - _v("credit")
        + _v("new_advance")
        - _v("old_advance")
        - _v("surrendered")
        - _v("buybacks")
        + _v("wholesale")
        - expenses
        + incomes
    )


def build_report_text(draft: dict[str, Any]) -> str:
    lines: list[str] = []

    notes = (draft.get("notes_text") or "").strip()
    if notes:
        lines.append(notes)
        lines.append("")

    for key in ("morning_cash", "day_cash"):
        val = draft.get(key)
        if val is not None:
            lines.append(f"{MONEY_FIELD_LINES[key]} {val:.0f}")

    for key in ("bn", "wholesale", "surrendered", "buybacks", "new_advance", "old_advance", "credit"):
        val = draft.get(key)
        if val:
            lines.append(f"{MONEY_FIELD_LINES[key]} {val:.0f}")

    for item in draft.get("extra_items") or []:
        name = (item.get("name") or "").strip()
        amount = item.get("amount")
        if name and amount is not None:
            lines.append(f"{name} {float(amount):.0f}")

    nf_primary = draft.get("nf_primary")
    nf_secondary = draft.get("nf_secondary")
    if nf_primary is not None and nf_secondary is not None:
        lines.append(f"нф {nf_primary:.0f} ({nf_secondary:.0f})")

    final_cash = calc_final_cash(draft)
    if draft.get("morning_cash") is not None or draft.get("day_cash") is not None:
        lines.append(f"в кассе {final_cash:.0f}")

    return "\n".join(lines).strip()


def _format_report_date(iso_date: str) -> str:
    d = date.fromisoformat(iso_date)
    month = MONTH_NAMES.get(d.month, str(d.month))
    return f"{d.day} {month} {d.year}"


def format_panel_body(data: dict, *, hint: str = "") -> str:
    draft = draft_from_data(data)
    report_preview = build_report_text(draft)
    if not report_preview:
        preview_block = "<i>(пока пусто — заполните поля ниже)</i>"
    else:
        preview_block = f"<pre>{html.escape(report_preview)}</pre>"

    lines = [
        "📊 <b>Вечерний отчет</b>",
        f"<i>{html.escape(_format_report_date(draft['report_date']))}</i>",
        "",
        preview_block,
        "",
        "Нажмите поле ниже, чтобы изменить.",
    ]
    if has_unsaved_changes(data):
        lines.extend(["", "● <b>Есть несохранённые изменения</b>"])
    if hint:
        lines.extend(["", html.escape(hint)])
    return "\n".join(lines)


def format_field_prompt_body(field_key: str, draft: dict[str, Any]) -> str:
    if field_key == "notes":
        current = (draft.get("notes_text") or "").strip()
        preview = _escape_md(current[:200] + ("…" if len(current) > 200 else "")) if current else "_(пусто)_"
        return (
            "📝 *Заметки*\n\n"
            f"Сейчас: {preview}\n\n"
            "Отправьте текст заметок (можно несколько строк).\n"
            "Или нажмите «Пропустить», чтобы очистить."
        )

    label = MONEY_FIELD_LABELS[field_key]
    current = draft.get(field_key)
    current_str = f"{current:.0f}" if current is not None else "—"
    return (
        f"💰 *{label}*\n\n"
        f"Сейчас: `{current_str}`\n\n"
        "Отправьте число или нажмите «Пропустить»."
    )


def format_nf_prompt_body(draft: dict[str, Any]) -> str:
    primary = draft.get("nf_primary")
    secondary = draft.get("nf_secondary")
    if primary is not None and secondary is not None:
        current = f"{primary:.0f} ({secondary:.0f})"
    else:
        current = "—"
    return (
        "📋 *НФ*\n\n"
        f"Сейчас: `{current}`\n\n"
        "Отправьте два числа, например:\n"
        "`297000 3600` или `297000 (3600)`"
    )


def format_extra_manage_body(extra_items: list) -> str:
    if not extra_items:
        return (
            "➕ *Расходы и приходы*\n\n"
            "Пока ничего не добавлено.\n"
            "Нажмите «Добавить», чтобы указать расход или приход."
        )
    lines = ["➕ *Расходы и приходы*", ""]
    for i, item in enumerate(extra_items, 1):
        kind = "расход" if item.get("kind") == "expense" else "приход"
        name = item.get("name", "—")
        amount = item.get("amount", 0)
        lines.append(f"{i}. {kind}: {name} — {float(amount):.0f}")
    return "\n".join(lines)


async def _edit_ui(
    target: Union[CallbackQuery, Message],
    text: str,
    reply_markup,
    *,
    parse_mode: str = "HTML",
) -> Optional[int]:
    if isinstance(target, CallbackQuery):
        msg = target.message
        try:
            await msg.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            return msg.message_id
        except TelegramBadRequest:
            sent = await msg.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
            return sent.message_id
    sent = await target.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
    return sent.message_id


async def show_report_panel(
    target: Union[CallbackQuery, Message],
    state: FSMContext,
    *,
    hint: str = "",
) -> None:
    data = await state.get_data()
    draft = draft_from_data(data)
    body = format_panel_body(data, hint=hint)
    report_text = build_report_text(draft)
    kb = get_evening_report_panel_keyboard(
        report_text=report_text,
        has_changes=has_unsaved_changes(data),
    )
    msg_id = await _edit_ui(target, body, kb)
    if msg_id is not None:
        await state.update_data(er_panel_message_id=msg_id)


async def show_field_prompt(
    target: Union[CallbackQuery, Message],
    state: FSMContext,
    field_key: str,
) -> None:
    data = await state.get_data()
    draft = draft_from_data(data)
    if field_key == "nf":
        body = format_nf_prompt_body(draft)
    elif field_key == "notes":
        body = format_field_prompt_body("notes", draft)
    else:
        body = format_field_prompt_body(field_key, draft)
    await _edit_ui(target, body, get_evening_report_field_prompt_keyboard(field_key))


async def show_extra_manage_panel(
    target: Union[CallbackQuery, Message],
    state: FSMContext,
) -> None:
    data = await state.get_data()
    draft = draft_from_data(data)
    extra_items = draft.get("extra_items") or []
    body = format_extra_manage_body(extra_items)
    await _edit_ui(target, body, get_evening_report_extra_manage_keyboard(extra_items))


async def refresh_report_panel(
    bot,
    chat_id: int,
    state: FSMContext,
    *,
    hint: str = "",
) -> None:
    data = await state.get_data()
    panel_id = data.get("er_panel_message_id")
    if not panel_id:
        return
    draft = draft_from_data(data)
    body = format_panel_body(data, hint=hint)
    report_text = build_report_text(draft)
    kb = get_evening_report_panel_keyboard(
        report_text=report_text,
        has_changes=has_unsaved_changes(data),
    )
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=panel_id,
            text=body,
            reply_markup=kb,
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass


def sync_draft_to_state(draft: dict[str, Any]) -> dict[str, Any]:
    return {
        "er_report_id": draft.get("report_id"),
        "er_report_date": draft.get("report_date"),
        "er_notes_text": draft.get("notes_text"),
        "er_morning_cash": draft.get("morning_cash"),
        "er_day_cash": draft.get("day_cash"),
        "er_bn": draft.get("bn"),
        "er_new_advance": draft.get("new_advance"),
        "er_old_advance": draft.get("old_advance"),
        "er_surrendered": draft.get("surrendered"),
        "er_buybacks": draft.get("buybacks"),
        "er_wholesale": draft.get("wholesale"),
        "er_credit": draft.get("credit"),
        "er_nf_primary": draft.get("nf_primary"),
        "er_nf_secondary": draft.get("nf_secondary"),
        "er_extra_items": list(draft.get("extra_items") or []),
    }
