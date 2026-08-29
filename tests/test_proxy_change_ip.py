"""Тесты смены IP mobileproxy и mobile URL fallback."""
from __future__ import annotations

from app.config.settings import AVITO_MARKET_CATEGORY_ID, AVITO_MARKET_LOCATION_ID
from app.integrations.avito.market_search import build_market_mobile_api_url
from app.integrations.avito.proxy_change_ip import (
    ensure_json_format,
    mask_change_ip_url,
    _parse_change_response,
)
from app.utils.iphone_market_query import parse_iphone_market_query


def test_ensure_json_format_adds_param():
    url = ensure_json_format(
        "https://changeip.mobileproxy.rent/?proxy_key=abc123def456"
    )
    assert "format=json" in url
    assert "proxy_key=abc123def456" in url


def test_ensure_json_format_keeps_existing():
    url = ensure_json_format(
        "https://changeip.mobileproxy.rent/?proxy_key=abc&format=json"
    )
    assert url.count("format=") == 1


def test_mask_change_ip_url_hides_key():
    masked = mask_change_ip_url(
        "https://changeip.mobileproxy.rent/?proxy_key=adf27d08729bd9c7ccb8ae5d876d23c9"
    )
    assert "adf27d08729bd9c7ccb8ae5d876d23c9" not in masked
    assert "adf2" in masked
    assert "23c9" in masked


def test_parse_change_response_ok():
    ok, ip, _ = _parse_change_response(
        {"status": "OK", "new_ip": "1.2.3.4", "code": 0}
    )
    assert ok is True
    assert ip == "1.2.3.4"


def test_parse_change_response_fail():
    ok, ip, msg = _parse_change_response(
        {"status": "error", "message": "cooldown"}
    )
    assert ok is False
    assert ip is None
    assert "cooldown" in msg


def test_rotate_sticky_session_changes_id():
    from app.integrations.avito.proxy_change_ip import (
        has_sticky_session,
        rotate_sticky_session,
    )

    raw = (
        "pa2727472bccad72-zone-custom-region-ru-session-f9cdcb8778-sessTime-15"
        ":secret@ru.resigw.com:2333"
    )
    assert has_sticky_session(raw)
    rotated = rotate_sticky_session(raw)
    assert rotated is not None
    assert rotated != raw
    assert "session-f9cdcb8778" not in rotated
    assert "-sessTime-15:" in rotated
    assert rotated.endswith("@ru.resigw.com:2333")


def test_market_mobile_api_url():
    url = build_market_mobile_api_url(parse_iphone_market_query("13 mini 128"))
    assert url.startswith("https://m.avito.ru/api/11/items?")
    assert f"locationId={AVITO_MARKET_LOCATION_ID}" in url
    assert f"categoryId={AVITO_MARKET_CATEGORY_ID}" in url
    assert "query=" in url
