"""Dry-run: URI-reject abort + Telegram fallback, без media_publish.

Запуск в контейнере:
  docker cp scripts/dry_run_ig_uri_fallback.py tg_poster_app:/tmp/dry_run_ig_uri_fallback.py
  docker exec -w /app -e PYTHONPATH=/app tg_poster_app python /tmp/dry_run_ig_uri_fallback.py
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import MethodType
from unittest.mock import AsyncMock

from app.db.database import SessionLocal
from app.db.post_queries import fetch_post
from app.utils.text_formatter import format_for_instagram
from app.workers.instagram.graph_publisher import InstagramGraphPublisher

POST_ID = "d4362b12-5a78-49bd-87bd-321dd6ba6a16"
APP_LOG = Path("/app/app/logs/instagram_graph.log")


def log_app(msg: str) -> None:
    APP_LOG.parent.mkdir(parents=True, exist_ok=True)
    with APP_LOG.open("a", encoding="utf-8") as f:
        f.write(f"DRYRUN {msg}\n")
    print(msg)


async def main() -> None:
    pub = InstagramGraphPublisher()
    db = SessionLocal()
    try:
        post = fetch_post(db, POST_ID)
        assert post, "post not found"
        candidates = await pub._collect_media_candidates(post)
        summary = {name: len(urls) for name, urls in candidates}
        log_app(f"candidates={summary}")
        assert "vk" in summary and summary["vk"] == 6, summary
        assert "telegram" in summary and summary["telegram"] == 6, summary

        call_log: list[dict] = []

        async def fake_create_container(self, params: dict):
            url = params.get("image_url") or ""
            call_log.append(
                {
                    "has_image": bool(url),
                    "is_carousel_item": params.get("is_carousel_item"),
                    "media_type": params.get("media_type"),
                    "children_n": (
                        len((params.get("children") or "").split(","))
                        if params.get("children")
                        else 0
                    ),
                }
            )
            if url and getattr(self, "_dry_fail_once", True):
                self._dry_fail_once = False
                self._last_graph_error = "Only photo or video can be accepted as media type."
                self._last_graph_error_code = 9004
                self._last_graph_error_subcode = 2207052
                return None
            if params.get("media_type") == "CAROUSEL":
                return "carousel-dry-1"
            return f"child-dry-{len(call_log)}"

        pub._create_container = MethodType(fake_create_container, pub)

        caption = format_for_instagram(post.text)
        creation_id = None
        used_source = None
        for source_name, media_urls in candidates:
            pub._uri_reject_seen = False
            pub._dry_fail_once = source_name == "vk"
            cid = await pub._create_media_container(media_urls, caption)
            log_app(
                f"try source={source_name} ok={bool(cid)} uri_reject={pub._uri_reject_seen} "
                f"photos={pub._last_children_count} err_code={pub._last_graph_error_code}"
            )
            if cid:
                creation_id = cid
                used_source = source_name
                break
            if pub._uri_reject_seen or pub._is_skippable_media_error():
                log_app(f"fallback from {source_name}")
                continue
            break

        assert creation_id == "carousel-dry-1", creation_id
        assert used_source == "telegram", used_source
        assert pub._last_children_count == 6, pub._last_children_count
        carousel_calls = [c for c in call_log if c.get("media_type") == "CAROUSEL"]
        assert len(carousel_calls) == 1 and carousel_calls[0]["children_n"] == 6, carousel_calls

        pub._publish_creation = AsyncMock(
            side_effect=AssertionError("media_publish must not be called")
        )
        log_app(
            f"PASS dry-run: used_source={used_source} photos={pub._last_children_count} "
            f"creation_id={creation_id} media_publish=SKIPPED"
        )
        print("RESULT=PASS")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
