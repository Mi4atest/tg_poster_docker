"""Публичная ссылка на пост канала MAX вида https://max.ru/c/<chat>/<slug>.

В API поле ``url`` часто содержит ``.../mid.<hash>``, тогда клиент MAX показывает
«Ссылка не найдена». Короткий slug как в «Копировать ссылку» строится из ``seq``
(числовой порядковый id поста), см. https://vc.ru/id5849587/2846018-kak-poluchit-silku-na-post-v-max-cherez-api
"""
from __future__ import annotations

import base64
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_URL_KEYS = frozenset({"url", "link", "permalink", "publicUrl", "shareUrl"})
_SEQ_KEYS = frozenset({"seq", "max_mid", "maxMid", "sequence"})


def seq_to_max_share_slug(seq: int) -> str:
    """Кодирует seq в хвост URL (base64url, 8 байт big-endian, без padding)."""
    if seq < 0:
        raise ValueError("seq must be non-negative")
    try:
        raw = seq.to_bytes(8, "big", signed=False)
    except OverflowError:
        nb = (seq.bit_length() + 7) // 8
        raw = seq.to_bytes(nb, "big", signed=False)[-8:]
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def build_max_channel_share_url(chat_id: str, seq: int) -> str:
    cid = str(chat_id).strip()
    return f"https://max.ru/c/{cid}/{seq_to_max_share_slug(seq)}"


def _parse_seq(val: Any) -> Optional[int]:
    if val is None:
        return None
    if isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        # JSON мог отдать float — только если целое в безопасном диапазоне
        if val.is_integer() and abs(val) < 2**53:
            return int(val)
        return None
    if isinstance(val, str):
        s = val.strip()
        if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
            try:
                return int(s)
            except ValueError:
                return None
    return None


def extract_seq_from_max_api_payload(payload: Optional[Dict[str, Any]]) -> Optional[int]:
    """Ищет seq / max_mid в дереве ответа Max API (часто на верхнем уровне сообщения)."""
    if not isinstance(payload, dict):
        return None
    found: List[int] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for k in _SEQ_KEYS:
                if k in obj:
                    p = _parse_seq(obj.get(k))
                    if p is not None:
                        found.append(p)
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for it in obj:
                walk(it)

    walk(payload)
    return found[0] if found else None


def _collect_max_urls(payload: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(payload, dict):
        return []
    out: List[str] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for key, val in obj.items():
                if key in _URL_KEYS and isinstance(val, str):
                    v = val.strip()
                    low = v.lower()
                    if low.startswith("http") and "max.ru" in low:
                        out.append(v)
                else:
                    walk(val)
        elif isinstance(obj, list):
            for it in obj:
                walk(it)

    walk(payload)
    return out


def _last_path_segment(url: str) -> str:
    return url.rstrip("/").rsplit("/", maxsplit=1)[-1] if url else ""


def _is_mid_segment(url: str) -> bool:
    seg = _last_path_segment(url)
    return seg.lower().startswith("mid.") or "/mid." in url.lower()


def pick_best_max_public_url(payloads: List[Optional[Dict[str, Any]]]) -> Optional[str]:
    """Выбирает лучший уже готовый URL из ответов API (без mid в последнем сегменте)."""
    all_urls: List[str] = []
    for p in payloads:
        all_urls.extend(_collect_max_urls(p))
    if not all_urls:
        return None
    for u in all_urls:
        low = u.lower()
        if "web.max.ru" in low:
            continue
        if "max.ru/c/" in low and not _is_mid_segment(u):
            return u
    for u in all_urls:
        if "max.ru/c/" in u.lower() and not _is_mid_segment(u):
            return u
    return all_urls[0]


def resolve_max_channel_share_url(
    chat_id: str, *payloads: Optional[Dict[str, Any]]
) -> Optional[str]:
    """
    Итоговая публичная ссылка: сначала из seq (как в клиенте MAX), иначе лучший url из JSON.
    URL с ``.../mid.`` в конце в MAX-клиенте часто не открывается — такие не возвращаем.
    """
    plist = [p for p in payloads if isinstance(p, dict)]
    for p in plist:
        seq = extract_seq_from_max_api_payload(p)
        if seq is not None:
            try:
                return build_max_channel_share_url(chat_id, seq)
            except (OverflowError, ValueError) as e:
                logger.warning("Max share: seq=%s не закодирован в slug: %s", seq, e)
    picked = pick_best_max_public_url(plist)
    if picked and not _is_mid_segment(picked):
        return picked
    return None
