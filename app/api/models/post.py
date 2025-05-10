from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, JSON, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.db.database import Base

def generate_post_id():
    return str(uuid.uuid4())

class Post(Base):
    __tablename__ = "posts"

    id = Column(String, primary_key=True, default=generate_post_id)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Media files stored as Telegram file_ids
    photos = Column(JSON, default=list)  # List of photo file_ids
    videos = Column(JSON, default=list)  # List of video file_ids

    # Post status
    is_published_vk = Column(Boolean, default=False)
    is_published_telegram = Column(Boolean, default=False)
    is_published_instagram = Column(Boolean, default=False)

    # Publication timestamps
    published_vk_at = Column(DateTime, nullable=True)
    published_telegram_at = Column(DateTime, nullable=True)
    published_instagram_at = Column(DateTime, nullable=True)

    # Storage path (relative to media directory)
    storage_path = Column(String, nullable=True)

    # Post name (derived from first words of text)
    name = Column(String, nullable=True)

    # Publication logs
    logs = relationship("PublicationLog", back_populates="post", cascade="all, delete-orphan")

class PublicationLog(Base):
    __tablename__ = "publication_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(String, ForeignKey("posts.id", ondelete="CASCADE"))
    platform = Column(String, nullable=False)  # "vk", "telegram", etc.
    status = Column(String, nullable=False)  # "success", "error"
    message = Column(Text, nullable=True)  # Error message or success details
    timestamp = Column(DateTime, default=datetime.utcnow)

    # Relationship
    post = relationship("Post", back_populates="logs")
