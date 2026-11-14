"""NDJSON debug logger for VK flows (Cursor debug session 0f8def)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

try:
    from app.config.settings import BASE_DIR, MEDIA_DIR
except Exception:  # pragma: no cover
    BASE_DIR = Path("/app")
    MEDIA_DIR = Path("/app/media")

# Host Cursor path (compose mount ./.cursor:/app/.cursor) + media fallback.
_LOG_PATHS = (
    Path("/app/.cursor/debug-0f8def.log"),
    Path("/root/tg_poster_docker/.cursor/debug-0f8def.log"),
    Path(MEDIA_DIR) / "debug-0f8def.log",
    Path(BASE_DIR) / "media" / "debug-0f8def.log",
)


def vk_token_debug_meta() -> dict[str, Any]:
    return {}


def vk_exc_debug_meta(exc: BaseException) -> dict[str, Any]:
    return {
        "type": type(exc).__name__,
        "msg": str(exc)[:300],
        "code": getattr(exc, "code", None),
    }


def vk_dbg(
    hypothesis_id: str,
    location: str,
    message: str,
    data: Optional[dict[str, Any]] = None,
    *,
    run_id: str = "pre",
) -> None:
    payload = {
        "sessionId": "0f8def",
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data or {},
        "timestamp": int(time.time() * 1000),
    }
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    for path in _LOG_PATHS:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            continue
