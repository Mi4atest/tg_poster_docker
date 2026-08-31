"""Сухой прогон заголовков SPFA и impersonate-fallback."""
from app.integrations.avito.browser_fetch import (
    canonicalize_headers,
    impersonate_candidates,
    impersonate_is_android,
    resolve_impersonate,
    spfa_request_headers,
)


def test_canonicalize_headers_drops_duplicates_and_hop_by_hop():
    headers = canonicalize_headers(
        {
            "Accept": "application/json",
            "accept": "text/html",
            "Host": "www.avito.ru",
            "User-Agent": "UA-1",
            "user-agent": "UA-2",
        }
    )
    assert "Accept" not in headers
    assert headers["accept"] == "text/html"
    assert headers["user-agent"] == "UA-2"
    assert "host" not in headers


def test_spfa_request_headers_keep_fingerprint_accept():
    headers = spfa_request_headers(
        {
            "accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "sec-ch-ua-mobile": "?1",
            "user-agent": "old",
        },
        user_agent="Mozilla/5.0 Android Chrome/131",
    )
    assert "Accept" not in headers
    assert headers["accept"].startswith("text/html")
    assert "application/json" not in headers["accept"]
    assert headers["user-agent"] == "Mozilla/5.0 Android Chrome/131"
    assert headers["sec-ch-ua-mobile"] == "?1"


def test_impersonate_candidates_do_not_mix_android_and_desktop():
    android = impersonate_candidates("chrome136_android")
    assert android[0] == "chrome136_android"
    assert android[1] == "chrome131_android"
    assert "chrome131" not in android
    assert all(impersonate_is_android(name) for name in android)

    desktop = impersonate_candidates("chrome136")
    assert desktop[0] == "chrome136"
    assert desktop[1] == "chrome131"
    assert not any(impersonate_is_android(name) for name in desktop)


def test_resolve_impersonate_defaults_to_android():
    assert resolve_impersonate(None) == "chrome131_android"
    assert resolve_impersonate("  ") == "chrome131_android"
    assert resolve_impersonate("chrome131_android") == "chrome131_android"
