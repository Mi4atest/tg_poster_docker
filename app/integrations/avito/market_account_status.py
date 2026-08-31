"""Справка по кабинетам SPFA и mobileproxy для экранов оценки рынка."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from typing import Any, Optional

from app.config.settings import (
    AVITO_MARKET_RESIDENTIAL_PACKAGE_DAYS,
    AVITO_MARKET_RESIDENTIAL_PACKAGE_MB,
)
from app.integrations.avito.mobileproxy_client import MobileproxyClient, MobileproxyError
from app.integrations.avito.spfa_client import SpfaBalanceError, SpfaClient, SpfaError

logger = logging.getLogger(__name__)

_CACHE_TTL_SEC = 5 * 60
_TRAFFIC_KEYS_REMAINING = (
    "traffic_left_mb",
    "traffic_remain_mb",
    "remaining_mb",
    "remain_mb",
    "traffic_left",
    "traffic_remain",
)
_TRAFFIC_KEYS_USED = (
    "traffic_used_mb",
    "used_mb",
    "traffic_used",
)
_TRAFFIC_KEYS_TOTAL = (
    "traffic_mb",
    "traffic_total_mb",
    "quota_mb",
    "traffic_limit_mb",
)
_STICKY_LOGIN_RE = re.compile(r"(?i)^(.+?)-session-")

_cache_lock = asyncio.Lock()
_cache: dict[str, Any] = {"at": 0.0, "html_short": "", "html_detail": ""}


@dataclass
class MarketAccountStatus:
    spfa_balance: Optional[float] = None
    spfa_error: str = ""
    proxy_balance: Optional[float] = None
    proxy_error: str = ""
    proxy_id: Optional[int] = None
    proxy_exp: str = ""
    proxy_geo: str = ""
    proxy_operator: str = ""
    is_residential: bool = False
    used_mb: Optional[float] = None
    remaining_mb: Optional[float] = None
    package_mb: float = float(AVITO_MARKET_RESIDENTIAL_PACKAGE_MB)
    package_days: int = int(AVITO_MARKET_RESIDENTIAL_PACKAGE_DAYS)
    extras: list[str] = field(default_factory=list)

    def short_html(self) -> str:
        lines = ["<b>Кабинеты</b>"]
        lines.append(f"SPFA: {escape(self._spfa_line())}")
        lines.append(f"Прокси: {escape(self._proxy_short_line())}")
        return "\n".join(lines)

    def detail_html(self) -> str:
        lines = ["Сейчас в кабинетах:"]
        lines.append(f"SPFA: {escape(self._spfa_line())}")
        lines.append(f"Прокси: {escape(self._proxy_short_line())}")
        if self.proxy_error:
            lines.append(f"Кабинет прокси: {escape(self.proxy_error)}")
        if self.proxy_exp:
            lines.append(f"Аренда до: {escape(self.proxy_exp)}")
        extra_bits = []
        if self.proxy_operator:
            extra_bits.append(self.proxy_operator)
        if self.proxy_geo:
            extra_bits.append(self.proxy_geo)
        if extra_bits:
            lines.append("Канал: " + escape(", ".join(extra_bits)))
        if self.remaining_mb is not None and self.remaining_mb < self.package_mb * 0.15:
            lines.append("Трафика мало — имеет смысл докупить пакет, пока оценка не встала.")
        if self.spfa_balance is not None and self.spfa_balance < 50:
            lines.append("На SPFA мало средств — cookies могут перестать покупаться.")
        return "\n".join(lines)

    def _spfa_line(self) -> str:
        if self.spfa_error:
            return self.spfa_error
        if self.spfa_balance is None:
            return "ключ не задан"
        return f"{_fmt_rub(self.spfa_balance)}"

    def _proxy_short_line(self) -> str:
        if self.proxy_error and self.used_mb is None and self.proxy_balance is None:
            return self.proxy_error
        parts: list[str] = []
        if self.used_mb is not None or self.remaining_mb is not None:
            parts.append(self._traffic_phrase())
        elif self.is_residential:
            parts.append("пакет 1 ГБ / 3 мес.")
        elif self.proxy_exp:
            parts.append(f"до {self.proxy_exp}")
        if self.proxy_balance is not None:
            parts.append(f"кабинет {_fmt_rub(self.proxy_balance)}")
        if not parts:
            return "токен API не задан — расход ГБ в кабинете"
        return ", ".join(parts)

    def _traffic_phrase(self) -> str:
        used = self.used_mb if self.used_mb is not None else 0.0
        package = self.package_mb
        remaining = self.remaining_mb
        if remaining is None and package:
            remaining = max(0.0, package - used)
        used_s = _fmt_data_mb(used)
        pack_s = _fmt_data_mb(package) if package else "1 ГБ"
        if remaining is None:
            return f"потрачено {used_s} за {self.package_days} дн."
        return (
            f"потрачено {used_s} из {pack_s} за {self.package_days} дн. "
            f"(осталось {_fmt_data_mb(remaining)})"
        )


def invalidate_account_status_cache() -> None:
    _cache["at"] = 0.0
    _cache["html_short"] = ""
    _cache["html_detail"] = ""


def _fmt_exp(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    for fmt, size in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d", 10)):
        try:
            parsed = datetime.strptime(value[:size], fmt)
        except ValueError:
            continue
        return parsed.strftime("%d.%m.%Y")
    return value


def _fmt_rub(value: float) -> str:
    number = f"{value:,.2f}".replace(",", " ").replace(".", ",")
    if number.endswith(",00"):
        number = number[:-3]
    return f"{number} ₽"


def _fmt_data_mb(mb: float) -> str:
    value = max(0.0, float(mb))
    if value >= 950:
        gb = value / 1024.0
        text = f"{gb:.2f}".replace(".", ",")
        if text.endswith(",00"):
            text = text[:-3]
        return f"{text} ГБ"
    if value >= 10:
        return f"{int(round(value))} МБ"
    return f"{value:.1f}".replace(".", ",") + " МБ"


def _first_number(row: dict[str, Any], keys: tuple[str, ...]) -> Optional[float]:
    for key in keys:
        if key not in row or row[key] in (None, ""):
            continue
        try:
            return float(str(row[key]).replace(",", ".").replace(" ", ""))
        except (TypeError, ValueError):
            continue
    return None


def _sum_delta_mb(rows: list[dict[str, Any]]) -> float:
    total = 0.0
    for row in rows:
        try:
            total += float(str(row.get("delta_mb") or 0).replace(",", "."))
        except (TypeError, ValueError):
            continue
    return total


def parse_proxy_login_host(proxy: str) -> tuple[str, str]:
    value = (proxy or "").strip()
    if "://" in value:
        value = value.split("://", 1)[1]
    login = ""
    host = ""
    if "@" in value:
        userinfo, host = value.rsplit("@", 1)
        login = userinfo.split(":", 1)[0]
    elif ":" in value:
        host = value
    return login.strip(), host.strip()


def looks_residential(*, configured_proxy: str, row: Optional[dict[str, Any]]) -> bool:
    blob = (configured_proxy or "").lower()
    if "resigw" in blob or "residential" in blob or "-session-" in blob:
        return True
    if not row:
        return False
    login = str(row.get("proxy_login") or "").lower()
    host = str(row.get("proxy_hostname") or row.get("proxy_host_ip") or "").lower()
    if "-session-" in login or "residential" in host or "resigw" in host:
        return True
    return bool(_first_number(row, _TRAFFIC_KEYS_TOTAL + _TRAFFIC_KEYS_REMAINING))


def pick_proxy_row(
    rows: list[dict[str, Any]],
    configured_proxy: str,
) -> Optional[dict[str, Any]]:
    if not rows:
        return None
    login, host = parse_proxy_login_host(configured_proxy)
    login_l = login.lower()
    host_l = host.lower()
    sticky = _STICKY_LOGIN_RE.match(login)
    base_login = sticky.group(1).lower() if sticky else login_l

    def _score(row: dict[str, Any]) -> int:
        row_login = str(row.get("proxy_login") or "").lower()
        row_host = str(
            row.get("proxy_hostname")
            or row.get("proxy_independent_http_hostname")
            or ""
        ).lower()
        score = 0
        if login_l and row_login == login_l:
            score += 100
        if base_login and (row_login == base_login or login_l.startswith(row_login + "-")):
            score += 80
        if host_l and row_host:
            host_only = host_l.split(":")[0]
            if row_host in host_l or host_only == row_host:
                score += 40
        return score

    ranked = sorted(rows, key=_score, reverse=True)
    if _score(ranked[0]) > 0:
        return ranked[0]
    if len(rows) == 1:
        return rows[0]
    return ranked[0]


def _proxy_id(row: dict[str, Any]) -> Optional[int]:
    raw = row.get("proxy_id")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _human_cabinet_error(exc: BaseException) -> str:
    text = str(exc)
    lowered = text.lower()
    if "wrong ip" in lowered or "authorization error #4" in lowered:
        return "кабинет не пускает этот IP (белый список API)"
    if "wrong token" in lowered or "authorization error #3" in lowered:
        return "токен API не подошёл"
    if "no authorization" in lowered:
        return "токен API не задан"
    if "lonely" in lowered or "too many" in lowered:
        return "кабинет просит подождать (лимит запросов)"
    return "кабинет сейчас не отвечает"


async def _load_status() -> MarketAccountStatus:
    from app.services.settings_service import get_settings_service

    settings = get_settings_service()
    status = MarketAccountStatus()
    spfa_key = settings.get_spfa_api_key()
    mp_token = settings.get_mobileproxy_api_token()
    configured_proxy = settings.get_avito_market_proxy()

    async def _spfa() -> None:
        if not spfa_key:
            return
        try:
            status.spfa_balance = await SpfaClient(spfa_key).get_balance()
        except (SpfaBalanceError, SpfaError, TimeoutError, OSError) as exc:
            logger.warning("SPFA balance failed: %s", exc)
            status.spfa_error = "сейчас не отвечает" if not isinstance(exc, SpfaBalanceError) else "ключ не принят"

    async def _proxy() -> None:
        if not mp_token:
            status.proxy_error = "токен API не задан — расход ГБ в кабинете"
            return
        client = MobileproxyClient(mp_token)
        try:
            status.proxy_balance = await client.get_balance()
        except (MobileproxyError, TimeoutError, OSError) as exc:
            logger.warning("mobileproxy balance failed: %s", exc)
            status.proxy_error = _human_cabinet_error(exc)
        row: Optional[dict[str, Any]] = None
        try:
            rows = await client.get_my_proxies()
            row = pick_proxy_row(rows, configured_proxy)
        except (MobileproxyError, TimeoutError, OSError) as exc:
            logger.warning("mobileproxy get_my_proxy failed: %s", exc)
            if not status.proxy_error:
                status.proxy_error = _human_cabinet_error(exc)
        if row:
            status.proxy_id = _proxy_id(row)
            status.proxy_exp = _fmt_exp(str(row.get("proxy_exp") or ""))
            status.proxy_geo = str(row.get("proxy_geo") or "").strip()
            status.proxy_operator = str(row.get("proxy_operator") or "").strip()
            remaining = _first_number(row, _TRAFFIC_KEYS_REMAINING)
            used = _first_number(row, _TRAFFIC_KEYS_USED)
            total = _first_number(row, _TRAFFIC_KEYS_TOTAL)
            if total:
                status.package_mb = total
            if remaining is not None:
                status.remaining_mb = remaining
                if used is None and total:
                    status.used_mb = max(0.0, total - remaining)
            elif used is not None:
                status.used_mb = used
                if total:
                    status.remaining_mb = max(0.0, total - used)
        status.is_residential = looks_residential(
            configured_proxy=configured_proxy, row=row
        )
        if status.is_residential and status.used_mb is None:
            try:
                days = status.package_days
                traffic = await client.get_residential_traffic(
                    proxy_id=status.proxy_id,
                    days=days,
                )
                status.used_mb = _sum_delta_mb(traffic)
                status.remaining_mb = max(0.0, status.package_mb - status.used_mb)
            except (MobileproxyError, TimeoutError, OSError) as exc:
                logger.warning("mobileproxy traffic failed: %s", exc)
                if not status.proxy_error:
                    status.extras.append(_human_cabinet_error(exc))

    await asyncio.gather(_spfa(), _proxy())
    return status


async def load_market_account_status_html(*, detailed: bool = False) -> str:
    now = time.monotonic()
    async with _cache_lock:
        if now - float(_cache.get("at") or 0) < _CACHE_TTL_SEC and _cache.get("html_short"):
            return str(_cache["html_detail"] if detailed else _cache["html_short"])
        try:
            status = await _load_status()
        except Exception:
            logger.exception("Failed to load market account status")
            return ""
        _cache["at"] = time.monotonic()
        _cache["html_short"] = status.short_html()
        _cache["html_detail"] = status.detail_html()
        return str(_cache["html_detail"] if detailed else _cache["html_short"])
