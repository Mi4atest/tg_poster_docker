import logging
from typing import Optional

from app.workers.instagram.graph_publisher import InstagramGraphPublisher

logger = logging.getLogger(__name__)

_LAST_GRAPH_PUBLISHER: Optional[InstagramGraphPublisher] = None


def last_instagram_publish_error() -> Optional[str]:
    """Последняя ошибка Graph publisher (для удлинения retry при URI reject)."""
    pub = _LAST_GRAPH_PUBLISHER
    if not pub:
        return None
    return pub.last_error


async def publish_post_to_instagram(post_id: str) -> bool:
    """Публикация поста в Instagram через официальный Graph API."""
    global _LAST_GRAPH_PUBLISHER
    graph_publisher = InstagramGraphPublisher()
    _LAST_GRAPH_PUBLISHER = graph_publisher
    if graph_publisher.enabled:
        logger.info("Публикация в Instagram через Graph API")
    else:
        logger.error(
            "Graph API не настроен. Нужны INSTAGRAM_GRAPH_ACCESS_TOKEN и INSTAGRAM_GRAPH_USER_ID"
        )
    ok = await graph_publisher.publish_post(post_id)

    if ok:
        try:
            from app.workers.instagram.story_publisher import maybe_auto_publish_instagram_story

            await maybe_auto_publish_instagram_story(post_id)
        except Exception as story_err:
            logger.error(
                "Auto IG story after feed publish failed for %s: %s",
                post_id,
                story_err,
            )
    return bool(ok)
