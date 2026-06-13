"""Комментарии к посту в ленте VK от лица сообщества (#неактуально).

Используется при пометке товара «недоступен» и снятии пометки при восстановлении.
Логика зеркалит Instagram (graph_client): комментарий ищется по тексту от лица
группы, id нигде не хранится.
"""
import logging
from typing import List, Optional, Tuple

from app.utils.vk_client import get_community_vk_session, resolved_vk_group_id_int

logger = logging.getLogger(__name__)

# Текст комментария-маркера «неактуально» (без решётки — это комментарий, не хэштег).
UNAVAILABLE_COMMENT = "неактуально"


def parse_vk_post_id(vk_post_id: Optional[str]) -> Optional[Tuple[int, int]]:
    """Разобрать "{owner_id}_{post_id}" (owner_id отрицательный) → (owner_id, post_id)."""
    if not vk_post_id:
        return None
    try:
        owner_str, post_str = str(vk_post_id).split("_", 1)
        return int(owner_str), int(post_str)
    except (ValueError, AttributeError):
        logger.error("Invalid vk_post_id format: %s", vk_post_id)
        return None


class VKWallCommentClient:
    """Тонкая обёртка над wall.* для комментариев от лица сообщества."""

    def __init__(self) -> None:
        self.group_id = resolved_vk_group_id_int()
        self.owner_id = -self.group_id
        self.vk = get_community_vk_session().get_api()

    def _find_group_unavailable_comment_ids(self, owner_id: int, post_id: int) -> List[int]:
        """ID комментариев от лица группы, содержащих маркер «неактуально»."""
        needle = UNAVAILABLE_COMMENT.lower()
        ids: List[int] = []
        try:
            resp = self.vk.wall.getComments(
                owner_id=owner_id,
                post_id=post_id,
                count=100,
                sort="asc",
            )
        except Exception as e:
            logger.error(
                "VK wall.getComments failed owner=%s post=%s: %s", owner_id, post_id, e
            )
            return ids

        for item in resp.get("items", []):
            from_id = item.get("from_id")
            text = (item.get("text") or "").strip().lower()
            cid = item.get("id")
            if from_id == self.owner_id and cid and needle in text:
                ids.append(int(cid))
        return ids

    def create_unavailable_comment(self, owner_id: int, post_id: int) -> bool:
        """Оставить комментарий «неактуально» от лица группы (идемпотентно)."""
        if self._find_group_unavailable_comment_ids(owner_id, post_id):
            logger.info(
                "VK post %s_%s already has group #неактуально comment", owner_id, post_id
            )
            return True
        try:
            self.vk.wall.createComment(
                owner_id=owner_id,
                post_id=post_id,
                from_group=self.group_id,
                message=UNAVAILABLE_COMMENT,
            )
            logger.info("VK unavailable comment posted for %s_%s", owner_id, post_id)
            return True
        except Exception as e:
            logger.error(
                "VK wall.createComment failed owner=%s post=%s: %s", owner_id, post_id, e
            )
            return False

    def remove_unavailable_comments(self, owner_id: int, post_id: int) -> bool:
        """Удалить комментарии «неактуально» от лица группы под постом."""
        ids = self._find_group_unavailable_comment_ids(owner_id, post_id)
        if not ids:
            logger.info("VK post %s_%s: no #неактуально comment to remove", owner_id, post_id)
            return True
        all_ok = True
        for cid in ids:
            try:
                self.vk.wall.deleteComment(owner_id=owner_id, comment_id=cid)
                logger.info("VK deleted comment %s on post %s_%s", cid, owner_id, post_id)
            except Exception as e:
                logger.error(
                    "VK wall.deleteComment failed owner=%s comment=%s: %s",
                    owner_id,
                    cid,
                    e,
                )
                all_ok = False
        return all_ok
