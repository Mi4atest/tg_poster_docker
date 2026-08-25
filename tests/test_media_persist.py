"""Persist-on-create: медиагруппа на диск без утечки токена бота в Graph."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.media_persist_service import (
    is_telegram_bot_file_url,
    persist_post_media,
    photo_path,
    video_path,
    write_post_sidecar,
)
from app.workers.instagram.graph_publisher import InstagramGraphPublisher


def test_is_telegram_bot_file_url():
    assert is_telegram_bot_file_url(
        "https://api.telegram.org/file/bot123:ABC/photos/file.jpg"
    )
    assert not is_telegram_bot_file_url("https://appleshop.ap43.ru/media/2026/01/01/x/photo_0.jpg")
    assert not is_telegram_bot_file_url("https://appleshop.ap43.ru/api/telegram/file/AgAC123")


def test_persist_post_media_writes_files(tmp_path, monkeypatch):
    import app.services.media_persist_service as svc

    monkeypatch.setattr(svc, "MEDIA_DIR", tmp_path)

    def fake_download(file_id, save_path, timeout=60, token=None):
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        Path(save_path).write_bytes(b"bytes-" + file_id.encode())
        return True

    monkeypatch.setattr(svc, "download_telegram_file", fake_download)
    monkeypatch.setattr(svc, "resolve_bot_token", lambda: "token")

    result = persist_post_media("2026/08/25/demo", ["phA", "phB"], ["vid1"])
    assert result["photos_ok"] == 2
    assert result["videos_ok"] == 1
    assert not result["errors"]
    post_dir = tmp_path / "2026/08/25/demo"
    assert photo_path(post_dir, 0).read_bytes() == b"bytes-phA"
    assert photo_path(post_dir, 1).read_bytes() == b"bytes-phB"
    assert video_path(post_dir, 0).read_bytes() == b"bytes-vid1"

    again = persist_post_media("2026/08/25/demo", ["phA"], ["vid1"])
    assert again["photos_skipped"] == 1
    assert again["videos_skipped"] == 1


def test_write_post_sidecar_includes_persist_stats(tmp_path, monkeypatch):
    import app.services.media_persist_service as svc
    import json

    monkeypatch.setattr(svc, "MEDIA_DIR", tmp_path)
    write_post_sidecar(
        "2026/08/25/demo",
        "hello",
        photos=["a"],
        videos=[],
        persist_result={"photos_ok": 1, "errors": []},
    )
    payload = json.loads((tmp_path / "2026/08/25/demo/media.json").read_text())
    assert payload["photos"] == ["a"]
    assert payload["persisted"]["photos_ok"] == 1
    assert (tmp_path / "2026/08/25/demo/text.txt").read_text() == "hello"


def _publisher_with_media_dir(tmp_path, monkeypatch) -> InstagramGraphPublisher:
    monkeypatch.setattr("app.workers.instagram.graph_publisher.MEDIA_DIR", tmp_path)
    with patch("app.workers.instagram.graph_publisher.InstagramGraphTokenManager") as tm_cls:
        tm = MagicMock()
        tm.get_access_token.return_value = "ig-token"
        tm.get_ig_user_id.return_value = "123"
        tm.get_media_base_url.return_value = "https://appleshop.ap43.ru/media"
        tm_cls.return_value = tm
        return InstagramGraphPublisher()


def test_graph_local_urls_have_no_bot_token(tmp_path, monkeypatch):
    post_dir = tmp_path / "2026/08/25/demo"
    post_dir.mkdir(parents=True)
    (post_dir / "photo_0.jpg").write_bytes(b"jpg")
    (post_dir / "video_0.mp4").write_bytes(b"mp4")
    pub = _publisher_with_media_dir(tmp_path, monkeypatch)
    post = SimpleNamespace(
        id="p1",
        storage_path="2026/08/25/demo",
        photos=["tg-photo"],
        videos=["tg-video"],
        vk_post_id=None,
    )

    async def no_vk(_post):
        return []

    pub._get_vk_wall_photo_urls = no_vk

    import asyncio

    items, audit = asyncio.run(pub._assemble_merged_media_items(post))
    urls = [url for _, url in items]
    assert len(urls) == 2
    assert all(url.startswith("https://appleshop.ap43.ru/media/2026/08/25/demo/") for url in urls)
    assert all(not is_telegram_bot_file_url(url) for url in urls)
    assert {row["source"] for row in audit} == {"local"}


def test_create_container_refuses_bot_token_url(tmp_path, monkeypatch):
    pub = _publisher_with_media_dir(tmp_path, monkeypatch)

    import asyncio

    result = asyncio.run(
        pub._create_container(
            {
                "image_url": "https://api.telegram.org/file/botSECRET/photos/x.jpg",
            }
        )
    )
    assert result is None
    assert "токен" in (pub.last_error or "").lower() or "Отказ" in (pub.last_error or "")


def test_safe_media_items_drops_bot_token_urls(tmp_path, monkeypatch):
    pub = _publisher_with_media_dir(tmp_path, monkeypatch)
    safe = pub._safe_media_items(
        [
            ("photo", "https://api.telegram.org/file/botSECRET/photos/x.jpg"),
            ("photo", "https://appleshop.ap43.ru/media/2026/a.jpg"),
        ]
    )
    assert safe == [("photo", "https://appleshop.ap43.ru/media/2026/a.jpg")]
