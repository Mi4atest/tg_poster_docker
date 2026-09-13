"""Read-only probe of current VK tokens/API (no token values in logs)."""
from __future__ import annotations

import json
import sys
from typing import Any

import requests

from app.utils.vk_client import (
    get_community_vk_session,
    get_market_vk_session,
    resolved_vk_group_id_int,
)
from app.utils.vk_debug_log import vk_dbg, vk_exc_debug_meta, vk_token_debug_meta


def _call(label: str, fn, hypothesis_id: str) -> dict[str, Any]:
    try:
        result = fn()
        summary: Any
        if isinstance(result, dict):
            summary = {
                "keys": sorted(list(result.keys()))[:12],
                "count": result.get("count"),
                "items_len": len(result.get("items") or [])
                if isinstance(result.get("items"), list)
                else None,
            }
        elif isinstance(result, list):
            summary = {"list_len": len(result)}
        else:
            summary = {"type": type(result).__name__}
        vk_dbg(
            hypothesis_id,
            "debug_vk_api_probe.py:_call",
            f"OK {label}",
            {"label": label, "summary": summary},
            run_id="probe",
        )
        print(f"OK  {label} {json.dumps(summary, ensure_ascii=False)}")
        return {"ok": True, "label": label, "summary": summary}
    except Exception as exc:
        meta = vk_exc_debug_meta(exc)
        vk_dbg(
            hypothesis_id,
            "debug_vk_api_probe.py:_call",
            f"FAIL {label}",
            {"label": label, **meta, "token": vk_token_debug_meta()},
            run_id="probe",
        )
        print(f"FAIL {label} code={meta.get('code')} msg={meta.get('msg')}")
        return {"ok": False, "label": label, **meta}


def _raw_method(host: str, method: str, token: str, extra: dict[str, Any]) -> dict[str, Any]:
    params = {"access_token": token, "v": "5.199", **extra}
    url = f"https://{host}/method/{method}"
    resp = requests.post(url, data=params, timeout=20)
    body = resp.json()
    err = body.get("error") or {}
    return {
        "http": resp.status_code,
        "has_response": "response" in body,
        "error_code": err.get("error_code"),
        "error_msg": (err.get("error_msg") or "")[:300],
    }


def main() -> int:
    meta = vk_token_debug_meta()
    vk_dbg("A", "debug_vk_api_probe.py:main", "token meta", meta, run_id="probe")
    print("TOKEN_META", json.dumps(meta, ensure_ascii=False))

    gid = None
    try:
        gid = resolved_vk_group_id_int()
        print(f"group_id={gid}")
    except Exception as exc:
        print(f"group_id FAIL {exc}")
        vk_dbg("D", "debug_vk_api_probe.py:main", "group_id missing", {"err": str(exc)[:200]}, run_id="probe")

    mvk = get_market_vk_session().get_api()
    cvk = get_community_vk_session().get_api()
    owner = -int(gid or 0) if gid else None

    _call("market.users.get", lambda: mvk.users.get(), "A")
    _call("community.users.get", lambda: cvk.users.get(), "C")
    try:
        _call(
            "market.account.getAppPermissions",
            lambda: mvk.account.getAppPermissions(),
            "B",
        )
    except Exception:
        pass
    if gid:
        _call(
            "community.groups.getById",
            lambda: cvk.groups.getById(group_id=gid),
            "C",
        )
        _call(
            "market.groups.getById",
            lambda: mvk.groups.getById(group_id=gid),
            "C",
        )
        _call(
            "market.market.get",
            lambda: mvk.market.get(owner_id=owner, count=1),
            "B",
        )
        _call(
            "community.wall.get",
            lambda: cvk.wall.get(owner_id=owner, count=1),
            "C",
        )

    from app.utils.vk_client import market_token

    token = market_token()
    extra = {"count": 1}
    if owner:
        extra["owner_id"] = owner
    for host in ("api.vk.ru", "api.vk.com"):
        try:
            raw = _raw_method(host, "market.get", token, extra)
            vk_dbg(
                "D",
                "debug_vk_api_probe.py:host",
                f"host {host} market.get",
                raw,
                run_id="probe",
            )
            print(
                f"HOST {host} market.get http={raw['http']} "
                f"ok={raw['has_response']} code={raw['error_code']} msg={raw['error_msg']}"
            )
        except Exception as exc:
            vk_dbg(
                "D",
                "debug_vk_api_probe.py:host",
                f"host {host} exception",
                {"type": type(exc).__name__, "msg": str(exc)[:200]},
                run_id="probe",
            )
            print(f"HOST {host} EXC {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
