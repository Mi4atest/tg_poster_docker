"""OAuth callback для заявки в developers.avito.ru (при client_credentials не вызывается в рантайме)."""
import html

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


def _esc(value: object) -> str:
    """Экранирование для вставки в HTML (защита от отражённого XSS)."""
    return html.escape("" if value is None else str(value), quote=True)


@router.get("/avito/oauth/callback", response_class=HTMLResponse)
async def avito_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    """
    Публичный HTTPS endpoint для Redirect URL в кабинете Авито.
    При grant_type=client_credentials сюда никто не редиректит; достаточно ответа 200.
    """
    if error:
        body = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>Avito OAuth</title></head><body>"
            f"<p>Ошибка авторизации: {_esc(error)}</p>"
            f"<p>{_esc(error_description or '')}</p>"
            "</body></html>"
        )
        return HTMLResponse(content=body, status_code=400)

    if code:
        body = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>Avito OAuth</title></head><body>"
            "<p>Привязка Авито получена. Можно закрыть вкладку.</p>"
            "</body></html>"
        )
        return HTMLResponse(content=body, status_code=200)

    return HTMLResponse(
        content=(
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>AppleShop — Avito</title></head><body>"
            "<p>Endpoint OAuth для интеграции AppleShop / Telegram Poster.</p>"
            "<p>OK</p></body></html>"
        ),
        status_code=200,
    )
