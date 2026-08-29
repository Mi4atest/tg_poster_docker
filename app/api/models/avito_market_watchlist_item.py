"""Управляемый список конфигураций для фонового обновления рынка Avito."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)

from app.db.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class AvitoMarketWatchlistItem(Base):
    __tablename__ = "avito_market_watchlist_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model = Column(String(80), nullable=False)
    memory_gb = Column(Integer, nullable=False)
    tier = Column(String(8), nullable=False, default="daily")
    enabled = Column(Boolean, nullable=False, default=True)
    source = Column(String(24), nullable=False, default="manual")
    last_snapshot_id = Column(
        Integer,
        ForeignKey("avito_market_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_attempt_at = Column(DateTime, nullable=True)
    last_refreshed_at = Column(DateTime, nullable=True)
    next_refresh_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("model", "memory_gb", name="uq_avito_market_wl_model_mem"),
        Index("ix_avito_market_wl_due", "enabled", "next_refresh_at"),
    )
