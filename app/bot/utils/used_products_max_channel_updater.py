"""
Обновление сообщений в канале Max со списком б/у товаров.
Тот же список, что в Telegram-каталоге, со ссылками на посты в Max.
Редактируются только существующие сообщения по max_used_products_list_message_ids.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.db.database import SessionLocal
from app.db.product_queries import fetch_used_products_for_list
from app.integrations.max.client import create_max_api_client, extract_message_id
from app.bot.utils.product_list_formatter import format_full_products_list
from app.bot.utils.used_products_lists import RESERVED_SLOT_TEXT
from app.services.settings_service import get_settings_service
from app.utils.max_share_link import resolve_max_channel_share_url
from app.utils.product_formatter import format_product_name_for_list

logger = logging.getLogger(__name__)

MAX_MESSAGE_MAX_LENGTH = 3900
MAX_USED_CATALOG_RESERVED_COUNT = 15
# Официально не больше 2 правок/отправки в секунду на один канал.
# Глобально у бота до 30 rps: один токен на несколько серверов делит этот бюджет,
# но лимит 2/с считается отдельно на каждый chat_id.
_CHANNEL_WRITE_MIN_INTERVAL = 1.0
_write_lock = asyncio.Lock()
_last_write_at = 0.0
_reserve_lock = asyncio.Lock()


def _product_max_href(product: Dict[str, Any]) -> Optional[str]:
    """Только рабочая публичная ссылка max.ru/c/... (без mid.hash)."""
    share = str(product.get("max_share_url") or "").strip()
    if not share:
        return None
    low = share.lower()
    if not low.startswith(("http://", "https://")):
        return None
    if "/mid." in low or low.rstrip("/").split("/")[-1].startswith("mid."):
        return None
    return share


def _with_max_name_links(products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for product in products:
        row = dict(product)
        row["max_list_href"] = _product_max_href(product)
        out.append(row)
    return out


def _get_used_products_from_db() -> List[Dict[str, Any]]:
    db = SessionLocal()
    try:
        return _with_max_name_links(fetch_used_products_for_list(db))
    finally:
        db.close()


def _format_product_line(product: Dict[str, Any]) -> str:
    formatted_name = format_product_name_for_list(product.get("name", "Без названия"))
    price = product.get("price", "") or "Цена не указана"
    max_href = product.get("max_list_href") or _product_max_href(product)
    vk_link = product.get("vk_product_link")
    line_parts = []
    if max_href:
        line_parts.append(f'<a href="{max_href}">{formatted_name}</a>')
    else:
        line_parts.append(formatted_name)
    line_parts.append(f"- {price}")
    if vk_link:
        line_parts.append(f'<a href="{vk_link}">ВК</a>')
    return " ".join(line_parts)


def _msk_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=3)


def _build_header(total: int, updated_at: Optional[datetime] = None) -> str:
    dt = updated_at or _msk_now()
    return (
        f"<b>📦 Каталог б/у ({total})</b>\n"
        f"Обновлено {dt.strftime('%d.%m.%y')} в {dt.strftime('%H:%M')}\n"
    )


def _published_at_to_utc_naive(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _build_today_block(products: List[Dict[str, Any]]) -> str:
    """Товары, опубликованные в Max за последние 24 часа."""
    now_utc = datetime.now(timezone.utc)
    cutoff_utc = (now_utc - timedelta(hours=24)).replace(tzinfo=None)
    today_products = [
        p for p in products
        if p.get("published_max_at")
        and _published_at_to_utc_naive(p["published_max_at"]) is not None
        and _published_at_to_utc_naive(p["published_max_at"]) >= cutoff_utc
    ]
    if not today_products:
        return "<blockquote><b>🆕 Новинки:</b>\n—</blockquote>\n"
    lines = ["<b>🆕 Новинки:</b>"]
    for p in today_products:
        lines.append(f"<b>🆕 {_format_product_line(p)}</b>")
    return "<blockquote>" + "\n".join(lines) + "\n</blockquote>\n"


def _build_full_text(products: List[Dict[str, Any]]) -> str:
    total = len(products)
    header = _build_header(total)
    today_block = _build_today_block(products)
    separator = "━━━━━━━━━━━━━━\n"
    full_list = format_full_products_list(products, name_link_key="max_list_href")
    if full_list.startswith("📦 Список товаров пуст") or full_list.startswith("📦 Нет других"):
        full_list = ""
    return header + today_block + separator + full_list


def split_text_into_chunks(full_text: str, max_len: int = MAX_MESSAGE_MAX_LENGTH) -> List[str]:
    if not full_text or len(full_text) <= max_len:
        return [full_text] if full_text else []
    chunks: List[str] = []
    rest = full_text
    while rest:
        if len(rest) <= max_len:
            chunks.append(rest)
            break
        block = rest[:max_len]
        last_newline = block.rfind("\n")
        if last_newline > max_len // 2:
            cut = last_newline + 1
        else:
            cut = max_len
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip("\n")
    return chunks


async def _wait_channel_write_slot() -> None:
    global _last_write_at
    async with _write_lock:
        elapsed = time.monotonic() - _last_write_at
        if elapsed < _CHANNEL_WRITE_MIN_INTERVAL:
            await asyncio.sleep(_CHANNEL_WRITE_MIN_INTERVAL - elapsed)
        _last_write_at = time.monotonic()


async def _edit_catalog_message(client, message_id: str, text: str, *, chat_id: str = "") -> bool:
    await _wait_channel_write_slot()
    try:
        await client.edit_message_text(
            chat_id=chat_id or "",
            message_id=message_id,
            text=text or RESERVED_SLOT_TEXT,
            parse_mode="html",
        )
        return True
    except Exception as exc:
        logger.warning(
            "Max catalog: не удалось править message_id=%s chat_id=%s: %s",
            message_id,
            chat_id or "?",
            exc,
        )
        return False


def _chat_title_from_payload(payload: Optional[dict]) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("title", "name", "chat_title"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    chat = payload.get("chat") or payload.get("result")
    if isinstance(chat, dict):
        return _chat_title_from_payload(chat)
    return ""


async def fetch_max_catalog_target() -> dict[str, str]:
    """Куда этот инстанс будет постить каталог (канал из настроек ЭТОГО сервера)."""
    service = get_settings_service()
    chat_id = (service.get_max_channel_id() or "").strip()
    title = ""
    if chat_id:
        try:
            info = await create_max_api_client().get_chat(chat_id)
            title = _chat_title_from_payload(info)
        except Exception as exc:
            logger.warning("Max catalog: не удалось прочитать канал %s: %s", chat_id, exc)
    return {"chat_id": chat_id, "title": title}


async def update_used_products_list_in_max_channel() -> bool:
    """
    Обновляет зарезервированные сообщения каталога б/у в канале Max.
    Правит только mid из настроек ЭТОГО проекта — чужой канал того же бота не трогает.
    """
    service = get_settings_service()
    chat_id = (service.get_max_channel_id() or "").strip()
    message_ids = list(service.get_max_used_products_list_message_ids() or [])
    if not chat_id:
        logger.debug("USED_PRODUCTS_LIST_MAX: MAX_CHANNEL_ID not set, skip")
        return False
    if not message_ids:
        logger.debug("USED_PRODUCTS_LIST_MAX: message ids not set, skip")
        return False

    products = await asyncio.to_thread(_get_used_products_from_db)
    full_text = _build_full_text(products)
    chunks = split_text_into_chunks(full_text)
    if not chunks:
        chunks = [RESERVED_SLOT_TEXT]

    if len(chunks) > len(message_ids):
        logger.warning(
            "Max catalog: текст на %s частей, зарезервировано только %s — хвост не поместится",
            len(chunks),
            len(message_ids),
        )

    logger.info(
        "Max catalog refresh: chat_id=%s slots=%s parts=%s",
        chat_id,
        len(message_ids),
        len(chunks),
    )
    client = create_max_api_client()
    edited = 0
    for i, mid in enumerate(message_ids):
        chunk = chunks[i] if i < len(chunks) else RESERVED_SLOT_TEXT
        if await _edit_catalog_message(client, str(mid), chunk, chat_id=chat_id):
            edited += 1
    return edited > 0


async def reserve_max_used_catalog_messages(
    count: int = MAX_USED_CATALOG_RESERVED_COUNT,
    *,
    force: bool = False,
    append: bool = False,
) -> List[str]:
    """
    Публикует `count` текстовых заглушек в MAX_CHANNEL_ID ЭТОГО сервера
    и сохраняет mid в его БД. Чужой канал того же бота не используется.

    Если ID уже есть и force=False и append=False — ничего не постит.
    append=True — дописывает новые слоты к текущим ID.
    force=True — создаёт новые слоты и заменяет список ID (старые посты в канале остаются).
    """
    if count < 1 or count > 30:
        raise RuntimeError("Число слотов должно быть от 1 до 30")
    if force and append:
        raise RuntimeError("Нельзя одновременно force и append")

    service = get_settings_service()
    chat_id = (service.get_max_channel_id() or "").strip()
    if not chat_id:
        raise RuntimeError("MAX_CHANNEL_ID не задан")

    existing = list(service.get_max_used_products_list_message_ids() or [])
    if existing and not force and not append:
        logger.info(
            "Max catalog: chat_id=%s уже зарезервировано %s, skip",
            chat_id,
            len(existing),
        )
        return existing

    logger.info(
        "Max catalog reserve: chat_id=%s count=%s append=%s force=%s existing=%s",
        chat_id,
        count,
        append,
        force,
        len(existing),
    )
    client = create_max_api_client()
    await client.get_me()
    await client.get_chat(chat_id)

    new_ids: List[str] = []
    first_payloads: list = []
    for i in range(count):
        await _wait_channel_write_slot()
        resp = await client.send_message(
            chat_id,
            RESERVED_SLOT_TEXT,
            parse_mode="html",
            disable_web_page_preview=True,
        )
        mid = extract_message_id(resp)
        if not mid:
            raise RuntimeError(f"Max catalog: не получен message_id у слота {i + 1}/{count}: {resp}")
        new_ids.append(mid)
        if i == 0:
            first_payloads.append(resp)
            try:
                first_payloads.append(await client.get_message(mid))
            except Exception as fetch_err:
                logger.warning("Max catalog: GET первого слота %s: %s", mid, fetch_err)
        logger.info(
            "Max catalog: chat_id=%s слот %s/%s mid=%s",
            chat_id,
            i + 1,
            count,
            mid,
        )

    ids = (existing + new_ids) if append else new_ids
    service.set_max_used_products_list_message_ids(ids)

    share = resolve_max_channel_share_url(chat_id, *first_payloads)
    if share and not append and (force or not service.get_max_used_catalog_url()):
        service.update({"signatures": {"max_used_catalog_url": share}})
        logger.info("Max catalog: URL каталога в постах = %s", share)

    return ids


async def reserve_and_fill_max_used_catalog(
    count: int,
    *,
    force: bool = False,
    append: bool = False,
) -> tuple[List[str], bool]:
    """Резерв слотов + заполнение каталога. Один проход за раз."""
    if _reserve_lock.locked():
        raise RuntimeError("Резерв каталога Max уже выполняется")
    async with _reserve_lock:
        ids = await reserve_max_used_catalog_messages(count, force=force, append=append)
        ok = await update_used_products_list_in_max_channel()
        return ids, ok
