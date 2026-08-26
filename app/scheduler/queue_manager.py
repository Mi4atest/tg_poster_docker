import logging
import re
import threading
from contextlib import contextmanager
from types import SimpleNamespace
from typing import List, Optional, Dict
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, desc, text

from app.db.database import SessionLocal
from app.api.models.post import Post, PublicationQueue

logger = logging.getLogger(__name__)

_IG_RETRY_MARKER_RE = re.compile(r"\[ig_retry=(\d+)/(\d+)\]")


class QueueManager:
    """Управление очередью публикаций."""

    def __init__(self, db: Optional[Session] = None):
        """Инициализация менеджера очереди."""
        self.db = db or SessionLocal()
        self._db_lock = threading.RLock()

    @contextmanager
    def _isolated_session(self):
        """Отдельная сессия для конкурентных воркеров (не делят self.db)."""
        db = SessionLocal()
        try:
            yield db
        finally:
            try:
                db.close()
            except Exception:
                pass

    def _release_read_transaction(self) -> None:
        """Сбросить read-only транзакцию, чтобы не держать соединение idle in transaction."""
        try:
            self.db.rollback()
        except Exception as e:
            logger.debug("QueueManager rollback after read: %s", e)

    def add_post_to_queue(
        self,
        post_id: str,
        platforms: List[str],
        priority: int = 0,
        scheduled_at: Optional[datetime] = None
    ) -> List[PublicationQueue]:
        """Добавить пост в очередь для указанных платформ."""
        try:
            post_row = (
                self.db.execute(
                    text("SELECT id FROM posts WHERE id = :id LIMIT 1"),
                    {"id": post_id},
                )
                .first()
            )
            if not post_row:
                logger.error("Post %s not found", post_id)
                return []

            queue_items: List[PublicationQueue] = []
            now = datetime.now(timezone.utc)

            for platform in platforms:
                existing = (
                    self.db.execute(
                        text(
                            "SELECT id FROM publication_queue "
                            "WHERE post_id = :post_id AND platform = :platform "
                            "AND status IN ('pending', 'publishing', 'paused') "
                            "LIMIT 1"
                        ),
                        {"post_id": post_id, "platform": platform},
                    )
                    .first()
                )
                if existing:
                    logger.info(
                        "Post %s already in queue for platform %s", post_id, platform
                    )
                    continue

                qid = self.db.execute(
                    text(
                        "INSERT INTO publication_queue "
                        "(post_id, platform, status, priority, scheduled_at, created_at) "
                        "VALUES (:post_id, :platform, 'pending', :priority, :scheduled_at, :created_at) "
                        "RETURNING id"
                    ),
                    {
                        "post_id": post_id,
                        "platform": platform,
                        "priority": priority,
                        "scheduled_at": scheduled_at,
                        "created_at": now,
                    },
                ).scalar()
                queue_items.append(
                    PublicationQueue(
                        id=qid,
                        post_id=post_id,
                        platform=platform,
                        status="pending",
                        priority=priority,
                        scheduled_at=scheduled_at,
                        created_at=now,
                    )
                )

            if queue_items:
                self.db.execute(
                    text(
                        "UPDATE posts SET in_queue = true, queue_status = 'pending', "
                        "scheduled_at = COALESCE(:scheduled_at, scheduled_at), "
                        "updated_at = NOW() WHERE id = :id"
                    ),
                    {"id": post_id, "scheduled_at": scheduled_at},
                )

            self.db.commit()

            if queue_items:
                logger.info(
                    "Added post %s to queue for platforms: %s", post_id, platforms
                )
            return queue_items

        except Exception as e:
            logger.error("Error adding post %s to queue: %s", post_id, e)
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
            else:
                self.db.rollback()
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
            else:
                self.db.rollback()
            return updated
        except Exception as e:
            logger.error(f"Error bumping queue priority for post {post_id}: {str(e)}")
            self.db.rollback()
            return 0

    def get_pending_items(
        self, platform: str, limit: int = 50
    ) -> List[PublicationQueue]:
        """Все готовые pending-задачи платформы (для батча Авито)."""
        with self._isolated_session() as db:
            try:
                now = datetime.now(timezone.utc)
                rows = (
                    db.execute(
                        text(
                            "SELECT id, post_id, platform, status, priority, scheduled_at, "
                            "published_at, error_message, created_at "
                            "FROM publication_queue "
                            "WHERE platform = :platform AND status = 'pending' "
                            "AND (scheduled_at IS NULL OR scheduled_at <= :now) "
                            "ORDER BY priority DESC, created_at ASC "
                            "LIMIT :limit"
                        ),
                        {"platform": platform, "now": now, "limit": limit},
                    )
                    .mappings()
                    .all()
                )
                return [PublicationQueue(**dict(row)) for row in rows]
            except Exception as e:
                logger.error("Error getting pending items for %s: %s", platform, e)
                return []

    def count_pending(self, platform: str) -> int:
        with self._isolated_session() as db:
            try:
                now = datetime.now(timezone.utc)
                return (
                    db.execute(
                        text(
                            "SELECT COUNT(*) FROM publication_queue "
                            "WHERE platform = :platform AND status = 'pending' "
                            "AND (scheduled_at IS NULL OR scheduled_at <= :now)"
                        ),
                        {"platform": platform, "now": now},
                    ).scalar()
                    or 0
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
            else:
                self.db.rollback()
            return updated
        except Exception as e:
            logger.error("Error resetting queue items: %s", e)
            self.db.rollback()
            return 0

    def recover_stale_publishing_items(self, stale_minutes: Optional[int] = None) -> int:
        """Вернуть зависшие publishing-задачи в pending.

        При stale_minutes=None (старт приложения) сбрасываются все publishing —
        после рестарта активной публикации уже нет.
        """
        with self._isolated_session() as db:
            try:
                if stale_minutes is None:
                    rows = db.execute(
                        text(
                            "UPDATE publication_queue SET status = 'pending', error_message = NULL "
                            "WHERE status = 'publishing' RETURNING id, post_id, platform"
                        )
                    ).fetchall()
                else:
                    cutoff = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
                    rows = db.execute(
                        text(
                            "UPDATE publication_queue SET status = 'pending' "
                            "WHERE status = 'publishing' AND created_at < :cutoff "
                            "RETURNING id, post_id, platform"
                        ),
                        {"cutoff": cutoff},
                    ).fetchall()
                if rows:
                    db.commit()
                    for row in rows:
                        logger.warning(
                            "Recovered stale publishing queue item id=%s post=%s platform=%s",
                            row[0],
                            row[1],
                            row[2],
                        )
                    return len(rows)
                return 0
            except Exception as e:
                logger.error("Error recovering stale publishing items: %s", e)
                db.rollback()
                return 0

    def get_next_post(self, platform: str) -> Optional[PublicationQueue]:
        """Получить следующий пост для публикации на указанной платформе."""
        with self._isolated_session() as db:
            try:
                now = datetime.now(timezone.utc)
                row = (
                    db.execute(
                        text(
                            "SELECT id, post_id, platform, status, priority, scheduled_at, "
                            "published_at, error_message, created_at "
                            "FROM publication_queue "
                            "WHERE platform = :platform AND status = 'pending' "
                            "AND (scheduled_at IS NULL OR scheduled_at <= :now) "
                            "ORDER BY priority DESC, created_at ASC "
                            "LIMIT 1"
                        ),
                        {"platform": platform, "now": now},
                    )
                    .mappings()
                    .first()
                )
                if not row:
                    return None
                return PublicationQueue(**dict(row))

            except Exception as e:
                logger.error("Error getting next post for platform %s: %s", platform, e)
                return None

    def mark_as_publishing(self, queue_item_id: int) -> bool:
        """Отметить запись как публикующуюся.
        
        Args:
            queue_item_id: ID записи очереди
            
        Returns:
            True если успешно, False иначе
        """
        with self._isolated_session() as db:
            try:
                row = db.execute(
                    text(
                        "UPDATE publication_queue SET status = 'publishing' "
                        "WHERE id = :id AND status = 'pending' RETURNING id"
                    ),
                    {"id": queue_item_id},
                ).first()
                if not row:
                    return False
                db.commit()
                return True

            except Exception as e:
                logger.error("Error marking queue item %s as publishing: %s", queue_item_id, e)
                db.rollback()
                return False

    def mark_as_completed(self, queue_item_id: int) -> bool:
        """Отметить запись как завершенную.
        
        Args:
            queue_item_id: ID записи очереди
            
        Returns:
            True если успешно, False иначе
        """
        with self._isolated_session() as db:
            try:
                now = datetime.now(timezone.utc)
                row = (
                    db.execute(
                        text(
                            "UPDATE publication_queue SET status = 'completed', published_at = :now "
                            "WHERE id = :id RETURNING post_id"
                        ),
                        {"id": queue_item_id, "now": now},
                    )
                    .first()
                )
                if not row:
                    return False

                post_id = row[0]
                remaining = db.execute(
                    text(
                        "SELECT COUNT(*) FROM publication_queue "
                        "WHERE post_id = :post_id AND status IN ('pending', 'publishing', 'paused')"
                    ),
                    {"post_id": post_id},
                ).scalar() or 0

                if remaining == 0:
                    db.execute(
                        text(
                            "UPDATE posts SET in_queue = false, queue_status = 'completed', "
                            "updated_at = NOW() WHERE id = :id"
                        ),
                        {"id": post_id},
                    )

                db.commit()
                return True

            except Exception as e:
                logger.error("Error marking queue item %s as completed: %s", queue_item_id, e)
                db.rollback()
                return False

    def mark_as_failed(self, queue_item_id: int, error_message: str) -> bool:
        """Отметить запись как неудачную.
        
        Args:
            queue_item_id: ID записи очереди
            error_message: Сообщение об ошибке
            
        Returns:
            True если успешно, False иначе
        """
        with self._isolated_session() as db:
            try:
                row = db.execute(
                    text(
                        "UPDATE publication_queue SET status = 'failed', error_message = :msg "
                        "WHERE id = :id RETURNING post_id"
                    ),
                    {"id": queue_item_id, "msg": (error_message or "")[:2000]},
                ).first()
                if not row:
                    return False

                post_id = row[0]
                remaining = db.execute(
                    text(
                        "SELECT COUNT(*) FROM publication_queue "
                        "WHERE post_id = :post_id AND status IN ('pending', 'publishing', 'paused')"
                    ),
                    {"post_id": post_id},
                ).scalar() or 0
                if remaining == 0:
                    any_completed = db.execute(
                        text(
                            "SELECT 1 FROM publication_queue "
                            "WHERE post_id = :post_id AND status = 'completed' LIMIT 1"
                        ),
                        {"post_id": post_id},
                    ).first()
                    db.execute(
                        text(
                            "UPDATE posts SET in_queue = false, queue_status = :qstatus, "
                            "updated_at = NOW() WHERE id = :id"
                        ),
                        {
                            "id": post_id,
                            "qstatus": "completed" if any_completed else "failed",
                        },
                    )

                db.commit()
                return True

            except Exception as e:
                logger.error("Error marking queue item %s as failed: %s", queue_item_id, e)
                db.rollback()
                return False

    def requeue_for_retry(
        self,
        queue_item_id: int,
        error_message: str,
        *,
        delay_seconds: int,
        max_attempts: int = 5,
    ) -> bool:
        """Вернуть задачу в pending с отложенным scheduled_at (авто-ретрай).

        Returns:
            True если ретрай поставлен, False если лимит попыток исчерпан.
        """
        delay_seconds = max(30, int(delay_seconds or 0))
        max_attempts = max(1, int(max_attempts or 1))
        with self._isolated_session() as db:
            try:
                row = (
                    db.execute(
                        text(
                            "SELECT id, post_id, error_message FROM publication_queue "
                            "WHERE id = :id"
                        ),
                        {"id": queue_item_id},
                    )
                    .mappings()
                    .first()
                )
                if not row:
                    return False

                prev = row["error_message"] or ""
                match = _IG_RETRY_MARKER_RE.search(prev)
                attempt = int(match.group(1)) + 1 if match else 1
                if attempt > max_attempts:
                    return False

                now = datetime.now(timezone.utc)
                scheduled_at = now + timedelta(seconds=delay_seconds)
                marker = f"[ig_retry={attempt}/{max_attempts}]"
                msg = f"{marker} {error_message or 'publish failed'}".strip()[:2000]

                db.execute(
                    text(
                        "UPDATE publication_queue SET status = 'pending', "
                        "scheduled_at = :scheduled_at, error_message = :msg, published_at = NULL "
                        "WHERE id = :id"
                    ),
                    {
                        "id": queue_item_id,
                        "scheduled_at": scheduled_at,
                        "msg": msg,
                    },
                )
                db.execute(
                    text(
                        "UPDATE posts SET in_queue = true, queue_status = 'pending', "
                        "updated_at = NOW() WHERE id = :post_id"
                    ),
                    {"post_id": row["post_id"]},
                )
                db.commit()
                logger.warning(
                    "Requeued queue item %s post=%s attempt=%s/%s in %ss: %s",
                    queue_item_id,
                    row["post_id"],
                    attempt,
                    max_attempts,
                    delay_seconds,
                    msg[:200],
                )
                return True
            except Exception as e:
                logger.error("Error requeueing queue item %s for retry: %s", queue_item_id, e)
                db.rollback()
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

    def resume_paused_for_post_platform(self, post_id: str, platform: str) -> int:
        """Снять pause у задач post+platform. Возвращает число обновлённых строк."""
        try:
            rows = (
                self.db.query(PublicationQueue)
                .filter(
                    and_(
                        PublicationQueue.post_id == post_id,
                        PublicationQueue.platform == platform,
                        PublicationQueue.status == "paused",
                    )
                )
                .all()
            )
            n = 0
            for row in rows:
                row.status = "pending"
                n += 1
            if n:
                self.db.commit()
            else:
                self.db.rollback()
            return n
        except Exception as e:
            logger.error("Error resuming paused queue items for %s/%s: %s", post_id, platform, e)
            self.db.rollback()
            return 0

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
            remaining = (
                self.db.query(PublicationQueue)
                .filter(
                    PublicationQueue.post_id == post_id,
                    PublicationQueue.status.in_(["pending", "publishing", "paused"]),
                )
                .count()
            )

            if remaining == 0:
                # Больше нет активных записей в очереди, обновляем статус поста
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
                .filter(
                    PublicationQueue.post_id == post_id,
                    PublicationQueue.status.in_(["pending", "publishing", "paused"]),
                )
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
        """Получить статистику очереди."""
        with self._isolated_session() as db:
            try:
                platforms = ["vk", "telegram", "instagram", "max", "avito"]
                rows = (
                    db.execute(
                        text(
                            "SELECT platform, COUNT(*) AS cnt FROM publication_queue "
                            "WHERE status IN ('pending', 'publishing', 'paused') "
                            "AND platform = ANY(:platforms) "
                            "GROUP BY platform"
                        ),
                        {"platforms": platforms},
                    )
                    .mappings()
                    .all()
                )
                stats = {p: 0 for p in platforms}
                for row in rows:
                    stats[row["platform"]] = int(row["cnt"])
                stats["total"] = sum(stats.values())
                return stats

            except Exception as e:
                logger.error("Error getting queue stats: %s", e)
                return {}

    def get_queue_for_platform(self, platform: str) -> List[SimpleNamespace]:
        """Получить записи очереди для платформы (с именем поста, без lazy-load ORM)."""
        with self._isolated_session() as db:
            try:
                rows = (
                    db.execute(
                        text(
                            "SELECT pq.id, pq.post_id, pq.platform, pq.status, pq.priority, "
                            "pq.scheduled_at, pq.published_at, pq.error_message, pq.created_at, "
                            "COALESCE(p.name, '') AS post_name "
                            "FROM publication_queue pq "
                            "LEFT JOIN posts p ON p.id = pq.post_id "
                            "WHERE pq.platform = :platform "
                            "AND pq.status IN ('pending', 'publishing', 'paused') "
                            "ORDER BY pq.priority DESC, pq.created_at ASC"
                        ),
                        {"platform": platform},
                    )
                    .mappings()
                    .all()
                )
                return [SimpleNamespace(**dict(row)) for row in rows]

            except Exception as e:
                logger.error("Error getting queue for platform %s: %s", platform, e)
                return []

    def fetch_queue_item(self, queue_item_id: int) -> Optional[SimpleNamespace]:
        """Одна запись очереди с именем поста (raw SQL)."""
        with self._isolated_session() as db:
            try:
                row = (
                    db.execute(
                        text(
                            "SELECT pq.id, pq.post_id, pq.platform, pq.status, pq.priority, "
                            "pq.scheduled_at, pq.published_at, pq.error_message, pq.created_at, "
                            "COALESCE(p.name, '') AS post_name "
                            "FROM publication_queue pq "
                            "LEFT JOIN posts p ON p.id = pq.post_id "
                            "WHERE pq.id = :id LIMIT 1"
                        ),
                        {"id": queue_item_id},
                    )
                    .mappings()
                    .first()
                )
                return SimpleNamespace(**dict(row)) if row else None
            except Exception as e:
                logger.error("Error fetching queue item %s: %s", queue_item_id, e)
                return None

    def close(self):
        """Закрыть сессию базы данных."""
        if self.db:
            self.db.close()

