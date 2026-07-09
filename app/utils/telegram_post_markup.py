"""Утилиты URL для блока «Каталог б/у» в тексте постов."""


def normalize_catalog_url(raw: str) -> str:
    return (raw or "").strip()


def is_valid_catalog_button_url(raw: str) -> bool:
    """Допустимы только http(s) с непустым host."""
    from urllib.parse import urlparse

    u = urlparse(normalize_catalog_url(raw))
    return u.scheme in ("http", "https") and bool(u.netloc)
