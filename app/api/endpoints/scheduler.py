from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel

from app.db.database import get_db
from app.api.models.post import PublicationQueue
from app.scheduler.orchestrator import PublicationOrchestrator

router = APIRouter()

# Глобальный экземпляр оркестратора (будет инициализирован при запуске)
_orchestrator: Optional[PublicationOrchestrator] = None


def get_orchestrator() -> PublicationOrchestrator:
    """Получить экземпляр оркестратора."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = PublicationOrchestrator()
        _orchestrator.start()
    return _orchestrator


class AddToQueueRequest(BaseModel):
    post_id: str
    platforms: Optional[List[str]] = None
    priority: int = 0
    scheduled_at: Optional[datetime] = None


class PauseRequest(BaseModel):
    post_id: Optional[str] = None
    platform: Optional[str] = None
    global_pause: bool = False


class ResumeRequest(BaseModel):
    post_id: Optional[str] = None
    platform: Optional[str] = None
    global_resume: bool = False


@router.post("/queue/add")
async def add_to_queue(request: AddToQueueRequest):
    """Добавить пост в очередь публикации."""
    orchestrator = get_orchestrator()
    success = orchestrator.add_post_to_queue(
        post_id=request.post_id,
        platforms=request.platforms,
        priority=request.priority,
        scheduled_at=request.scheduled_at
    )
    if not success:
        raise HTTPException(status_code=400, detail="Failed to add post to queue")
    return {"success": True, "message": "Post added to queue"}


@router.get("/queue")
async def get_queue(platform: Optional[str] = None, db: Session = Depends(get_db)):
    """Получить очередь публикаций."""
    orchestrator = get_orchestrator()
    
    if platform:
        queue_items = orchestrator.get_queue_for_platform(platform)
    else:
        # Получаем все записи очереди
        queue_items = db.query(PublicationQueue).filter(
            PublicationQueue.status.in_(["pending", "publishing", "paused"])
        ).all()
    
    return {
        "items": [
            {
                "id": item.id,
                "post_id": item.post_id,
                "platform": item.platform,
                "status": item.status,
                "priority": item.priority,
                "scheduled_at": item.scheduled_at.isoformat() if item.scheduled_at else None,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in queue_items
        ]
    }


@router.get("/queue/stats")
async def get_queue_stats():
    """Получить статистику очереди."""
    orchestrator = get_orchestrator()
    stats = orchestrator.get_queue_stats()
    return stats


@router.post("/pause")
async def pause_publication(request: PauseRequest):
    """Приостановить публикацию."""
    orchestrator = get_orchestrator()
    
    if request.global_pause:
        orchestrator.pause_global()
    elif request.platform:
        orchestrator.pause_platform(request.platform)
    elif request.post_id:
        orchestrator.pause_post(request.post_id)
    else:
        raise HTTPException(status_code=400, detail="Must specify post_id, platform, or global_pause")
    
    return {"success": True, "message": "Publication paused"}


@router.post("/resume")
async def resume_publication(request: ResumeRequest):
    """Возобновить публикацию."""
    orchestrator = get_orchestrator()
    
    if request.global_resume:
        orchestrator.resume_global()
    elif request.platform:
        orchestrator.resume_platform(request.platform)
    elif request.post_id:
        orchestrator.resume_post(request.post_id)
    else:
        raise HTTPException(status_code=400, detail="Must specify post_id, platform, or global_resume")
    
    return {"success": True, "message": "Publication resumed"}


@router.delete("/queue/{queue_id}")
async def remove_from_queue(queue_id: int, db: Session = Depends(get_db)):
    """Удалить запись из очереди."""
    queue_item = db.query(PublicationQueue).filter(PublicationQueue.id == queue_id).first()
    if not queue_item:
        raise HTTPException(status_code=404, detail="Queue item not found")
    
    orchestrator = get_orchestrator()
    orchestrator.cancel_post(queue_item.post_id)
    
    return {"success": True, "message": "Item removed from queue"}


@router.post("/publish-now/{post_id}")
async def publish_now(post_id: str, platforms: Optional[List[str]] = None):
    """Опубликовать пост вне очереди."""
    orchestrator = get_orchestrator()
    success = orchestrator.publish_now(post_id, platforms)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to add post to queue")
    return {"success": True, "message": "Post added to queue with high priority"}

