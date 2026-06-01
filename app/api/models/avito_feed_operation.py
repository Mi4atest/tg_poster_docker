"""Операции фида Авито: снятие с публикации (архив) и служебные записи."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text

from app.db.database import Base


class AvitoFeedOperation(Base):
    __tablename__ = "avito_feed_operations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    operation_type = Column(String(32), nullable=False, default="archive")
    product_id = Column(Integer, nullable=True, index=True)
    post_id = Column(String, nullable=True, index=True)
    avito_item_id = Column(BigInteger, nullable=True)
    product_name = Column(String(255), nullable=True)
    status = Column(String(32), nullable=False, default="pending", index=True)
    enqueued_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    failed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
