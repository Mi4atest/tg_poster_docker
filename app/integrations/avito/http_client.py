"""Минимальный async-клиент Авито API (token, accounts/self, items)."""
from __future__ import annotations

import asyncio
import json
import logging
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Optional
from urllib.parse import quote, urlencode

import aiohttp

from app.config.settings import APP_LOG_DIR

logger = logging.getLogger(__name__)

_file_handler_added = False


def _ensure_app_avito_log_handler() -> None:
    """Пишет логи модуля в app/logs/avito_http.log (и в корневой логгер, если настроен)."""
    global _file_handler_added
    if _file_handler_added:
        return
    try:
        APP_LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            APP_LOG_DIR / "avito_http.log",
            maxBytes=2_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(fh)
        logger.setLevel(logging.INFO)
        _file_handler_added = True
    except OSError:
        pass

AVITO_API_BASE = "https://api.avito.ru"
# Не более одного параллельного запроса на процесс по умолчанию (лимиты Авито)
_semaphore = asyncio.Semaphore(1)


class AvitoApiError(Exception):
    def __init__(self, message: str, status: Optional[int] = None, body: Optional[str] = None):
        super().__init__(message)
        self.status = status
        self.body = body

    def __str__(self) -> str:
        base = super().__str__()
        if self.body:
            return f"{base}: {self.body[:400]}"
        return base


async def _request_json(
    method: str,
    path: str,
    *,
    token: Optional[str] = None,
    json_data: Optional[dict] = None,
    data: Optional[dict] = None,
    form: bool = False,
) -> Dict[str, Any]:
    _ensure_app_avito_log_handler()
    url = f"{AVITO_API_BASE.rstrip('/')}/{path.lstrip('/')}"
    headers: Dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if json_data is not None and not form:
        headers.setdefault("Content-Type", "application/json")

    async with _semaphore:
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            if form and data:
                body = urlencode(data)
                hdrs = {**headers, "Content-Type": "application/x-www-form-urlencoded"}
                async with session.request(method, url, headers=hdrs, data=body) as resp:
                    text = await resp.text()
                    if resp.status >= 400:
                        logger.warning("Avito %s %s -> %s %s", method, path, resp.status, text[:500])
                        raise AvitoApiError(f"Avito HTTP {resp.status}", status=resp.status, body=text[:2000])
                    if not text:
                        return {}
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return {"raw": text}
            kwargs: Dict[str, Any] = {"headers": headers}
            if json_data is not None:
                kwargs["json"] = json_data
            async with session.request(method, url, **kwargs) as resp:
                text = await resp.text()
                if resp.status == 429:
                    await asyncio.sleep(2.0)
                    raise AvitoApiError("Avito rate limit 429", status=429, body=text[:500])
                if resp.status >= 400:
                    logger.warning("Avito %s %s -> %s %s", method, path, resp.status, text[:500])
                    raise AvitoApiError(f"Avito HTTP {resp.status}", status=resp.status, body=text[:2000])
                if not text:
                    return {}
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"raw": text}


async def fetch_client_credentials_token(client_id: str, client_secret: str) -> Dict[str, Any]:
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    return await _request_json("POST", "/token", data=data, form=True)


async def fetch_account_self(access_token: str) -> Dict[str, Any]:
    return await _request_json("GET", "/core/v1/accounts/self", token=access_token)


async def fetch_item(access_token: str, user_id: int, item_id: int) -> Dict[str, Any]:
    return await _request_json(
        "GET",
        f"/core/v1/accounts/{int(user_id)}/items/{int(item_id)}/",
        token=access_token,
    )


async def post_item_archive(access_token: str, user_id: int, item_id: int) -> Dict[str, Any]:
    """
    Снятие объявления с публикации (архив).

    В открытых выгрузках swagger этот путь может отсутствовать, но на ``api.avito.ru`` он
    встречается у интеграторов: ``POST /core/v1/accounts/{user_id}/items/{item_id}/archive``.
    Пробуем вариант без слэша и со слэшем на конце (разные шлюзы).
    """
    uid, iid = int(user_id), int(item_id)
    paths = (
        f"/core/v1/accounts/{uid}/items/{iid}/archive",
        f"/core/v1/accounts/{uid}/items/{iid}/archive/",
    )
    last: Optional[AvitoApiError] = None
    for path in paths:
        try:
            data = await _request_json("POST", path, token=access_token, json_data=None)
            if isinstance(data, dict):
                result = data.get("result")
                if isinstance(result, dict) and result.get("status") is False:
                    msg = result.get("message") or str(data)
                    raise AvitoApiError(f"Avito: {msg}", body=str(data)[:2000])
            logger.info("Avito archive POST ok path=%s item_id=%s", path, iid)
            return data
        except AvitoApiError as exc:
            last = exc
            if exc.status == 404 and path != paths[-1]:
                continue
            raise
    if last:
        raise last
    return {}


async def put_stock_management_stocks_zero(access_token: str, item_id: int) -> Dict[str, Any]:
    """
    API «Управление остатками» (stock-management): выставить остаток 0.

    Спецификация: ``PUT https://api.avito.ru/stock-management/1/stocks`` с телом
    ``{"stocks": [{"item_id": int, "quantity": 0}]}`` — для объявлений с остатками
    (`developers.avito.ru` / каталог *Управление остатками*).
    """
    body = {"stocks": [{"item_id": int(item_id), "quantity": 0}]}
    data = await _request_json(
        "PUT",
        "/stock-management/1/stocks",
        token=access_token,
        json_data=body,
    )
    try:
        snippet = json.dumps(data, ensure_ascii=False)[:800]
    except (TypeError, ValueError):
        snippet = str(data)[:800]
    logger.info("Avito stock-management PUT quantity=0 item_id=%s response=%s", item_id, snippet)
    return data


async def post_item_update(access_token: str, user_id: int, item_id: int, body: dict) -> Dict[str, Any]:
    """
    Запись полей объявления в Core API.

    В публичном Swagger каталога «Объявления» для пути
    ``/core/v1/accounts/{user_id}/items/{item_id}/`` указан только **GET**;
    POST/PATCH для создания/редактирования через этот URL у многих приложений
    дают **405** / **404** (см. ``app/logs/avito_http.log``).

    - ``item_id == 0``: в коде остаётся попытка **POST** ``.../items/0/`` (исторический контракт);
      при 405 см. ``AvitoAutoCreateUnavailableError`` в publisher.
    - существующее объявление: **PATCH**, затем **PUT** при HTTP 405.
    """
    path = f"/core/v1/accounts/{int(user_id)}/items/{int(item_id)}/"
    if int(item_id) == 0:
        methods: tuple[str, ...] = ("POST",)
    else:
        methods = ("PATCH", "PUT")

    data: Dict[str, Any] = {}
    for method in methods:
        try:
            data = await _request_json(
                method,
                path,
                token=access_token,
                json_data=body,
            )
            break
        except AvitoApiError as exc:
            if exc.status == 405 and method != methods[-1]:
                continue
            raise

    if isinstance(data, dict):
        result = data.get("result")
        if isinstance(result, dict) and result.get("status") is False:
            msg = result.get("message") or str(data)
            raise AvitoApiError(f"Avito: {msg}", body=str(data)[:2000])
    return data


async def post_autoload_upload(access_token: str) -> Dict[str, Any]:
    """Запуск выгрузки по URL фида из профиля автозагрузки."""
    return await _request_json("POST", "/autoload/v1/upload", token=access_token, json_data={})


async def get_autoload_report_items(access_token: str, ad_ids_query: str) -> Dict[str, Any]:
    """GET /autoload/v2/reports/items?query=... — статус объявления из файла по Id."""
    q = quote(str(ad_ids_query), safe=",|")
    path = f"/autoload/v2/reports/items?query={q}"
    return await _request_json("GET", path, token=access_token)


async def post_item_price_update(access_token: str, item_id: int, price_rub: int) -> Dict[str, Any]:
    """
    Смена цены объявления (каталог API Авито).

    POST ``/core/v1/items/{item_id}/update_price`` с телом ``{"price": int}`` —
    не путать с PATCH на ``.../accounts/{user_id}/items/{id}/`` (там нет смены цены, 404/405).
    """
    path = f"/core/v1/items/{int(item_id)}/update_price"
    data = await _request_json(
        "POST",
        path,
        token=access_token,
        json_data={"price": int(price_rub)},
    )
    if isinstance(data, dict):
        result = data.get("result")
        if isinstance(result, dict) and result.get("status") is False:
            msg = result.get("message") or str(data)
            raise AvitoApiError(f"Avito: {msg}", body=str(data)[:2000])
    logger.info("Avito price updated item_id=%s price=%s", item_id, price_rub)
    return data
