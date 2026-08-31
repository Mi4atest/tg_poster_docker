"""Сухой прогон справки SPFA / mobileproxy."""
from __future__ import annotations

from app.integrations.avito.market_account_status import (
    MarketAccountStatus,
    _fmt_data_mb,
    _fmt_exp,
    _fmt_rub,
    _sum_delta_mb,
    looks_residential,
    parse_proxy_login_host,
    pick_proxy_row,
)
from app.integrations.avito.mobileproxy_client import _as_list


def test_format_money_and_traffic():
    assert _fmt_rub(1240) == "1 240 ₽"
    assert _fmt_rub(12.5) == "12,50 ₽"
    assert "ГБ" in _fmt_data_mb(1024)
    assert _fmt_data_mb(187).endswith("МБ")
    assert _fmt_exp("2026-11-30 23:59:59") == "30.11.2026"


def test_sum_traffic_and_remaining_phrase():
    used = _sum_delta_mb(
        [{"date": "2026-08-01", "delta_mb": "100.5"}, {"delta_mb": "86.5"}]
    )
    assert abs(used - 187.0) < 0.01
    status = MarketAccountStatus(
        spfa_balance=500,
        proxy_balance=1500,
        used_mb=187,
        remaining_mb=1024 - 187,
        is_residential=True,
    )
    text = status.short_html()
    assert "SPFA" in text
    assert "187" in text or "МБ" in text
    assert "1 500" in text or "1500" in text
    assert "b4857c" not in text
    assert "Попробуйте позже" not in text


def test_pick_proxy_matches_sticky_login():
    rows = [
        {"proxy_id": 1, "proxy_login": "other", "proxy_hostname": "a.example"},
        {
            "proxy_id": 42,
            "proxy_login": "userbase",
            "proxy_hostname": "ru.resigw.com",
        },
    ]
    configured = "userbase-session-abc-sessTime-15:pass@ru.resigw.com:2333"
    picked = pick_proxy_row(rows, configured)
    assert picked is not None
    assert picked["proxy_id"] == 42
    login, host = parse_proxy_login_host(configured)
    assert login.startswith("userbase-session-")
    assert "resigw" in host
    assert looks_residential(configured_proxy=configured, row=picked) is True


def test_as_list_accepts_bare_array_and_wrapped():
    assert len(_as_list([{"proxy_id": 1}])) == 1
    assert len(_as_list({"status": "ok", "list": [{"proxy_id": 2}]})) == 1
    assert _as_list({"status": "ok"}) == []


def test_token_missing_is_optional_hint():
    status = MarketAccountStatus(proxy_error="токен API не задан — расход ГБ в кабинете")
    line = status._proxy_short_line()
    assert "токен API не задан" in line
    assert "ГБ" in line
