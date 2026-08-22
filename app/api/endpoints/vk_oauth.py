"""VK ID OAuth 2.1 (PKCE) → VK_MARKET_ACCESS_TOKEN. Legacy oauth.vk.ru для Web-приложений не работает."""
import html
import json
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config.settings import VK_APP_ID, VK_APP_SECRET
from app.utils.vk_pkce import generate_pkce, pop_pkce_verifier, save_pkce_state

router = APIRouter()

REDIRECT_URI = "https://appleshop.ap43.ru/vk/oauth/callback"
VKID_AUTHORIZE = "https://id.vk.ru/authorize"
VKID_TOKEN = "https://id.vk.ru/oauth2/auth"


def _esc(value: object) -> str:
    """Экранирование для вставки в HTML (защита от отражённого XSS)."""
    return html.escape("" if value is None else str(value), quote=True)


def _html(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"<!DOCTYPE html><html lang='ru'><head><meta charset='utf-8'><title>{_esc(title)}</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem}"
        "textarea{width:100%;height:120px;word-break:break-all}.ok{color:green}.err{color:#c00}"
        "code{font-size:13px}</style></head><body>"
        f"{body}</body></html>",
        status_code=status,
    )


def _exchange_vkid_code(code: str, device_id: str, state: str, verifier: str) -> dict:
    resp = requests.post(
        VKID_TOKEN,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": REDIRECT_URI,
            "client_id": VK_APP_ID,
            "device_id": device_id,
            "state": state,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    return resp.json()


@router.get("/vk/oauth/help", response_class=HTMLResponse)
async def vk_oauth_help():
    app_id = _esc(VK_APP_ID or "54604726")
    body = f"""
<h1>Токен для VK Market (<code>VK_MARKET_ACCESS_TOKEN</code>)</h1>
<p><b>Standalone на dev.vk.ru больше не создаётся</b> — приложения перенесены в
<a href="https://id.vk.ru/about/business/go/docs/ru/vkid/latest/vk-id/connection/create-application">VK ID</a>.
Старый <code>oauth.vk.ru</code> даёт <b>Security Error</b> — это нормально для Web-приложения.</p>

<h2 class="err">Важно (проверено на сервере, май 2026)</h2>
<ul>
  <li><b>Ключ группы</b> (<code>vk1.a.*</code> из «Работа с API») → стена OK, <b>market.* — нет</b> (ошибка 27).</li>
  <li><b>VK ID OAuth</b> (<code>vk2.a.*</code>) → <b>market.* — нет</b> (ошибки 1051/15).</li>
  <li>Для цен и «недоступен» нужен <b>user token <code>vk1.a.*</code></b> с правом <b>market</b> (старый тип VK API).</li>
</ul>

<h2>Как получить vk1.a для маркета</h2>
<ol>
  <li>После разблокировки профиля — токен через проверенный способ с правом <b>market</b>
      (не vkhost, если хотите избежать блокировок; либо напишите
      <a href="mailto:devsupport@corp.vk.ru">devsupport@corp.vk.ru</a> — как серверу получить vk1.a для app {app_id}).</li>
  <li>В <code>.env</code> одна строка, <b>без дубля</b>:
    <code>VK_MARKET_ACCESS_TOKEN=vk1.a.XXXX</code> (не <code>VK_MARKET_ACCESS_TOKEN=VK_MARKET_ACCESS_TOKEN=...</code>).</li>
  <li><code>VK_ACCESS_TOKEN</code> — отдельно ключ из группы.</li>
</ol>
<p><a href="/vk/oauth/vkid/start">VK ID OAuth (даёт vk2.a — для маркета обычно не подходит)</a></p>

<h2>Два токена в .env</h2>
<ul>
  <li><code>VK_ACCESS_TOKEN</code> — ключ сообщества (посты на стену).</li>
  <li><code>VK_MARKET_ACCESS_TOKEN</code> — user <code>vk1.a.*</code> + market (цены, скрытие товара).</li>
</ul>
<p>Проверка: <code>docker-compose exec app python -m app.scripts.verify_vk_tokens</code></p>
"""
    return _html("VK Market token", body)


@router.get("/vk/oauth/vkid/start")
async def vk_oauth_vkid_start():
    """Старт VK ID OAuth с PKCE → id.vk.ru/authorize."""
    if not VK_APP_ID or VK_APP_ID == "your_vk_app_id":
        return RedirectResponse("/vk/oauth/help", status_code=302)

    verifier, challenge, state = generate_pkce()
    save_pkce_state(state, verifier)

    params = {
        "response_type": "code",
        "client_id": VK_APP_ID,
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "scope": "vkid.personal_info",
    }
    url = f"{VKID_AUTHORIZE}?{urlencode(params)}"
    return RedirectResponse(url, status_code=302)


@router.get("/vk/oauth/start")
async def vk_oauth_start():
    """Редирект на VK ID (не legacy oauth.vk.ru)."""
    return RedirectResponse("/vk/oauth/vkid/start", status_code=302)


@router.get("/vk/oauth/callback", response_class=HTMLResponse)
async def vk_oauth_callback(
    code: str | None = Query(None),
    device_id: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    error_description: str | None = Query(None),
):
    """Обмен code на access_token на сервере (без JS — требование VK ID для redirect URI)."""
    if error:
        return _html(
            "VK ID — ошибка",
            f'<p class="err">{_esc(error)}</p><p>{_esc(error_description or "")}</p>'
            f'<p><a href="/vk/oauth/help">Назад к инструкции</a></p>',
            400,
        )

    if code and device_id and state:
        verifier = pop_pkce_verifier(state)
        if not verifier:
            return _html(
                "VK ID",
                "<p class='err'>Сессия PKCE истекла. <a href='/vk/oauth/vkid/start'>Начните снова</a></p>",
                400,
            )
        try:
            data = _exchange_vkid_code(code, device_id, state, verifier)
        except Exception as e:
            return _html("VK ID", f"<p class='err'>Ошибка запроса: {_esc(e)}</p>", 502)

        if "access_token" in data:
            token = data["access_token"]
            refresh = data.get("refresh_token", "")
            vk2_warn = ""
            if str(token).startswith("vk2.a."):
                vk2_warn = (
                    '<p class="err"><b>Внимание:</b> токен <code>vk2.a.*</code> (VK ID) '
                    "<b>не работает</b> с <code>market.edit</code> / ценами в боте (ошибки 1051/15). "
                    "Для VK Market нужен <b>vk1.a.*</b> user token с правом market — см. инструкцию ниже.</p>"
                )
            body = f"""
<h1 class="ok">Токен VK ID получен</h1>
{vk2_warn}
<p>Если токен <code>vk1.a.*</code> — в <code>.env</code> (только значение, без префикса имени переменной):</p>
<pre>VK_MARKET_ACCESS_TOKEN={_esc(token)}</pre>
<p>Затем: <code>docker-compose restart app</code> и
<code>python -m app.scripts.verify_vk_tokens</code></p>
<p>scope: {_esc(data.get('scope', ''))} | expires_in: {_esc(data.get('expires_in', ''))} | user_id: {_esc(data.get('user_id', ''))}</p>
<p><a href="/vk/oauth/help">Как получить vk1.a для маркета</a></p>
"""
            if refresh:
                body += (
                    "<p>Refresh token (сохраните отдельно для продления):<br>"
                    f"<textarea readonly>{_esc(refresh)}</textarea></p>"
                )
            body += "<p><a href='/vk/oauth/help'>Инструкция</a></p>"
            return _html("VK — токен", body)

        return _html(
            "VK ID — ошибка обмена",
            f"<pre>{_esc(json.dumps(data, ensure_ascii=False, indent=2))}</pre>"
            "<p><a href='/vk/oauth/help'>Помощь</a></p>",
            400,
        )

    return _html(
        "VK OAuth callback",
        "<p>Нет параметра <code>code</code> в URL.</p>"
        "<p><a href='/vk/oauth/vkid/start'><b>Войти через VK ID</b></a></p>"
        f"<p>Redirect URI: <code>{_esc(REDIRECT_URI)}</code></p>",
    )
