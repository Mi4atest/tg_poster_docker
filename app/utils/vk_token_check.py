"""Проверка токена VK: какие права выданы и отвечает ли API.

С 07.09.2026 VK ввёл месячные лимиты вызовов на приложение. Токен от чужого
приложения (Kate Mobile, VFeed) может иметь все права, но упираться в ошибку 9,
когда квота приложения выжата. Поэтому проверяем и права, и живые вызовы.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

API_VERSION = "5.199"
_TIMEOUT = 15

# Права, без которых проект не работает: bit -> (scope, за что отвечает)
REQUIRED_SCOPES: dict[int, tuple[str, str]] = {
    1 << 2: ("photos", "фото для стены и товаров"),
    1 << 6: ("stories", "публикация сторис"),
    1 << 13: ("wall", "посты и комментарии"),
    1 << 16: ("offline", "работа с IP сервера"),
    1 << 18: ("groups", "доступ к сообществу"),
    1 << 27: ("market", "цены и товары"),
}
OPTIONAL_SCOPES: dict[int, str] = {
    1 << 1: "friends",
    1 << 3: "audio",
    1 << 4: "video",
    1 << 7: "pages",
    1 << 10: "status",
    1 << 11: "notes",
    1 << 12: "messages",
    1 << 15: "ads",
    1 << 17: "docs",
    1 << 19: "notifications",
    1 << 20: "stats",
    1 << 22: "email",
    1 << 28: "phone",
}


def _call(token: str, method: str, **params: Any) -> tuple[Optional[Any], Optional[tuple[int, str]]]:
    """Вернуть (response, None) либо (None, (код, текст ошибки))."""
    params.update({"access_token": token, "v": API_VERSION})
    try:
        resp = requests.post(
            f"https://api.vk.com/method/{method}", data=params, timeout=_TIMEOUT
        )
        body = resp.json()
    except Exception as exc:
        return None, (-1, f"{type(exc).__name__}: {exc}"[:150])
    err = body.get("error") or {}
    if err:
        return None, (int(err.get("error_code") or 0), str(err.get("error_msg") or "")[:150])
    return body.get("response"), None


def _explain(code: int, msg: str) -> str:
    if code == 9:
        return "квота приложения выжата (VK отдаёт «Flood control»)"
    if code == 5 and "another ip" in msg.lower():
        return "токен привязан к IP: нужно право «Доступ в любое время»"
    if code == 5:
        return "токен недействителен"
    if code == 15:
        return "доступ запрещён"
    if code == 27:
        return "метод недоступен ключу сообщества (нужен user-токен)"
    if code == 1051:
        return "метод недоступен этому типу токена/профиля"
    if code == 1438:
        return "в сообществе не включён раздел «Товары»"
    return msg or f"код {code}"


def check_vk_token(token: str) -> str:
    """Отчёт о токене для чата: права + живые вызовы. Без значения токена."""
    token = (token or "").strip()
    if not token:
        return "❌ Токен пустой."

    lines: list[str] = []
    prefix = token[:6]
    lines.append(f"🔑 Токен: {len(token)} символов, префикс <code>{prefix}</code>")

    mask, err = _call(token, "account.getAppPermissions")
    is_community = bool(err and err[0] == 27)
    missing: list[tuple[str, str]] = []
    have: list[str] = []

    if is_community:
        lines.append("ℹ️ Это ключ <b>сообщества</b>, не user-токен.")
        perms, perm_err = _call(token, "groups.getTokenPermissions")
        if perm_err:
            lines.append(f"❌ Права сообщества: {_explain(*perm_err)}")
        elif isinstance(perms, dict):
            names = [str(p.get("name")) for p in (perms.get("permissions") or []) if p.get("name")]
            lines.append("✅ Заявленные права сообщества: " + ", ".join(names) if names else "права пустые")
            have = names
    elif err:
        lines.append(f"❌ Права прочитать не удалось: {_explain(*err)}")
    else:
        mask_i = int(mask or 0)
        missing = [(s, why) for bit, (s, why) in REQUIRED_SCOPES.items() if not mask_i & bit]
        have = [s for bit, (s, _) in REQUIRED_SCOPES.items() if mask_i & bit]
        extra = [name for bit, name in OPTIONAL_SCOPES.items() if mask_i & bit]
        if mask_i == 0:
            lines.append("❌ Права: приложение не выдало ни одного права")
        else:
            lines.append(f"✅ Есть права: {', '.join(sorted(have)) or '—'}")
            if extra:
                lines.append(f"➕ Дополнительно: {', '.join(sorted(extra))}")
        if missing:
            lines.append("❌ Не хватает: " + "; ".join(f"{s} ({why})" for s, why in sorted(missing)))

    group_id = None
    try:
        from app.utils.vk_client import resolved_vk_group_id_int

        group_id = resolved_vk_group_id_int()
    except Exception:
        pass

    checks: list[tuple[str, str, dict[str, Any]]] = []
    if not is_community:
        checks.append(("Аккаунт", "users.get", {}))
    if group_id:
        checks.append(("Сообщество", "groups.getById", {"group_id": group_id}))
        checks.append(("Товары", "market.get", {"owner_id": -group_id, "count": 1}))
        checks.append(("Стена", "wall.get", {"owner_id": -group_id, "count": 1}))
        checks.append(("Фото стены", "photos.getWallUploadServer", {"group_id": group_id}))
        checks.append(("Сторис", "stories.getPhotoUploadServer", {"add_to_news": 1}))

    lines.append("")
    lines.append("<b>Живые вызовы API:</b>")
    quota_blocked = False
    ok_methods: list[str] = []
    for label, method, params in checks:
        _, call_err = _call(token, method, **params)
        if call_err is None:
            ok_methods.append(method)
            lines.append(f"✅ {label} ({method})")
        else:
            if call_err[0] == 9:
                quota_blocked = True
            lines.append(f"❌ {label} ({method}): {_explain(*call_err)}")

    lines.append("")
    if is_community:
        lines.append(
            "⚠️ Ключ сообщества для этого проекта недостаточен: цены, скрытие "
            "товаров, посты с фото и комментарии требуют user-токена. "
            "Верните токен vk.com (тип «Пользователь»), иначе витрина снова встанет."
        )
    elif quota_blocked:
        lines.append(
            "⚠️ Приложение, от имени которого выдан токен, исчерпало месячный лимит "
            "VK API. Токен рабочий, но вызовы не проходят — нужен токен другого "
            "приложения либо своё приложение в VK ID."
        )
    elif missing:
        lines.append(
            "⚠️ API отвечает, но прав не хватает: возьмите токен приложения, "
            "которому VK разрешает нужные права."
        )
    else:
        lines.append("🎉 Токен полностью рабочий: права на месте, вызовы проходят.")
    return "\n".join(lines)
