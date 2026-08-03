"""Публичные схемы витрины (только безопасные поля)."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class PublicProductLinks(BaseModel):
    """Все доступные публичные ссылки на товар (пустые не дублируем в UI — фронт смотрит non-null)."""

    telegram: Optional[str] = None
    vk_market: Optional[str] = None
    vk_post: Optional[str] = None
    max: Optional[str] = None  # лучшая кликабельная (share_url или https max_link)
    max_link: Optional[str] = None
    max_share_url: Optional[str] = None
    instagram: Optional[str] = None
    avito: Optional[str] = None


class PublicProduct(BaseModel):
    id: int
    name: str
    display_label: Optional[str] = None
    price: Optional[str] = None
    collection_name: Optional[str] = None
    kind: Literal["used", "new"]
    status: str
    availability_status: Optional[str] = None
    image_urls: list[str] = Field(default_factory=list)
    video_urls: list[str] = Field(default_factory=list)
    storage_path: Optional[str] = None
    # Плоские поля (совместимость) + полный набор
    telegram_link: Optional[str] = None
    vk_product_id: Optional[int] = None
    vk_product_link: Optional[str] = None
    vk_post_link: Optional[str] = None
    max_link: Optional[str] = None
    max_share_url: Optional[str] = None
    instagram_link: Optional[str] = None
    avito_item_id: Optional[str] = None
    avito_url: Optional[str] = None
    links: PublicProductLinks = Field(default_factory=PublicProductLinks)
    created_at: Optional[datetime] = None


class PublicProductList(BaseModel):
    items: list[PublicProduct]
    total: int
    kind: Literal["used", "new"]
