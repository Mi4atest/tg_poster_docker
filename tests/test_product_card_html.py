"""Сухой прогон карточки б/у-товара (format_product_card_html)."""
from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from app.bot.handlers.product_management import format_product_card_html


def _strip_html(text: str) -> str:
    return re.sub(r"</?i>", "", text).replace("<b>", "").replace("</b>", "")


BASE = {
    "name": "iPhone 13 128Gb Pink 1141",
    "price": "23900₽",
    "category_name": "смартфоны",
    "collection_name": "iPhone б/у",
    "published_at": "2026-07-01T09:35:00+00:00",  # 12:35 МСК
    "vk_product_link": "https://vk.ru/market-1?w=product-1_99",
    "telegram_link": "https://t.me/test/1",
}


def test_active_product_card():
    product = {**BASE, "status": "active", "created_at": "2026-07-01T09:35:00+00:00"}
    text = _strip_html(format_product_card_html(product))
    assert "📦 iPhone 13 128Gb Pink 1141" in text
    assert "💵 Цена: 23900₽" in text
    assert "📂 Категория: смартфоны" in text
    assert "📁 Подборка: iPhone б/у" in text
    assert "📅 с 01.07.2026, 12:35" in text
    assert "✅ Статус: Активен" in text
    assert "дн. в продаже" in text
    assert "Ссылка на товар в ВК" in text


def test_unavailable_product_card():
    product = {
        **BASE,
        "status": "unavailable",
        "archived_at": "2026-07-12T15:45:00+00:00",  # 18:45 МСК
        "created_at": "2026-07-01T09:35:00+00:00",
    }
    text = _strip_html(format_product_card_html(product))
    assert "📅 с 01.07.2026, 12:35" in text
    assert "🚫 Статус: Недоступен" in text
    assert "с 12.07.2026, 18:45 · 12 дн. в продаже" in text


def test_unavailable_without_archived_at_uses_updated_at():
    product = {
        **BASE,
        "status": "unavailable",
        "archived_at": None,
        "updated_at": "2026-07-12T15:45:00+00:00",
        "created_at": "2026-07-01T09:35:00+00:00",
    }
    text = _strip_html(format_product_card_html(product))
    assert "с 12.07.2026, 18:45 · 12 дн. в продаже" in text


def test_price_update_scenario():
    product = {**BASE, "status": "active", "price": "21900₽", "created_at": BASE["published_at"]}
    text = _strip_html(format_product_card_html(product))
    assert "💵 Цена: 21900₽" in text
    assert "📅 с 01.07.2026, 12:35" in text


def test_product_card_with_price_history():
    product = {**BASE, "status": "active", "created_at": BASE["published_at"]}
    history = [
        {
            "old_price": None,
            "new_price": "23900₽",
            "changed_at": "2026-07-01T09:35:00+00:00",
            "source": "publication",
        },
        {
            "old_price": "23900₽",
            "new_price": "21900₽",
            "changed_at": "2026-07-10T09:35:00+00:00",
            "source": "manual",
        },
    ]
    text = format_product_card_html(product, price_history=history)
    assert "<blockquote expandable>" in text
    assert "23900" in text
    assert "21900" in text
    assert "↓" in text


def test_product_card_without_real_price_changes():
    product = {**BASE, "status": "active", "created_at": BASE["published_at"]}
    history = [
        {
            "old_price": None,
            "new_price": "23900₽",
            "changed_at": "2026-07-01T09:35:00+00:00",
            "source": "publication",
        },
    ]
    text = format_product_card_html(product, price_history=history)
    assert "blockquote" not in text


def test_restore_to_active_scenario():
    product = {
        **BASE,
        "status": "active",
        "archived_at": None,
        "created_at": BASE["published_at"],
    }
    text = _strip_html(format_product_card_html(product))
    assert "✅ Статус: Активен" in text
    assert "🚫" not in text


@pytest.mark.parametrize(
    "scenario",
    [
        "Список б/у → клик",
        "Архив → клик",
        "Товар недоступен → подтверждение",
        "Восстановить",
        "Смена цены → сохранение",
        "Смена цены → Назад",
    ],
)
def test_same_helper_all_scenarios(scenario: str):
    """Все сценарии используют один хелпер — структура карточки одинакова."""
    if "Архив" in scenario or "недоступен" in scenario:
        product = {
            **BASE,
            "status": "unavailable",
            "archived_at": "2026-07-12T15:45:00+00:00",
            "created_at": BASE["published_at"],
        }
    else:
        product = {**BASE, "status": "active", "created_at": BASE["published_at"]}
    if "цены" in scenario and "сохранение" in scenario:
        product["price"] = "21900₽"

    text = format_product_card_html(product)
    assert text.startswith("📦 <b>")
    assert "📅 с" in text
    assert "Статус:" in text
    assert "💵 Цена:" in text
