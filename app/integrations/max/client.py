import asyncio
import logging
from typing import Any, Optional

import aiohttp


logger = logging.getLogger(__name__)


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
                        if response.status >= 400:
                            raise RuntimeError(f"Max API {http_method} {path} failed: status={response.status}, body={data}")

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
        raise RuntimeError(f"Max API {method_name} failed: {last_error}")

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

    async def edit_message_text(
        self,
        chat_id: str,
        message_id: int,
        text: str,
        parse_mode: Optional[str] = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"text": text}
        if parse_mode:
            body["format"] = "markdown" if "markdown" in parse_mode.lower() else "html"
        return await self._request("PUT", "/messages", payload=body, query={"message_id": str(message_id)})

    async def edit_message_caption(
        self,
        chat_id: str,
        message_id: int,
        caption: str,
        parse_mode: Optional[str] = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"text": caption}
        if parse_mode:
            body["format"] = "markdown" if "markdown" in parse_mode.lower() else "html"
        return await self._request("PUT", "/messages", payload=body, query={"message_id": str(message_id)})
