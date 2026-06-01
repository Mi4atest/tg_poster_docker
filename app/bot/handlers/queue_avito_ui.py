"""Тексты и сборка экранов очереди Авито."""
from __future__ import annotations

import html
from typing import List, Optional

from app.integrations.avito.archive_queue import list_pending, list_recent_completed
from app.integrations.avito.avito_feed_dispatcher import (
    AvitoFeedQueueSummary,
    format_avito_queue_header,
    get_queue_summary,
)
from app.scheduler.queue_manager import QueueManager


def format_recent_archive_lines() -> List[str]:
    """Строки ✅ для блока «снят(ы) с Авито»."""
    rows = list_recent_completed(hours=48)
    lines = []
    for r in rows:
        name = html.escape((r.get("product_name") or "Товар")[:60])
        t = r.get("time_str") or "—"
        lines.append(f'✅ «{name}» в {t}')
    return lines


def build_queue_menu_text(stats: dict, queue_manager: QueueManager) -> str:
    summary = get_queue_summary(queue_manager)
    total = stats.get("total", 0)

    parts = ["📋 <b>Очередь публикаций</b>", ""]
    recent = format_recent_archive_lines()
    if recent:
        parts.append("снят(ы) с Авито")
        parts.append("")
        parts.extend(recent)
        parts.append("")

    parts.append(f"Всего в очереди: {total}")
    parts.append("")
    parts.append(f"📱 ВК: {stats.get('vk', 0)}")
    parts.append(f"📢 Telegram: {stats.get('telegram', 0)}")
    parts.append(f"📸 Instagram: {stats.get('instagram', 0)}")
    parts.append(f"💬 Max: {stats.get('max', 0)}")
    parts.append(format_avito_queue_header(summary, compact=True))
    return "\n".join(parts)


def build_avito_platform_text(
    summary: AvitoFeedQueueSummary,
    publish_items: list,
    archive_items: List[dict],
) -> str:
    lines = ["📋 <b>Очередь: Авито</b>", ""]
    recent = format_recent_archive_lines()
    if recent:
        lines.append("снят(ы) с Авито")
        lines.append("")
        lines.extend(recent)
        lines.append("")

    lines.append(format_avito_queue_header(summary, compact=False))
    lines.append("")
    lines.append("<b>Ждут выгрузки на сайт</b> (новые объявления):")
    if not publish_items:
        lines.append("— пусто")
    else:
        for i, item in enumerate(publish_items[:12], 1):
            name = item.post.name if item.post else f"Пост {item.post_id[:8]}"
            lines.append(f"{i}. ⏳ {html.escape(name[:50])}")
        if len(publish_items) > 12:
            lines.append(f"… ещё {len(publish_items) - 12}")

    lines.append("")
    lines.append("<b>На снятие с публикации</b>:")
    if not archive_items:
        lines.append("— пусто")
    else:
        for i, row in enumerate(archive_items[:12], 1):
            name = html.escape((row.get("product_name") or f"#{row.get('product_id')}")[:50])
            lines.append(f"{i}. 🕐 {name}")
        if len(archive_items) > 12:
            lines.append(f"… ещё {len(archive_items) - 12}")

    if summary.manual_mode:
        lines.append("")
        lines.append(
            "<i>Один файл на Авито — не чаще 1 раза в час. "
            "ВК, Telegram и Max публикуются по своей очереди.</i>"
        )
    return "\n".join(lines)
