"""Тесты поиска постов в архиве (без БД — разбор дат и лимиты)."""

from unittest.mock import MagicMock

import pytest

from app.services.archive_service import (
    POST_SEARCH_LIMIT_MAX,
    parse_search_date,
    query_posts_search,
)


@pytest.mark.parametrize(
    "query,expected",
    [
        ("2025", (True, 2025, None, None)),
        ("0825", (True, 2025, 8, None)),
        ("08.25", (True, 2025, 8, None)),
        ("250825", (True, 2025, 8, 25)),
        ("25.08.25", (True, 2025, 8, 25)),
        ("202508", (True, 2025, 8, None)),
        ("2025.08", (True, 2025, 8, None)),
        ("20250825", (True, 2025, 8, 25)),
        ("2025.08.25", (True, 2025, 8, 25)),
        ("Apple Watch", (False, None, None, None)),
        ("9999", (False, None, None, None)),
    ],
)
def test_parse_search_date(query, expected):
    parts = parse_search_date(query)
    assert (parts.is_date_search, parts.year, parts.month, parts.day) == expected


def test_query_posts_search_empty_query_returns_empty_list():
    db = MagicMock()
    assert query_posts_search(db, "") == []
    assert query_posts_search(db, "   ") == []
    db.query.assert_not_called()


def test_query_posts_search_caps_limit():
    db = MagicMock()
    chain = db.query.return_value.filter.return_value
    chain.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []

    query_posts_search(db, "iphone", limit=9999)
    chain.order_by.return_value.offset.return_value.limit.assert_called_with(POST_SEARCH_LIMIT_MAX)
