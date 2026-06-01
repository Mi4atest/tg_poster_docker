from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, JSON, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.db.database import Base

def generate_story_id():
    return str(uuid.uuid4())

class Story(Base):
    __tablename__ = "stories"

    id = Column(String, primary_key=True, default=generate_story_id)
    post_id = Column(String, ForeignKey("posts.id", ondelete="CASCADE"))
    platform = Column(String, nullable=False)  # "vk", "telegram", "instagram"
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Extracted data from post
    model_name = Column(String, nullable=True)
    price = Column(String, nullable=True)
    
    # Media file (usually first photo from post)
    media_file_id = Column(String, nullable=True)
    
    # Link to the original post
    post_link = Column(String, nullable=True)
    
    # Status
    is_published = Column(Boolean, default=False)
    published_at = Column(DateTime, nullable=True)
    
    # Publication logs
    logs = relationship("StoryPublicationLog", back_populates="story", cascade="all, delete-orphan")
    
    # Relationship to post
    post = relationship("Post", backref="stories")

class StoryPublicationLog(Base):
    __tablename__ = "story_publication_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    story_id = Column(String, ForeignKey("stories.id", ondelete="CASCADE"))
    status = Column(String, nullable=False)  # "success", "error"
    message = Column(Text, nullable=True)  # Error message or success details
    timestamp = Column(DateTime, default=datetime.utcnow)

    # Relationship
    story = relationship("Story", back_populates="logs")
