from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
from datetime import datetime

from app.db.database import Base


class ProductPriceHistory(Base):
    __tablename__ = "product_price_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    old_price = Column(String, nullable=True)
    new_price = Column(String, nullable=False)
    changed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    source = Column(String, default="manual", nullable=False)  # publication | manual | bulk

    product = relationship("Product", backref="price_history")

    __table_args__ = (
        Index("ix_product_price_history_product_changed", "product_id", "changed_at"),
    )
