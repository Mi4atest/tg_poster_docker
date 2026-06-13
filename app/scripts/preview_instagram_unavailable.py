"""Dry-run / live test: комментарий #неактуально под постом Instagram (Graph API)."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))


async def run(
    *,
    product_id: int | None,
    name_contains: str | None,
    media_id: str | None,
    live: bool,
) -> dict:
    from app.api.models.post import Post
    from app.api.models.product import Product
    from app.bot.handlers.product_management import (
        mark_instagram_post_unavailable,
        resolve_product_instagram_link,
        resolve_product_instagram_media_id,
    )
    from app.db.database import SessionLocal
    from app.workers.instagram.graph_client import InstagramGraphClient, UNAVAILABLE_COMMENT

    client = InstagramGraphClient()
    out: dict = {
        "graph_api_enabled": client.enabled,
        "mode": "live" if live else "dry-run",
        "comment_would_be": UNAVAILABLE_COMMENT,
    }

    if not client.enabled:
        out["error"] = "Instagram Graph API не настроен (токен / user id)"
        return out

    db = SessionLocal()
    try:
        product = None
        post = None

        if media_id:
            out["instagram_media_id"] = media_id.strip()
        elif product_id or name_contains:
            q = db.query(Product)
            if product_id:
                product = q.filter(Product.id == product_id).first()
            else:
                product = q.filter(Product.name.ilike(f"%{name_contains}%")).first()
            if not product:
                out["error"] = "product_not_found"
                return out
            post = db.query(Post).filter(Post.id == product.post_id).first()
            product_dict = {
                "id": product.id,
                "name": product.name,
                "post_id": product.post_id,
                "instagram_link": product.instagram_link,
                "instagram_media_id": product.instagram_media_id,
            }
            resolved_media_id = resolve_product_instagram_media_id(product_dict)
            resolved_link = resolve_product_instagram_link(product_dict)
            out.update(
                {
                    "product_id": product.id,
                    "product_name": product.name,
                    "product_instagram_link": product.instagram_link,
                    "product_instagram_media_id": product.instagram_media_id,
                    "resolved_instagram_link": resolved_link,
                    "resolved_instagram_media_id": resolved_media_id,
                    "post_id": post.id if post else None,
                    "post_is_published_instagram": bool(post.is_published_instagram) if post else False,
                    "post_instagram_link": post.instagram_link if post else None,
                    "post_instagram_media_id": post.instagram_media_id if post else None,
                }
            )
            media_id = resolved_media_id
        else:
            post = (
                db.query(Post)
                .filter(Post.is_published_instagram.is_(True))
                .order_by(Post.published_instagram_at.desc())
                .first()
            )
            if post:
                media_id = (post.instagram_media_id or "").strip() or None
                out.update(
                    {
                        "post_id": post.id,
                        "post_name": post.name,
                        "post_instagram_link": post.instagram_link,
                        "post_instagram_media_id": post.instagram_media_id,
                        "is_published_instagram": post.is_published_instagram,
                    }
                )

        if not media_id:
            out["error"] = "no_instagram_media_id"
            out["hint"] = (
                "Укажите --product-id / --name-contains / --media-id "
                "или опубликуйте пост в IG после обновления (ссылка сохранится автоматически)"
            )
            return out

        out["instagram_media_id"] = media_id
        permalink, shortcode = await client.fetch_media_permalink(media_id)
        out["permalink_from_api"] = permalink
        out["shortcode_from_api"] = shortcode

        comments = await client.list_comment_texts(media_id)
        out["existing_comments_count"] = len(comments)
        out["existing_comments_preview"] = comments[:10]
        out["already_has_unavailable_comment"] = await client.has_unavailable_comment(media_id)

        if live:
            if product:
                product_dict = {
                    "id": product.id,
                    "post_id": product.post_id,
                    "instagram_link": product.instagram_link,
                    "instagram_media_id": product.instagram_media_id or media_id,
                }
                out["live_result"] = await mark_instagram_post_unavailable(product_dict)
            else:
                out["live_result"] = await client.post_comment(media_id, UNAVAILABLE_COMMENT)
            comments_after = await client.list_comment_texts(media_id)
            out["comments_after_count"] = len(comments_after)
            out["comments_after_preview"] = comments_after[:10]
        else:
            out["would_post_comment"] = not out["already_has_unavailable_comment"]
            if out["already_has_unavailable_comment"]:
                out["action"] = "skip_already_marked"
            elif permalink:
                out["action"] = f"post_comment_on_{permalink}"
            else:
                out["action"] = f"post_comment_on_media_id_{media_id}"

        return out
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Dry-run / live test Instagram unavailable comment")
    parser.add_argument("--product-id", type=int)
    parser.add_argument("--name-contains", type=str)
    parser.add_argument("--media-id", type=str, help="Instagram Graph media ID (минуя БД)")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Реально отправить комментарий (без флага — только dry-run)",
    )
    args = parser.parse_args()
    result = asyncio.run(
        run(
            product_id=args.product_id,
            name_contains=args.name_contains,
            media_id=args.media_id,
            live=args.live,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("error"):
        sys.exit(1)
    if args.live and result.get("live_result") is False:
        sys.exit(2)


if __name__ == "__main__":
    main()
