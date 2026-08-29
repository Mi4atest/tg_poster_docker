"""Тесты клиента SPFA и URL-конвертации."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.integrations.avito.market_search import build_market_web_url
from app.integrations.avito.spfa_client import SpfaClient, SpfaCookies, validate_proxy_for_mobile
from app.utils.iphone_market_query import parse_iphone_market_query


def test_validate_proxy_for_mobile_requires_auth():
    with pytest.raises(Exception):
        validate_proxy_for_mobile("")
    with pytest.raises(Exception):
        validate_proxy_for_mobile("1.2.3.4:8000")
    assert validate_proxy_for_mobile("user:pass@1.2.3.4:8000").startswith("user:")


def test_spfa_cookie_cache_roundtrip(tmp_path: Path):
    cache = tmp_path / "cookies.json"
    client = SpfaClient("sk_test", cache_path=cache)
    item = SpfaCookies(
        cookie_id="1",
        cookies={"f": "abc", "srv_id": "xyz"},
        user_agent="UA",
        fingerprint={"impersonate": "chrome"},
        mobile=False,
        purchased_at=time.time(),
    )
    client._save_cache(item)
    loaded = client._load_cache()
    assert loaded is not None
    assert loaded.cookie_id == "1"
    assert loaded.cookies["f"] == "abc"
    assert loaded.impersonate == "chrome"


def test_market_web_url_contains_query():
    q = parse_iphone_market_query("13 mini 128")
    url = build_market_web_url(q)
    assert "all/telefony" in url
    assert "iPhone" in url or "13" in url
