from sqlalchemy import Boolean, Column, Integer, String, DateTime, BigInteger
from datetime import datetime

from app.db.database import Base


class NewMenuButton(Base):
    """Пользовательская кнопка в меню «Список новых» (конструктор)."""

    __tablename__ = "new_menu_buttons"

    id = Column(Integer, primary_key=True, autoincrement=True)
    parent_path = Column(String(512), nullable=False, index=True)
    label = Column(String(128), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    is_service = Column(Boolean, nullable=False, default=False, server_default="false")
    created_by_user_id = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
