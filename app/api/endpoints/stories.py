from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime, timezone

from app.db.database import get_db
from app.api.models.post import Post
from app.api.models.story import Story, StoryPublicationLog
from app.api.schemas.story import StoryCreate, Story as StorySchema, StoryList
from app.utils.text_extractor import extract_model_and_price

router = APIRouter()

@router.post("/{post_id}/platform/{platform}", response_model=StorySchema, status_code=status.HTTP_201_CREATED)
def create_story(post_id: str, platform: str, db: Session = Depends(get_db)):
    """Create a new story for a post."""
    # Check if platform is valid
    if platform not in ["vk", "telegram", "instagram"]:
        raise HTTPException(status_code=400, detail="Invalid platform")
    
    # Get post from database
    post = db.query(Post).filter(Post.id == post_id).first()
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    
    # Check if story already exists for this post and platform
    existing_story = db.query(Story).filter(
        Story.post_id == post_id,
        Story.platform == platform
    ).first()
    
    if existing_story:
        return existing_story
    
    # Extract model name and price from post text
    model_name, price = extract_model_and_price(post.text)
    
    # Get first photo as media file
    media_file_id = post.photos[0] if post.photos else None
    
    # Create post link based on platform
    post_link = None
    if platform == "vk" and post.is_published_vk:
        # VK post link would be set after publishing
        pass
    elif platform == "telegram" and post.is_published_telegram:
        # Telegram post link would be set after publishing
        pass
    elif platform == "instagram" and post.is_published_instagram:
        # Instagram post link would be set after publishing
        pass
    
    # Create story object
    db_story = Story(
        post_id=post_id,
        platform=platform,
        model_name=model_name,
        price=price,
        media_file_id=media_file_id,
        post_link=post_link
    )
    
    # Save story to database
    db.add(db_story)
    db.commit()
    db.refresh(db_story)
    
    return db_story

@router.get("/", response_model=StoryList)
def get_stories(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """Get all stories."""
    stories = db.query(Story).offset(skip).limit(limit).all()
    return {"stories": stories}

@router.get("/{story_id}", response_model=StorySchema)
def get_story(story_id: str, db: Session = Depends(get_db)):
    """Get a specific story by ID."""
    story = db.query(Story).filter(Story.id == story_id).first()
    if story is None:
        raise HTTPException(status_code=404, detail="Story not found")
    return story

@router.post("/{story_id}/publish", response_model=StorySchema)
async def publish_story(story_id: str, db: Session = Depends(get_db)):
    """Publish a story."""
    story = db.query(Story).filter(Story.id == story_id).first()
    if story is None:
        raise HTTPException(status_code=404, detail="Story not found")
    
    # Check if already published
    if story.is_published:
        return story
    
    # Call the appropriate worker to publish the story
    success = False
    try:
        if story.platform == "vk":
            from app.workers.vk.story_publisher import publish_story_to_vk
            success = await publish_story_to_vk(story_id)
        elif story.platform == "telegram":
            from app.workers.telegram.story_publisher import publish_story_to_telegram
            success = await publish_story_to_telegram(story_id)
        elif story.platform == "instagram":
            from app.workers.instagram.story_publisher import publish_story_to_instagram
            success = await publish_story_to_instagram(story_id)
        
        if not success:
            # If the worker failed, add an error log
            log = StoryPublicationLog(
                story_id=story.id,
                status="error",
                message=f"Failed to publish story to {story.platform}"
            )
            db.add(log)
            db.commit()
            
            raise HTTPException(status_code=500, detail=f"Failed to publish story to {story.platform}")
    except Exception as e:
        # If an exception occurred, add an error log
        log = StoryPublicationLog(
            story_id=story.id,
            status="error",
            message=str(e)
        )
        db.add(log)
        db.commit()
        
        raise HTTPException(status_code=500, detail=str(e))
    
    # Refresh the story to get the updated status
    db.refresh(story)
    return story
