"""VK ID OAuth 2.1 (PKCE) → токен инстанса (одна группа + один назначенный user)."""
from __future__ import annotations

import html
import json
import logging
import time
from typing import Any, Optional
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from app.utils.vk_client import resolved_vk_group_id_int, vk_app_id
from app.utils.vk_pkce import generate_pkce, pop_pkce_verifier, save_pkce_state
from app.utils.vk_public_url import (
    vk_oauth_help_url,
    vk_oauth_owner_id,
    vk_oauth_redirect_uri,
    vk_oauth_start_url,
)

logger = logging.getLogger(__name__)

router = APIRouter()

VKID_AUTHORIZE = "https://id.vk.ru/authorize"
VKID_TOKEN = "https://id.vk.ru/oauth2/auth"
# Права, которые нужны проекту. VK ID выдаст только разрешённые приложению.
DEFAULT_SCOPE = "offline market wall photos stories groups"
NEEDED_SCOPES = frozenset({"market", "wall", "photos", "stories", "groups", "offline"})
# editor=2, moderator=1, admin=3 — для market/wall нужен минимум editor.
MIN_GROUP_ADMIN_LEVEL = 2


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


def _redirect_uri() -> str:
    return vk_oauth_redirect_uri()


def _exchange_vkid_code(code: str, device_id: str, state: str, verifier: str) -> dict:
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": _redirect_uri(),
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


def _parse_user_id(data: dict[str, Any]) -> Optional[int]:
    raw = data.get("user_id")
    if raw is None and isinstance(data.get("user_ids"), list) and data["user_ids"]:
        raw = data["user_ids"][0]
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def check_group_admin_level(access_token: str, group_id: int) -> tuple[bool, int, str]:
    """Проверить, что токен даёт editor/admin именно в group_id инстанса."""
    try:
        resp = requests.get(
            "https://api.vk.com/method/groups.getById",
            params={
                "access_token": access_token,
                "group_id": abs(int(group_id)),
                "fields": "is_admin,admin_level,is_member",
                "v": "5.199",
            },
            timeout=20,
        )
        payload = resp.json()
    except Exception as exc:
        return False, 0, f"groups.getById failed: {exc}"

    if payload.get("error"):
        err = payload["error"]
        return False, 0, str(err.get("error_msg") or err)[:200]

    items = payload.get("response")
    if isinstance(items, dict):
        items = items.get("groups") or items.get("items") or []
    if not isinstance(items, list) or not items:
        return False, 0, "группа не найдена в ответе groups.getById"

    group = items[0] if isinstance(items[0], dict) else {}
    try:
        level = int(group.get("admin_level") or 0)
    except (TypeError, ValueError):
        level = 0
    is_admin = bool(group.get("is_admin")) or level >= MIN_GROUP_ADMIN_LEVEL
    if not is_admin or level < MIN_GROUP_ADMIN_LEVEL:
        return (
            False,
            level,
            f"нужен editor/admin в группе {abs(int(group_id))}, admin_level={level}",
        )
    return True, level, "ok"


def validate_oauth_binding(
    data: dict[str, Any],
    *,
    require_full_scopes: bool = True,
) -> tuple[bool, str, dict[str, Any]]:
    """Проверки владельца / scopes / роли в группе до записи токена."""
    meta: dict[str, Any] = {}
    token = str(data.get("access_token") or "")
    if not token:
        return False, "нет access_token", meta

    user_id = _parse_user_id(data)
    meta["user_id"] = user_id
    owner = vk_oauth_owner_id()
    meta["expected_owner_id"] = owner
    if owner is not None:
        if user_id is None:
            return False, "VK не вернул user_id", meta
        if int(user_id) != int(owner):
            return (
                False,
                f"user_id={user_id} не совпадает с VK_OAUTH_OWNER_ID={owner} этого инстанса",
                meta,
            )

    granted = str(data.get("scope") or "")
    granted_set = set(granted.split()) if granted else set()
    meta["scope"] = granted
    missing = sorted(NEEDED_SCOPES - granted_set)
    meta["missing_scopes"] = missing
    if require_full_scopes and missing:
        return False, f"не хватает прав: {', '.join(missing)}", meta
    if "market" not in granted_set:
        return False, "нет права market — токен не сохранён", meta

    try:
        group_id = resolved_vk_group_id_int()
    except Exception as exc:
        return False, f"VK_GROUP_ID не настроен: {exc}", meta
    meta["group_id"] = group_id

    ok_role, level, role_detail = check_group_admin_level(token, group_id)
    meta["admin_level"] = level
    if not ok_role:
        return False, role_detail, meta

    return True, "ok", meta


def _store_tokens(
    data: dict,
    device_id: str,
    *,
    binding_meta: Optional[dict[str, Any]] = None,
) -> str:
    """Сохранить токены и метаданные привязки. Возвращает текст о том, что записано."""
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
    meta = binding_meta or {}
    user_id = meta.get("user_id")
    if user_id is None:
        user_id = _parse_user_id(data)
    integrations: dict[str, Any] = {
        "vk_token_device_id": device_id,
        "vk_token_expires_at": (int(time.time()) + expires_in if expires_in else 0),
        "vk_oauth_bound_at": int(time.time()),
        "vk_oauth_scope": str(data.get("scope") or meta.get("scope") or "")[:300],
    }
    if user_id is not None:
        integrations["vk_oauth_user_id"] = int(user_id)
    if meta.get("group_id") is not None:
        integrations["vk_oauth_group_id"] = int(meta["group_id"])
    if meta.get("admin_level") is not None:
        integrations["vk_oauth_admin_level"] = int(meta["admin_level"])
    service.update({"integrations": integrations})
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
    redirect = _esc(_redirect_uri())
    start = _esc(vk_oauth_start_url())
    owner = vk_oauth_owner_id()
    try:
        group_id = resolved_vk_group_id_int()
    except Exception:
        group_id = None
    owner_line = (
        f"<p>Назначенный VK user_id этого инстанса: <code>{_esc(owner)}</code></p>"
        if owner is not None
        else "<p class='err'>VK_OAUTH_OWNER_ID не задан — задайте в .env, иначе любой "
        "аккаунт с market сможет перезаписать токен.</p>"
    )
    group_line = (
        f"<p>Группа инстанса: <code>{_esc(group_id)}</code> "
        "(нужен editor/admin именно в ней).</p>"
        if group_id
        else "<p class='err'>VK_GROUP_ID / integrations.vk_group_id не задан.</p>"
    )
    body = f"""
<h1>Своё приложение VK ID — привязка инстанса</h1>
<p>ID приложения: <code>{app_id}</code>. Redirect URI:
<code>{redirect}</code>.</p>
{owner_line}
{group_line}
<p>Модель: <b>один инстанс → одна группа → один назначенный VK-пользователь</b>.
Токен и refresh хранятся только в БД этой копии; не копируйте их на другие серверы.</p>

<h2>Как привязать</h2>
<ol>
  <li>В кабинете VK-приложения добавьте Redirect URI этого поддомена.</li>
  <li>В .env задайте <code>PUBLIC_BASE_URL</code>, <code>VK_GROUP_ID</code>,
      <code>VK_OAUTH_OWNER_ID</code>.</li>
  <li>Под аккаунтом владельца откройте
      <a href="{start}">вход через VK ID</a>.</li>
</ol>

<p><a href="{start}"><b>Войти через VK ID и получить токен</b></a></p>
"""
    return _html("VK ID — привязка инстанса", body)


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
        "redirect_uri": _redirect_uri(),
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
            ok, detail, meta = validate_oauth_binding(data, require_full_scopes=True)
            saved = ""
            if ok:
                saved = _store_tokens(data, device_id, binding_meta=meta)
                verdict = (
                    '<p class="ok"><b>Все проверки пройдены, токен записан в бота.</b></p>'
                )
            else:
                logger.warning(
                    "VK OAuth rejected: %s meta=%s",
                    detail,
                    {k: meta.get(k) for k in ("user_id", "expected_owner_id", "group_id", "missing_scopes")},
                )
                verdict = (
                    f'<p class="err"><b>Токен в боте не заменял:</b> {_esc(detail)}</p>'
                )

            probe = ""
            if ok:
                try:
                    from app.utils.vk_token_check import check_vk_token

                    probe = check_vk_token(token)
                except Exception as exc:
                    probe = f"Проверка не удалась: {exc}"

            body = f"""
<h1 class="{'ok' if ok else 'err'}">Токен VK ID получен</h1>
{verdict}
<p><b>Выданные права:</b> <code>{_esc(meta.get('scope') or data.get('scope') or '—')}</code></p>
<p>Тип токена: <code>{_esc(token[:6])}…</code> |
expires_in: {_esc(data.get('expires_in', ''))} сек |
user_id: {_esc(meta.get('user_id') or data.get('user_id') or '')} |
group: {_esc(meta.get('group_id') or '—')} |
admin_level: {_esc(meta.get('admin_level') if meta.get('admin_level') is not None else '—')}</p>
<p>Redirect URI: <code>{_esc(_redirect_uri())}</code></p>
<p>Сохранено в секреты бота: <code>{_esc(saved or 'не сохраняли')}</code>.</p>
<p>{"Есть refresh_token — бот сможет продлевать сессию без повторного входа."
    if ok and data.get("refresh_token") else
    ("refresh_token не выдан: когда токен истечёт, войдите ещё раз." if ok else "")}</p>
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
        f"<p><a href='{_esc(vk_oauth_start_url())}'><b>Войти через VK ID</b></a></p>"
        f"<p>Redirect URI: <code>{_esc(_redirect_uri())}</code></p>"
        f"<p>Help: <code>{_esc(vk_oauth_help_url())}</code></p>",
    )
