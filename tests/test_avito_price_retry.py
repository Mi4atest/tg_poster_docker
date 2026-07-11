import asyncio
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from app.integrations.avito import http_client as avito_http


@pytest.mark.parametrize(
    "status,expected",
    [
        (429, True),
        (500, True),
        (502, True),
        (503, True),
        (400, False),
        (404, False),
    ],
)
def test_is_transient_avito_price_error_http(status, expected):
    err = avito_http.AvitoApiError("x", status=status, body="{}")
    assert avito_http.is_transient_avito_price_error(err) is expected


def test_is_transient_avito_price_error_network():
    assert avito_http.is_transient_avito_price_error(aiohttp.ClientConnectionError()) is True
    assert avito_http.is_transient_avito_price_error(asyncio.TimeoutError()) is True


def test_post_item_price_update_succeeds_on_second_attempt():
    calls = {"n": 0}

    async def fake_request(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise avito_http.AvitoApiError("Avito HTTP 500", status=500, body='{"message":"internal error"}')
        return {"result": {"status": True}}

    async def run():
        with patch.object(avito_http, "_request_json", side_effect=fake_request):
            with patch.object(avito_http.asyncio, "sleep", new=AsyncMock()) as sleep_mock:
                data = await avito_http.post_item_price_update("token", 123, 50000)
        assert data == {"result": {"status": True}}
        assert calls["n"] == 2
        sleep_mock.assert_awaited_once_with(3.0)

    asyncio.run(run())


def test_post_item_price_update_retries_all_backoffs_then_raises():
    async def fake_request(*args, **kwargs):
        raise avito_http.AvitoApiError("Avito HTTP 500", status=500, body='{"message":"internal error"}')

    async def run():
        with patch.object(avito_http, "_request_json", side_effect=fake_request):
            with patch.object(avito_http.asyncio, "sleep", new=AsyncMock()) as sleep_mock:
                with pytest.raises(avito_http.AvitoApiError) as exc_info:
                    await avito_http.post_item_price_update("token", 123, 50000)
        assert exc_info.value.status == 500
        assert sleep_mock.await_count == 3
        sleep_mock.assert_any_await(3.0)
        sleep_mock.assert_any_await(7.0)
        sleep_mock.assert_any_await(15.0)

    asyncio.run(run())


def test_post_item_price_update_no_retry_on_400():
    async def fake_request(*args, **kwargs):
        raise avito_http.AvitoApiError(
            "Avito HTTP 400",
            status=400,
            body='{"error":{"message":"Required parameters are not filled"}}',
        )

    async def run():
        with patch.object(avito_http, "_request_json", side_effect=fake_request):
            with patch.object(avito_http.asyncio, "sleep", new=AsyncMock()) as sleep_mock:
                with pytest.raises(avito_http.AvitoApiError) as exc_info:
                    await avito_http.post_item_price_update("token", 123, 50000)
        assert exc_info.value.status == 400
        sleep_mock.assert_not_awaited()

    asyncio.run(run())
