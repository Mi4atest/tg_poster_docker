"""Хелперы отображения очереди без ORM lazy-load."""


def queue_item_display_name(item) -> str:
  name = getattr(item, "post_name", None)
  if name:
    return name
  post = getattr(item, "post", None)
  if post and getattr(post, "name", None):
    return post.name
  post_id = getattr(item, "post_id", "") or ""
  return f"Пост {post_id[:8]}" if post_id else "Пост"
