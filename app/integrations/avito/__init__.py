"""Интеграция с API Авито (OAuth, чтение объявления, обновление цены, архив)."""

from app.integrations.avito.parse import parse_avito_item_ref, ParsedAvitoRef

__all__ = ["parse_avito_item_ref", "ParsedAvitoRef"]
