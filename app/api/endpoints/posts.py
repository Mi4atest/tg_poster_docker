from fastapi import APIRouter, Depends, HTTPException, status, Body, Response
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Optional
from datetime import datetime, timezone
import os
import json

from pydantic import BaseModel

from app.db.database import get_db
from app.api.models.post import Post, PublicationLog
from app.api.schemas.post import PostCreate, Post as PostSchema, PostList
from app.config.settings import MEDIA_DIR, MEDIA_STRUCTURE
from app.integrations.avito.errors import AvitoAutoCreateUnavailableError

router = APIRouter()


def _fetch_post_row(db: Session, post_id: str) -> Optional[dict]:
    row = (
        db.execute(text("SELECT * FROM posts WHERE id = :id LIMIT 1"), {"id": post_id})
        .mappings()
        .first()
    )
    return dict(row) if row else None


def _row_as_post_schema(db: Session, row: dict) -> PostSchema:
    data = dict(row)
    if data.get("photos") is None:
        data["photos"] = []
    if data.get("videos") is None:
        data["videos"] = []
    data["logs"] = []
    return PostSchema.model_validate(data)


class PublishPostOptions(BaseModel):
    signature_enabled: Optional[bool] = None

def generate_post_name(text: str, max_length: int = 70) -> str:
    """Generate a post name from the first words of the text with timestamp."""
    from datetime import datetime
    
    # Берем больше слов для информативности
    words = text.split()
    name = " ".join(words[:8])  # Take first 8 words instead of 5
    
    # Добавляем временную метку для уникальности
    timestamp = datetime.now().strftime("%d%m%H%M")
    
    # Обрезаем название, оставляя место для временной метки
    if len(name) > (max_length - 10):
        name = name[:(max_length - 10)] + "..."
    
    # Добавляем временную метку в конец названия
    name = f"{name} [{timestamp}]"
    
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
        storage_path=storage_path,
        avito_draft=post_data.avito_draft if isinstance(post_data.avito_draft, dict) else None,
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

@router.get("/archive/summary")
def get_archive_summary(db: Session = Depends(get_db)):
    """Lightweight aggregate for archive tree: counts per (year, month, day) in UTC.

    Returns only counts grouped by date components — no post text, photos, videos or logs.
    Used by the bot to render the year/month/day navigation without loading all posts.
    """
    from sqlalchemy import extract, func

    rows = (
        db.query(
            extract("year", Post.created_at).label("y"),
            extract("month", Post.created_at).label("m"),
            extract("day", Post.created_at).label("d"),
            func.count(Post.id).label("cnt"),
        )
        .group_by("y", "m", "d")
        .all()
    )
    return {
        "buckets": [
            {"year": int(r.y), "month": int(r.m), "day": int(r.d), "count": int(r.cnt)}
            for r in rows
        ]
    }


@router.get("/archive/day")
def get_archive_day(year: int, month: int, day: int, db: Session = Depends(get_db)):
    """Lightweight list of posts for a specific UTC day (no logs, no full text).

    Returns minimal fields needed to render the day-level archive screen and the
    'today' section of the root archive view: id, name, created_at, photos, videos.
    """
    from sqlalchemy import extract

    rows = (
        db.query(
            Post.id,
            Post.name,
            Post.created_at,
            Post.photos,
            Post.videos,
            Post.published_vk_at,
            Post.published_telegram_at,
            Post.published_instagram_at,
            Post.published_max_at,
        )
        .filter(
            extract("year", Post.created_at) == year,
            extract("month", Post.created_at) == month,
            extract("day", Post.created_at) == day,
        )
        .order_by(Post.created_at.desc())
        .all()
    )

    def _iso(v):
        return v.isoformat() if v else None

    return {
        "posts": [
            {
                "id": r.id,
                "name": r.name,
                "created_at": _iso(r.created_at),
                "photos": r.photos or [],
                "videos": r.videos or [],
                "published_vk_at": _iso(r.published_vk_at),
                "published_telegram_at": _iso(r.published_telegram_at),
                "published_instagram_at": _iso(r.published_instagram_at),
                "published_max_at": _iso(r.published_max_at),
            }
            for r in rows
        ]
    }


def _pending_posts_filter(query):
    """Черновики: не опубликованы в основных сетях и не в очереди публикации."""
    from sqlalchemy import or_

    from app.utils.vk_market_sync import POST_ID_FOR_NEW_PRODUCTS

    return query.filter(
        Post.id != POST_ID_FOR_NEW_PRODUCTS,
        Post.is_published_vk.is_(False),
        Post.is_published_telegram.is_(False),
        Post.is_published_instagram.is_(False),
        Post.is_published_max.is_(False),
        or_(Post.in_queue.is_(False), Post.in_queue.is_(None)),
    )


@router.get("/pending/count")
def get_pending_count(db: Session = Depends(get_db)):
    """Количество черновиков (лёгкий запрос для главного меню)."""
    count = _pending_posts_filter(db.query(Post)).count()
    return {"count": count}


@router.get("/pending")
def get_pending_posts(db: Session = Depends(get_db)):
    """Список черновиков без загрузки всей таблицы posts."""
    rows = (
        _pending_posts_filter(db.query(Post))
        .order_by(Post.created_at.desc())
        .all()
    )
    return {
        "posts": [
            {
                "id": r.id,
                "name": r.name,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "photos": r.photos or [],
                "videos": r.videos or [],
            }
            for r in rows
        ]
    }


@router.get("/", response_model=PostList)
def get_posts(skip: int = 0, limit: int = 10000, search: str = None, db: Session = Depends(get_db)):
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
    row = _fetch_post_row(db, post_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Post not found")
    return _row_as_post_schema(db, row)

@router.delete("/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_post(post_id: str, db: Session = Depends(get_db)):
    """Delete a post."""
    from app.utils.vk_market_sync import POST_ID_FOR_NEW_PRODUCTS

    if post_id == POST_ID_FOR_NEW_PRODUCTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Служебный пост синхронизации новых товаров нельзя удалить как черновик.",
        )

    post = db.query(Post).filter(Post.id == post_id).first()
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    storage_path = post.storage_path

    from app.api.models.product import Product

    db.query(Product).filter(Product.post_id == post_id).delete(synchronize_session=False)
    db.delete(post)
    db.commit()

    # Delete post files
    post_dir = MEDIA_DIR / storage_path
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

    if "photos" in data:
        post.photos = data["photos"]

    if "videos" in data:
        post.videos = data["videos"]

    if "avito_draft" in data and isinstance(data["avito_draft"], dict):
        post.avito_draft = data["avito_draft"]

    # Обновляем время изменения
    post.updated_at = datetime.now(timezone.utc)

    # Сохраняем изменения в базе данных
    db.commit()
    db.refresh(post)

    # Проверяем, есть ли у поста путь хранения
    if post.storage_path:
        # Проверяем существование директории и создаем её при необходимости
        post_dir = MEDIA_DIR / post.storage_path
        os.makedirs(post_dir, exist_ok=True)
        
        # Обновляем текстовый файл
        with open(post_dir / "text.txt", "w", encoding="utf-8") as f:
            f.write(post.text)
        
        # Обновляем файл с медиа
        with open(post_dir / "media.json", "w", encoding="utf-8") as f:
            json.dump({
                "photos": post.photos,
                "videos": post.videos
            }, f, ensure_ascii=False, indent=2)
    else:
        # Если у поста нет пути хранения, создаем новый
        year = datetime.now().strftime("%Y")
        month = datetime.now().strftime("%m")
        day = datetime.now().strftime("%d")
        
        # Создаем директорию для хранения файлов поста
        post_dir = MEDIA_DIR / year / month / day / post.name
        os.makedirs(post_dir, exist_ok=True)
        
        # Обновляем путь хранения в базе данных
        post.storage_path = f"{year}/{month}/{day}/{post.name}"
        db.commit()
        
        # Сохраняем текст и медиа
        with open(post_dir / "text.txt", "w", encoding="utf-8") as f:
            f.write(post.text)
        
        with open(post_dir / "media.json", "w", encoding="utf-8") as f:
            json.dump({
                "photos": post.photos,
                "videos": post.videos
            }, f, ensure_ascii=False, indent=2)

    return post

@router.post("/{post_id}/publish/{platform}", response_model=PostSchema)
async def publish_post(
    post_id: str,
    platform: str,
    options: Optional[PublishPostOptions] = Body(default=None),
    db: Session = Depends(get_db),
):
    """Publish a post to a specific platform."""
    post = db.query(Post).filter(Post.id == post_id).first()
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")

    if platform not in ["vk", "telegram", "instagram", "max", "avito"]:
        raise HTTPException(status_code=400, detail="Invalid platform")

    # Call the appropriate worker to publish the post
    success = False
    signature_enabled = True  # По умолчанию включено
    if options and options.signature_enabled is not None:
        signature_enabled = bool(options.signature_enabled)

    try:
        if platform == "vk":
            from app.workers.vk.publisher import publish_post_to_vk
            success = await publish_post_to_vk(post_id, signature_enabled=signature_enabled)
        elif platform == "telegram":
            from app.workers.telegram.publisher import publish_post_to_telegram
            success = await publish_post_to_telegram(post_id, signature_enabled=signature_enabled)
        elif platform == "instagram":
            from app.workers.instagram.publisher import publish_post_to_instagram
            success = await publish_post_to_instagram(post_id)
        elif platform == "max":
            from app.workers.max.publisher import publish_post_to_max
            success = await publish_post_to_max(post_id, signature_enabled=signature_enabled)
        elif platform == "avito":
            from app.workers.avito.publisher import publish_post_to_avito
            success = await publish_post_to_avito(post_id, signature_enabled=signature_enabled)

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
    except AvitoAutoCreateUnavailableError as e:
        log = PublicationLog(
            post_id=post.id,
            platform=platform,
            status="error",
            message=e.user_message[:2000],
        )
        db.add(log)
        db.commit()
        raise HTTPException(status_code=422, detail=e.user_message)
    except HTTPException:
        raise
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

@router.get("/export/telegram")
def export_telegram_posts(db: Session = Depends(get_db), format: str = "txt"):
    """Export published Telegram posts with links.
    
    Args:
        format: Export format - 'txt' or 'csv' (default: 'txt')
    
    Returns:
        Text file with posts in format: {post.name} - {post.telegram_link}
    """
    # Get only published posts with Telegram links
    posts = db.query(Post).filter(
        Post.is_published_telegram == True,
        Post.telegram_link.isnot(None),
        Post.telegram_link != ""
    ).order_by(Post.published_telegram_at.desc()).all()
    
    if format.lower() == "csv":
        # CSV format
        lines = ["Post Name,Telegram Link"]
        for post in posts:
            name = (post.name or "Без названия").replace(",", ";")  # Replace commas to avoid CSV issues
            link = post.telegram_link or ""
            lines.append(f"{name},{link}")
        
        content = "\n".join(lines)
        return Response(
            content=content,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=telegram_posts_export.csv"}
        )
    else:
        # TXT format (default)
        lines = []
        for post in posts:
            name = post.name or "Без названия"
            link = post.telegram_link or ""
            if link:
                lines.append(f"{name} - {link}")
        
        content = "\n".join(lines)
        return Response(
            content=content,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=telegram_posts_export.txt"}
        )
