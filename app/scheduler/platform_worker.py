import logging
import asyncio
from typing import Optional
from datetime import datetime, timezone, timedelta

from app.scheduler.queue_manager import QueueManager
from app.workers.vk.publisher import publish_post_to_vk
from app.workers.telegram.publisher import publish_post_to_telegram
from app.workers.instagram.publisher import publish_post_to_instagram
from app.workers.max.publisher import publish_post_to_max
from app.workers.avito.publisher import (
    publish_autoload_batch,
    publish_post_to_avito,
)
from app.integrations.avito.autoload_coordinator import get_coordinator
from app.integrations.avito.errors import AvitoAutoCreateUnavailableError
from app.services.settings_service import get_settings_service

logger = logging.getLogger(__name__)


class PlatformWorker:
    """Worker для публикации постов на конкретной платформе."""

    # Интервалы публикации в секундах
    INTERVALS = {
        "vk": 3 * 60,  # 3 минуты
        "telegram": 3 * 60,  # 3 минуты
        "instagram": 30 * 60,  # 30 минут
        "max": 3 * 60,  # 3 минуты
        "avito": 60 * 60,
    }

    def __init__(self, platform: str, queue_manager: QueueManager, signature_enabled: bool = True, orchestrator=None):
        """Инициализация worker.
        
        Args:
            platform: Платформа ("vk", "telegram", "instagram", "max", "avito")
            queue_manager: Менеджер очереди
            signature_enabled: Включена ли подпись
            orchestrator: Ссылка на оркестратор (для проверки глобальной паузы)
        """
        self.platform = platform
        self.queue_manager = queue_manager
        self.signature_enabled = signature_enabled
        self.orchestrator = orchestrator
        self.is_running = False
        self.is_paused = False
        self.last_published_at: Optional[datetime] = None
        self.current_task: Optional[asyncio.Task] = None

    async def publish_post(self, queue_item_id: int, post_id: str) -> bool:
        """Опубликовать пост на платформе.
        
        Args:
            queue_item_id: ID записи очереди
            post_id: ID поста
            
        Returns:
            True если успешно, False иначе
        """
        try:
            logger.info(f"Publishing post {post_id} to {self.platform}")
            
            # Вызываем соответствующий publisher
            if self.platform == "vk":
                success = await publish_post_to_vk(post_id, signature_enabled=self.signature_enabled)
            elif self.platform == "telegram":
                success = await publish_post_to_telegram(post_id, signature_enabled=self.signature_enabled)
            elif self.platform == "instagram":
                success = await publish_post_to_instagram(post_id)
            elif self.platform == "max":
                success = await publish_post_to_max(post_id, signature_enabled=self.signature_enabled)
            elif self.platform == "avito":
                success = await publish_post_to_avito(
                    post_id, signature_enabled=self.signature_enabled
                )
            else:
                logger.error(f"Unknown platform: {self.platform}")
                return False

            if success:
                self.queue_manager.mark_as_completed(queue_item_id)
                self.last_published_at = datetime.now(timezone.utc)
                logger.info(f"Successfully published post {post_id} to {self.platform}")
            else:
                error_msg = f"Failed to publish post {post_id} to {self.platform}"
                self.queue_manager.mark_as_failed(queue_item_id, error_msg)
                logger.error(error_msg)

            return success

        except Exception as e:
            error_msg = f"Error publishing post {post_id} to {self.platform}: {str(e)}"
            logger.error(error_msg)
            self.queue_manager.mark_as_failed(queue_item_id, error_msg)
            return False

    async def wait_for_interval(self):
        """Ожидание интервала между публикациями."""
        if self.last_published_at is None:
            # Первая публикация, не ждем
            return

        try:
            interval = get_settings_service().get_platform_interval_minutes(self.platform) * 60
        except Exception:
            interval = self.INTERVALS.get(self.platform, 3 * 60)
        elapsed = (datetime.now(timezone.utc) - self.last_published_at).total_seconds()
        
        if elapsed < interval:
            wait_time = interval - elapsed
            logger.info(f"Waiting {wait_time:.1f} seconds before next publication to {self.platform}")
            await asyncio.sleep(wait_time)

    async def _run_avito_batch_cycle(self) -> None:
        """Авито: накопление в очереди; выгрузка — через AvitoFeedDispatcher (worker archive)."""
        from app.integrations.avito.avito_feed_dispatcher import is_manual_feed_upload

        pending = self.queue_manager.get_pending_items("avito")
        if not pending and is_manual_feed_upload():
            await asyncio.sleep(8)
            return
        if is_manual_feed_upload():
            await asyncio.sleep(8)
            return
        await asyncio.sleep(5)

    async def run(self):
        """Основной цикл worker."""
        self.is_running = True
        logger.info(f"Platform worker for {self.platform} started")

        while self.is_running:
            try:
                global_paused = self.orchestrator.global_pause if self.orchestrator else False
                try:
                    disabled_in_settings = not get_settings_service().is_platform_enabled(self.platform)
                except Exception:
                    disabled_in_settings = False

                if self.is_paused or global_paused:
                    await asyncio.sleep(1)
                    continue

                if self.platform == "avito":
                    if disabled_in_settings and not self.queue_manager.get_pending_items("avito"):
                        await asyncio.sleep(5)
                        continue
                    await self._run_avito_batch_cycle()
                    continue

                if disabled_in_settings:
                    queue_item = self.queue_manager.get_next_post(self.platform)
                    if not queue_item:
                        await asyncio.sleep(1)
                        continue
                else:
                    queue_item = self.queue_manager.get_next_post(self.platform)

                if queue_item:
                    await self.wait_for_interval()
                    if not self.queue_manager.mark_as_publishing(queue_item.id):
                        continue
                    await self.publish_post(queue_item.id, queue_item.post_id)
                else:
                    await asyncio.sleep(5)

            except Exception as e:
                logger.error(f"Error in platform worker {self.platform}: {str(e)}")
                await asyncio.sleep(5)

        logger.info(f"Platform worker for {self.platform} stopped")

    def pause(self):
        """Приостановить worker."""
        self.is_paused = True
        logger.info(f"Platform worker for {self.platform} paused")

    def resume(self):
        """Возобновить worker."""
        self.is_paused = False
        logger.info(f"Platform worker for {self.platform} resumed")

    def stop(self):
        """Остановить worker."""
        self.is_running = False
        logger.info(f"Platform worker for {self.platform} stopping")

