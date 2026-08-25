from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
import os

from app.db.database import get_db
from app.api.models.post import Post
from app.api.models.story import Story, StoryPublicationLog
from app.api.schemas.story import Story as StorySchema, StoryList
from app.utils.text_extractor import extract_model_and_price

router = APIRouter()


@router.post("/{post_id}/platform/{platform}", response_model=StorySchema, status_code=status.HTTP_201_CREATED)
def create_story(post_id: str, platform: str, db: Session = Depends(get_db)):
    """Create a new story for a post."""
    if platform not in ["vk", "telegram", "instagram"]:
        raise HTTPException(status_code=400, detail="Invalid platform")

    post = db.query(Post).filter(Post.id == post_id).first()
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")

    existing_story = db.query(Story).filter(
        Story.post_id == post_id,
        Story.platform == platform,
    ).first()

    if existing_story:
        return existing_story

    model_name, price = extract_model_and_price(post.text)
    media_file_id = post.photos[0] if post.photos else None

    db_story = Story(
        post_id=post_id,
        platform=platform,
        model_name=model_name,
        price=price,
        media_file_id=media_file_id,
        post_link=None,
    )
    db.add(db_story)
    db.commit()
    db.refresh(db_story)
    return db_story


@router.get("/", response_model=StoryList)
def get_stories(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    stories = db.query(Story).offset(skip).limit(limit).all()
    return {"stories": stories}


@router.get("/{story_id}", response_model=StorySchema)
def get_story(story_id: str, db: Session = Depends(get_db)):
    story = db.query(Story).filter(Story.id == story_id).first()
    if story is None:
        raise HTTPException(status_code=404, detail="Story not found")
    return story


@router.post("/{post_id}/preview/vk")
async def preview_vk_story(post_id: str, db: Session = Depends(get_db)):
    """Собрать превью-кадр VK-сторис без публикации. JPEG + файл в media/story_previews/."""
    post = db.query(Post).filter(Post.id == post_id).first()
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    if not post.is_published_vk or not post.vk_post_id:
        raise HTTPException(status_code=400, detail="Post must be published to VK first")
    if not post.photos:
        raise HTTPException(status_code=400, detail="Post has no photos")

    from app.workers.vk.story_publisher import compose_vk_story_preview

    path = await compose_vk_story_preview(post_id)
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=500, detail="Failed to compose story preview")

    return FileResponse(
        path,
        media_type="image/jpeg",
        filename=f"vk_story_preview_{post_id}.jpg",
    )


@router.post("/{story_id}/publish", response_model=StorySchema)
async def publish_story(story_id: str, db: Session = Depends(get_db)):
    """Publish a story. Для VK и Instagram повторная публикация разрешена."""
    story = db.query(Story).filter(Story.id == story_id).first()
    if story is None:
        raise HTTPException(status_code=404, detail="Story not found")

    # VK/IG сторис эфемерны — не возвращаем early, можно переопубликовать
    if story.is_published and story.platform not in ("vk", "instagram"):
        return story

    if story.platform in ("vk", "instagram") and story.is_published:
        story.is_published = False
        db.commit()
        db.refresh(story)

    post = db.query(Post).filter(Post.id == story.post_id).first() if story.post_id else None
    if story.platform == "vk":
        if not post or not post.is_published_vk or not post.vk_post_id:
            raise HTTPException(
                status_code=400,
                detail="VK story requires the post to be published on the community wall first",
            )
    elif story.platform == "instagram":
        from app.workers.instagram.story_publisher import ig_story_block_reason

        blocked = ig_story_block_reason(post)
        if blocked:
            raise HTTPException(status_code=400, detail=blocked)

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
            fail_detail = f"Failed to publish story to {story.platform}"
            if story.platform == "instagram":
                from app.workers.instagram.story_publisher import last_instagram_story_error

                fail_detail = last_instagram_story_error() or fail_detail
            log = StoryPublicationLog(
                story_id=story.id,
                status="error",
                message=fail_detail,
            )
            db.add(log)
            db.commit()
            raise HTTPException(status_code=500, detail=fail_detail)
    except HTTPException:
        raise
    except Exception as e:
        log = StoryPublicationLog(
            story_id=story.id,
            status="error",
            message=str(e),
        )
        db.add(log)
        db.commit()
        raise HTTPException(status_code=500, detail=str(e))

    db.refresh(story)
    return story
