import logging

from app.config.settings import MAX_API_BASE_URL, MAX_BOT_TOKEN
from app.integrations.max.client import MaxApiClient
from app.services.settings_service import get_settings_service


logger = logging.getLogger(__name__)


async def run_max_permissions_preflight() -> bool:
    """Проверяет, что бот доступен и видит целевой канал."""
    MAX_CHANNEL_ID = get_settings_service().get_max_channel_id()
    if not MAX_BOT_TOKEN or not MAX_CHANNEL_ID:
        logger.error("MAX_BOT_TOKEN или MAX_CHANNEL_ID не заданы")
        return False
    client = MaxApiClient(MAX_BOT_TOKEN, MAX_API_BASE_URL)
    try:
        await client.get_me()
        await client.get_chat(MAX_CHANNEL_ID)
        return True
    except Exception as exc:
        logger.error("Max preflight failed: %s", exc)
        return False
