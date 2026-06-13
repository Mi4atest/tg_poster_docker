"""Instagram Graph API: permalink и комментарии к своим постам."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import aiohttp

from app.workers.instagram.token_manager import InstagramGraphTokenManager

logger = logging.getLogger(__name__)

UNAVAILABLE_COMMENT = "#неактуально"


class InstagramGraphClient:
    def __init__(self) -> None:
        self.token_manager = InstagramGraphTokenManager()
        self.access_token = self.token_manager.get_access_token()
        self.api_version = os.getenv("INSTAGRAM_GRAPH_API_VERSION", "v19.0").strip()
        self.timeout_seconds = int(os.getenv("INSTAGRAM_GRAPH_TIMEOUT_SECONDS", "60"))

    @property
    def enabled(self) -> bool:
        return bool(self.access_token and self.token_manager.get_ig_user_id())

    @property
    def base_url(self) -> str:
        return f"https://graph.facebook.com/{self.api_version}"

    async def fetch_media_permalink(self, media_id: str) -> Tuple[Optional[str], Optional[str]]:
        """Вернуть (permalink, shortcode) для опубликованного медиа."""
        if not media_id or not self.enabled:
            return None, None
        endpoint = f"{self.base_url}/{media_id}"
        params = {"fields": "permalink,shortcode", "access_token": self.access_token}
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(endpoint, params=params) as response:
                data = await response.json(content_type=None)
                if response.status >= 400:
                    logger.error("Graph API permalink error (%s): %s", response.status, data)
                    return None, None
                permalink = (data or {}).get("permalink")
                shortcode = (data or {}).get("shortcode")
                return (
                    str(permalink).strip() if permalink else None,
                    str(shortcode).strip() if shortcode else None,
                )

    async def list_comments(self, media_id: str, *, limit: int = 50) -> List[dict]:
        if not media_id or not self.enabled:
            return []
        endpoint = f"{self.base_url}/{media_id}/comments"
        params = {
            "fields": "id,text,username,timestamp",
            "limit": str(min(limit, 100)),
            "access_token": self.access_token,
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        items: List[dict] = []
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(endpoint, params=params) as response:
                data = await response.json(content_type=None)
                if response.status >= 400:
                    logger.warning("Graph API list comments error (%s): %s", response.status, data)
                    return []
                for item in (data or {}).get("data", []):
                    if item:
                        items.append(dict(item))
        return items

    async def list_comment_texts(self, media_id: str, *, limit: int = 50) -> List[str]:
        texts: List[str] = []
        for item in await self.list_comments(media_id, limit=limit):
            text = (item.get("text") or "").strip()
            if text:
                texts.append(text)
        return texts

    async def find_unavailable_comment_ids(self, media_id: str) -> List[str]:
        needle = UNAVAILABLE_COMMENT.lower()
        ids: List[str] = []
        for item in await self.list_comments(media_id, limit=100):
            text = (item.get("text") or "").strip()
            cid = str(item.get("id") or "").strip()
            if cid and needle in text.lower():
                ids.append(cid)
        return ids

    async def has_unavailable_comment(self, media_id: str) -> bool:
        return bool(await self.find_unavailable_comment_ids(media_id))

    async def delete_comment(self, comment_id: str) -> bool:
        if not comment_id or not self.enabled:
            return False
        endpoint = f"{self.base_url}/{comment_id}"
        params = {"access_token": self.access_token}
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.delete(endpoint, params=params) as response:
                data = await response.json(content_type=None)
                if response.status >= 400:
                    logger.error("Graph API delete comment error (%s): %s", response.status, data)
                    return False
                return bool((data or {}).get("success"))

    async def post_comment(self, media_id: str, message: str) -> bool:
        if not media_id or not message or not self.enabled:
            return False
        endpoint = f"{self.base_url}/{media_id}/comments"
        payload = {"message": message, "access_token": self.access_token}
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(endpoint, data=payload) as response:
                data = await response.json(content_type=None)
                if response.status >= 400:
                    logger.error("Graph API post comment error (%s): %s", response.status, data)
                    return False
                return bool((data or {}).get("id"))

    async def list_recent_media(self, *, limit: int = 25) -> List[dict]:
        items, _ = await self.list_all_media(max_items=limit)
        return items

    async def list_all_media(
        self,
        *,
        max_items: int = 500,
        since: Optional[datetime] = None,
        page_delay_sec: float = 1.0,
    ) -> Tuple[List[dict], bool]:
        """Загрузить медиа с пагинацией. Возвращает (items, truncated)."""
        import asyncio

        ig_user_id = self.token_manager.get_ig_user_id()
        if not ig_user_id or not self.enabled:
            return [], False

        since_utc = None
        if since is not None:
            since_utc = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
            if since_utc.tzinfo != timezone.utc:
                since_utc = since_utc.astimezone(timezone.utc)

        endpoint = f"{self.base_url}/{ig_user_id}/media"
        params = {
            "fields": "id,permalink,caption,timestamp",
            "limit": "50",
            "access_token": self.access_token,
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        collected: List[dict] = []
        truncated = False
        next_url: Optional[str] = None

        async with aiohttp.ClientSession(timeout=timeout) as session:
            while True:
                if next_url:
                    request = session.get(next_url)
                else:
                    request = session.get(endpoint, params=params)

                async with request as response:
                    data = await response.json(content_type=None)
                    if response.status >= 400:
                        logger.error("Graph API list media error (%s): %s", response.status, data)
                        break

                    batch = list((data or {}).get("data", []))
                    stop_pagination = False
                    for item in batch:
                        if since_utc and item.get("timestamp"):
                            try:
                                ts = datetime.fromisoformat(
                                    str(item["timestamp"]).replace("Z", "+00:00")
                                )
                                if ts < since_utc:
                                    stop_pagination = True
                                    break
                            except Exception:
                                pass
                        collected.append(item)
                        if len(collected) >= max_items:
                            truncated = True
                            stop_pagination = True
                            break

                    if stop_pagination or truncated:
                        break

                    paging = (data or {}).get("paging") or {}
                    next_url = paging.get("next")
                    if not next_url:
                        break

                await asyncio.sleep(page_delay_sec)

        return collected, truncated
