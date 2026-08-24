from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

class PostBase(BaseModel):
    text: str

class PostCreate(PostBase):
    photos: List[str] = Field(default_factory=list)
    videos: List[str] = Field(default_factory=list)
    avito_draft: Optional[dict] = None


class PublicationLogBase(BaseModel):
    platform: str
    status: str
    message: Optional[str] = None
    timestamp: datetime

class PublicationLog(PublicationLogBase):
    id: int
    post_id: str

    class Config:
        orm_mode = True

class Post(PostBase):
    id: str
    created_at: datetime
    updated_at: datetime
    photos: List[str]
    videos: List[str]
    is_published_vk: bool
    is_published_telegram: bool
    is_published_instagram: bool
    is_published_max: bool
    is_published_avito: bool = False
    # Успешная публикация сторис ВК (из таблицы stories, не колонка posts)
    is_published_vk_story: bool = False
    published_vk_at: Optional[datetime] = None
    published_telegram_at: Optional[datetime] = None
    published_instagram_at: Optional[datetime] = None
    published_max_at: Optional[datetime] = None
    published_avito_at: Optional[datetime] = None
    storage_path: Optional[str] = None
    name: Optional[str] = None
    telegram_link: Optional[str] = None
    max_link: Optional[str] = None
    max_share_url: Optional[str] = None
    vk_post_id: Optional[str] = None
    vk_post_link: Optional[str] = None
    instagram_link: Optional[str] = None
    instagram_media_id: Optional[str] = None
    avito_item_id: Optional[str] = None
    avito_url: Optional[str] = None
    avito_draft: Optional[dict] = None
    logs: List[PublicationLog] = []

    class Config:
        orm_mode = True

class PostList(BaseModel):
    posts: List[Post]

    class Config:
        orm_mode = True


class PostSearchItem(BaseModel):
    id: str
    name: Optional[str] = None
    text: str
    created_at: Optional[datetime] = None
    photos: List[str] = Field(default_factory=list)
    videos: List[str] = Field(default_factory=list)


class PostSearchList(BaseModel):
    posts: List[PostSearchItem]
