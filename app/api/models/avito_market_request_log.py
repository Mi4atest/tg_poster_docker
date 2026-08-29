"""Журнал живых запросов к Avito для скользящего суточного лимита."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Integer, String

from app.db.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class AvitoMarketRequestLog(Base):
    __tablename__ = "avito_market_request_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    requested_at = Column(DateTime, nullable=False, default=_utcnow, index=True)
    cache_key = Column(String(160), nullable=False, default="")
    source = Column(String(24), nullable=False, default="manual")
