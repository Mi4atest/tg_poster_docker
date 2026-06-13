"""Backfill instagram_link / instagram_media_id для уже опубликованных постов (Graph API).

Запуск батчами (не грузит API и БД):
  python app/scripts/backfill_instagram_links.py --apply --since 2026-05-08 --all
  python app/scripts/backfill_instagram_links.py --apply --since 2026-05-08 --batch-size 25 --delay 2
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

root = Path(__file__).resolve().parent.parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))


def _normalize_caption(text: str) -> str:
    s = (text or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s[:120]


def _parse_since(value: str) -> datetime:
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            dt = datetime.strptime(value.strip(), fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Неверный формат даты: {value!r} (ожидается YYYY-MM-DD)")


def _match_post_to_media(post, media_items: List[dict], used_media_ids: set[str]) -> Optional[dict]:
    from app.utils.text_formatter import format_for_instagram

    post_caption = _normalize_caption(format_for_instagram(post.text or ""))
    post_caption_raw = _normalize_caption(post.text or "")
    best = None
    best_score = 0
    for item in media_items:
        mid = str(item.get("id") or "").strip()
        if not mid or mid in used_media_ids:
            continue
        ig_caption = _normalize_caption(item.get("caption") or "")
        score = 0
        if post_caption and ig_caption:
            if post_caption[:60] == ig_caption[:60]:
                score = 100
            elif post_caption[:40] in ig_caption or ig_caption[:40] in post_caption:
                score = 80
            elif post_caption_raw[:40] in ig_caption:
                score = 60
        if score > best_score:
            best_score = score
            best = item
    if best and best_score >= 60:
        return {**best, "match_score": best_score}
    return None


async def _apply_batch(db, matched: List[dict]) -> int:
    from sqlalchemy import text

    for entry in matched:
        db.execute(
            text(
                "UPDATE posts SET instagram_media_id = :mid, instagram_link = :link "
                "WHERE id = :pid"
            ),
            {
                "mid": entry["instagram_media_id"],
                "link": entry.get("instagram_link"),
                "pid": entry["post_id"],
            },
        )
        db.execute(
            text(
                "UPDATE products SET instagram_media_id = :mid, instagram_link = :link "
                "WHERE post_id = :pid"
            ),
            {
                "mid": entry["instagram_media_id"],
                "link": entry.get("instagram_link"),
                "pid": entry["post_id"],
            },
        )
    db.commit()
    return len(matched)


async def backfill(
    *,
    dry_run: bool,
    since: datetime,
    batch_size: int,
    delay_sec: float,
    process_all: bool,
    max_posts: Optional[int],
) -> dict:
    from app.api.models.post import Post
    from app.db.database import SessionLocal
    from app.workers.instagram.graph_client import InstagramGraphClient

    client = InstagramGraphClient()
    if not client.enabled:
        return {"error": "graph_api_not_configured"}

    db = SessionLocal()
    summary = {
        "dry_run": dry_run,
        "since": since.date().isoformat(),
        "batch_size": batch_size,
        "delay_sec": delay_sec,
        "batches": [],
        "total_posts_missing": 0,
        "total_matched": 0,
        "total_unmatched": 0,
        "media_fetched": 0,
        "media_truncated": False,
    }
    try:
        q = (
            db.query(Post)
            .filter(Post.is_published_instagram.is_(True))
            .filter(Post.published_instagram_at >= since.replace(tzinfo=None))
            .filter((Post.instagram_media_id.is_(None)) | (Post.instagram_media_id == ""))
            .order_by(Post.published_instagram_at.desc())
        )
        if max_posts:
            q = q.limit(max_posts)
        posts = q.all()
        summary["total_posts_missing"] = len(posts)
        if not posts:
            summary["message"] = "Нечего backfill — все посты уже имеют instagram_media_id"
            return summary

        media_items, truncated = await client.list_all_media(
            max_items=max(500, len(posts) + 50),
            since=since,
            page_delay_sec=max(delay_sec, 0.5),
        )
        summary["media_fetched"] = len(media_items)
        summary["media_truncated"] = truncated

        used_media_ids: set[str] = set()
        pending: List[dict] = []
        batch_num = 0

        for post in posts:
            hit = _match_post_to_media(post, media_items, used_media_ids)
            if not hit:
                summary["total_unmatched"] += 1
                continue
            mid = str(hit["id"])
            used_media_ids.add(mid)
            permalink = (hit.get("permalink") or "").strip() or None
            pending.append(
                {
                    "post_id": post.id,
                    "post_name": (post.name or "")[:60],
                    "instagram_media_id": mid,
                    "instagram_link": permalink,
                    "match_score": hit["match_score"],
                }
            )

            if len(pending) >= batch_size:
                batch_num += 1
                batch_items = pending[:]
                pending.clear()
                if not dry_run:
                    applied = await _apply_batch(db, batch_items)
                    summary["total_matched"] += applied
                else:
                    summary["total_matched"] += len(batch_items)
                summary["batches"].append(
                    {"batch": batch_num, "count": len(batch_items), "items": batch_items}
                )
                if process_all or batch_num == 1:
                    print(
                        f"Batch {batch_num}: matched {len(batch_items)} "
                        f"({'dry-run' if dry_run else 'applied'})",
                        flush=True,
                    )
                await asyncio.sleep(delay_sec)

        if pending:
            batch_num += 1
            if not dry_run:
                applied = await _apply_batch(db, pending)
                summary["total_matched"] += applied
            else:
                summary["total_matched"] += len(pending)
            summary["batches"].append(
                {"batch": batch_num, "count": len(pending), "items": pending}
            )
            print(
                f"Batch {batch_num}: matched {len(pending)} "
                f"({'dry-run' if dry_run else 'applied'})",
                flush=True,
            )

        return summary
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Записать в БД")
    parser.add_argument("--since", type=str, default="2026-05-08", help="Дата начала (YYYY-MM-DD)")
    parser.add_argument("--batch-size", type=int, default=20, help="Размер батча записи в БД")
    parser.add_argument("--delay", type=float, default=2.0, help="Пауза между батчами (сек)")
    parser.add_argument("--all", action="store_true", dest="process_all", help="Обработать все посты")
    parser.add_argument("--limit", type=int, default=None, help="Макс. постов за один запуск")
    args = parser.parse_args()

    since = _parse_since(args.since)
    if not args.process_all and args.limit is None:
        args.limit = args.batch_size

    result = asyncio.run(
        backfill(
            dry_run=not args.apply,
            since=since,
            batch_size=max(1, args.batch_size),
            delay_sec=max(0.0, args.delay),
            process_all=args.process_all,
            max_posts=args.limit,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
