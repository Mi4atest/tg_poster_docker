"""Dry-run persist-on-create + Graph URL safety (без media_publish и без Telegram).

Запуск на хосте:
  PYTHONPATH=/root/tg_poster_docker python scripts/dry_run_media_persist.py

В контейнере:
  docker exec -w /app -e PYTHONPATH=/app tg_poster_app python /app/scripts/dry_run_media_persist.py
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.media_persist_service import (
    is_telegram_bot_file_url,
    persist_post_media,
    write_post_sidecar,
)
from app.workers.instagram.graph_publisher import InstagramGraphPublisher


def _ok(msg: str) -> None:
    print(f"OK  {msg}")


def _fail(msg: str) -> None:
    raise AssertionError(msg)


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="media-persist-dryrun-") as raw:
        tmp = Path(raw)
        import app.services.media_persist_service as persist_svc
        import app.workers.instagram.graph_publisher as graph_mod

        persist_svc.MEDIA_DIR = tmp
        graph_mod.MEDIA_DIR = tmp

        def fake_download(file_id, save_path, timeout=60, token=None):
            dest = Path(save_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"dryrun-" + str(file_id).encode())
            return True

        persist_svc.download_telegram_file = fake_download  # type: ignore[method-assign]
        persist_svc.resolve_bot_token = lambda: "dry-token"  # type: ignore[method-assign]

        storage = "2026/08/25/dryrun_post"
        result = persist_post_media(storage, ["photo-id-1", "photo-id-2"], ["video-id-1"])
        if result["photos_ok"] != 2 or result["videos_ok"] != 1 or result["errors"]:
            _fail(f"persist_post_media unexpected: {result}")
        write_post_sidecar(
            storage,
            "dryrun caption",
            photos=["photo-id-1", "photo-id-2"],
            videos=["video-id-1"],
            persist_result=result,
        )
        sidecar = json.loads((tmp / storage / "media.json").read_text(encoding="utf-8"))
        if sidecar.get("persisted", {}).get("photos_ok") != 2:
            _fail(f"sidecar persist stats missing: {sidecar}")
        _ok(f"persist wrote photo_0.jpg photo_1.jpg video_0.mp4 under {storage}")

        with patch.object(graph_mod, "InstagramGraphTokenManager") as tm_cls:
            tm = MagicMock()
            tm.get_access_token.return_value = "ig-token"
            tm.get_ig_user_id.return_value = "ig-user"
            tm.get_media_base_url.return_value = "https://appleshop.ap43.ru/media"
            tm_cls.return_value = tm
            pub = InstagramGraphPublisher()

        post = SimpleNamespace(
            id="dryrun-post",
            storage_path=storage,
            photos=["photo-id-1", "photo-id-2"],
            videos=["video-id-1"],
            vk_post_id=None,
            text="dryrun caption",
        )

        async def no_vk(_post):
            return []

        pub._get_vk_wall_photo_urls = no_vk  # type: ignore[method-assign]
        pub._get_vk_market_photo_urls = no_vk  # type: ignore[method-assign]

        merged, audit = await pub._assemble_merged_media_items(post)
        candidates = await pub._collect_media_candidates(post)
        candidate_names = [name for name, _items in candidates]
        urls = [url for _kind, url in merged]
        if "telegram" in candidate_names:
            _fail(f"telegram source must not be a Graph candidate: {candidate_names}")
        if candidate_names[:1] != ["local"]:
            _fail(f"expected local first, got {candidate_names}")
        if any(is_telegram_bot_file_url(url) for url in urls):
            _fail(f"bot-token URL leaked into Graph candidates: {urls}")
        if not all("/media/2026/08/25/dryrun_post/" in url for url in urls):
            _fail(f"expected public /media/ URLs, got {urls}")
        if {row["source"] for row in audit} != {"local"}:
            _fail(f"unexpected audit sources: {audit}")
        _ok(f"graph assembly source=local urls={len(urls)} no bot-token")

        leaked = await pub._create_container(
            {"image_url": "https://api.telegram.org/file/botSECRET/photos/x.jpg"}
        )
        if leaked is not None:
            _fail("bot-token image_url must be refused")
        _ok("create_container refuses telegram bot-token URL")

        captured: list[str] = []

        async def fake_create(params):
            for key in ("image_url", "video_url"):
                if params.get(key):
                    captured.append(str(params[key]))
            return f"cid-{len(captured)}"

        pub._create_container = fake_create  # type: ignore[method-assign]
        pub._wait_for_container_ready = AsyncMock(return_value=True)  # type: ignore[method-assign]
        creation_id = await pub._create_media_container(merged, "caption")
        if not creation_id:
            _fail("local media container should succeed with fake Graph")
        if any(is_telegram_bot_file_url(url) for url in captured):
            _fail(f"bot-token reached fake Graph payload: {captured}")
        _ok(f"fake Graph payload urls={captured} creation_id={creation_id}")

    print("RESULT=PASS")


if __name__ == "__main__":
    asyncio.run(main())
