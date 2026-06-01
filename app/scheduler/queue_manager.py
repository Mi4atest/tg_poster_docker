import logging
from typing import List, Optional, Dict
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, desc

from app.db.database import SessionLocal
from app.api.models.post import Post, PublicationQueue

logger = logging.getLogger(__name__)


class QueueManager:
    """Управление очередью публикаций."""

    def __init__(self, db: Optional[Session] = None):
        """Инициализация менеджера очереди."""
        self.db = db or SessionLocal()

    def add_post_to_queue(
        self,
        post_id: str,
        platforms: List[str],
        priority: int = 0,
        scheduled_at: Optional[datetime] = None
    ) -> List[PublicationQueue]:
        """Добавить пост в очередь для указанных платформ.
        
        Args:
            post_id: ID поста
            platforms: Список платформ ["vk", "telegram", "instagram", "max"]
            priority: Приоритет (чем выше, тем раньше публикуется)
            scheduled_at: Запланированное время публикации
            
        Returns:
            Список созданных записей очереди
        """
        try:
            # Получаем пост
            post = self.db.query(Post).filter(Post.id == post_id).first()
            if not post:
                logger.error(f"Post {post_id} not found")
                return []

            queue_items = []

            for platform in platforms:
                # Проверяем, не находится ли уже пост в очереди для этой платформы
                existing = self.db.query(PublicationQueue).filter(
                    and_(
                        PublicationQueue.post_id == post_id,
                        PublicationQueue.platform == platform,
                        PublicationQueue.status.in_(["pending", "publishing", "paused"])
                    )
                ).first()
                
                if existing:
                    logger.info(f"Post {post_id} already in queue for platform {platform}")
                    continue

                # Создаем запись в очереди
                queue_item = PublicationQueue(
                    post_id=post_id,
                    platform=platform,
                    status="pending",
                    priority=priority,
                    scheduled_at=scheduled_at,
                    created_at=datetime.now(timezone.utc)
                )
                self.db.add(queue_item)
                queue_items.append(queue_item)

            # Обновляем статус поста
            post.in_queue = True
            post.queue_status = "pending"
            if scheduled_at:
                post.scheduled_at = scheduled_at

            self.db.commit()
            
            logger.info(f"Added post {post_id} to queue for platforms: {platforms}")
            return queue_items

        except Exception as e:
            logger.error(f"Error adding post {post_id} to queue: {str(e)}")
            self.db.rollback()
            return []

    def bump_queue_priority_for_platforms(
        self, post_id: str, platforms: List[str], priority: int
    ) -> int:
        """Поднять приоритет уже существующих записей очереди (pending/paused/publishing). Новые строки не создаёт."""
        if not platforms or priority < 0:
            return 0
        try:
            updated = 0
            for platform in platforms:
                row = (
                    self.db.query(PublicationQueue)
                    .filter(
                        and_(
                            PublicationQueue.post_id == post_id,
                            PublicationQueue.platform == platform,
                            PublicationQueue.status.in_(
                                ["pending", "paused", "publishing"]
                            ),
                        )
                    )
                    .first()
                )
                if row:
                    row.priority = max(int(row.priority or 0), int(priority))
                    updated += 1
            if updated:
                self.db.commit()
            return updated
        except Exception as e:
            logger.error(f"Error bumping queue priority for {post_id}: {str(e)}")
            self.db.rollback()
            return 0

    def bump_queue_priority_for_post(self, post_id: str, priority: int) -> int:
        """Поднять приоритет всех pending/paused/publishing задач этого поста (для legacy-кнопки без платформы)."""
        try:
            rows = (
                self.db.query(PublicationQueue)
                .filter(
                    and_(
                        PublicationQueue.post_id == post_id,
                        PublicationQueue.status.in_(
                            ["pending", "paused", "publishing"]
                        ),
                    )
                )
                .all()
            )
            updated = 0
            for row in rows:
                row.priority = max(int(row.priority or 0), int(priority))
                updated += 1
            if updated:
                self.db.commit()
            return updated
        except Exception as e:
            logger.error(f"Error bumping queue priority for post {post_id}: {str(e)}")
            self.db.rollback()
            return 0

    def get_pending_items(
        self, platform: str, limit: int = 50
    ) -> List[PublicationQueue]:
        """Все готовые pending-задачи платформы (для батча Авито)."""
        try:
            now = datetime.now(timezone.utc)
            return (
                self.db.query(PublicationQueue)
                .filter(
                    and_(
                        PublicationQueue.platform == platform,
                        PublicationQueue.status == "pending",
                        or_(
                            PublicationQueue.scheduled_at.is_(None),
                            PublicationQueue.scheduled_at <= now,
                        ),
                    )
                )
                .order_by(desc(PublicationQueue.priority), PublicationQueue.created_at)
                .limit(limit)
                .all()
            )
        except Exception as e:
            logger.error("Error getting pending items for %s: %s", platform, e)
            return []

    def count_pending(self, platform: str) -> int:
        try:
            now = datetime.now(timezone.utc)
            return (
                self.db.query(PublicationQueue)
                .filter(
                    and_(
                        PublicationQueue.platform == platform,
                        PublicationQueue.status == "pending",
                        or_(
                            PublicationQueue.scheduled_at.is_(None),
                            PublicationQueue.scheduled_at <= now,
                        ),
                    )
                )
                .count()
            )
        except Exception as e:
            logger.error("Error counting pending for %s: %s", platform, e)
            return 0

    def reset_items_to_pending(
        self,
        queue_item_ids: List[int],
        *,
        scheduled_at: Optional[datetime] = None,
        error_message: Optional[str] = None,
    ) -> int:
        """Вернуть задачи в pending (например после 429)."""
        if not queue_item_ids:
            return 0
        try:
            updated = 0
            for qid in queue_item_ids:
                row = (
                    self.db.query(PublicationQueue)
                    .filter(PublicationQueue.id == qid)
                    .first()
                )
                if not row:
                    continue
                row.status = "pending"
                row.scheduled_at = scheduled_at
                if error_message:
                    row.error_message = error_message[:2000]
                updated += 1
            if updated:
                self.db.commit()
            return updated
        except Exception as e:
            logger.error("Error resetting queue items: %s", e)
            self.db.rollback()
            return 0

    def get_next_post(self, platform: str) -> Optional[PublicationQueue]:
        """Получить следующий пост для публикации на указанной платформе.
        
        Args:
            platform: Платформа ("vk", "telegram", "instagram", "max")
            
        Returns:
            Следующая запись очереди или None
        """
        try:
            now = datetime.now(timezone.utc)
            
            # Ищем пост с наивысшим приоритетом, который готов к публикации
            queue_item = self.db.query(PublicationQueue).filter(
                and_(
                    PublicationQueue.platform == platform,
                    PublicationQueue.status == "pending",
                    or_(
                        PublicationQueue.scheduled_at.is_(None),
                        PublicationQueue.scheduled_at <= now
                    )
                )
            ).order_by(
                desc(PublicationQueue.priority),
                PublicationQueue.created_at
            ).first()

            return queue_item

        except Exception as e:
            logger.error(f"Error getting next post for platform {platform}: {str(e)}")
            return None

    def mark_as_publishing(self, queue_item_id: int) -> bool:
        """Отметить запись как публикующуюся.
        
        Args:
            queue_item_id: ID записи очереди
            
        Returns:
            True если успешно, False иначе
        """
        try:
            queue_item = self.db.query(PublicationQueue).filter(
                PublicationQueue.id == queue_item_id
            ).first()
            
            if not queue_item:
                return False

            queue_item.status = "publishing"
            self.db.commit()
            return True

        except Exception as e:
            logger.error(f"Error marking queue item {queue_item_id} as publishing: {str(e)}")
            self.db.rollback()
            return False

    def mark_as_completed(self, queue_item_id: int) -> bool:
        """Отметить запись как завершенную.
        
        Args:
            queue_item_id: ID записи очереди
            
        Returns:
            True если успешно, False иначе
        """
        try:
            queue_item = self.db.query(PublicationQueue).filter(
                PublicationQueue.id == queue_item_id
            ).first()
            
            if not queue_item:
                return False

            queue_item.status = "completed"
            queue_item.published_at = datetime.now(timezone.utc)
            
            # Проверяем, все ли платформы для этого поста завершены
            post_id = queue_item.post_id
            remaining = self.db.query(PublicationQueue).filter(
                and_(
                    PublicationQueue.post_id == post_id,
                    PublicationQueue.status.in_(["pending", "publishing", "paused"])
                )
            ).count()
            
            if remaining == 0:
                # Все платформы завершены, обновляем статус поста
                post = self.db.query(Post).filter(Post.id == post_id).first()
                if post:
                    post.in_queue = False
                    post.queue_status = "completed"
                    # Пост считается архивированным, если опубликован во все основные платформы
                    # (ВК и Telegram - обязательные, Instagram - опциональный)
                    if post.is_published_vk and post.is_published_telegram:
                        # Пост автоматически попадает в архив через фильтрацию в get_posts_api
                        pass

            self.db.commit()
            return True

        except Exception as e:
            logger.error(f"Error marking queue item {queue_item_id} as completed: {str(e)}")
            self.db.rollback()
            return False

    def mark_as_failed(self, queue_item_id: int, error_message: str) -> bool:
        """Отметить запись как неудачную.
        
        Args:
            queue_item_id: ID записи очереди
            error_message: Сообщение об ошибке
            
        Returns:
            True если успешно, False иначе
        """
        try:
            queue_item = self.db.query(PublicationQueue).filter(
                PublicationQueue.id == queue_item_id
            ).first()
            
            if not queue_item:
                return False

            queue_item.status = "failed"
            queue_item.error_message = error_message
            self.db.commit()
            return True

        except Exception as e:
            logger.error(f"Error marking queue item {queue_item_id} as failed: {str(e)}")
            self.db.rollback()
            return False

    def pause_queue_item(self, queue_item_id: int) -> bool:
        """Приостановить публикацию записи.
        
        Args:
            queue_item_id: ID записи очереди
            
        Returns:
            True если успешно, False иначе
        """
        try:
            queue_item = self.db.query(PublicationQueue).filter(
                PublicationQueue.id == queue_item_id
            ).first()
            
            if not queue_item:
                return False

            if queue_item.status == "publishing":
                # Нельзя поставить на паузу публикующийся пост
                return False

            queue_item.status = "paused"
            self.db.commit()
            return True

        except Exception as e:
            logger.error(f"Error pausing queue item {queue_item_id}: {str(e)}")
            self.db.rollback()
            return False

    def resume_queue_item(self, queue_item_id: int) -> bool:
        """Возобновить публикацию записи.
        
        Args:
            queue_item_id: ID записи очереди
            
        Returns:
            True если успешно, False иначе
        """
        try:
            queue_item = self.db.query(PublicationQueue).filter(
                PublicationQueue.id == queue_item_id
            ).first()
            
            if not queue_item:
                return False

            if queue_item.status == "paused":
                queue_item.status = "pending"
                self.db.commit()
                return True

            return False

        except Exception as e:
            logger.error(f"Error resuming queue item {queue_item_id}: {str(e)}")
            self.db.rollback()
            return False

    def remove_from_queue(self, queue_item_id: int) -> bool:
        """Удалить запись из очереди (возврат в отложенные).
        
        Args:
            queue_item_id: ID записи очереди
            
        Returns:
            True если успешно, False иначе
        """
        try:
            queue_item = self.db.query(PublicationQueue).filter(
                PublicationQueue.id == queue_item_id
            ).first()
            
            if not queue_item:
                return False

            post_id = queue_item.post_id
            self.db.delete(queue_item)
            
            # Проверяем, остались ли еще записи для этого поста
            remaining = self.db.query(PublicationQueue).filter(
                PublicationQueue.post_id == post_id
            ).count()
            
            if remaining == 0:
                # Больше нет записей в очереди, обновляем статус поста
                post = self.db.query(Post).filter(Post.id == post_id).first()
                if post:
                    post.in_queue = False
                    post.queue_status = None

            self.db.commit()
            return True

        except Exception as e:
            logger.error(f"Error removing queue item {queue_item_id}: {str(e)}")
            self.db.rollback()
            return False

    def cancel_pending_jobs_for_post_platform(self, post_id: str, platform: str) -> int:
        """Удалить из очереди задачи post_id+platform в статусе pending/paused (не трогаем publishing)."""
        try:
            rows = (
                self.db.query(PublicationQueue)
                .filter(
                    and_(
                        PublicationQueue.post_id == post_id,
                        PublicationQueue.platform == platform,
                        PublicationQueue.status.in_(["pending", "paused"]),
                    )
                )
                .all()
            )
            n = len(rows)
            if not n:
                return 0
            for row in rows:
                self.db.delete(row)
            self.db.flush()
            remaining = (
                self.db.query(PublicationQueue)
                .filter(PublicationQueue.post_id == post_id)
                .count()
            )
            if remaining == 0:
                post = self.db.query(Post).filter(Post.id == post_id).first()
                if post:
                    post.in_queue = False
                    post.queue_status = None
            self.db.commit()
            return n
        except Exception as e:
            logger.error("cancel_pending_jobs_for_post_platform: %s", e)
            self.db.rollback()
            return 0

    def get_queue_stats(self) -> Dict[str, int]:
        """Получить статистику очереди.
        
        Returns:
            Словарь со статистикой по платформам
        """
        try:
            stats = {}
            platforms = ["vk", "telegram", "instagram", "max", "avito"]
            
            for platform in platforms:
                count = self.db.query(PublicationQueue).filter(
                    and_(
                        PublicationQueue.platform == platform,
                        PublicationQueue.status.in_(["pending", "publishing", "paused"])
                    )
                ).count()
                stats[platform] = count
            
            stats["total"] = sum(stats.values())
            return stats

        except Exception as e:
            logger.error(f"Error getting queue stats: {str(e)}")
            return {}

    def get_queue_for_platform(self, platform: str) -> List[PublicationQueue]:
        """Получить все записи очереди для платформы.
        
        Args:
            platform: Платформа ("vk", "telegram", "instagram", "max")
            
        Returns:
            Список записей очереди
        """
        try:
            return self.db.query(PublicationQueue).filter(
                and_(
                    PublicationQueue.platform == platform,
                    PublicationQueue.status.in_(["pending", "publishing", "paused"])
                )
            ).order_by(
                desc(PublicationQueue.priority),
                PublicationQueue.created_at
            ).all()

        except Exception as e:
            logger.error(f"Error getting queue for platform {platform}: {str(e)}")
            return []

    def close(self):
        """Закрыть сессию базы данных."""
        if self.db:
            self.db.close()

