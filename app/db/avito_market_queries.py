"""Синхронные DB-операции для кэша рыночной оценки Avito."""
from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

from app.api.models.avito_market_snapshot import AvitoMarketSnapshot
from app.db.database import SessionLocal
from app.utils.iphone_market_query import IphoneMarketQuery
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
        "private_summary": row.private_summary,
        "business_summary": row.business_summary,
        "listing_audit": row.listing_audit or [],
        "fetched_at": row.fetched_at,
        "expires_at": row.expires_at,
        "last_error_at": row.last_error_at,
        "last_error": row.last_error,
        "retry_after": row.retry_after,
    }


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


def list_recent_market_snapshots(*, limit: int = 12) -> list[dict[str, Any]]:
    """Последние успешные отчёты из кэша (без нового запроса к Avito)."""
    db = SessionLocal()
    try:
        rows = (
            db.query(AvitoMarketSnapshot)
            .filter(
                AvitoMarketSnapshot.status == "success",
                AvitoMarketSnapshot.fetched_at.isnot(None),
                AvitoMarketSnapshot.median_rub.isnot(None),
            )
            .order_by(AvitoMarketSnapshot.fetched_at.desc())
            .limit(max(1, min(int(limit), 20)))
            .all()
        )
        return [_snapshot_dict(row) for row in rows]
    finally:
        db.close()


def get_active_market_block_until() -> Optional[datetime]:
    """Глобальная пауза после 439: max(retry_after) по error-снимкам с ограничением Avito."""
    db = SessionLocal()
    try:
        now = _utcnow()
        rows = (
            db.query(AvitoMarketSnapshot.retry_after, AvitoMarketSnapshot.last_error)
            .filter(
                AvitoMarketSnapshot.status == "error",
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
) -> dict[str, Any]:
    now = _utcnow()
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
        row.median_rub = summary.median_rub if summary else None
        row.q25_rub = summary.q25_rub if summary else None
        row.q75_rub = summary.q75_rub if summary else None
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
            }
            for item in analysis.accepted_listings[:100]
        ]
        row.fetched_at = now
        row.expires_at = now + timedelta(seconds=ttl_seconds)
        row.last_error_at = None
        row.last_error = None
        row.retry_after = None
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
