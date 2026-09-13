"""Debug NDJSON for VK API failures (session 9145ef). Do not log tokens."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional
from urllib.request import Request, urlopen

try:
    from app.config.settings import APP_LOG_DIR, BASE_DIR
except Exception:  # pragma: no cover
    BASE_DIR = Path("/app")
    APP_LOG_DIR = BASE_DIR / "app" / "logs"

_LOG_PATHS = (
    Path("/root/tg_poster_docker/.cursor/debug-9145ef.log"),
    Path("/app/media/.cursor-debug-9145ef.log"),
    BASE_DIR / "media" / ".cursor-debug-9145ef.log",
    APP_LOG_DIR / "vk_debug.ndjson",
)
_INGEST = "http://172.20.0.1:7705/ingest/0a911300-a7f4-43f6-89b8-87b0d58fe342"


def _token_shape(raw: str) -> dict[str, Any]:
    t = (raw or "").strip()
    if not t:
        return {"set": False, "len": 0, "prefix": "", "vk1": False, "vk2": False}
    return {
        "set": True,
        "len": len(t),
        "prefix": t[:6],
        "vk1": t.startswith("vk1.a."),
        "vk2": t.startswith("vk2.a."),
    }


def vk_token_debug_meta() -> dict[str, Any]:
    try:
        from app.utils.vk_client import community_token, market_token, market_token_source

        mt = market_token()
        ct = community_token()
        return {
            "source": market_token_source(),
            "market": _token_shape(mt),
            "community": _token_shape(ct),
            "same_token": bool(mt) and mt == ct,
        }
    except Exception as exc:
        return {"meta_error": type(exc).__name__}


def vk_exc_debug_meta(exc: BaseException) -> dict[str, Any]:
    from app.utils.vk_client import vk_api_error_code

    err = getattr(exc, "error", None)
    err_msg = None
    method = None
    if isinstance(err, dict):
        err_msg = err.get("error_msg")
        params = err.get("request_params") or []
        if isinstance(params, list):
            for p in params:
                if isinstance(p, dict) and p.get("key") == "method":
                    method = p.get("value")
                    break
    return {
        "type": type(exc).__name__,
        "code": vk_api_error_code(exc),
        "msg": str(exc)[:400],
        "error_msg": (str(err_msg)[:300] if err_msg else None),
        "method": method,
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
        "sessionId": "9145ef",
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
    try:
        req = Request(
            _INGEST,
            data=line.encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Debug-Session-Id": "9145ef",
            },
            method="POST",
        )
        urlopen(req, timeout=1).read()
    except Exception:
        pass
