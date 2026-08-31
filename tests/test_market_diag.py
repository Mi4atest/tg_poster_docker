"""Сухой прогон кодов отказов и текстов для продавца."""
from app.integrations.avito.market_diag import (
    CODE_CAPTCHA,
    CODE_DAILY_LIMIT,
    CODE_HTTP_439,
    CODE_INTERVAL,
    CODE_NO_PROXY,
    CODE_PARSE,
    infer_market_code,
    user_facing_market_error,
    user_notice,
)
from app.integrations.avito.market_search import AvitoMarketBlockedError


def test_user_notices_are_specific_and_plain():
    interval = user_notice(CODE_INTERVAL, wait_sec=42)
    assert "42 сек" in interval
    assert "Попробуйте позже" not in interval

    daily = user_notice(CODE_DAILY_LIMIT, daily_limit=40)
    assert "40" in daily
    assert "завтра" in daily.lower() or "Завтра" in daily

    proxy = user_notice(CODE_NO_PROXY)
    assert "прокси" in proxy.lower()
    assert "Настройки" in proxy

    blocked = user_notice(CODE_HTTP_439, wait_mins=60)
    assert "робот" in blocked.lower()
    assert "60" in blocked

    captcha = user_notice(CODE_CAPTCHA, wait_mins=20)
    assert "робот" in captcha.lower()
    assert "20" in captcha

    parse = user_notice(CODE_PARSE)
    assert "объявлен" in parse.lower()


def test_http_439_without_proxy_explains_proxy():
    text = user_notice(CODE_HTTP_439, wait_mins=60, has_proxy=False)
    assert "прокси" in text.lower()


def test_infer_codes_from_logs_and_old_phrases():
    assert infer_market_code("http_439") == CODE_HTTP_439
    assert infer_market_code("Avito вернул HTTP 439") == CODE_HTTP_439
    assert infer_market_code("достигнут безопасный суточный лимит запросов") == (
        CODE_DAILY_LIMIT
    )
    assert infer_market_code("Подождите ещё 12 сек. между новыми поисками") == (
        CODE_INTERVAL
    )
    assert infer_market_code("В выдаче Avito не найдены карточки") == CODE_PARSE
    assert infer_market_code("Avito попросил подтвердить, что запрос не от робота") == (
        CODE_CAPTCHA
    )


def test_user_facing_maps_old_try_later_to_lively_text():
    text = user_facing_market_error(
        "Avito временно ограничил автоматические запросы. "
        "Оценка недоступна какое-то время — попробуйте позже."
    )
    assert "Попробуйте позже" not in text
    assert "Avito" in text


def test_blocked_error_keeps_http_code_and_proxy_flag():
    exc = AvitoMarketBlockedError(
        "Avito вернул HTTP 439",
        code=CODE_HTTP_439,
        has_proxy=False,
    )
    assert exc.code == CODE_HTTP_439
    assert exc.has_proxy is False
    assert exc.soft is False
