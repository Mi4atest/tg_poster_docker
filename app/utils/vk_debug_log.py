"""Legacy no-op stub (debug session instrumentation removed)."""
from __future__ import annotations

from typing import Any, Optional


def vk_token_debug_meta() -> dict[str, Any]:
    return {}


def vk_exc_debug_meta(exc: BaseException) -> dict[str, Any]:
    return {"type": type(exc).__name__, "msg": str(exc)[:200]}


def vk_dbg(
    hypothesis_id: str,
    location: str,
    message: str,
    data: Optional[dict[str, Any]] = None,
    *,
    run_id: str = "pre",
) -> None:
    return
