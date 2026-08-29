"""NDJSON debug logger for Cursor debug session (session e758b8)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

try:
    from app.config.settings import BASE_DIR
except Exception:  # pragma: no cover
    BASE_DIR = Path("/app")

# Host Cursor path (если доступен) + media volume (Docker → ./media на хосте).
_LOG_PATHS = (
    Path("/root/tg_poster_docker/.cursor/debug-e758b8.log"),
    BASE_DIR / "media" / "avito_market_cache" / "debug-e758b8.log",
    Path("/app/media/avito_market_cache/debug-e758b8.log"),
)


def agent_dbg(
    hypothesis_id: str,
    location: str,
    message: str,
    data: Optional[dict[str, Any]] = None,
    *,
    run_id: str = "pre",
) -> None:
    payload = {
        "sessionId": "e758b8",
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
