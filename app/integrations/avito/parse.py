"""Парсинг ссылки или строки с id объявления Авито."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class ParsedAvitoRef:
    item_id: Optional[str]
    canonical_url: Optional[str]


def parse_avito_item_ref(raw: str) -> ParsedAvitoRef:
    """
    Извлекает числовой id объявления из URL Авито или из «голого» id.

    Поддерживаются варианты:
    - https://www.avito.ru/.../1234567890 или .../slug_1234567890
    - https://m.avito.ru/.../1234567890
    - ссылки с query: ?id=…, &itemId=… (как в приложении / «поделиться»)
    - 1234567890
    - если в тексте есть avito.*, но id не в path — последняя группа из 9–12 цифр (частые id объявлений)
    """
    text = (raw or "").strip()
    if not text:
        return ParsedAvitoRef(item_id=None, canonical_url=None)

    if re.fullmatch(r"\d{6,}", text):
        return ParsedAvitoRef(item_id=text, canonical_url=f"https://www.avito.ru/{text}")

    # Параметры запроса (мобильное приложение, редиректы)
    mq = re.search(
        r"(?:\?|&)(?:id|item_id|itemId|itemid)=(\d{6,})(?:&|#|$)",
        text,
        re.I,
    )
    if mq:
        item_id = mq.group(1)
        base = text.split("?", 1)[0].rstrip("/")
        return ParsedAvitoRef(item_id=item_id, canonical_url=base or f"https://www.avito.ru/{item_id}")

    # Из URL: сегмент пути из одних цифр (часто …/1234567890)
    m = re.search(r"/(\d{6,})(?:[/?#]|$)", text)
    if m:
        item_id = m.group(1)
        return ParsedAvitoRef(item_id=item_id, canonical_url=text.split("?", 1)[0].rstrip("/"))

    # Слаг …_1234567890 в конце сегмента
    m2 = re.search(r"_(\d{6,})(?:[/?#]|$)", text)
    if m2:
        item_id = m2.group(1)
        return ParsedAvitoRef(item_id=item_id, canonical_url=text.split("?", 1)[0].rstrip("/"))

    # Короткие ссылки / редкий формат: в строке есть домен Авито и отдельное 9–12-значное число
    if re.search(r"avito\.(ru|com|page|app)", text, re.I):
        nums = re.findall(r"\d{9,12}", text)
        if nums:
            item_id = nums[-1]
            return ParsedAvitoRef(item_id=item_id, canonical_url=f"https://www.avito.ru/{item_id}")

    return ParsedAvitoRef(item_id=None, canonical_url=None)
