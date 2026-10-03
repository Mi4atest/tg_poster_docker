"""Извлечение access_token из вставки с vkhost / oauth.vk.ru blank.html."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote


_ACCESS_TOKEN_RE = re.compile(r"access_token=([^&\s#]+)", re.IGNORECASE)


def extract_vk_access_token(raw: str) -> str:
    """Вернуть чистый токен: из полной ссылки OAuth или как есть.

    Примеры:
    - https://oauth.vk.ru/blank.html#access_token=vk1.a.XXX&expires_in=86400&user_id=1
    - access_token=vk1.a.XXX&expires_in=86400
    - vk1.a.XXX
    """
    text = unquote((raw or "").strip())
    if not text:
        return ""
    if "access_token=" not in text.lower():
        return text

    fragment = text
    if "#" in text:
        fragment = text.split("#", 1)[1]
    elif "?" in text and "access_token=" not in text.split("?", 1)[0].lower():
        fragment = text.split("?", 1)[1]

    # parse_qs не всегда дружит с «голым» куском после #, но для access_token=…&… подходит.
    qs = parse_qs(fragment, keep_blank_values=False)
    token = (qs.get("access_token") or [""])[0].strip()
    if token:
        return token

    match = _ACCESS_TOKEN_RE.search(text)
    if match:
        return unquote(match.group(1)).strip()
    return text
