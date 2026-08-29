"""Нормализация короткого запроса для оценки рынка iPhone."""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.utils.iphone_parser import IPHONE_MODELS


SUPPORTED_MEMORY_GB = frozenset({16, 32, 64, 128, 256, 512, 1024})


class MarketQueryError(ValueError):
    """Запрос нельзя однозначно преобразовать в модель и объём памяти."""


@dataclass(frozen=True)
class IphoneMarketQuery:
    model: str
    memory_gb: int
    raw: str = ""

    @property
    def display_memory(self) -> str:
        return "1 ТБ" if self.memory_gb == 1024 else f"{self.memory_gb} ГБ"

    @property
    def display_name(self) -> str:
        return f"{self.model} {self.display_memory}"

    @property
    def cache_key(self) -> str:
        return f"{self.model.lower()}:{self.memory_gb}"

    @property
    def search_text(self) -> str:
        memory = "1 ТБ" if self.memory_gb == 1024 else f"{self.memory_gb} ГБ"
        return f"{self.model} {memory}"


_WORD_REPLACEMENTS = (
    (r"\bмини\b", "mini"),
    (r"\bпро\s+макс\b", "pro max"),
    (r"\bпро\b", "pro"),
    (r"\bплюс\b", "plus"),
    (r"\bэйр\b", "air"),
)


def _normalize_words(value: str) -> str:
    normalized = value.lower().replace("ё", "е")
    for pattern, replacement in _WORD_REPLACEMENTS:
        normalized = re.sub(pattern, replacement, normalized)
    normalized = re.sub(r"[(),;/_-]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _extract_memory(value: str) -> tuple[int, str]:
    tb_match = re.search(r"\b1\s*(?:tb|тб)\b", value, re.IGNORECASE)
    if tb_match:
        return 1024, f"{value[:tb_match.start()]} {value[tb_match.end():]}".strip()

    unit_match = re.search(r"\b(\d{2,4})\s*(?:gb|гб)\b", value, re.IGNORECASE)
    if unit_match:
        memory = int(unit_match.group(1))
        remainder = f"{value[:unit_match.start()]} {value[unit_match.end():]}".strip()
        return memory, remainder

    bare_match = re.search(r"\b(\d{2,4})\s*$", value)
    if bare_match:
        memory = int(bare_match.group(1))
        return memory, value[:bare_match.start()].strip()

    raise MarketQueryError("Не указан объём памяти, например 128 или 128 ГБ.")


def _model_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for canonical, info in IPHONE_MODELS.items():
        candidates = [canonical, *info.get("variants", [])]
        for candidate in candidates:
            alias = _normalize_words(candidate)
            aliases[alias] = canonical
            if alias.startswith("iphone "):
                aliases[alias.removeprefix("iphone ").strip()] = canonical
    return aliases


_MODEL_ALIASES = _model_aliases()


def parse_iphone_market_query(raw: str) -> IphoneMarketQuery:
    """Разобрать запросы вида ``13 мини 128`` и ``iPhone 13 mini 128 ГБ``."""
    if not raw or not raw.strip():
        raise MarketQueryError("Напишите модель и память, например: 13 mini 128.")

    normalized = _normalize_words(raw)
    normalized = re.sub(r"^(?:apple\s+)?", "", normalized).strip()
    memory, model_part = _extract_memory(normalized)
    if memory not in SUPPORTED_MEMORY_GB:
        raise MarketQueryError("Не удалось распознать объём памяти iPhone.")

    model_part = re.sub(r"\b(?:бу|б\s*у|used)\b", "", model_part)
    model_part = re.sub(r"\s+", " ", model_part).strip()
    model = _MODEL_ALIASES.get(model_part)
    if model is None:
        raise MarketQueryError(
            "Не удалось распознать модель. Пример корректного запроса: 13 mini 128."
        )

    return IphoneMarketQuery(model=model, memory_gb=memory, raw=raw.strip())
