from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime, timezone
import os
import json

from app.db.database import get_db
from app.api.models.post import Post, PublicationLog
from app.api.schemas.post import PostCreate, Post as PostSchema, PostList
from app.config.settings import MEDIA_DIR, MEDIA_STRUCTURE

router = APIRouter()

def generate_post_name(text: str, max_length: int = 50) -> str:
    """Generate a post name from the first words of the text."""
    words = text.split()
    name = " ".join(words[:5])  # Take first 5 words
    if len(name) > max_length:
        name = name[:max_length] + "..."
    return name

def create_storage_path(post_name: str) -> str:
    """Create a storage path for the post based on current date and post name."""
    now = datetime.now()
    path = MEDIA_STRUCTURE.format(
        year=now.strftime("%Y"),
        month=now.strftime("%m"),
        day=now.strftime("%d"),
        post_name=post_name.replace(" ", "_").replace("/", "_")
    )
    full_path = MEDIA_DIR / path
    os.makedirs(full_path, exist_ok=True)
    return path

@router.post("/", response_model=PostSchema, status_code=status.HTTP_201_CREATED)
def create_post(post_data: PostCreate, db: Session = Depends(get_db)):
    """Create a new post."""
    # Generate post name from text
    post_name = generate_post_name(post_data.text)

    # Create storage path
    storage_path = create_storage_path(post_name)

    # Ensure photos and videos are lists
    photos = post_data.photos if isinstance(post_data.photos, list) else []
    videos = post_data.videos if isinstance(post_data.videos, list) else []

    # Create post object
    db_post = Post(
        text=post_data.text,
        photos=photos,
        videos=videos,
        name=post_name,
        storage_path=storage_path
    )

    # Save post to database
    db.add(db_post)
    db.commit()
    db.refresh(db_post)

    # Save post text to file
    post_dir = MEDIA_DIR / storage_path
    with open(post_dir / "text.txt", "w", encoding="utf-8") as f:
        f.write(post_data.text)

    # Save media references to file
    with open(post_dir / "media.json", "w", encoding="utf-8") as f:
        json.dump({
            "photos": photos,
            "videos": videos
        }, f, ensure_ascii=False, indent=2)

    return db_post

@router.get("/", response_model=PostList)
def get_posts(skip: int = 0, limit: int = 100, search: str = None, db: Session = Depends(get_db)):
    """Get all posts with optional search by text or date."""
    from sqlalchemy import or_, extract, func
    import re

    query = db.query(Post)

    # If search parameter is provided, filter posts
    if search:
        # Check if search is a date pattern
        is_date_search = False
        year = None
        month = None
        day = None

        # Try to parse different date formats

        # Format: YYYY (year only)
        if re.match(r'^\d{4}$', search):
            year_value = int(search)
            # Проверяем, что год находится в разумных пределах (1900-2100)
            if 1900 <= year_value <= 2100:
                year = year_value
                is_date_search = True
            else:
                # Если год за пределами разумного диапазона, ищем как текст
                is_date_search = False

        # Format: MMYY or MM.YY (month and 2-digit year)
        elif re.match(r'^\d{2}(\.|\/|-)?\d{2}$', search):
            # Extract month and year
            if '.' in search or '/' in search or '-' in search:
                parts = re.split(r'[./-]', search)
                month = int(parts[0])
                year = int(parts[1])
                if year < 100:  # Convert 2-digit year to 4-digit
                    year += 2000
            else:
                month = int(search[:2])
                year = int(search[2:]) + 2000
            is_date_search = True

        # Format: DDMMYY or DD.MM.YY (day, month and 2-digit year)
        elif re.match(r'^\d{2}(\.|\/|-)?\d{2}(\.|\/|-)?\d{2}$', search):
            # Extract day, month and year
            if '.' in search or '/' in search or '-' in search:
                parts = re.split(r'[./-]', search)
                day = int(parts[0])
                month = int(parts[1])
                year = int(parts[2])
                if year < 100:  # Convert 2-digit year to 4-digit
                    year += 2000
            else:
                day = int(search[:2])
                month = int(search[2:4])
                year = int(search[4:]) + 2000
            is_date_search = True

        # Format: YYYYMM or YYYY.MM (year and month)
        elif re.match(r'^\d{4}(\.|\/|-)?\d{2}$', search):
            # Extract year and month
            if '.' in search or '/' in search or '-' in search:
                parts = re.split(r'[./-]', search)
                year = int(parts[0])
                month = int(parts[1])
            else:
                year = int(search[:4])
                month = int(search[4:])
            is_date_search = True

        # Format: YYYYMMDD or YYYY.MM.DD (full date)
        elif re.match(r'^\d{4}(\.|\/|-)?\d{2}(\.|\/|-)?\d{2}$', search):
            # Extract year, month and day
            if '.' in search or '/' in search or '-' in search:
                parts = re.split(r'[./-]', search)
                year = int(parts[0])
                month = int(parts[1])
                day = int(parts[2])
            else:
                year = int(search[:4])
                month = int(search[4:6])
                day = int(search[6:])
            is_date_search = True

        # Всегда выполняем поиск по тексту
        search_term = f"%{search}%"
        text_query = query.filter(Post.text.ilike(search_term))

        # Отладочный вывод
        print(f"Searching for text: '{search}' with pattern: '{search_term}'")
        # Выведем все посты и их тексты для отладки
        all_posts = db.query(Post).all()
        for post in all_posts:
            if search in post.text:
                print(f"Found match in post {post.id}: '{post.text[:100]}...'")
            else:
                print(f"No match in post {post.id}: '{post.text[:50]}...'")
        print(f"Total posts: {len(all_posts)}")

        if is_date_search:
            # Если это похоже на дату, также ищем по дате
            date_query = db.query(Post)
            date_filters = []

            if year:
                date_filters.append(extract('year', Post.created_at) == year)

            if month:
                date_filters.append(extract('month', Post.created_at) == month)

            if day:
                date_filters.append(extract('day', Post.created_at) == day)

            # Apply date filters
            for date_filter in date_filters:
                date_query = date_query.filter(date_filter)

            # Объединяем результаты поиска по тексту и по дате
            query = text_query.union(date_query)
        else:
            # Только поиск по тексту
            query = text_query

    # Order by creation date and apply pagination
    posts = query.order_by(Post.created_at.desc()).offset(skip).limit(limit).all()
    return {"posts": posts}

@router.get("/{post_id}", response_model=PostSchema)
def get_post(post_id: str, db: Session = Depends(get_db)):
    """Get a specific post by ID."""
    post = db.query(Post).filter(Post.id == post_id).first()
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    return post

@router.delete("/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_post(post_id: str, db: Session = Depends(get_db)):
    """Delete a post."""
    post = db.query(Post).filter(Post.id == post_id).first()
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")

    # Delete post from database
    db.delete(post)
    db.commit()

    # Delete post files
    post_dir = MEDIA_DIR / post.storage_path
    if os.path.exists(post_dir):
        import shutil
        shutil.rmtree(post_dir)

    return None

@router.post("/{post_id}", response_model=PostSchema)
async def update_post(post_id: str, data: dict, db: Session = Depends(get_db)):
    """Update a post."""
    # Проверяем, что это запрос на обновление
    if data.get("_method") != "update":
        raise HTTPException(status_code=400, detail="Invalid request method")

    # Получаем пост из базы данных
    post = db.query(Post).filter(Post.id == post_id).first()
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")

    # Обновляем поля поста
    if "text" in data:
        post.text = data["text"]
        # Обновляем имя поста на основе нового текста
        post.name = generate_post_name(data["text"])

        # Обновляем текстовый файл
        post_dir = MEDIA_DIR / post.storage_path
        with open(post_dir / "text.txt", "w", encoding="utf-8") as f:
            f.write(data["text"])

    if "photos" in data:
        post.photos = data["photos"]

    if "videos" in data:
        post.videos = data["videos"]

    # Обновляем время изменения
    post.updated_at = datetime.now(timezone.utc)

    # Сохраняем изменения в базе данных
    db.commit()
    db.refresh(post)

    # Обновляем файл с медиа
    post_dir = MEDIA_DIR / post.storage_path
    with open(post_dir / "media.json", "w", encoding="utf-8") as f:
        json.dump({
            "photos": post.photos,
            "videos": post.videos
        }, f, ensure_ascii=False, indent=2)

    return post

@router.post("/{post_id}/publish/{platform}", response_model=PostSchema)
async def publish_post(post_id: str, platform: str, db: Session = Depends(get_db)):
    """Publish a post to a specific platform."""
    post = db.query(Post).filter(Post.id == post_id).first()
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")

    if platform not in ["vk", "telegram", "instagram"]:
        raise HTTPException(status_code=400, detail="Invalid platform")

    # Call the appropriate worker to publish the post
    success = False
    try:
        if platform == "vk":
            from app.workers.vk.publisher import publish_post_to_vk
            success = await publish_post_to_vk(post_id)
        elif platform == "telegram":
            from app.workers.telegram.publisher import publish_post_to_telegram
            success = await publish_post_to_telegram(post_id)
        elif platform == "instagram":
            from app.workers.instagram.publisher import publish_post_to_instagram
            success = await publish_post_to_instagram(post_id)

        if not success:
            # If the worker failed, add an error log
            log = PublicationLog(
                post_id=post.id,
                platform=platform,
                status="error",
                message=f"Failed to publish to {platform}"
            )
            db.add(log)
            db.commit()

            raise HTTPException(status_code=500, detail=f"Failed to publish to {platform}")
    except Exception as e:
        # If an exception occurred, add an error log
        log = PublicationLog(
            post_id=post.id,
            platform=platform,
            status="error",
            message=str(e)
        )
        db.add(log)
        db.commit()

        raise HTTPException(status_code=500, detail=str(e))

    # Refresh the post to get the updated status
    db.refresh(post)
    return post
