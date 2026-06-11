"""Dry-run: что произойдёт при mark_max_post_unavailable для товара (без правки в Max)."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))


async def preview(*, product_id: int | None, name_contains: str | None) -> dict:
    from app.api.models.post import Post
    from app.api.models.product import Product
    from app.bot.handlers.product_management import (
        _extract_max_message_id,
        _max_attachment_summary,
        resolve_product_max_link,
    )
    from app.config.settings import MAX_API_BASE_URL, MAX_BOT_TOKEN
    from app.db.database import SessionLocal
    from app.integrations.max.client import MaxApiClient
    from app.services.settings_service import get_settings_service
    from app.utils.text_formatter import format_for_max, format_for_max_plain

    db = SessionLocal()
    try:
        q = db.query(Product)
        if product_id:
            product = q.filter(Product.id == product_id).first()
        elif name_contains:
            product = q.filter(Product.name.ilike(f"%{name_contains}%")).first()
        else:
            raise ValueError("Укажите --product-id или --name-contains")

        if not product:
            return {"error": "product_not_found"}

        post = db.query(Post).filter(Post.id == product.post_id).first()
        product_dict = {
            "id": product.id,
            "name": product.name,
            "post_id": product.post_id,
            "max_link": product.max_link,
            "price": product.price,
            "status": product.status,
        }
        max_link = resolve_product_max_link(product_dict)
        message_id = _extract_max_message_id(max_link or "")
        has_media = bool(post and (post.photos or post.videos))
        original_text = (post.text or "") if post else ""
        text_with_unavailable = f"#неактуально\n\n{original_text}"
        formatted = format_for_max(text_with_unavailable, signature_enabled=True)
        plain = format_for_max_plain(text_with_unavailable, signature_enabled=True)

        out = {
            "product_id": product.id,
            "product_name": product.name,
            "product_status": product.status,
            "product_max_link": product.max_link,
            "resolved_max_link": max_link,
            "message_id": message_id,
            "post_id": post.id if post else None,
            "photos_count": len(post.photos or []) if post else 0,
            "videos_count": len(post.videos or []) if post else 0,
            "is_published_max": bool(post.is_published_max) if post else False,
            "max_share_url": post.max_share_url if post else None,
            "telegram_link": post.telegram_link if post else None,
            "edit_branch": "edit_message_caption" if has_media else "edit_message_text",
            "put_body_would_be": {
                "text": f"<{len(formatted)} chars>",
                "format": "markdown",
                "disable_link_preview": False,
                "attachments": "NOT_SENT",
            },
            "formatted_text_preview": formatted[:600],
            "plain_fallback_preview": plain[:300],
        }

        service = get_settings_service()
        token = (service.get_secret("max_bot_token") or MAX_BOT_TOKEN or "").strip()
        channel = (service.get_max_channel_id() or "").strip()
        out["max_channel_id"] = channel
        if token and message_id:
            client = MaxApiClient(token, MAX_API_BASE_URL)
            try:
                msg = await client.get_message(str(message_id))
                out["current_attachments_in_max"] = _max_attachment_summary(msg)
            except Exception as e:
                out["current_attachments_in_max"] = {"error": str(e)[:300]}
        else:
            out["current_attachments_in_max"] = {"error": "no token or message_id"}

        return out
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-id", type=int)
    parser.add_argument("--name-contains", type=str)
    args = parser.parse_args()
    result = asyncio.run(preview(product_id=args.product_id, name_contains=args.name_contains))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
