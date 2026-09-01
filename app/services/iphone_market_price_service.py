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
    merge_harvest_into_snapshot,
    record_live_request,
    record_market_daily_gap,
    record_market_error,
    refresh_snapshot_filters,
    save_market_snapshot,
)
from app.db.database import run_db
from app.integrations.avito.market_diag import (
    CODE_AVITO_BLOCK,
    CODE_DAILY_LIMIT,
    CODE_FAIL,
    CODE_INTERVAL,
    CODE_LIVE,
    infer_market_code,
    log_market,
    user_facing_market_error,
    user_notice,
)
from app.integrations.avito.market_search import (
    AvitoMarketBlockedError,
    AvitoMarketError,
    MarketListing,
    fetch_market_listings,
)
from app.utils.iphone_market_query import IphoneMarketQuery
from app.utils.market_daily import QUALITY_SOFT, classify_sample_quality, quote_is_carried
from app.utils.market_harvest import group_foreign_listings, listings_from_audit
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
    quote_as_of: Optional[datetime] = None
    quote_quality: Optional[str] = None
    quote_carried: bool = False
    last_error: Optional[str] = None
    last_error_at: Optional[datetime] = None


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
    return listings_from_audit(raw)


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
        quality = snapshot.get("quote_quality")
        if quality:
            return quality == QUALITY_SOFT
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
        quote_quality = snapshot.get("quote_quality")
        if not quote_quality and snapshot.get("median_rub") is not None:
            quote_quality = classify_sample_quality(
                int(snapshot.get("used_count") or 0),
                min_sample_size=AVITO_MARKET_MIN_SAMPLE_SIZE,
                min_soft_sample_size=AVITO_MARKET_SOFT_SAMPLE_SIZE,
            )
        quote_as_of = snapshot.get("quote_as_of") or snapshot.get("fetched_at")
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
            quote_as_of=quote_as_of if isinstance(quote_as_of, datetime) else None,
            quote_quality=str(quote_quality) if quote_quality else None,
            quote_carried=quote_is_carried(
                quote_quality=str(quote_quality) if quote_quality else None,
                used_count=int(snapshot.get("used_count") or 0),
                has_summary=summary is not None,
            ),
            last_error=str(snapshot.get("last_error") or "") or None,
            last_error_at=snapshot.get("last_error_at")
            if isinstance(snapshot.get("last_error_at"), datetime)
            else None,
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
            code = infer_market_code(last or CODE_AVITO_BLOCK)
            log_market("skip", code, wait_mins=mins, source="snapshot_retry")
            return user_notice(code, wait_mins=mins)

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
            log_market("skip", CODE_AVITO_BLOCK, wait_mins=mins, source="global_retry")
            return user_notice(CODE_AVITO_BLOCK, wait_mins=mins)

        if self._blocked_until and self._blocked_until > now:
            mins = max(1, int((self._blocked_until - now).total_seconds() / 60) + 1)
            log_market("skip", CODE_AVITO_BLOCK, wait_mins=mins, source="memory_block")
            return user_notice(CODE_AVITO_BLOCK, wait_mins=mins)
        failed_until = self._failed_until.get(key)
        if failed_until and failed_until > now:
            mins = max(1, int((failed_until - now).total_seconds() / 60) + 1)
            log_market("skip", CODE_FAIL, wait_mins=mins, source="fail_cooldown")
            return user_notice(CODE_FAIL, wait_mins=mins)
        if daily_count >= AVITO_MARKET_DAILY_REQUEST_LIMIT:
            log_market(
                "skip",
                CODE_DAILY_LIMIT,
                count=daily_count,
                limit=AVITO_MARKET_DAILY_REQUEST_LIMIT,
            )
            return user_notice(
                CODE_DAILY_LIMIT,
                daily_limit=AVITO_MARKET_DAILY_REQUEST_LIMIT,
            )
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
            log_market("skip", CODE_INTERVAL, wait_sec=wait)
            return user_notice(CODE_INTERVAL, wait_sec=wait)
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

    async def _harvest_bonus_listings(
        self,
        source_query: IphoneMarketQuery,
        listings: list[MarketListing],
    ) -> None:
        """Разложить чужие карточки по уже существующим снимкам. Без HTTP и лимита."""
        buckets = group_foreign_listings(listings, source_query)
        if not buckets:
            return
        for target, items in buckets.items():
            merged = await run_db(
                merge_harvest_into_snapshot,
                self._cache_key(target),
                target,
                items,
                region=AVITO_MARKET_REGION,
            )
            if not merged:
                continue
            log_market(
                "harvest",
                CODE_LIVE,
                model=target.cache_key,
                added=len(items),
                used=merged.get("used_count"),
                snapshot_id=merged.get("id"),
            )

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
                    # #region agent log
                    try:
                        from app.integrations.avito.debug_agent_log import agent_dbg

                        agent_dbg(
                            "D",
                            "iphone_market_price_service.py:restriction",
                            "live skipped by restriction",
                            {
                                "source": source,
                                "key": key[:80],
                                "reason_prefix": reason[:80],
                                "has_snapshot": bool(snapshot and snapshot.get("status") == "success"),
                                "daily_count": int(daily_count or 0),
                            },
                            run_id="wl",
                        )
                    except Exception:
                        pass
                    # #endregion
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
                        source=source,
                    )
                    try:
                        await self._harvest_bonus_listings(query, listings)
                    except Exception:
                        logger.exception("Avito market harvest failed")
                    return self._from_snapshot(query, saved, live_fetched=True)
                except AvitoMarketBlockedError as exc:
                    soft = bool(getattr(exc, "soft", False))
                    cooldown = (
                        20 * 60 if soft else AVITO_MARKET_BLOCK_COOLDOWN_SEC
                    )
                    self._blocked_until = _utcnow() + timedelta(seconds=cooldown)
                    code = getattr(exc, "code", None) or CODE_AVITO_BLOCK
                    has_proxy = getattr(exc, "has_proxy", True)
                    wait_mins = max(1, int(cooldown / 60))
                    reason = user_notice(
                        code,
                        wait_mins=wait_mins,
                        has_proxy=has_proxy,
                    )
                    retry_after_seconds = cooldown
                    log_market(
                        "cooldown",
                        code,
                        soft=soft,
                        cooldown_sec=cooldown,
                        has_proxy=has_proxy,
                        source=source,
                    )
                    logger.warning(
                        "Avito market request blocked code=%s soft=%s cooldown=%ss: %s",
                        code,
                        soft,
                        cooldown,
                        exc,
                    )
                    # #region agent log
                    try:
                        from app.integrations.avito.debug_agent_log import agent_dbg

                        agent_dbg(
                            "B",
                            "iphone_market_price_service.py:blocked",
                            "live classified as block",
                            {"source": source, "soft": soft, "cooldown": cooldown},
                            run_id="wl",
                        )
                    except Exception:
                        pass
                    # #endregion
                except AvitoMarketError as exc:
                    self._failed_until[key] = _utcnow() + timedelta(minutes=15)
                    code = getattr(exc, "code", None) or CODE_FAIL
                    has_proxy = getattr(exc, "has_proxy", True)
                    reason = user_notice(code, wait_mins=15, has_proxy=has_proxy)
                    retry_after_seconds = 15 * 60
                    log_market(
                        "cooldown",
                        code,
                        retry_min=15,
                        has_proxy=has_proxy,
                        source=source,
                    )
                    logger.warning("Avito market request failed code=%s: %s", code, exc)
                    # #region agent log
                    try:
                        from app.integrations.avito.debug_agent_log import agent_dbg

                        agent_dbg(
                            "A",
                            "iphone_market_price_service.py:failed",
                            "live classified as fail",
                            {"source": source, "code": code, "error": str(exc)[:160]},
                            run_id="wl",
                        )
                    except Exception:
                        pass
                    # #endregion
                except Exception:
                    self._failed_until[key] = _utcnow() + timedelta(minutes=15)
                    reason = user_notice(CODE_FAIL, wait_mins=15)
                    retry_after_seconds = 15 * 60
                    log_market("cooldown", CODE_FAIL, retry_min=15, source=source)
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
                    await run_db(
                        record_market_daily_gap,
                        query,
                        region=AVITO_MARKET_REGION,
                        source=source,
                    )
                except Exception:
                    logger.exception("Failed to record Avito market error")
                return self._fallback_or_raise(query, snapshot, reason)

    async def get_cached_report(self, snapshot_id: int) -> MarketPriceEstimate:
        """Открыть сохранённый отчёт без запроса к Avito."""
        snapshot = await run_db(refresh_snapshot_filters, int(snapshot_id))
        if not snapshot:
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
