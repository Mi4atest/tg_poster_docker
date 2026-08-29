import logging
import asyncio
from typing import Dict, Optional
from datetime import datetime, timezone

from app.config import settings as env_settings
from app.scheduler.queue_manager import QueueManager
from app.scheduler.platform_worker import PlatformWorker
from app.scheduler.avito_archive_worker import AvitoArchiveWorker
from app.scheduler.market_watchlist_worker import MarketWatchlistWorker
from app.services.settings_service import get_settings_service
from app.workers.instagram.token_manager import InstagramGraphTokenManager

logger = logging.getLogger(__name__)


class PublicationOrchestrator:
    """Оркестратор для управления параллельной публикацией постов."""

    def __init__(self, signature_enabled: bool = True):
        """Инициализация оркестратора.
        
        Args:
            signature_enabled: Включена ли подпись по умолчанию
        """
        self.queue_manager = QueueManager()
        self.signature_enabled = signature_enabled
        self.workers: Dict[str, PlatformWorker] = {}
        self.worker_tasks: Dict[str, asyncio.Task] = {}
        self.maintenance_tasks: Dict[str, asyncio.Task] = {}
        self.avito_archive_worker = AvitoArchiveWorker(self.queue_manager, orchestrator=self)
        self.market_watchlist_worker = MarketWatchlistWorker()
        self.is_running = False
        self.global_pause = False

    def start(self):
        """Запустить оркестратор."""
        if self.is_running:
            logger.warning("Orchestrator is already running")
            return

        self.is_running = True
        try:
            svc = get_settings_service()
            self.global_pause = svc.is_global_publication_pause()
            platform_pauses = svc.get_platform_publication_pauses()
        except Exception:
            self.global_pause = False
            platform_pauses = {}
        logger.info(
            "Orchestrator started with global_pause=%s platform_pauses=%s (from persisted settings)",
            self.global_pause,
            {k: v for k, v in platform_pauses.items() if v} or "{}",
        )

        # Создаем workers для каждой платформы
        platforms = ["vk", "telegram", "instagram", "max", "avito"]
        for platform in platforms:
            worker = PlatformWorker(platform, self.queue_manager, self.signature_enabled, orchestrator=self)
            if platform_pauses.get(platform):
                worker.pause()
            self.workers[platform] = worker
            logger.info(f"Created worker for platform: {platform}")

        logger.info("Publication orchestrator initialized (workers will start when event loop is running)")
    
    async def start_workers(self):
        """Запустить workers в асинхронном контексте."""
        if not self.is_running:
            return

        recovered = self.queue_manager.recover_stale_publishing_items()
        if recovered:
            logger.warning("Recovered %s stale publishing queue item(s) on startup", recovered)

        platforms = ["vk", "telegram", "instagram", "max", "avito"]
        for platform in platforms:
            if platform in self.workers and platform not in self.worker_tasks:
                worker = self.workers[platform]
                task = asyncio.create_task(worker.run())
                self.worker_tasks[platform] = task
                logger.info(f"Started worker task for platform: {platform}")

        if "instagram_token_refresh" not in self.maintenance_tasks:
            task = asyncio.create_task(self._run_instagram_token_refresh())
            self.maintenance_tasks["instagram_token_refresh"] = task
            logger.info("Started daily Instagram token refresh task")

        if "avito_archive" not in self.maintenance_tasks:
            task = asyncio.create_task(self.avito_archive_worker.run())
            self.maintenance_tasks["avito_archive"] = task
            logger.info("Started Avito archive queue worker")

        if "avito_market_watchlist" not in self.maintenance_tasks:
            task = asyncio.create_task(self.market_watchlist_worker.run())
            self.maintenance_tasks["avito_market_watchlist"] = task
            logger.info("Started Avito market watchlist worker")

    async def _run_instagram_token_refresh(self):
        manager = InstagramGraphTokenManager()
        interval = int(getattr(env_settings, "INSTAGRAM_GRAPH_TOKEN_DAILY_CHECK_INTERVAL_SECONDS", 86400) or 86400)
        while self.is_running:
            try:
                await manager.check_and_refresh_token(trigger_alerts=True)
            except Exception as exc:
                logger.error("Instagram token refresh task failed: %s", exc)
            await asyncio.sleep(interval)

    async def stop(self):
        """Остановить оркестратор."""
        if not self.is_running:
            return

        logger.info("Stopping publication orchestrator...")
        self.is_running = False
        self.avito_archive_worker.stop()
        self.market_watchlist_worker.stop()

        # Останавливаем всех workers
        for platform, worker in self.workers.items():
            worker.stop()

        # Ждем завершения всех задач
        for platform, task in self.worker_tasks.items():
            try:
                await asyncio.wait_for(task, timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning(f"Worker task for {platform} did not stop in time")
            except Exception as e:
                logger.error(f"Error stopping worker task for {platform}: {str(e)}")

        for task_name, task in self.maintenance_tasks.items():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=3.0)
            except Exception:
                logger.info("Maintenance task %s stopped", task_name)

        self.workers.clear()
        self.worker_tasks.clear()
        self.maintenance_tasks.clear()
        self.queue_manager.close()
        logger.info("Publication orchestrator stopped")

    def add_post_to_queue(
        self,
        post_id: str,
        platforms: Optional[list] = None,
        priority: int = 0,
        scheduled_at: Optional[datetime] = None,
        enforce_enabled_filter: bool = True,
        allow_avito_without_vk_market: bool = False,
    ) -> bool:
        """Добавить пост в очередь публикации.
        
        Args:
            post_id: ID поста
            platforms: Список платформ (по умолчанию все: ["vk", "telegram", "instagram", "max"])
            priority: Приоритет (чем выше, тем раньше публикуется)
            scheduled_at: Запланированное время публикации
            enforce_enabled_filter: Применять ли фильтр enabled из настроек
            allow_avito_without_vk_market: если True, Авито не отфильтровывается по «Товары ВК»
                (явная кнопка «в Авито» из архива / карточки поста).
            
        Returns:
            True если успешно, False иначе
        """
        if platforms is None:
            platforms = ["vk", "telegram", "instagram", "max", "avito"]
        if enforce_enabled_filter:
            try:
                settings_service = get_settings_service()
                platforms = [p for p in platforms if settings_service.is_platform_enabled(p)]
                platforms = [
                    p
                    for p in platforms
                    if p != "avito"
                    or (
                        settings_service.is_avito_platform_only_enabled()
                        if allow_avito_without_vk_market
                        else settings_service.is_avito_queue_allowed()
                    )
                ]
            except Exception:
                pass
        if not platforms:
            return False

        queue_items = self.queue_manager.add_post_to_queue(
            post_id=post_id,
            platforms=platforms,
            priority=priority,
            scheduled_at=scheduled_at
        )
        bumped = 0
        if len(queue_items) == 0:
            bumped = self.queue_manager.bump_queue_priority_for_platforms(
                post_id, platforms, priority
            )
        if "avito" in platforms and (queue_items or bumped > 0):
            try:
                from app.integrations.avito.autoload_coordinator import get_coordinator

                get_coordinator().touch_enqueue()
            except Exception:
                pass

        if len(queue_items) > 0:
            return True
        return bumped > 0

    def bump_queued_publication_priority(self, post_id: str, priority: int = 999) -> bool:
        """Только поднять приоритет уже существующих задач (старые callback без платформы)."""
        n = self.queue_manager.bump_queue_priority_for_post(post_id, priority)
        return n > 0

    def pause_global(self):
        """Приостановить все публикации (глобальная пауза)."""
        self.global_pause = True
        try:
            get_settings_service().set_global_publication_pause(True)
        except Exception as exc:
            logger.warning("Could not persist global pause: %s", exc)
        logger.info("Global pause activated")

    def resume_global(self):
        """Возобновить все публикации (индивидуальные паузы платформ не сбрасываются)."""
        self.global_pause = False
        try:
            get_settings_service().set_global_publication_pause(False)
        except Exception as exc:
            logger.warning("Could not persist global resume: %s", exc)
        logger.info("Global pause deactivated")

    def is_platform_paused(self, platform: str) -> bool:
        worker = self.workers.get(platform)
        return bool(worker and worker.is_paused)

    def get_platform_pauses(self) -> Dict[str, bool]:
        return {name: self.is_platform_paused(name) for name in self.workers}

    def pause_platform(self, platform: str):
        """Приостановить публикации для конкретной платформы."""
        if platform in self.workers:
            self.workers[platform].pause()
            try:
                get_settings_service().set_platform_publication_pause(platform, True)
            except Exception as exc:
                logger.warning("Could not persist platform pause %s: %s", platform, exc)
            logger.info(f"Paused platform: {platform}")

    def resume_platform(self, platform: str):
        """Возобновить публикации для конкретной платформы."""
        if platform in self.workers:
            self.workers[platform].resume()
            try:
                get_settings_service().set_platform_publication_pause(platform, False)
            except Exception as exc:
                logger.warning("Could not persist platform resume %s: %s", platform, exc)
            logger.info(f"Resumed platform: {platform}")

    def pause_post(self, post_id: str):
        """Приостановить публикацию конкретного поста."""
        from app.api.models.post import PublicationQueue

        try:
            queue_items = self.queue_manager.db.query(PublicationQueue).filter(
                PublicationQueue.post_id == post_id,
                PublicationQueue.status.in_(["pending", "publishing"]),
            ).all()
            for item in queue_items:
                self.queue_manager.pause_queue_item(item.id)
            logger.info(f"Paused post: {post_id}")
        finally:
            self.queue_manager._release_read_transaction()

    def resume_post(self, post_id: str):
        """Возобновить публикацию конкретного поста."""
        from app.api.models.post import PublicationQueue

        try:
            queue_items = self.queue_manager.db.query(PublicationQueue).filter(
                PublicationQueue.post_id == post_id,
                PublicationQueue.status == "paused",
            ).all()
            for item in queue_items:
                self.queue_manager.resume_queue_item(item.id)
            logger.info(f"Resumed post: {post_id}")
        finally:
            self.queue_manager._release_read_transaction()

    def cancel_post(self, post_id: str):
        """Отменить публикацию поста (удалить из очереди)."""
        from app.api.models.post import PublicationQueue

        try:
            queue_items = self.queue_manager.db.query(PublicationQueue).filter(
                PublicationQueue.post_id == post_id,
            ).all()
            for item in queue_items:
                self.queue_manager.remove_from_queue(item.id)
            logger.info(f"Cancelled post: {post_id}")
        finally:
            self.queue_manager._release_read_transaction()

    def publish_now(self, post_id: str, platforms: Optional[list] = None) -> bool:
        """Опубликовать пост вне очереди (с высоким приоритетом).

        Сначала снимает pause по указанным платформам — иначе воркер не возьмёт задачу.
        """
        plats = platforms or ["vk", "telegram", "instagram", "max", "avito"]
        for platform in plats:
            self.queue_manager.resume_paused_for_post_platform(post_id, platform)
        return self.add_post_to_queue(
            post_id=post_id,
            platforms=platforms,
            priority=999,
            scheduled_at=None,
        )

    def get_queue_stats(self) -> dict:
        """Получить статистику очереди.
        
        Returns:
            Словарь со статистикой
        """
        return self.queue_manager.get_queue_stats()

    def get_queue_for_platform(self, platform: str) -> list:
        """Получить очередь для платформы.
        
        Args:
            platform: Платформа ("vk", "telegram", "instagram")
            
        Returns:
            Список записей очереди
        """
        return self.queue_manager.get_queue_for_platform(platform)

