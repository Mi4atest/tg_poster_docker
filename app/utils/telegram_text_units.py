"""Длина текста в единицах UTF-16 (как в Bot API Telegram)."""


def telegram_text_units(text: str) -> int:
    return len((text or "").encode("utf-16-le")) // 2
