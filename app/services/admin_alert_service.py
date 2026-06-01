import logging
from typing import List

from aiogram import Bot

from app.config import settings as env_settings
from app.services.settings_service import get_settings_service

logger = logging.getLogger(__name__)


def _resolve_alert_chat_ids() -> List[int]:
    settings_data = get_settings_service().get_all()
    contacts = settings_data.get("contacts", {})
    chat_ids: List[int] = []

    contact_id = contacts.get("telegram_user_id") or env_settings.TELEGRAM_CONTACT_USER_ID
    if contact_id:
        try:
            chat_ids.append(int(contact_id))
        except (TypeError, ValueError):
            pass

    for user_id in env_settings.ALLOWED_USER_IDS:
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            continue
        if uid not in chat_ids:
            chat_ids.append(uid)
    return chat_ids


async def send_admin_alert(text: str) -> None:
    token = env_settings.TELEGRAM_BOT_TOKEN
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN не задан, пропускаем admin alert")
        return

    chat_ids = _resolve_alert_chat_ids()
    if not chat_ids:
        logger.warning("Не найдено chat_id для admin alert")
        return

    bot = Bot(token=token)
    try:
        for chat_id in chat_ids:
            try:
                await bot.send_message(chat_id=chat_id, text=text)
            except Exception as exc:
                logger.error("Не удалось отправить alert в chat_id=%s: %s", chat_id, exc)
    finally:
        await bot.session.close()
