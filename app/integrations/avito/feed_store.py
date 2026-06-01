"""Хранение актуального XML-фида для автозагрузки Авито."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from app.config.settings import MEDIA_DIR

FEED_DIR = MEDIA_DIR / "avito_feed"
FEED_XML_PATH = FEED_DIR / "current.xml"
FEED_META_PATH = FEED_DIR / "current_meta.json"


def _ensure_dir() -> None:
    FEED_DIR.mkdir(parents=True, exist_ok=True)


def save_feed(
    xml_content: str,
    post_id: str,
    ad_id: str,
    *,
    post_ids: Optional[list] = None,
    ad_ids: Optional[list] = None,
) -> None:
    _ensure_dir()
    FEED_XML_PATH.write_text(xml_content, encoding="utf-8")
    meta: Dict[str, Any] = {"post_id": post_id, "ad_id": ad_id}
    if post_ids:
        meta["post_ids"] = list(post_ids)
    if ad_ids:
        meta["ad_ids"] = list(ad_ids)
    FEED_META_PATH.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def load_feed_xml() -> Optional[str]:
    if not FEED_XML_PATH.is_file():
        return None
    return FEED_XML_PATH.read_text(encoding="utf-8")


def load_feed_meta() -> Dict[str, Any]:
    if not FEED_META_PATH.is_file():
        return {}
    try:
        data = json.loads(FEED_META_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}
