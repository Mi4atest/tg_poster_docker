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
    is_published_max = Column(Boolean, default=False)

    # Publication timestamps
    published_vk_at = Column(DateTime, nullable=True)
    published_telegram_at = Column(DateTime, nullable=True)
    published_instagram_at = Column(DateTime, nullable=True)
    published_max_at = Column(DateTime, nullable=True)
    is_published_avito = Column(Boolean, default=False)
    published_avito_at = Column(DateTime, nullable=True)
    avito_item_id = Column(String, nullable=True)
    avito_url = Column(String, nullable=True)
    avito_draft = Column(JSON, nullable=True)  # черновик атрибутов для фазы B (экран/корпус и т.д.)

    # Storage path (relative to media directory)
    storage_path = Column(String, nullable=True)

    # Post name (derived from first words of text)
    name = Column(String, nullable=True)

    # Telegram post link
    telegram_link = Column(String, nullable=True)
    max_link = Column(String, nullable=True)
    max_share_url = Column(String, nullable=True)  # Публичная ссылка на пост (max.ru/c/...), из ответа API

    # VK post link and ID
    vk_post_id = Column(String, nullable=True)  # ID поста в ВК (owner_id_post_id)
    vk_post_link = Column(String, nullable=True)  # Ссылка на пост в ВК

    # Queue management fields
    in_queue = Column(Boolean, default=False)  # находится ли пост в очереди публикации
    queue_status = Column(String, nullable=True)  # статус в очереди: pending, publishing, paused, completed
    scheduled_at = Column(DateTime, nullable=True)  # запланированное время публикации

    # Publication logs
    logs = relationship("PublicationLog", back_populates="post", cascade="all, delete-orphan")
    queue_items = relationship("PublicationQueue", back_populates="post", cascade="all, delete-orphan")

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


class PublicationQueue(Base):
    __tablename__ = "publication_queue"

    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(String, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    platform = Column(String, nullable=False)  # платформа: "vk", "telegram", "instagram"
    status = Column(String, nullable=False, default="pending")  # статус: "pending", "publishing", "paused", "completed", "failed"
    priority = Column(Integer, default=0)  # приоритет (для внеочередной публикации, чем выше, тем раньше)
    scheduled_at = Column(DateTime, nullable=True)  # запланированное время публикации
    published_at = Column(DateTime, nullable=True)  # время публикации
    error_message = Column(Text, nullable=True)  # сообщение об ошибке
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationship
    post = relationship("Post", back_populates="queue_items")


class AppSettings(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, default=1)
    config = Column(JSON, nullable=False, default=dict)
    encrypted_secrets = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
