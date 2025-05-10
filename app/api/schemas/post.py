from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

class PostBase(BaseModel):
    text: str

class PostCreate(PostBase):
    photos: List[str] = Field(default_factory=list)
    videos: List[str] = Field(default_factory=list)

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
    published_vk_at: Optional[datetime] = None
    published_telegram_at: Optional[datetime] = None
    published_instagram_at: Optional[datetime] = None
    storage_path: Optional[str] = None
    name: Optional[str] = None
    logs: List[PublicationLog] = []

    class Config:
        orm_mode = True

class PostList(BaseModel):
    posts: List[Post]

    class Config:
        orm_mode = True
