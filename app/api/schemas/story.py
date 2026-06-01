from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

class StoryBase(BaseModel):
    post_id: str
    platform: str

class StoryCreate(StoryBase):
    model_name: Optional[str] = None
    price: Optional[str] = None
    media_file_id: Optional[str] = None
    post_link: Optional[str] = None

class StoryPublicationLogBase(BaseModel):
    status: str
    message: Optional[str] = None
    timestamp: datetime

class StoryPublicationLog(StoryPublicationLogBase):
    id: int
    story_id: str

    class Config:
        orm_mode = True

class Story(StoryBase):
    id: str
    created_at: datetime
    model_name: Optional[str] = None
    price: Optional[str] = None
    media_file_id: Optional[str] = None
    post_link: Optional[str] = None
    is_published: bool
    published_at: Optional[datetime] = None
    logs: List[StoryPublicationLog] = []

    class Config:
        orm_mode = True

class StoryList(BaseModel):
    stories: List[Story]

    class Config:
        orm_mode = True
