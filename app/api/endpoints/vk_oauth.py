"""VK ID OAuth 2.1 (PKCE) → VK_MARKET_ACCESS_TOKEN. Legacy oauth.vk.ru для Web-приложений не работает."""
import html
import json
import time
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from app.utils.vk_client import vk_app_id
from app.utils.vk_pkce import generate_pkce, pop_pkce_verifier, save_pkce_state

router = APIRouter()

REDIRECT_URI = "https://appleshop.ap43.ru/vk/oauth/callback"
VKID_AUTHORIZE = "https://id.vk.ru/authorize"
VKID_TOKEN = "https://id.vk.ru/oauth2/auth"
# Права, которые нужны проекту. VK ID выдаст только разрешённые приложению —
# фактический список смотрим в поле scope ответа на обмен кода.
DEFAULT_SCOPE = "offline market wall photos stories groups"


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
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
        "client_id": vk_app_id(),
        "device_id": device_id,
        "state": state,
    }
    try:
        from app.services.settings_service import get_settings_service

        secret = str(get_settings_service().get_secret("vk_app_secret") or "").strip()
        if secret:
            payload["client_secret"] = secret
    except Exception:
        pass
    resp = requests.post(
        VKID_TOKEN,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    return resp.json()


def _store_tokens(data: dict, device_id: str) -> str:
    """Сохранить токены в секреты бота. Возвращает текст о том, что записано."""
    from app.services.settings_service import get_settings_service

    token = str(data.get("access_token") or "")
    if not token:
        return ""
    service = get_settings_service()
    service.set_secret("vk_access_token", token)
    service.set_secret("vk_market_access_token", token)
    saved = ["vk_access_token", "vk_market_access_token"]
    refresh = str(data.get("refresh_token") or "")
    if refresh:
        service.set_secret("vk_refresh_token", refresh)
        saved.append("vk_refresh_token")
    expires_in = int(data.get("expires_in") or 0)
    service.update(
        {
            "integrations": {
                "vk_token_device_id": device_id,
                "vk_token_expires_at": (
                    int(time.time()) + expires_in if expires_in else 0
                ),
            }
        }
    )
    try:
        from app.services.price_sync_service import reset_vk_publisher

        reset_vk_publisher()
    except Exception:
        pass
    try:
        from app.utils.vk_flood_gate import clear_flood

        clear_flood("new VK ID token")
    except Exception:
        pass
    return ", ".join(saved)


@router.get("/vk/oauth/help", response_class=HTMLResponse)
async def vk_oauth_help():
    app_id = _esc(vk_app_id() or "54604726")
    body = f"""
<h1>Своё приложение VK ID — Appleshop Poster</h1>
<p>ID приложения: <code>{app_id}</code>. Redirect URI:
<code>{_esc(REDIRECT_URI)}</code>.</p>
<p>Лимит 10 000 вызовов в месяц выдаётся этому приложению автоматически.
Подтверждение профиля нужно не ради лимита, а чтобы в разделе «Доступы»
появились пункты <b>товары</b> и <b>сообщества</b>.</p>

<h2>Официальный путь (без письма в поддержку)</h2>
<p>Источник:
<a href="https://id.vk.ru/about/faq/business/vkid/accesses/30049">FAQ VK ID: расширенные доступы</a>.</p>
<ol>
  <li>Подтвердите профиль через VK Бизнес ID (резидент РФ).</li>
  <li>В настройках приложения откройте «Доступы». Кроме ФИО / почты / телефона
      должны появиться <b>товары</b> и <b>сообщества</b>.</li>
  <li>Включите оба пункта — откроется окно обоснования. Это модерация
      в кабинете, ответ обещают за 3 рабочих дня.</li>
  <li>Когда доступы одобрят — снова
      <a href="/vk/oauth/vkid/start">войдите через VK ID</a>.
      Если в отчёте будет <code>market</code>, бот сам сохранит токен.</li>
</ol>
<p>Письмо на <a href="mailto:devsupport@corp.vk.com">devsupport@corp.vk.com</a>
имеет смысл только если профиль уже подтверждён, а пунктов «товары» /
«сообщества» в кабинете всё равно нет.</p>

<p><a href="/vk/oauth/vkid/start"><b>Войти через VK ID и получить токен</b></a></p>
"""
    return _html("VK ID — своё приложение", body)


@router.get("/vk/oauth/vkid/start")
async def vk_oauth_vkid_start(scope: str | None = Query(None)):
    """Старт VK ID OAuth с PKCE → id.vk.ru/authorize."""
    app_id = vk_app_id()
    if not app_id:
        return RedirectResponse("/vk/oauth/help", status_code=302)

    verifier, challenge, state = generate_pkce()
    save_pkce_state(state, verifier)

    params = {
        "response_type": "code",
        "client_id": app_id,
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "scope": (scope or DEFAULT_SCOPE).strip(),
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
            token = str(data["access_token"])
            granted = str(data.get("scope") or "")
            granted_set = set(granted.split())
            needed = {"market", "wall", "photos", "stories", "groups", "offline"}
            missing = sorted(needed - granted_set)
            can_replace = "market" in granted_set
            saved = _store_tokens(data, device_id) if can_replace else ""
            keep_note = (
                ""
                if can_replace
                else "<p class='err'>Токен в боте <b>не заменял</b>: у нового нет "
                "права market, а текущий токен vk.com уже умеет менять цены. "
                "Когда поддержка включит market — войдите ещё раз.</p>"
            )
            verdict = (
                f'<p class="err"><b>VK не выдал права:</b> {_esc(", ".join(missing))}. '
                "Их включают по заявке в "
                "devsupport@corp.vk.com — подтверждать профиль бизнеса для "
                "лимита 10 000 не нужно.</p>"
                if missing
                else '<p class="ok"><b>Все нужные права выданы, токен записан в бота.</b></p>'
            )
            probe = ""
            try:
                from app.utils.vk_token_check import check_vk_token

                probe = check_vk_token(token)
            except Exception as exc:
                probe = f"Проверка не удалась: {exc}"
            body = f"""
<h1 class="ok">Токен VK ID получен</h1>
{verdict}
<p><b>Выданные права:</b> <code>{_esc(granted or '—')}</code></p>
<p>Тип токена: <code>{_esc(token[:6])}…</code> |
expires_in: {_esc(data.get('expires_in', ''))} сек |
user_id: {_esc(data.get('user_id', ''))}</p>
{keep_note}
<p>Сохранено в секреты бота: <code>{_esc(saved or 'не сохраняли — нет market')}</code>.</p>
<p>{"Есть refresh_token — бот сможет продлевать сессию без повторного входа."
    if data.get("refresh_token") else "refresh_token не выдан: когда токен истечёт, войдите ещё раз."}</p>
<pre>{_esc(probe)}</pre>
<p><a href="/vk/oauth/help">К инструкции</a></p>
"""
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
