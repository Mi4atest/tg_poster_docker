"""Фоновая синхронизация цены/статуса на площадках и дашборд «📡 Синхронизация площадок»."""
from __future__ import annotations

import asyncio
import html
import logging
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Deque, Dict, List, Optional

from aiogram import Bot
from aiogram.types import LinkPreviewOptions

from app.utils.product_dashboard_label import get_product_dashboard_label
from app.utils.time_msk import format_dashboard_ts_msk

logger = logging.getLogger(__name__)

LIST_REFRESH_DEBOUNCE_SEC = 20.0
TELEGRAM_TEXT_LIMIT = 4090
_vk_publisher_singleton = None


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    OK = "ok"
    PARTIAL = "partial"
    ERROR = "error"


class SyncJobKind(str, Enum):
    PRICE = "price"
    UNAVAILABLE = "unavailable"


@dataclass
class PlatformResult:
    vk: Optional[bool] = None
    telegram: Optional[bool] = None
    max_: Optional[bool] = None
    instagram: Optional[bool] = None
    avito: Optional[bool] = None
    detail: Optional[str] = None


@dataclass
class PlatformSyncJob:
    job_id: str
    chat_id: int
    product_id: int
    display_label: str
    kind: SyncJobKind
    formatted_price: str = ""
    price_value: int = 0
    mark_telegram_enabled: bool = True
    refresh_used_list: bool = False
    refresh_availability_list: bool = False
    status: JobStatus = JobStatus.QUEUED
    platforms: PlatformResult = field(default_factory=PlatformResult)
    attempt: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None

    def short_label(self) -> str:
        return html.escape((self.display_label or "Товар").strip())


# обратная совместимость
PriceSyncJob = PlatformSyncJob


def _get_vk_publisher():
    global _vk_publisher_singleton
    if _vk_publisher_singleton is None:
        from app.workers.vk.product_publisher import VKProductPublisher

        _vk_publisher_singleton = VKProductPublisher()
    return _vk_publisher_singleton


@dataclass
class ChatDashboard:
    message_id: Optional[int] = None
    jobs: Deque[PlatformSyncJob] = field(default_factory=lambda: deque(maxlen=50))
    pending_count: int = 0
    done_count: int = 0
    error_count: int = 0


class PriceSyncService:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[PlatformSyncJob] = asyncio.Queue()
        self._dashboards: Dict[int, ChatDashboard] = {}
        self._worker_task: Optional[asyncio.Task] = None
        self._bot: Optional[Bot] = None
        self._list_debounce_task: Optional[asyncio.Task] = None
        self._list_debounce_used = False
        self._list_debounce_availability = False

    def start(self, bot: Bot) -> None:
        self._bot = bot
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop(), name="platform_sync_worker")
            logger.info("PlatformSyncService worker started")

    async def stop(self) -> None:
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        if self._list_debounce_task and not self._list_debounce_task.done():
            self._list_debounce_task.cancel()

    async def _enqueue(
        self,
        bot: Bot,
        job: PlatformSyncJob,
    ) -> PlatformSyncJob:
        self.start(bot)
        dash = self._get_dashboard(job.chat_id)
        dash.pending_count += 1
        dash.jobs.appendleft(job)
        await self._queue.put(job)
        await self._refresh_dashboard(bot, job.chat_id)
        return job

    async def enqueue_price_sync(
        self,
        bot: Bot,
        *,
        chat_id: int,
        product_id: int,
        product: dict,
        formatted_price: str,
        price_value: int,
        refresh_used_list: bool = True,
        refresh_availability_list: bool = False,
    ) -> PlatformSyncJob:
        job = PlatformSyncJob(
            job_id=uuid.uuid4().hex[:10],
            chat_id=chat_id,
            product_id=product_id,
            display_label=get_product_dashboard_label(product),
            kind=SyncJobKind.PRICE,
            formatted_price=formatted_price,
            price_value=price_value,
            refresh_used_list=refresh_used_list,
            refresh_availability_list=refresh_availability_list,
        )
        return await self._enqueue(bot, job)

    async def enqueue_unavailable_sync(
        self,
        bot: Bot,
        *,
        chat_id: int,
        product_id: int,
        product: dict,
        mark_telegram_enabled: bool = True,
        refresh_used_list: bool = True,
        refresh_availability_list: bool = False,
    ) -> PlatformSyncJob:
        job = PlatformSyncJob(
            job_id=uuid.uuid4().hex[:10],
            chat_id=chat_id,
            product_id=product_id,
            display_label=get_product_dashboard_label(product),
            kind=SyncJobKind.UNAVAILABLE,
            mark_telegram_enabled=mark_telegram_enabled,
            refresh_used_list=refresh_used_list,
            refresh_availability_list=refresh_availability_list,
        )
        return await self._enqueue(bot, job)

    def _get_dashboard(self, chat_id: int) -> ChatDashboard:
        if chat_id not in self._dashboards:
            self._dashboards[chat_id] = ChatDashboard()
        return self._dashboards[chat_id]

    async def _worker_loop(self) -> None:
        while True:
            job = await self._queue.get()
            bot = self._bot
            if bot is None:
                self._queue.task_done()
                continue
            try:
                if job.kind == SyncJobKind.PRICE:
                    await self._process_price_job(bot, job)
                else:
                    await self._process_unavailable_job(bot, job)
            except Exception:
                logger.exception("Platform sync job failed product_id=%s kind=%s", job.product_id, job.kind)
                job.status = JobStatus.ERROR
                job.platforms.detail = "Внутренняя ошибка"
            finally:
                dash = self._get_dashboard(job.chat_id)
                dash.pending_count = max(0, dash.pending_count - 1)
                if job.status == JobStatus.OK:
                    dash.done_count += 1
                elif job.status in (JobStatus.ERROR, JobStatus.PARTIAL):
                    dash.error_count += 1
                job.finished_at = datetime.now(timezone.utc)
                await self._refresh_dashboard(bot, job.chat_id)
                if job.refresh_used_list or job.refresh_availability_list:
                    self._schedule_list_refresh(
                        used=job.refresh_used_list,
                        availability=job.refresh_availability_list,
                    )
                self._queue.task_done()

    def _load_product_dict(self, product_id: int) -> Optional[dict]:
        from app.db.database import SessionLocal
        from app.api.models.product import Product
        from app.api.models.post import Post

        db = SessionLocal()
        try:
            product = db.query(Product).filter(Product.id == product_id).first()
            if not product:
                return None
            vk_post_id = None
            vk_post_link = None
            if product.post_id:
                post = db.query(Post).filter(Post.id == product.post_id).first()
                if post:
                    vk_post_id = post.vk_post_id
                    vk_post_link = post.vk_post_link
            return {
                "id": product.id,
                "name": product.name,
                "price": product.price,
                "telegram_link": product.telegram_link,
                "max_link": product.max_link,
                "instagram_link": product.instagram_link,
                "instagram_media_id": product.instagram_media_id,
                "post_id": product.post_id,
                "vk_product_id": product.vk_product_id,
                "vk_post_id": vk_post_id,
                "vk_post_link": vk_post_link,
                "avito_item_id": product.avito_item_id,
                "collection_name": product.collection_name,
                "custom_button_id": product.custom_button_id,
            }
        finally:
            db.close()

    async def _process_price_job(self, bot: Bot, job: PlatformSyncJob) -> None:
        job.status = JobStatus.RUNNING
        job.attempt += 1
        await self._refresh_dashboard(bot, job.chat_id)

        from app.bot.handlers.product_management import (
            resolve_product_max_link,
            update_max_post_price,
            update_telegram_post_price,
        )

        product_dict = self._load_product_dict(job.product_id)
        if not product_dict:
            job.status = JobStatus.ERROR
            job.platforms.detail = "Товар не найден в БД"
            return

        pr = job.platforms

        if product_dict.get("vk_product_id"):
            try:
                publisher = _get_vk_publisher()
                pr.vk = await publisher.update_product_price(
                    int(product_dict["vk_product_id"]), job.price_value
                )
            except Exception as e:
                logger.error("VK price sync failed product_id=%s: %s", job.product_id, e)
                pr.vk = False
        else:
            pr.vk = None

        if product_dict.get("avito_item_id"):
            try:
                from app.integrations.avito import actions as avito_actions

                item_id = int(str(product_dict["avito_item_id"]).strip())
                await avito_actions.update_item_price_rub(item_id, job.price_value)
                pr.avito = True
            except Exception as e:
                logger.error("Avito price sync failed product_id=%s: %s", job.product_id, e)
                pr.avito = False
        else:
            pr.avito = None

        if product_dict.get("telegram_link"):
            pr.telegram = await update_telegram_post_price(
                product_dict["telegram_link"],
                product_dict.get("price") or "",
                job.formatted_price,
            )
        else:
            pr.telegram = None

        max_link = resolve_product_max_link(product_dict)
        if max_link:
            pr.max_ = await update_max_post_price(
                max_link,
                product_dict.get("price") or "",
                job.formatted_price,
            )
        else:
            pr.max_ = None

        self._finalize_job_status(job)

    async def _process_unavailable_job(self, bot: Bot, job: PlatformSyncJob) -> None:
        job.status = JobStatus.RUNNING
        job.attempt += 1
        await self._refresh_dashboard(bot, job.chat_id)

        from app.bot.handlers.product_management import (
            mark_instagram_post_unavailable,
            mark_max_post_unavailable,
            mark_telegram_post_unavailable,
            mark_vk_post_unavailable,
            resolve_product_instagram_media_id,
            resolve_product_max_link,
            resolve_product_vk_post_id,
        )

        product_dict = self._load_product_dict(job.product_id)
        if not product_dict:
            job.status = JobStatus.ERROR
            job.platforms.detail = "Товар не найден в БД"
            return

        pr = job.platforms

        if product_dict.get("vk_product_id"):
            try:
                from app.utils.vk_client import get_market_vk_session, resolved_vk_group_id_int

                owner_id = -resolved_vk_group_id_int()
                vk = get_market_vk_session().get_api()
                await asyncio.to_thread(
                    vk.market.edit,
                    owner_id=owner_id,
                    item_id=int(product_dict["vk_product_id"]),
                    deleted=1,
                )
                pr.vk = True
            except Exception as e:
                logger.error("VK unavailable sync failed product_id=%s: %s", job.product_id, e)
                pr.vk = False
        else:
            pr.vk = None

        # Комментарий «неактуально» от лица группы под постом в ленте VK (best-effort).
        # Только при включённом переключателе «Товары ВК» и наличии поста в ленте.
        if resolve_product_vk_post_id(product_dict):
            try:
                await mark_vk_post_unavailable(product_dict)
            except Exception as e:
                logger.error(
                    "VK unavailable comment failed product_id=%s: %s", job.product_id, e
                )

        if product_dict.get("avito_item_id"):
            try:
                from app.integrations.avito import archive_queue as avito_archive_queue

                item_id = int(str(product_dict["avito_item_id"]).strip())
                if not product_dict.get("post_id"):
                    pr.avito = False
                    if not pr.detail:
                        pr.detail = "нет поста для архива Авито"
                else:
                    _created, detail = avito_archive_queue.enqueue(
                        product_id=int(product_dict["id"]),
                        avito_item_id=item_id,
                        post_id=str(product_dict["post_id"]),
                        product_name=product_dict.get("name") or "",
                    )
                    pr.avito = True
                    if detail and "ошиб" in detail.lower():
                        pr.avito = False
                        pr.detail = detail[:120]
            except Exception as e:
                logger.error("Avito archive enqueue failed product_id=%s: %s", job.product_id, e)
                pr.avito = False
        else:
            pr.avito = None

        if job.mark_telegram_enabled and product_dict.get("telegram_link"):
            pr.telegram = await mark_telegram_post_unavailable(product_dict["telegram_link"])
        else:
            pr.telegram = None

        max_link = resolve_product_max_link(product_dict)
        if job.mark_telegram_enabled and max_link:
            pr.max_ = await mark_max_post_unavailable(max_link)
        else:
            pr.max_ = None

        ig_media_id = resolve_product_instagram_media_id(product_dict)
        if job.mark_telegram_enabled and ig_media_id:
            pr.instagram = await mark_instagram_post_unavailable(product_dict)
        else:
            pr.instagram = None

        self._finalize_job_status(job)

    def _finalize_job_status(self, job: PlatformSyncJob) -> None:
        pr = job.platforms
        failures = [v for v in (pr.vk, pr.telegram, pr.max_, pr.instagram, pr.avito) if v is False]
        if failures:
            job.status = JobStatus.PARTIAL if any(
                v is True for v in (pr.vk, pr.telegram, pr.max_, pr.instagram, pr.avito)
            ) else JobStatus.ERROR
            if not pr.detail:
                parts = []
                if pr.vk is False:
                    parts.append("ВК")
                if pr.telegram is False:
                    parts.append("ТГ")
                if pr.max_ is False:
                    parts.append("Max")
                if pr.instagram is False:
                    parts.append("IG")
                if pr.avito is False:
                    parts.append("Авито")
                pr.detail = "ошибка: " + ", ".join(parts)
        else:
            job.status = JobStatus.OK

    def _schedule_list_refresh(self, *, used: bool, availability: bool) -> None:
        if used:
            self._list_debounce_used = True
        if availability:
            self._list_debounce_availability = True
        if self._list_debounce_task and not self._list_debounce_task.done():
            self._list_debounce_task.cancel()

        async def _run() -> None:
            try:
                await asyncio.sleep(LIST_REFRESH_DEBOUNCE_SEC)
                bot = self._bot
                if bot is None:
                    return
                if self._list_debounce_used:
                    from app.bot.utils.used_products_channel_updater import (
                        update_used_products_list_in_channel,
                    )

                    await update_used_products_list_in_channel(bot)
                if self._list_debounce_availability:
                    from app.bot.utils.channel_updater import update_availability_message

                    await update_availability_message(bot)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Debounced list refresh failed")
            finally:
                self._list_debounce_used = False
                self._list_debounce_availability = False

        self._list_debounce_task = asyncio.create_task(_run(), name="platform_list_debounce")

    def _running_hint(self, job: PlatformSyncJob) -> str:
        if job.kind == SyncJobKind.UNAVAILABLE:
            return "🚫 ВК · ТГ · IG · Max · Авито"
        return "ВК · ТГ · IG · Max · Авито"

    def _format_job_line(self, job: PlatformSyncJob) -> str:
        label = job.short_label()
        if job.status == JobStatus.QUEUED:
            return f"⏳ {label} — в очереди"
        if job.status == JobStatus.RUNNING:
            return f"⏳ {label} — {self._running_hint(job)}"
        if job.status == JobStatus.OK:
            ts = format_dashboard_ts_msk(job.finished_at or job.created_at)
            return f"✅ {label} — всё OK ({ts})"
        if job.status == JobStatus.PARTIAL:
            detail = html.escape(job.platforms.detail or "частично")
            ts = format_dashboard_ts_msk(job.finished_at or job.created_at)
            return f"⚠️ {label} — {detail} ({ts})"
        detail = html.escape(job.platforms.detail or "ошибка")
        ts = format_dashboard_ts_msk(job.finished_at or job.created_at)
        return f"❌ {label} — {detail} ({ts})"

    def render_dashboard_text(self, chat_id: int) -> str:
        dash = self._get_dashboard(chat_id)
        active = [j for j in dash.jobs if j.status in (JobStatus.QUEUED, JobStatus.RUNNING)]
        recent_done = [
            j
            for j in dash.jobs
            if j.status in (JobStatus.OK, JobStatus.PARTIAL, JobStatus.ERROR)
        ]

        queued = sum(1 for j in dash.jobs if j.status == JobStatus.QUEUED)
        running = sum(1 for j in dash.jobs if j.status == JobStatus.RUNNING)
        in_queue = queued + running
        footer = (
            f"В очереди: {in_queue} · Готово: {dash.done_count} · Ошибки: {dash.error_count}"
        )
        footer_block = f"\n\n{footer}"
        budget = TELEGRAM_TEXT_LIMIT - len(footer_block)

        lines: List[str] = []
        for job in active + recent_done:
            line = self._format_job_line(job)
            candidate = "\n".join(lines + [line]) if lines else line
            if len(candidate) > budget:
                break
            lines.append(line)

        if not lines:
            lines.append("⏳ Пока нет операций синхронизации")

        return "\n".join(lines) + footer_block

    async def _refresh_dashboard(self, bot: Bot, chat_id: int) -> None:
        dash = self._get_dashboard(chat_id)
        text = self.render_dashboard_text(chat_id)
        opts = {
            "parse_mode": "HTML",
            "link_preview_options": LinkPreviewOptions(is_disabled=True),
        }
        try:
            if dash.message_id is None:
                msg = await bot.send_message(chat_id=chat_id, text=text, **opts)
                dash.message_id = msg.message_id
                return
            from app.bot.utils.telegram_edit import edit_message_text_safe

            ok = await edit_message_text_safe(
                bot,
                chat_id=chat_id,
                message_id=dash.message_id,
                text=text,
                parse_mode="HTML",
                link_preview_disabled=True,
                apply_rate_limit=False,
            )
            if not ok:
                msg = await bot.send_message(chat_id=chat_id, text=text, **opts)
                dash.message_id = msg.message_id
        except Exception:
            logger.exception("Failed to refresh sync dashboard chat_id=%s", chat_id)
            try:
                msg = await bot.send_message(chat_id=chat_id, text=text, **opts)
                dash.message_id = msg.message_id
            except Exception:
                logger.exception("Failed to create sync dashboard chat_id=%s", chat_id)


_price_sync_service: Optional[PriceSyncService] = None


def get_price_sync_service() -> PriceSyncService:
    global _price_sync_service
    if _price_sync_service is None:
        _price_sync_service = PriceSyncService()
    return _price_sync_service


NEW_COLLECTION_VALUES = {"iPhone новые", "Airpods", "Apple Watch", "iPad", "custom"}


def is_used_product_branch(product: dict) -> bool:
    coll = (product.get("collection_name") or "").strip()
    return coll not in NEW_COLLECTION_VALUES


def is_new_product_branch(product: dict) -> bool:
    coll = (product.get("collection_name") or "").strip()
    return coll in {"iPhone новые", "Airpods", "Apple Watch", "iPad"}


def format_price_saved_immediate_message(
    formatted_price: str,
    *,
    price_change=None,
) -> str:
    """Мгновенный ответ пользователю после сохранения цены в БД."""
    from app.utils.price_change import format_price_change_html_lines

    lines: List[str] = []
    if price_change is not None:
        lines.extend(format_price_change_html_lines(price_change))
    lines.append(f"✅ Сохранено в базе: {html.escape(formatted_price)}")
    lines.append(
        "⏳ Площадки синхронизируются — статус в сообщении «📡 Синхронизация площадок»"
    )
    return "\n".join(lines)


def format_unavailable_saved_immediate_message() -> str:
    """Мгновенный ответ после пометки «недоступен» в БД."""
    return (
        "✅ Помечен недоступным в базе\n"
        "⏳ Площадки синхронизируются — статус в сообщении «📡 Синхронизация площадок»"
    )
