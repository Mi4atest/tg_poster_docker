"""Гейты и параметры публикации Instagram Stories через Graph API."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.workers.instagram.graph_publisher import InstagramGraphPublisher
from app.workers.instagram.story_publisher import (
    InstagramStoryPublisher,
    ig_story_block_reason,
)
from app.workers.vk.story_publisher import parse_vk_story_ref, pick_largest_photo_url


def test_story_container_is_stories_without_caption():
    params = InstagramGraphPublisher.story_container_params(
        "https://cdn.example/media/story_previews/x_ig_1.jpg"
    )
    assert params["media_type"] == "STORIES"
    assert params["image_url"].endswith("x_ig_1.jpg")
    assert "caption" not in params
    assert "user_tags" not in params


def test_ig_story_block_reason_requires_feed_post_and_photos():
    assert ig_story_block_reason(None) == "Пост не найден"
    assert ig_story_block_reason({"photos": ["p1"], "is_published_instagram": False}) == (
        "Сначала опубликуйте пост в ленту Instagram"
    )
    assert ig_story_block_reason({"photos": [], "is_published_instagram": True}) == (
        "Нет фотографий для сторис"
    )
    assert ig_story_block_reason({"photos": ["p1"], "is_published_instagram": True}) is None


def test_parse_vk_story_ref():
    assert (
        parse_vk_story_ref("https://vk.ru/stories-129808251_456242029")
        == "-129808251_456242029"
    )
    assert (
        parse_vk_story_ref("https://vk.com/stories-129808251_456242029")
        == "-129808251_456242029"
    )
    assert parse_vk_story_ref("") is None
    assert parse_vk_story_ref("https://vk.ru/wall-1_2") is None


def test_pick_largest_photo_url():
    url = pick_largest_photo_url(
        [
            {"width": 100, "height": 200, "url": "https://cdn/small.jpg"},
            {"width": 1080, "height": 1920, "url": "https://cdn/full.jpg"},
            {"width": 500, "height": 800, "url": "https://cdn/mid.jpg"},
        ]
    )
    assert url == "https://cdn/full.jpg"


def test_publish_story_image_sends_stories_media_type():
    with patch("app.workers.instagram.graph_publisher.InstagramGraphTokenManager") as tm_cls:
        tm = MagicMock()
        tm.get_access_token.return_value = "token"
        tm.get_ig_user_id.return_value = "123"
        tm.get_media_base_url.return_value = "https://cdn.example/media"
        tm.preflight_or_error = AsyncMock(return_value=(True, None))
        tm_cls.return_value = tm

        pub = InstagramGraphPublisher()
        captured = {}

        async def fake_create(params):
            captured.update(params)
            return "cid-1"

        async def fake_publish(_creation_id):
            pub._last_published_media_id = "media-9"
            return True

        pub._create_container = fake_create
        pub._wait_for_container_ready = AsyncMock(return_value=True)
        pub._publish_creation = fake_publish

        media_id = asyncio.run(
            pub.publish_story_image("https://cdn.example/media/story.jpg")
        )
        assert media_id == "media-9"
        assert captured == {
            "image_url": "https://cdn.example/media/story.jpg",
            "media_type": "STORIES",
        }


def test_publish_with_fallbacks_switches_on_uri_reject():
    with patch("app.workers.instagram.graph_publisher.InstagramGraphTokenManager") as tm_cls:
        tm = MagicMock()
        tm.get_access_token.return_value = "token"
        tm.get_ig_user_id.return_value = "123"
        tm.get_media_base_url.return_value = "https://cdn.example/media"
        tm.preflight_or_error = AsyncMock(return_value=(True, None))
        tm_cls.return_value = tm

        publisher = InstagramStoryPublisher()
        publisher.publish_retries = 2
        publisher.publish_retry_delay_seconds = 0
        tried = []

        async def fake_publish(url):
            tried.append(url)
            if "local" in url:
                publisher.graph._last_graph_error = "Media download error"
                publisher.graph._last_graph_error_code = 9004
                publisher.graph._last_graph_error_subcode = 2207052
                publisher.graph._last_published_media_id = None
                return None
            publisher.graph._last_graph_error = None
            publisher.graph._last_published_media_id = "media-vk"
            return "media-vk"

        publisher.graph.publish_story_image = fake_publish
        post = MagicMock()
        post.id = "post-1"
        media_id = asyncio.run(
            publisher._publish_with_fallbacks(
                post,
                [
                    ("local_media", "https://cdn.example/media/local.jpg"),
                    ("vk_story_cdn", "https://sun9.userapi.com/vk.jpg"),
                ],
            )
        )
        assert media_id == "media-vk"
        assert tried == [
            "https://cdn.example/media/local.jpg",
            "https://sun9.userapi.com/vk.jpg",
        ]
