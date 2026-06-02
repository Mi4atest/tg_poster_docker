import logging

from app.config.settings import MAX_API_BASE_URL, MAX_BOT_TOKEN
from app.integrations.max.client import MaxApiClient
from app.services.settings_service import get_settings_service


logger = logging.getLogger(__name__)


async def run_max_permissions_preflight() -> bool:
    """Проверяет, что бот доступен и видит целевой канал."""
    service = get_settings_service()
    max_token = (service.get_secret("max_bot_token") or MAX_BOT_TOKEN or "").strip()
    max_channel_id = (service.get_max_channel_id() or "").strip()
    if not max_token or not max_channel_id:
        logger.error("MAX_BOT_TOKEN или MAX_CHANNEL_ID не заданы")
        return False
    client = MaxApiClient(max_token, MAX_API_BASE_URL)
    try:
        await client.get_me()
        await client.get_chat(max_channel_id)
        return True
    except Exception as exc:
        logger.error("Max preflight failed: %s", exc)
        return False
