from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import relationship

from app.db.database import Base


class ProductSale(Base):
    """Продажа новой позиции (SKU остаётся в каталоге). Кормит сводку месяца."""

    __tablename__ = "product_sales"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(
        Integer,
        ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
    )
    name = Column(String, nullable=False)
    collection_name = Column(String, nullable=True)
    price = Column(String, nullable=True)
    sold_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    product = relationship("Product", backref="sales")

    __table_args__ = (
        Index("ix_product_sales_sold_at", "sold_at"),
        Index("ix_product_sales_product_id", "product_id"),
    )
