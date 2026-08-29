"""Последний успешный снимок рыночной оценки Avito."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Integer, JSON, String, Text

from app.db.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class AvitoMarketSnapshot(Base):
    __tablename__ = "avito_market_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cache_key = Column(String(160), nullable=False, unique=True, index=True)
    model = Column(String(80), nullable=False)
    memory_gb = Column(Integer, nullable=False)
    region = Column(String(80), nullable=False, default="Киров")
    status = Column(String(24), nullable=False, default="success")
    total_count = Column(Integer, nullable=False, default=0)
    matched_count = Column(Integer, nullable=False, default=0)
    used_count = Column(Integer, nullable=False, default=0)
    outlier_count = Column(Integer, nullable=False, default=0)
    median_rub = Column(Integer, nullable=True)
    q25_rub = Column(Integer, nullable=True)
    q75_rub = Column(Integer, nullable=True)
    private_summary = Column(JSON, nullable=True)
    business_summary = Column(JSON, nullable=True)
    listing_audit = Column(JSON, nullable=True)
    fetched_at = Column(DateTime, nullable=True, default=_utcnow)
    expires_at = Column(DateTime, nullable=True)
    last_error_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    retry_after = Column(DateTime, nullable=True)
