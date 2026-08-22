"""PKCE для VK ID OAuth 2.1 (id.vk.ru)."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from pathlib import Path
from typing import Optional, Tuple

_PKCE_DIR = Path(__file__).resolve().parent.parent.parent / ".cursor" / "vk_pkce"

# Только безопасные имена файлов (как у secrets.token_urlsafe).
_STATE_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
# TTL сессии OAuth (секунды)
_PKCE_TTL_SEC = 15 * 60


def generate_pkce() -> Tuple[str, str, str]:
    verifier = secrets.token_urlsafe(48)[:128]
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    state = secrets.token_urlsafe(32)
    return verifier, challenge, state


def _is_safe_state(state: str) -> bool:
    return bool(state) and _STATE_RE.fullmatch(state) is not None


def _state_path(state: str) -> Optional[Path]:
    """Путь к файлу сессии; None если state недопустим или выходит за _PKCE_DIR."""
    if not _is_safe_state(state):
        return None
    _PKCE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _PKCE_DIR.chmod(0o700)
    except OSError:
        pass
    path = (_PKCE_DIR / f"{state}.json").resolve()
    try:
        if not path.is_relative_to(_PKCE_DIR.resolve()):
            return None
    except (ValueError, OSError):
        return None
    return path


def _cleanup_expired() -> None:
    """Удаляет просроченные PKCE-сессии (брошенные OAuth-флоу)."""
    if not _PKCE_DIR.is_dir():
        return
    now = time.time()
    try:
        for path in _PKCE_DIR.glob("*.json"):
            try:
                if now - path.stat().st_mtime > _PKCE_TTL_SEC:
                    path.unlink(missing_ok=True)
            except OSError:
                pass
    except OSError:
        pass


def save_pkce_state(state: str, verifier: str) -> None:
    path = _state_path(state)
    if path is None:
        raise ValueError("Invalid PKCE state")
    _cleanup_expired()
    path.write_text(
        json.dumps({"verifier": verifier, "created_at": time.time()}),
        encoding="utf-8",
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass


def pop_pkce_verifier(state: str) -> Optional[str]:
    path = _state_path(state)
    if path is None or not path.is_file():
        return None
    try:
        age = time.time() - path.stat().st_mtime
        if age > _PKCE_TTL_SEC:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("verifier")
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
