"""
Обновляет max_share_url у постов и связанных товаров, опубликованных в MAX раньше,
когда в БД остались ссылки вида .../mid... или max_share_url пустой.

Для каждого поста с max_link = max://channel/<chat>/<mid> вызывается GET /messages/{mid},
затем строится публичная ссылка https://max.ru/c/.../<slug> (как «Копировать ссылку» в MAX).

Запуск:
  docker-compose exec app python -m app.scripts.backfill_max_share_urls
  docker-compose exec app python -m app.scripts.backfill_max_share_urls --dry-run
  docker-compose exec app python -m app.scripts.backfill_max_share_urls --delay 0.5
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_MAX_LINK_RE = re.compile(r"^max://channel/(?P<chat>[^/]+)/(?P<mid>[^\s?#]+)\s*$", re.IGNORECASE)


def _needs_backfill(share_url: str | None) -> bool:
    s = (share_url or "").strip()
    if not s:
        return True
    low = s.lower()
    if "/mid." in low:
        return True
    seg = low.rstrip("/").split("/")[-1]
    return seg.startswith("mid.")


async def _process_post(db, client, post, dry_run: bool) -> str:
    from sqlalchemy import text

    from app.utils.max_share_link import resolve_max_channel_share_url

    raw = (post.max_link or "").strip()
    m = _MAX_LINK_RE.match(raw)
    if not m:
        return "skip_no_max_link"
    chat, mid = m.group("chat"), m.group("mid")
    if not _needs_backfill(post.max_share_url):
        return "skip_already_ok"
    try:
        info = await client.get_message(mid)
    except Exception as e:
        logger.error("post_id=%s GET /messages/%s: %s", post.id, mid, e)
        return "error_api"
    share = resolve_max_channel_share_url(chat, info)
    if not share:
        logger.warning("post_id=%s: публичная ссылка не получена (mid=%s)", post.id, mid)
        return "no_share"
    if dry_run:
        logger.info("[dry-run] post_id=%s -> %s", post.id, share)
        return "dry_ok"
    # UPDATE через SQL: ORM-flush по Product тянет FK new_menu_buttons, которой нет в metadata скрипта
    try:
        db.execute(text("UPDATE posts SET max_share_url = :u WHERE id = :id"), {"u": share, "id": post.id})
        db.execute(text("UPDATE products SET max_share_url = :u WHERE post_id = :pid"), {"u": share, "pid": post.id})
        db.commit()
        post.max_share_url = share
    except Exception as e:
        db.rollback()
        logger.error("post_id=%s запись в БД: %s", post.id, e)
        return "error_db"
    logger.info("post_id=%s обновлён: %s", post.id, share)
    return "updated"


async def async_main(dry_run: bool, delay_s: float) -> int:
    from app.api.models.post import Post
    from app.config.settings import MAX_API_BASE_URL, MAX_BOT_TOKEN
    from app.db.database import SessionLocal
    from app.integrations.max.client import MaxApiClient

    if not MAX_BOT_TOKEN or not MAX_API_BASE_URL:
        logger.error("MAX_BOT_TOKEN или MAX_API_BASE_URL не заданы в окружении")
        return 1

    client = MaxApiClient(MAX_BOT_TOKEN, MAX_API_BASE_URL)
    db = SessionLocal()
    try:
        posts = (
            db.query(Post)
            .filter(Post.is_published_max.is_(True))
            .filter(Post.max_link.isnot(None))
            .filter(Post.max_link.like("max://channel/%"))
            .order_by(Post.created_at.desc())
            .all()
        )
        logger.info("К обработке: постов с max://channel/... = %d", len(posts))
        counts: dict[str, int] = {}
        for post in posts:
            key = await _process_post(db, client, post, dry_run)
            counts[key] = counts.get(key, 0) + 1
            if delay_s > 0:
                await asyncio.sleep(delay_s)
        for k, v in sorted(counts.items()):
            logger.info("  %s: %d", k, v)
    finally:
        db.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Бэкап max_share_url для старых постов MAX")
    p.add_argument("--dry-run", action="store_true", help="только лог, без записи в БД")
    p.add_argument("--delay", type=float, default=0.35, help="пауза между запросами к API, сек")
    args = p.parse_args()
    return asyncio.run(async_main(args.dry_run, args.delay))


if __name__ == "__main__":
    sys.exit(main())
