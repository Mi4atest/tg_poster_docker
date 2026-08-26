from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from app.db.database import Base


class ShopNote(Base):
    """Напоминалка на главном экране (канцы / ассортимент / сервис)."""

    __tablename__ = "shop_notes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    body = Column(Text, nullable=False)
    category = Column(String(32), nullable=True)  # stationery | assortment | service
    is_done = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    done_at = Column(DateTime, nullable=True)
