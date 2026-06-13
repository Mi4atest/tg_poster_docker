from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
from datetime import datetime

from app.db.database import Base


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(String, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    
    # VK Market fields
    vk_product_id = Column(Integer, nullable=True)  # ID товара в ВК
    vk_product_link = Column(String, nullable=True)  # Ссылка на товар в ВК
    telegram_link = Column(String, nullable=True)  # Ссылка на пост в Telegram
    max_link = Column(String, nullable=True)  # Ссылка на пост в Max
    max_share_url = Column(String, nullable=True)  # Публичная ссылка max.ru/c/... для открытия в клиенте
    instagram_link = Column(String, nullable=True)  # Ссылка на пост в Instagram
    instagram_media_id = Column(String, nullable=True)  # ID медиа в Instagram Graph API
    avito_item_id = Column(String, nullable=True)  # ID объявления на Авито
    avito_url = Column(String, nullable=True)  # Публичная ссылка на объявление

    # Product information
    name = Column(String, nullable=False)  # Название товара
    price = Column(String, nullable=True)  # Цена (строка, т.к. может содержать валюту)
    payment_method = Column(String, nullable=True)  # Способ оплаты при архивации (cash, card, credit)
    final_price = Column(String, nullable=True)  # Финальная цена с учетом способа оплаты
    
    # Category
    category_id = Column(Integer, nullable=True)  # ID категории ВК
    category_name = Column(String, nullable=True)  # Название категории
    
    # Collection (подборка)
    collection_id = Column(Integer, nullable=True)  # ID подборки ВК
    collection_name = Column(String, nullable=True)  # Название подборки

    # Пользовательская ветка меню «Список новых» (конструктор)
    custom_button_id = Column(
        Integer,
        ForeignKey("new_menu_buttons.id", ondelete="SET NULL"),
        nullable=True,
    )
    
    # Status: "active", "unavailable", "deleted"
    status = Column(String, default="active", nullable=False)
    
    # Availability for new products: "available" (🟢 в наличии), "on_order" (🔴 на заказ)
    availability_status = Column(String, nullable=True)
    
    # ID сообщения в канале Telegram с актуальным наличием новых товаров (одно сообщение, legacy)
    channel_message_id = Column(Integer, nullable=True)
    # Список ID сообщений в канале (JSON-массив [100, 101, 102]) для длинного списка наличия
    availability_message_ids = Column(String, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    archived_at = Column(DateTime, nullable=True)  # Дата архивации товара
    
    # Relationship
    post = relationship("Post", backref="products", passive_deletes=True)
    
    # Indexes
    __table_args__ = (
        Index('ix_products_post_id', 'post_id'),
        Index('ix_products_vk_product_id', 'vk_product_id'),
        Index('ix_products_status', 'status'),
        Index('ix_products_custom_button_id', 'custom_button_id'),
    )


