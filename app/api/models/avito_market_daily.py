"""Дневные точки медианы и вилки Avito (одна строка на модель/память/день)."""
from __future__ import annotations

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)

from app.db.database import Base


class AvitoMarketDaily(Base):
    __tablename__ = "avito_market_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model = Column(String(80), nullable=False)
    memory_gb = Column(Integer, nullable=False)
    region = Column(String(80), nullable=False, default="Россия")
    observed_on = Column(Date, nullable=False)
    median_rub = Column(Integer, nullable=True)
    q25_rub = Column(Integer, nullable=True)
    q75_rub = Column(Integer, nullable=True)
    used_count = Column(Integer, nullable=False, default=0)
    total_count = Column(Integer, nullable=False, default=0)
    quality = Column(String(8), nullable=False, default="thin")
    source = Column(String(24), nullable=False, default="manual")
    snapshot_id = Column(
        Integer,
        ForeignKey("avito_market_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "model",
            "memory_gb",
            "region",
            "observed_on",
            name="uq_avito_market_daily_model_mem_region_day",
        ),
        Index("ix_avito_market_daily_lookup", "model", "memory_gb", "observed_on"),
    )
