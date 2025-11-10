import os
from typing import Optional

# Получаем настройки из переменных окружения
SIGNATURE_ENABLED = os.getenv("SIGNATURE_ENABLED", "true").lower() == "true"
SIGNATURE_VK = os.getenv("SIGNATURE_VK", "")
SIGNATURE_AVITO = os.getenv("SIGNATURE_AVITO", "")
SIGNATURE_TELEGRAM = os.getenv("SIGNATURE_TELEGRAM", "")
SIGNATURE_INSTAGRAM = os.getenv("SIGNATURE_INSTAGRAM", "")
SIGNATURE_VK_SHORT_AVITO = os.getenv("SIGNATURE_VK_SHORT_AVITO", "")
SIGNATURE_VK_SHORT_TELEGRAM = os.getenv("SIGNATURE_VK_SHORT_TELEGRAM", "")

def get_telegram_signature() -> str:
    """
    Возвращает подпись для постов в Telegram.
    
    Returns:
        str: Подпись с эмодзи и гиперссылками
    """
    if not SIGNATURE_ENABLED:
        return ""
    
    signature = "\n\n📱 Подписывайся:\n"
    
    if SIGNATURE_VK:
        signature += "📘 [ВКонтакте]({0})\n".format(SIGNATURE_VK)
    
    if SIGNATURE_AVITO:
        signature += "🛒 [Авито]({0})\n".format(SIGNATURE_AVITO)
    
    if SIGNATURE_TELEGRAM:
        signature += "✈️ [Телеграм]({0})\n".format(SIGNATURE_TELEGRAM)
    
    if SIGNATURE_INSTAGRAM:
        signature += "📷 [Инстаграм]({0})".format(SIGNATURE_INSTAGRAM)
    
    return signature

def get_vk_signature() -> str:
    """
    Возвращает подпись для постов в ВКонтакте.
    
    Returns:
        str: Подпись с прямыми ссылками
    """
    if not SIGNATURE_ENABLED:
        return ""
    
    signature = "\n\n📱 Подписывайся:\n"
    
    if SIGNATURE_VK_SHORT_AVITO:
        signature += "🛒 Авито: {0}\n".format(SIGNATURE_VK_SHORT_AVITO)
    
    if SIGNATURE_VK_SHORT_TELEGRAM:
        signature += "✈️ Телеграм: {0}".format(SIGNATURE_VK_SHORT_TELEGRAM)
    
    return signature

def get_instagram_signature() -> str:
    """
    Возвращает подпись для постов в Instagram.
    
    Returns:
        str: Простая подпись без ссылок
    """
    if not SIGNATURE_ENABLED:
        return ""
    
    signature = "\n\n📱 Подписывайся:\n"
    
    if SIGNATURE_VK:
        signature += "📘 ВКонтакте: {0}\n".format(SIGNATURE_VK)
    
    if SIGNATURE_TELEGRAM:
        signature += "✈️ Телеграм: {0}".format(SIGNATURE_TELEGRAM)
    
    return signature