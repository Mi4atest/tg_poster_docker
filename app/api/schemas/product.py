from pydantic import BaseModel, Field
from typing import Literal, Optional
from datetime import datetime


class PricePlatformSync(BaseModel):
    """Результат синхронизации цены на одной платформе (ответ PUT .../price)."""

    status: Literal["ok", "skipped", "error", "pending"] = "skipped"
    detail: Optional[str] = None


class PriceSyncReport(BaseModel):
    vk: PricePlatformSync = Field(default_factory=lambda: PricePlatformSync(status="skipped"))
    avito: PricePlatformSync = Field(default_factory=lambda: PricePlatformSync(status="skipped"))
    database: PricePlatformSync = Field(default_factory=lambda: PricePlatformSync(status="ok"))


class ProductBase(BaseModel):
    name: str
    price: Optional[str] = None
    category_id: Optional[int] = None
    category_name: Optional[str] = None
    collection_id: Optional[int] = None
    collection_name: Optional[str] = None
    status: str = "active"


class ProductCreate(ProductBase):
    post_id: str


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    price: Optional[str] = None
    category_id: Optional[int] = None
    category_name: Optional[str] = None
    collection_id: Optional[int] = None
    collection_name: Optional[str] = None
    status: Optional[str] = None


class ProductStatusUpdate(BaseModel):
    status: str  # "active", "unavailable", "deleted"
    sync_platforms: bool = True


class ProductTelegramLinkUpdate(BaseModel):
    telegram_link: str  # Ссылка на пост в Telegram (например https://t.me/AppleShop43/7547)


class ProductAvailabilityUpdate(BaseModel):
    availability_status: Optional[str] = None  # "available", "on_order"


class ProductAvitoLinkUpdate(BaseModel):
    """Привязка объявления Авито: URL и/или числовой id (парсится на сервере)."""
    avito_link_or_id: str


class Product(ProductBase):
    id: int
    post_id: str
    custom_button_id: Optional[int] = None
    vk_product_id: Optional[int] = None
    vk_product_link: Optional[str] = None
    vk_post_id: Optional[str] = None  # ID поста в ленте VK (из связанного поста)
    vk_post_link: Optional[str] = None  # Ссылка на пост в ленте VK (из связанного поста)
    telegram_link: Optional[str] = None
    max_link: Optional[str] = None
    max_share_url: Optional[str] = None
    instagram_link: Optional[str] = None
    instagram_media_id: Optional[str] = None
    avito_item_id: Optional[str] = None
    avito_url: Optional[str] = None
    # Устарело: ошибки Авито см. в ProductPriceUpdateResponse.price_sync
    avito_price_sync_note: Optional[str] = None
    payment_method: Optional[str] = None
    final_price: Optional[str] = None
    archive_kind: Optional[str] = None  # sale | transfer; NULL = продажа
    availability_status: Optional[str] = None
    channel_message_id: Optional[int] = None
    availability_message_ids: Optional[str] = None  # JSON-массив ID сообщений в канале
    created_at: datetime
    updated_at: datetime
    archived_at: Optional[datetime] = None
    published_at: Optional[datetime] = None  # обогащение: ранняя дата публикации из поста/товара
    price_changed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ProductList(BaseModel):
    items: list[Product]
    total: int


class ProductPriceUpdateResponse(BaseModel):
    """Ответ обновления цены: товар + статусы внешних систем."""

    product: Product
    price_sync: PriceSyncReport


class ProductStatusUpdateResponse(BaseModel):
    """Ответ смены статуса товара (в т.ч. «недоступен»): товар + статусы площадок."""

    product: Product
    status_sync: PriceSyncReport


