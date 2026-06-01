import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config.settings import MASTER_KEY


def _build_fernet_key(raw_key: str) -> bytes:
    """Derive a Fernet-compatible key from arbitrary string input."""
    digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_secret(value: str) -> str:
    """Encrypt sensitive value for DB storage."""
    if not value:
        return ""
    if not MASTER_KEY:
        # Без MASTER_KEY (часто в dev): не шифруем, помечаем префиксом — в prod задайте MASTER_KEY.
        return "devplain:" + base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii")
    fernet = Fernet(_build_fernet_key(MASTER_KEY))
    return fernet.encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    """Decrypt sensitive value from DB storage."""
    if not value:
        return ""
    if value.startswith("devplain:"):
        return base64.urlsafe_b64decode(value[9:].encode("ascii")).decode("utf-8")
    if not MASTER_KEY:
        raise ValueError("MASTER_KEY is not set")
    fernet = Fernet(_build_fernet_key(MASTER_KEY))
    try:
        return fernet.decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Unable to decrypt secret with current MASTER_KEY") from exc
