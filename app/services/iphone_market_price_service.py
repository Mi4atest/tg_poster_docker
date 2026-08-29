"""Оркестрация безопасной рыночной оценки iPhone по Avito."""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Awaitable, Callable, Optional

from app.config.settings import (
    AVITO_MARKET_BLOCK_COOLDOWN_SEC,
    AVITO_MARKET_CACHE_TTL_SEC,
    AVITO_MARKET_DAILY_REQUEST_LIMIT,
    AVITO_MARKET_MIN_REQUEST_INTERVAL_SEC,
    AVITO_MARKET_MIN_SAMPLE_SIZE,
    AVITO_MARKET_MIN_SELLER_GROUP_SIZE,
    AVITO_MARKET_REGION,
    AVITO_MARKET_SOFT_SAMPLE_SIZE,
    AVITO_MARKET_TIMEOUT_SEC,
)
from app.db.avito_market_queries import (
    count_live_requests_since,
    get_active_market_block_until,
    get_last_live_request_at,
    get_market_snapshot,
    get_market_snapshot_by_id,
    list_recent_market_snapshots,
    record_live_request,
    record_market_error,
    save_market_snapshot,
)
from app.db.database import run_db
from app.integrations.avito.market_search import (
    AvitoMarketBlockedError,
    AvitoMarketError,
    MarketListing,
    fetch_market_listings,
)
from app.utils.iphone_market_query import IphoneMarketQuery
from app.utils.price_stats import PriceSummary, analyze_market_listings


logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """UTC без tzinfo для совместимости с существующими DateTime-колонками."""
    return datetime.now(UTC).replace(tzinfo=None)


class MarketTemporarilyUnavailable(RuntimeError):
    """Нет свежих данных и сейчас нельзя безопасно обращаться к Avito."""


@dataclass(frozen=True)
class MarketPriceEstimate:
    query: IphoneMarketQuery
    region: str
    total_count: int
    matched_count: int
    used_count: int
    outlier_count: int
    summary: Optional[PriceSummary]
    private_summary: Optional[PriceSummary]
    business_summary: Optional[PriceSummary]
    fetched_at: datetime
    is_stale: bool = False
    stale_reason: Optional[str] = None
    is_soft: bool = False
    limit_hint: Optional[str] = None
    listings: tuple[MarketListing, ...] = ()
    live_fetched: bool = False
    snapshot_id: Optional[int] = None


ListingFetcher = Callable[[IphoneMarketQuery], Awaitable[list[MarketListing]]]


def _summary_from_json(value: object) -> Optional[PriceSummary]:
    if not isinstance(value, dict):
        return None
    try:
        return PriceSummary(
            count=int(value["count"]),
            median_rub=int(value["median_rub"]),
            q25_rub=int(value["q25_rub"]),
            q75_rub=int(value["q75_rub"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _listings_from_audit(raw: object) -> tuple[MarketListing, ...]:
    if not isinstance(raw, list):
        return ()
    items: list[MarketListing] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        try:
            price = int(row.get("price_rub") or 0)
            item_id = str(row.get("id") or "").strip()
            title = str(row.get("title") or "").strip()
        except (TypeError, ValueError):
            continue
        if not item_id or not title or price <= 0:
            continue
        seller = row.get("seller_type")
        items.append(
            MarketListing(
                item_id=item_id,
                title=title,
                price_rub=price,
                url=str(row.get("url") or ""),
                seller_type=str(seller) if seller else None,
                condition=str(row.get("condition") or "") or None,
                city=str(row.get("city") or ""),
                included=(
                    bool(row.get("included"))
                    if row.get("included") is not None
                    else True
                ),
                rejection_reason=str(row.get("rejection_reason") or "") or None,
            )
        )
    items.sort(key=lambda item: item.price_rub)
    return tuple(items)


def user_facing_market_error(reason: str) -> str:
    """Простые формулировки для продавца (без SPFA/прокси/HTTP)."""
    text = (reason or "").lower()
    if "через ~" in text or "через примерно" in text:
        return reason
    if "интервал" in text or "подождите" in text or "сек" in text:
        return reason
    if "суточн" in text or ("лимит" in text and "свеж" in text):
        return (
            "На сегодня достигнут лимит свежих поисков. "
            "Повторите завтра или откройте уже сохранённую оценку по этой модели."
        )
    if "ограничил" in text or "проверк" in text or "captcha" in text or "block" in text:
        return (
            "Avito временно ограничил автоматические запросы. "
            "Оценка недоступна какое-то время — попробуйте позже."
        )
    if "не удалось" in text or "ошибк" in text or "приостанов" in text:
        return "Сейчас не удалось обновить оценку рынка. Попробуйте позже."
    return "Сейчас оценка рынка временно недоступна. Попробуйте позже."


class IphoneMarketPriceService:
    def __init__(self, fetcher: Optional[ListingFetcher] = None) -> None:
        self._fetcher = fetcher or self._fetch
        self._fetch_semaphore = asyncio.Semaphore(1)
        self._key_locks: dict[str, asyncio.Lock] = {}
        self._last_request_at: Optional[datetime] = None
        self._request_times: deque[datetime] = deque()
        self._blocked_until: Optional[datetime] = None
        self._failed_until: dict[str, datetime] = {}

    async def _fetch(self, query: IphoneMarketQuery) -> list[MarketListing]:
        return await fetch_market_listings(query, timeout_seconds=AVITO_MARKET_TIMEOUT_SEC)

    @staticmethod
    def _cache_key(query: IphoneMarketQuery) -> str:
        return f"{AVITO_MARKET_REGION.lower()}:{query.cache_key}"

    @staticmethod
    def _is_soft_snapshot(snapshot: dict) -> bool:
        if snapshot.get("median_rub") is None:
            return False
        return int(snapshot.get("used_count") or 0) < AVITO_MARKET_MIN_SAMPLE_SIZE

    @staticmethod
    def _from_snapshot(
        query: IphoneMarketQuery,
        snapshot: dict,
        *,
        stale: bool = False,
        reason: Optional[str] = None,
        limit_hint: Optional[str] = None,
        live_fetched: bool = False,
    ) -> MarketPriceEstimate:
        summary = None
        if snapshot.get("median_rub") is not None:
            summary = PriceSummary(
                count=int(snapshot["used_count"]),
                median_rub=int(snapshot["median_rub"]),
                q25_rub=int(snapshot["q25_rub"]),
                q75_rub=int(snapshot["q75_rub"]),
            )
        snapshot_id = snapshot.get("id")
        try:
            snapshot_id_int = int(snapshot_id) if snapshot_id is not None else None
        except (TypeError, ValueError):
            snapshot_id_int = None
        return MarketPriceEstimate(
            query=query,
            region=str(snapshot.get("region") or AVITO_MARKET_REGION),
            total_count=int(snapshot.get("total_count") or 0),
            matched_count=int(snapshot.get("matched_count") or 0),
            used_count=int(snapshot.get("used_count") or 0),
            outlier_count=int(snapshot.get("outlier_count") or 0),
            summary=summary,
            private_summary=_summary_from_json(snapshot.get("private_summary")),
            business_summary=_summary_from_json(snapshot.get("business_summary")),
            fetched_at=snapshot["fetched_at"],
            is_stale=stale,
            stale_reason=reason,
            is_soft=IphoneMarketPriceService._is_soft_snapshot(snapshot),
            limit_hint=limit_hint,
            listings=_listings_from_audit(snapshot.get("listing_audit")),
            live_fetched=live_fetched,
            snapshot_id=snapshot_id_int,
        )

    @staticmethod
    def _fresh(snapshot: Optional[dict], now: datetime) -> bool:
        return bool(
            snapshot
            and snapshot.get("status") == "success"
            and snapshot.get("expires_at")
            and snapshot["expires_at"] > now
        )

    def _prune_daily_limit(self, now: datetime) -> None:
        threshold = now - timedelta(hours=24)
        while self._request_times and self._request_times[0] < threshold:
            self._request_times.popleft()

    def _restriction_reason(
        self,
        key: str,
        now: datetime,
        snapshot: Optional[dict],
        *,
        global_until: Optional[datetime] = None,
        daily_count: int = 0,
        last_request_at: Optional[datetime] = None,
    ) -> Optional[str]:
        if snapshot and snapshot.get("retry_after") and snapshot["retry_after"] > now:
            retry_after = snapshot["retry_after"]
            mins = max(1, int((retry_after - now).total_seconds() / 60) + 1)
            last = str(snapshot.get("last_error") or "")
            if "проверк" in last.lower() or "ограничил" in last.lower():
                return (
                    f"Avito временно ограничил автоматические запросы. "
                    f"Повторите через ~{mins} мин."
                )
            return user_facing_market_error(last or "обновление временно приостановлено")

        if global_until and global_until > now:
            mins = max(1, int((global_until - now).total_seconds() / 60) + 1)
            # #region agent log
            try:
                from app.integrations.avito.debug_agent_log import agent_dbg

                agent_dbg(
                    "D",
                    "iphone_market_price_service.py:global_block",
                    "live blocked by global retry_after",
                    {"mins": mins, "until": global_until.isoformat()},
                )
            except Exception:
                pass
            # #endregion
            return (
                f"Avito временно ограничил автоматические запросы. "
                f"Повторите через ~{mins} мин."
            )

        if self._blocked_until and self._blocked_until > now:
            mins = max(1, int((self._blocked_until - now).total_seconds() / 60) + 1)
            return (
                f"Avito временно ограничил автоматические запросы. "
                f"Повторите через ~{mins} мин."
            )
        failed_until = self._failed_until.get(key)
        if failed_until and failed_until > now:
            mins = max(1, int((failed_until - now).total_seconds() / 60) + 1)
            return f"Сейчас не удалось обновить оценку. Повторите через ~{mins} мин."
        if daily_count >= AVITO_MARKET_DAILY_REQUEST_LIMIT:
            return user_facing_market_error("достигнут безопасный суточный лимит запросов")
        effective_last = last_request_at or self._last_request_at
        if (
            effective_last
            and now - effective_last
            < timedelta(seconds=AVITO_MARKET_MIN_REQUEST_INTERVAL_SEC)
        ):
            wait = int(
                AVITO_MARKET_MIN_REQUEST_INTERVAL_SEC
                - (now - effective_last).total_seconds()
            )
            wait = max(1, wait)
            return (
                f"Подождите ещё {wait} сек. между новыми поисками — "
                "так мы не перегружаем Avito и не сжигаем лишние запросы."
            )
        return None

    @staticmethod
    def _fallback_or_raise(
        query: IphoneMarketQuery,
        snapshot: Optional[dict],
        reason: str,
    ) -> MarketPriceEstimate:
        friendly = user_facing_market_error(reason)
        if snapshot and snapshot.get("status") == "success" and snapshot.get("fetched_at"):
            return IphoneMarketPriceService._from_snapshot(
                query,
                snapshot,
                stale=True,
                reason=friendly,
                limit_hint=friendly,
            )
        raise MarketTemporarilyUnavailable(friendly)

    async def estimate(
        self,
        query: IphoneMarketQuery,
        *,
        source: str = "manual",
    ) -> MarketPriceEstimate:
        key = self._cache_key(query)
        now = _utcnow()
        snapshot = await run_db(get_market_snapshot, key)
        if self._fresh(snapshot, now):
            return self._from_snapshot(query, snapshot)

        lock = self._key_locks.setdefault(key, asyncio.Lock())
        async with lock:
            now = _utcnow()
            snapshot = await run_db(get_market_snapshot, key)
            if self._fresh(snapshot, now):
                return self._from_snapshot(query, snapshot)

            async with self._fetch_semaphore:
                now = _utcnow()
                global_until = await run_db(get_active_market_block_until)
                daily_count = await run_db(
                    count_live_requests_since, now - timedelta(hours=24)
                )
                last_request_at = await run_db(get_last_live_request_at)
                reason = self._restriction_reason(
                    key,
                    now,
                    snapshot,
                    global_until=global_until,
                    daily_count=int(daily_count or 0),
                    last_request_at=last_request_at,
                )
                if reason:
                    return self._fallback_or_raise(query, snapshot, reason)

                self._last_request_at = now
                self._request_times.append(now)
                await run_db(record_live_request, key, source=source)
                try:
                    listings = await self._fetcher(query)
                    analysis = analyze_market_listings(
                        listings,
                        query,
                        min_sample_size=AVITO_MARKET_MIN_SAMPLE_SIZE,
                        min_soft_sample_size=AVITO_MARKET_SOFT_SAMPLE_SIZE,
                        min_seller_group_size=AVITO_MARKET_MIN_SELLER_GROUP_SIZE,
                    )
                    saved = await run_db(
                        save_market_snapshot,
                        key,
                        query,
                        analysis,
                        region=AVITO_MARKET_REGION,
                        ttl_seconds=AVITO_MARKET_CACHE_TTL_SEC,
                    )
                    return self._from_snapshot(query, saved, live_fetched=True)
                except AvitoMarketBlockedError as exc:
                    soft = bool(getattr(exc, "soft", False))
                    cooldown = (
                        20 * 60 if soft else AVITO_MARKET_BLOCK_COOLDOWN_SEC
                    )
                    self._blocked_until = _utcnow() + timedelta(seconds=cooldown)
                    reason = "Avito запросил проверку или ограничил запросы"
                    retry_after_seconds = cooldown
                    logger.warning(
                        "Avito market request blocked (soft=%s cooldown=%ss): %s",
                        soft,
                        cooldown,
                        exc,
                    )
                except AvitoMarketError as exc:
                    self._failed_until[key] = _utcnow() + timedelta(minutes=15)
                    reason = "не удалось обновить данные Avito"
                    retry_after_seconds = 15 * 60
                    logger.warning("Avito market request failed: %s", exc)
                except Exception:
                    self._failed_until[key] = _utcnow() + timedelta(minutes=15)
                    reason = "внутренняя ошибка обновления оценки"
                    retry_after_seconds = 15 * 60
                    logger.exception("Unexpected Avito market estimate failure")

                try:
                    await run_db(
                        record_market_error,
                        key,
                        query,
                        reason,
                        region=AVITO_MARKET_REGION,
                        retry_after_seconds=retry_after_seconds,
                    )
                except Exception:
                    logger.exception("Failed to record Avito market error")
                return self._fallback_or_raise(query, snapshot, reason)

    async def get_cached_report(self, snapshot_id: int) -> MarketPriceEstimate:
        """Открыть сохранённый отчёт без запроса к Avito."""
        snapshot = await run_db(get_market_snapshot_by_id, int(snapshot_id))
        if not snapshot or snapshot.get("status") != "success" or not snapshot.get("fetched_at"):
            raise MarketTemporarilyUnavailable("Сохранённый отчёт не найден.")
        query = IphoneMarketQuery(
            model=str(snapshot["model"]),
            memory_gb=int(snapshot["memory_gb"]),
        )
        now = _utcnow()
        stale = not self._fresh(snapshot, now)
        return self._from_snapshot(
            query,
            snapshot,
            stale=stale,
            reason="показан сохранённый отчёт из истории" if stale else None,
        )

    async def list_recent_reports(self, *, limit: Optional[int] = None) -> list[dict]:
        return await run_db(list_recent_market_snapshots, limit=limit)


_service: Optional[IphoneMarketPriceService] = None


def get_iphone_market_price_service() -> IphoneMarketPriceService:
    global _service
    if _service is None:
        _service = IphoneMarketPriceService()
    return _service
