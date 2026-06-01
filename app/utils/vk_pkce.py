"""PKCE для VK ID OAuth 2.1 (id.vk.ru)."""
import base64
import hashlib
import json
import secrets
from pathlib import Path
from typing import Optional, Tuple

_PKCE_DIR = Path(__file__).resolve().parent.parent.parent / ".cursor" / "vk_pkce"


def generate_pkce() -> Tuple[str, str, str]:
    verifier = secrets.token_urlsafe(48)[:128]
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    state = secrets.token_urlsafe(32)
    return verifier, challenge, state


def save_pkce_state(state: str, verifier: str) -> None:
    _PKCE_DIR.mkdir(parents=True, exist_ok=True)
    path = _PKCE_DIR / f"{state}.json"
    path.write_text(json.dumps({"verifier": verifier}), encoding="utf-8")


def pop_pkce_verifier(state: str) -> Optional[str]:
    path = _PKCE_DIR / f"{state}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("verifier")
    finally:
        try:
            path.unlink()
        except OSError:
            pass
