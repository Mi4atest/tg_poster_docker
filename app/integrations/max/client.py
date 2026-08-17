import asyncio
import logging
from typing import Any, Optional

import aiohttp


logger = logging.getLogger(__name__)

_MEDIA_ATTACHMENT_TYPES = frozenset({"image", "video", "file", "audio"})


def extract_message_id(resp: dict | None) -> str | None:
    """Достаёт mid из типичных вариантов ответа Max API."""
    if not isinstance(resp, dict):
        return None
    result = resp.get("result")
    if isinstance(result, dict):
        mid = result.get("message_id") or result.get("id") or result.get("mid")
        if mid:
            return str(mid)
    if isinstance(result, list) and result:
        first = result[0]
        if isinstance(first, dict):
            mid = first.get("message_id") or first.get("id") or first.get("mid")
            if mid:
                return str(mid)
    msg = resp.get("message")
    if isinstance(msg, dict):
        mid = msg.get("message_id") or msg.get("id") or msg.get("mid")
        if mid:
            return str(mid)
        body = msg.get("body")
        if isinstance(body, dict):
            nested_mid = body.get("mid") or body.get("message_id") or body.get("id")
            if nested_mid:
                return str(nested_mid)
    body = resp.get("body")
    if isinstance(body, dict):
        nested_mid = body.get("mid") or body.get("message_id") or body.get("id")
        if nested_mid:
            return str(nested_mid)
    top_mid = resp.get("message_id") or resp.get("id") or resp.get("mid")
    return str(top_mid) if top_mid else None


def create_max_api_client() -> "MaxApiClient":
    """Клиент Max API: токен из настроек бота (encrypted_secrets), fallback — MAX_BOT_TOKEN из .env."""
    from app.config.settings import MAX_API_BASE_URL, MAX_BOT_TOKEN
    from app.services.settings_service import get_settings_service

    service = get_settings_service()
    token = (service.get_secret("max_bot_token") or MAX_BOT_TOKEN or "").strip()
    base_url = (MAX_API_BASE_URL or "https://botapi.max.ru").strip()
    return MaxApiClient(token, base_url)


def _preservable_attachments_from_message(msg: dict[str, Any]) -> list[dict[str, Any]]:
    """Медиа-вложения из GET /messages для повторной отправки при PUT (без link preview type=share)."""
    body: dict[str, Any] = {}
    if isinstance(msg.get("body"), dict):
        body = msg["body"]
    elif isinstance(msg.get("message"), dict):
        inner = msg["message"].get("body") or msg["message"]
        if isinstance(inner, dict):
            body = inner

    attachments = body.get("attachments")
    if not isinstance(attachments, list):
        return []

    preserved: list[dict[str, Any]] = []
    for item in attachments:
        if not isinstance(item, dict):
            continue
        a_type = item.get("type")
        payload = item.get("payload")
        if a_type in _MEDIA_ATTACHMENT_TYPES and isinstance(payload, dict):
            preserved.append({"type": a_type, "payload": payload})
    return preserved


class MaxApiClient:
    def __init__(self, token: str, base_url: str):
        self.token = token
        self.base_url = base_url.rstrip("/")

    async def _request(
        self,
        http_method: str,
        path: str,
        payload: Optional[dict[str, Any]] = None,
        query: Optional[dict[str, Any]] = None,
        retries: int = 2,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        last_error: Optional[Exception] = None
        async with aiohttp.ClientSession() as session:
            for attempt in range(retries + 1):
                try:
                    async with session.request(
                        http_method,
                        url,
                        params=query,
                        json=payload,
                        timeout=45,
                        headers={
                            "Authorization": self.token,
                            "Content-Type": "application/json",
                        },
                    ) as response:
                        data = await response.json(content_type=None)
                        if response.status == 429:
                            retry_after = 1.0
                            raw_retry = response.headers.get("Retry-After")
                            if raw_retry:
                                try:
                                    retry_after = min(float(raw_retry), 15.0)
                                except ValueError:
                                    retry_after = 1.0
                            last_error = RuntimeError(
                                f"Max API {http_method} {path} failed: status=429, body={data}"
                            )
                            if attempt < retries:
                                logger.info(
                                    "Max API 429 on %s %s, retry in %.1fs",
                                    http_method,
                                    path,
                                    retry_after,
                                )
                                await asyncio.sleep(retry_after)
                                continue
                            raise last_error
                        if response.status >= 400:
                            raise RuntimeError(
                                f"Max API {http_method} {path} failed: status={response.status}, body={data}"
                            )

                        # Max API иногда не возвращает поле ok, поэтому считаем успешным любой 2xx без code=error
                        if isinstance(data, dict) and data.get("code") and data.get("success") is False:
                            raise RuntimeError(f"Max API {http_method} {path} business error: {data}")
                        return data
                except Exception as exc:
                    last_error = exc
                    if attempt < retries:
                        await asyncio.sleep(1.0 + attempt)
                        continue
                    raise
        raise RuntimeError(f"Max API {http_method} {path} failed: {last_error}")

    async def get_chat(self, chat_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/chats/{chat_id}", payload=None, query=None)

    async def get_me(self) -> dict[str, Any]:
        return await self._request("GET", "/me", payload=None, query=None)

    async def get_message(self, message_id: str) -> dict[str, Any]:
        """Сообщение по mid (в т.ч. публичное поле url для постов канала)."""
        mid = str(message_id).strip()
        return await self._request("GET", f"/messages/{mid}", payload=None, query=None)

    async def send_message(
        self,
        chat_id: str,
        text: str,
        parse_mode: Optional[str] = None,
        disable_web_page_preview: bool = True,
        attachments: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "text": text,
            "disable_link_preview": disable_web_page_preview,
        }
        if attachments:
            body["attachments"] = attachments
        if parse_mode:
            body["format"] = "markdown" if "markdown" in parse_mode.lower() else "html"
        return await self._request("POST", "/messages", payload=body, query={"chat_id": chat_id})

    async def send_media_group(self, chat_id: str, media: list[dict[str, Any]]) -> dict[str, Any]:
        first = media[0] if media else {}
        caption = first.get("caption") or ""
        parse_mode = first.get("parse_mode")
        attachments = []
        for item in media:
            a_type = item.get("type")
            payload = item.get("payload")
            if not payload:
                continue
            attachments.append({"type": a_type, "payload": payload})

        return await self.send_message(
            chat_id=chat_id,
            text=caption,
            parse_mode=parse_mode,
            disable_web_page_preview=True,
            attachments=attachments,
        )

    async def create_upload(self, upload_type: str) -> dict[str, Any]:
        return await self._request("POST", "/uploads", payload=None, query={"type": upload_type})

    async def upload_binary(self, upload_url: str, file_bytes: bytes, filename: str) -> dict[str, Any]:
        data = aiohttp.FormData()
        data.add_field("data", file_bytes, filename=filename, content_type="application/octet-stream")
        async with aiohttp.ClientSession() as session:
            async with session.post(upload_url, data=data, timeout=120) as response:
                raw_text = await response.text()
                try:
                    import json
                    body = json.loads(raw_text) if raw_text else {}
                except Exception:
                    body = {"raw": raw_text}
                if response.status >= 400:
                    raise RuntimeError(f"Max upload failed: status={response.status}, body={body}")
                return body

    async def _edit_message(
        self,
        message_id: int | str,
        text: str,
        parse_mode: Optional[str] = None,
        *,
        preserve_attachments: bool = True,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "text": text,
            "disable_link_preview": True,
        }
        if parse_mode:
            body["format"] = "markdown" if "markdown" in parse_mode.lower() else "html"

        if preserve_attachments:
            try:
                msg = await self.get_message(str(message_id))
                preserved = _preservable_attachments_from_message(msg)
                if preserved:
                    body["attachments"] = preserved
            except Exception as exc:
                logger.warning(
                    "Max edit: could not preserve attachments for message_id=%s: %s",
                    message_id,
                    exc,
                )

        return await self._request("PUT", "/messages", payload=body, query={"message_id": str(message_id)})

    async def edit_message_text(
        self,
        chat_id: str,
        message_id: int | str,
        text: str,
        parse_mode: Optional[str] = None,
    ) -> dict[str, Any]:
        return await self._edit_message(message_id, text, parse_mode)

    async def edit_message_caption(
        self,
        chat_id: str,
        message_id: int | str,
        caption: str,
        parse_mode: Optional[str] = None,
    ) -> dict[str, Any]:
        return await self._edit_message(message_id, caption, parse_mode)
