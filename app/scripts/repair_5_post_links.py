"""One-off repair: backfill TG/Max/VK links for 5 posts (2026-07-03 queue batch)."""
from __future__ import annotations

import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from sqlalchemy import text

from app.db.database import SessionLocal


REPAIRS = [
    {
        "id": "8ba04f93-797b-404b-ba21-a61f99af37bd",
        "telegram_link": "https://t.me/AppleShop43/15075",
        "max_link": "max://channel/-71185634522473/mid.ffffbf41cd8a8a97019f28045ba12350",
        "max_share_url": "https://max.ru/c/-71185634522473/AZ8oBFuhI1A",
        "vk_post_id": "-129808251_25327",
        "vk_post_link": "https://vk.ru/wall-129808251_25327",
        "published_telegram_at": "2026-07-03 12:46:33+03:00",
        "published_max_at": "2026-07-03 12:46:42+03:00",
        "published_vk_at": "2026-07-03 12:47:36+03:00",
    },
    {
        "id": "d14da073-a939-4c83-80a8-14f329881081",
        "telegram_link": "https://t.me/AppleShop43/15082",
        "max_link": "max://channel/-71185634522473/mid.ffffbf41cd8a8a97019f280480140697",
        "max_share_url": "https://max.ru/c/-71185634522473/AZ8oBIAUBpc",
        "published_telegram_at": "2026-07-03 12:48:00+03:00",
        "published_max_at": "2026-07-03 12:48:10+03:00",
    },
    {
        "id": "bb0e642f-13f1-476a-a255-c33820fb5e2b",
        "telegram_link": "https://t.me/AppleShop43/15089",
        "max_link": "max://channel/-71185634522473/mid.ffffbf41cd8a8a97019f2804a5c04821",
        "max_share_url": "https://max.ru/c/-71185634522473/AZ8oBKXASCE",
        "published_telegram_at": "2026-07-03 12:52:00+03:00",
        "published_max_at": "2026-07-03 12:52:10+03:00",
    },
    {
        "id": "58148e88-2a0d-490a-acbe-0d59fbbc57b6",
        "telegram_link": "https://t.me/AppleShop43/15096",
        "max_link": "max://channel/-71185634522473/mid.ffffbf41cd8a8a97019f2804caed17db",
        "max_share_url": "https://max.ru/c/-71185634522473/AZ8oBMrtF9s",
        "published_telegram_at": "2026-07-03 12:56:00+03:00",
        "published_max_at": "2026-07-03 12:56:10+03:00",
    },
    {
        "id": "d99db0d8-3100-4015-bb38-ada13c35b46e",
        "telegram_link": "https://t.me/AppleShop43/15103",
        "max_link": "max://channel/-71185634522473/mid.ffffbf41cd8a8a97019f2804ef370707",
        "max_share_url": "https://max.ru/c/-71185634522473/AZ8oBO83Bwc",
        "published_telegram_at": "2026-07-03 13:00:00+03:00",
        "published_max_at": "2026-07-03 13:00:10+03:00",
    },
]


def main() -> int:
    db = SessionLocal()
    results = []
    try:
        for r in REPAIRS:
            pid = r["id"]
            sets = [
                "is_published_telegram = true",
                "telegram_link = :telegram_link",
                "published_telegram_at = COALESCE(published_telegram_at, :published_telegram_at)",
                "is_published_max = true",
                "max_link = :max_link",
                "max_share_url = :max_share_url",
                "published_max_at = COALESCE(published_max_at, :published_max_at)",
                "in_queue = false",
                "queue_status = 'completed'",
                "updated_at = NOW()",
            ]
            params = {
                "id": pid,
                "telegram_link": r["telegram_link"],
                "published_telegram_at": r["published_telegram_at"],
                "max_link": r["max_link"],
                "max_share_url": r["max_share_url"],
                "published_max_at": r["published_max_at"],
            }
            if r.get("vk_post_link"):
                sets.extend(
                    [
                        "is_published_vk = true",
                        "vk_post_id = COALESCE(vk_post_id, :vk_post_id)",
                        "vk_post_link = COALESCE(vk_post_link, :vk_post_link)",
                        "published_vk_at = COALESCE(published_vk_at, :published_vk_at)",
                    ]
                )
                params["vk_post_id"] = r["vk_post_id"]
                params["vk_post_link"] = r["vk_post_link"]
                params["published_vk_at"] = r["published_vk_at"]

            db.execute(text(f"UPDATE posts SET {', '.join(sets)} WHERE id = :id"), params)
            db.execute(
                text(
                    "UPDATE products SET telegram_link = :telegram_link, "
                    "max_link = :max_link, max_share_url = :max_share_url, "
                    "updated_at = NOW() WHERE post_id = :id"
                ),
                {
                    "id": pid,
                    "telegram_link": r["telegram_link"],
                    "max_link": r["max_link"],
                    "max_share_url": r["max_share_url"],
                },
            )
            db.execute(
                text(
                    "UPDATE publication_queue SET status = 'completed', error_message = NULL, "
                    "published_at = COALESCE(published_at, NOW()) "
                    "WHERE post_id = :id AND platform IN ('telegram', 'max') AND status = 'failed'"
                ),
                {"id": pid},
            )
            if r.get("vk_post_link"):
                db.execute(
                    text(
                        "UPDATE publication_queue SET status = 'completed', error_message = NULL, "
                        "published_at = COALESCE(published_at, NOW()) "
                        "WHERE post_id = :id AND platform = 'vk' AND status = 'failed'"
                    ),
                    {"id": pid},
                )
            row = (
                db.execute(
                    text(
                        "SELECT is_published_vk, is_published_telegram, is_published_max, "
                        "telegram_link, max_share_url, vk_post_link, in_queue, queue_status "
                        "FROM posts WHERE id = :id"
                    ),
                    {"id": pid},
                )
                .mappings()
                .first()
            )
            results.append({"post_id": pid[:8], **dict(row)})
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    for x in results:
        print(x)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
