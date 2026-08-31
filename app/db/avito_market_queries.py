"""Синхронные DB-операции для кэша рыночной оценки Avito."""
from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

from app.api.models.avito_market_snapshot import AvitoMarketSnapshot
from app.api.models.avito_market_daily import AvitoMarketDaily
from app.api.models.avito_market_request_log import AvitoMarketRequestLog
from app.config.settings import AVITO_MARKET_MIN_SAMPLE_SIZE, AVITO_MARKET_SOFT_SAMPLE_SIZE
from app.db.database import SessionLocal
from app.utils.iphone_market_query import IphoneMarketQuery
from app.utils.market_daily import (
    MARKET_DAILY_DAYS,
    QUALITY_GAP,
    QUALITY_OK,
    QUALITY_SOFT,
    classify_sample_quality,
    observed_on_msk,
    should_replace_daily,
)
from app.utils.price_stats import MarketAnalysis


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _snapshot_dict(row: AvitoMarketSnapshot) -> dict[str, Any]:
    return {
        "id": row.id,
        "cache_key": row.cache_key,
        "model": row.model,
        "memory_gb": row.memory_gb,
        "region": row.region,
        "status": row.status,
        "total_count": row.total_count,
        "matched_count": row.matched_count,
        "used_count": row.used_count,
        "outlier_count": row.outlier_count,
        "median_rub": row.median_rub,
        "q25_rub": row.q25_rub,
        "q75_rub": row.q75_rub,
        "quote_as_of": getattr(row, "quote_as_of", None),
        "quote_quality": getattr(row, "quote_quality", None),
        "private_summary": row.private_summary,
        "business_summary": row.business_summary,
        "listing_audit": row.listing_audit or [],
        "fetched_at": row.fetched_at,
        "expires_at": row.expires_at,
        "last_error_at": row.last_error_at,
        "last_error": row.last_error,
        "retry_after": row.retry_after,
    }


def _daily_dict(row: AvitoMarketDaily) -> dict[str, Any]:
    return {
        "id": row.id,
        "model": row.model,
        "memory_gb": row.memory_gb,
        "region": row.region,
        "observed_on": row.observed_on,
        "median_rub": row.median_rub,
        "q25_rub": row.q25_rub,
        "q75_rub": row.q75_rub,
        "used_count": row.used_count,
        "total_count": row.total_count,
        "quality": row.quality,
        "source": row.source,
        "snapshot_id": row.snapshot_id,
    }


def _upsert_daily(
    db,
    *,
    model: str,
    memory_gb: int,
    region: str,
    observed_on,
    quality: str,
    source: str,
    used_count: int,
    total_count: int,
    median_rub: Optional[int],
    q25_rub: Optional[int],
    q75_rub: Optional[int],
    snapshot_id: Optional[int],
    now: datetime,
) -> None:
    row = (
        db.query(AvitoMarketDaily)
        .filter(
            AvitoMarketDaily.model == model,
            AvitoMarketDaily.memory_gb == int(memory_gb),
            AvitoMarketDaily.region == str(region),
            AvitoMarketDaily.observed_on == observed_on,
        )
        .first()
    )
    if row is not None and not should_replace_daily(row.quality, quality):
        return
    if row is None:
        row = AvitoMarketDaily(
            model=model,
            memory_gb=int(memory_gb),
            region=str(region),
            observed_on=observed_on,
        )
        db.add(row)
    row.quality = quality
    row.source = (source or "manual")[:24]
    row.used_count = int(used_count or 0)
    row.total_count = int(total_count or 0)
    row.snapshot_id = snapshot_id
    if quality in {QUALITY_OK, QUALITY_SOFT}:
        row.median_rub = median_rub
        row.q25_rub = q25_rub
        row.q75_rub = q75_rub
    else:
        row.median_rub = None
        row.q25_rub = None
        row.q75_rub = None
    if row.created_at is None:
        row.created_at = now


def list_market_daily(
    model: str,
    memory_gb: int,
    *,
    days: int = MARKET_DAILY_DAYS,
    region: Optional[str] = None,
) -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        query = db.query(AvitoMarketDaily).filter(
            AvitoMarketDaily.model == str(model),
            AvitoMarketDaily.memory_gb == int(memory_gb),
        )
        if region:
            query = query.filter(AvitoMarketDaily.region == str(region))
        since = observed_on_msk(_utcnow()) - timedelta(days=max(1, int(days)) - 1)
        rows = (
            query.filter(AvitoMarketDaily.observed_on >= since)
            .order_by(AvitoMarketDaily.observed_on.asc())
            .all()
        )
        return [_daily_dict(row) for row in rows]
    finally:
        db.close()


def record_market_daily_gap(
    query: IphoneMarketQuery,
    *,
    region: str,
    source: str = "manual",
) -> None:
    now = _utcnow()
    db = SessionLocal()
    try:
        _upsert_daily(
            db,
            model=query.model,
            memory_gb=query.memory_gb,
            region=region,
            observed_on=observed_on_msk(now),
            quality=QUALITY_GAP,
            source=source,
            used_count=0,
            total_count=0,
            median_rub=None,
            q25_rub=None,
            q75_rub=None,
            snapshot_id=None,
            now=now,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_market_snapshot(cache_key: str) -> Optional[dict[str, Any]]:
    db = SessionLocal()
    try:
        row = (
            db.query(AvitoMarketSnapshot)
            .filter(AvitoMarketSnapshot.cache_key == cache_key)
            .first()
        )
        return _snapshot_dict(row) if row else None
    finally:
        db.close()


def get_market_snapshot_by_id(snapshot_id: int) -> Optional[dict[str, Any]]:
    db = SessionLocal()
    try:
        row = (
            db.query(AvitoMarketSnapshot)
            .filter(AvitoMarketSnapshot.id == int(snapshot_id))
            .first()
        )
        return _snapshot_dict(row) if row else None
    finally:
        db.close()


def get_latest_success_snapshot_for_config(
    model: str,
    memory_gb: int,
) -> Optional[dict[str, Any]]:
    """Последний успешный снимок по модели и памяти, без запроса к Avito."""
    db = SessionLocal()
    try:
        row = (
            db.query(AvitoMarketSnapshot)
            .filter(
                AvitoMarketSnapshot.model == str(model),
                AvitoMarketSnapshot.memory_gb == int(memory_gb),
                AvitoMarketSnapshot.status == "success",
                AvitoMarketSnapshot.q25_rub.isnot(None),
                AvitoMarketSnapshot.q75_rub.isnot(None),
                AvitoMarketSnapshot.fetched_at.isnot(None),
            )
            .order_by(AvitoMarketSnapshot.fetched_at.desc())
            .first()
        )
        return _snapshot_dict(row) if row else None
    finally:
        db.close()


def list_recent_market_snapshots(*, limit: Optional[int] = None) -> list[dict[str, Any]]:
    """Сохранённые успешные отчёты (без нового запроса к Avito)."""
    db = SessionLocal()
    try:
        query = (
            db.query(AvitoMarketSnapshot)
            .filter(
                AvitoMarketSnapshot.status == "success",
                AvitoMarketSnapshot.fetched_at.isnot(None),
            )
        )
        if limit is not None:
            query = query.order_by(AvitoMarketSnapshot.fetched_at.desc()).limit(
                max(1, min(int(limit), 200))
            )
        rows = query.all()
        return [_snapshot_dict(row) for row in rows]
    finally:
        db.close()


def get_active_market_block_until() -> Optional[datetime]:
    """Глобальная пауза после 439: max(retry_after) по снимкам с ограничением Avito."""
    db = SessionLocal()
    try:
        now = _utcnow()
        rows = (
            db.query(AvitoMarketSnapshot.retry_after, AvitoMarketSnapshot.last_error)
            .filter(
                AvitoMarketSnapshot.retry_after.isnot(None),
                AvitoMarketSnapshot.retry_after > now,
            )
            .all()
        )
        latest: Optional[datetime] = None
        for retry_after, last_error in rows:
            text = str(last_error or "").lower()
            if "ограничил" not in text and "проверк" not in text:
                continue
            if latest is None or (retry_after and retry_after > latest):
                latest = retry_after
        return latest
    finally:
        db.close()


def save_market_snapshot(
    cache_key: str,
    query: IphoneMarketQuery,
    analysis: MarketAnalysis,
    *,
    region: str,
    ttl_seconds: int,
    source: str = "manual",
) -> dict[str, Any]:
    now = _utcnow()
    quality = classify_sample_quality(
        analysis.used_count,
        min_sample_size=AVITO_MARKET_MIN_SAMPLE_SIZE,
        min_soft_sample_size=AVITO_MARKET_SOFT_SAMPLE_SIZE,
    )
    db = SessionLocal()
    try:
        row = (
            db.query(AvitoMarketSnapshot)
            .filter(AvitoMarketSnapshot.cache_key == cache_key)
            .first()
        )
        if row is None:
            row = AvitoMarketSnapshot(cache_key=cache_key)
            db.add(row)
        summary = analysis.summary
        row.model = query.model
        row.memory_gb = query.memory_gb
        row.region = region
        row.status = "success"
        row.total_count = analysis.total_count
        row.matched_count = analysis.matched_count
        row.used_count = analysis.used_count
        row.outlier_count = analysis.outlier_count
        if summary:
            row.median_rub = summary.median_rub
            row.q25_rub = summary.q25_rub
            row.q75_rub = summary.q75_rub
            row.quote_as_of = now
            row.quote_quality = quality
        elif row.median_rub is None:
            row.quote_quality = quality
        row.private_summary = asdict(analysis.private_summary) if analysis.private_summary else None
        row.business_summary = asdict(analysis.business_summary) if analysis.business_summary else None
        row.listing_audit = [
            {
                "id": item.item_id,
                "title": item.title[:300],
                "price_rub": item.price_rub,
                "seller_type": item.seller_type,
                "condition": item.condition,
                "city": item.city or "",
                "url": (item.url or "")[:500],
                "included": item.included,
                "rejection_reason": item.rejection_reason,
            }
            for item in analysis.audited_listings[:100]
        ]
        row.fetched_at = now
        row.expires_at = now + timedelta(seconds=ttl_seconds)
        row.last_error_at = None
        row.last_error = None
        row.retry_after = None
        db.flush()
        _upsert_daily(
            db,
            model=query.model,
            memory_gb=query.memory_gb,
            region=region,
            observed_on=observed_on_msk(now),
            quality=quality,
            source=source,
            used_count=analysis.used_count,
            total_count=analysis.total_count,
            median_rub=summary.median_rub if summary else None,
            q25_rub=summary.q25_rub if summary else None,
            q75_rub=summary.q75_rub if summary else None,
            snapshot_id=row.id,
            now=now,
        )
        db.commit()
        db.refresh(row)
        return _snapshot_dict(row)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def record_market_error(
    cache_key: str,
    query: IphoneMarketQuery,
    error: str,
    *,
    region: str,
    retry_after_seconds: int,
) -> None:
    db = SessionLocal()
    try:
        row = (
            db.query(AvitoMarketSnapshot)
            .filter(AvitoMarketSnapshot.cache_key == cache_key)
            .first()
        )
        if row is None:
            row = AvitoMarketSnapshot(
                cache_key=cache_key,
                model=query.model,
                memory_gb=query.memory_gb,
                region=region,
                status="error",
                fetched_at=None,
                expires_at=None,
            )
            db.add(row)
        now = _utcnow()
        row.last_error_at = now
        row.last_error = error[:1000]
        row.retry_after = now + timedelta(seconds=retry_after_seconds)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def record_live_request(cache_key: str, *, source: str = "manual") -> None:
    db = SessionLocal()
    try:
        db.add(
            AvitoMarketRequestLog(
                requested_at=_utcnow(),
                cache_key=(cache_key or "")[:160],
                source=(source or "manual")[:24],
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def count_live_requests_since(since: datetime) -> int:
    db = SessionLocal()
    try:
        return (
            db.query(AvitoMarketRequestLog)
            .filter(AvitoMarketRequestLog.requested_at >= since)
            .count()
        )
    finally:
        db.close()


def get_last_live_request_at() -> Optional[datetime]:
    db = SessionLocal()
    try:
        value = (
            db.query(AvitoMarketRequestLog.requested_at)
            .order_by(AvitoMarketRequestLog.requested_at.desc())
            .limit(1)
            .scalar()
        )
        return value
    finally:
        db.close()


def list_success_snapshot_configs() -> list[dict[str, Any]]:
    """Последний успешный снимок на каждую пару модель/память."""
    db = SessionLocal()
    try:
        rows = (
            db.query(AvitoMarketSnapshot)
            .filter(
                AvitoMarketSnapshot.status == "success",
                AvitoMarketSnapshot.fetched_at.isnot(None),
            )
            .order_by(AvitoMarketSnapshot.fetched_at.desc())
            .all()
        )
        seen: set[tuple[str, int]] = set()
        result: list[dict[str, Any]] = []
        for row in rows:
            key = (str(row.model), int(row.memory_gb))
            if key in seen:
                continue
            seen.add(key)
            result.append(_snapshot_dict(row))
        return result
    finally:
        db.close()
