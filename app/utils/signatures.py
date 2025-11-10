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
SIGNATURE_PHONE = os.getenv("SIGNATURE_PHONE", "")

# Настройки для контактных ссылок
TELEGRAM_CONTACT_USERNAME = os.getenv("TELEGRAM_CONTACT_USERNAME", "").strip().lstrip("@")
TELEGRAM_CONTACT_USER_ID = os.getenv("TELEGRAM_CONTACT_USER_ID", "")
if TELEGRAM_CONTACT_USER_ID:
    try:
        TELEGRAM_CONTACT_USER_ID = int(TELEGRAM_CONTACT_USER_ID)
    except ValueError:
        TELEGRAM_CONTACT_USER_ID = None
else:
    TELEGRAM_CONTACT_USER_ID = None

def get_telegram_signature(enabled: Optional[bool] = None, phone: Optional[str] = None) -> str:
    """
    Возвращает подпись для постов в Telegram.
    
    Args:
        enabled: Включена ли подпись (если None, используется значение из .env)
        phone: Номер телефона для добавления в подпись (если None, используется значение из .env)
    
    Returns:
        str: Подпись с эмодзи и гиперссылками
    """
    if enabled is None:
        enabled = SIGNATURE_ENABLED
    
    if not enabled:
        return ""
    
    if phone is None:
        phone = SIGNATURE_PHONE
    
    signature = "\n\n"
    
    # Добавляем контактные ссылки (две строки: Купить с ссылкой, номер телефона без ссылки)
    
    # Ссылка на чат с менеджером (используем https://t.me/ для прямого открытия чата)
    if TELEGRAM_CONTACT_USERNAME:
        manager_url = f"https://t.me/{TELEGRAM_CONTACT_USERNAME}"
        signature += f"[🛍️ Купить]({manager_url})\n"
    elif TELEGRAM_CONTACT_USER_ID:
        manager_url = f"tg://user?id={TELEGRAM_CONTACT_USER_ID}"
        signature += f"[🛍️ Купить]({manager_url})\n"
    
    # Номер телефона в чистом виде (без ссылки, Telegram автоматически сделает его кликабельным)
    if phone:
        phone_cleaned = phone.strip()
        # Убираем префикс tel: если есть
        if phone_cleaned.lower().startswith("tel:"):
            phone_cleaned = phone_cleaned[4:]
        phone_cleaned = phone_cleaned.lstrip("/")
        if phone_cleaned:
            signature += f"📞 {phone_cleaned}\n"
    
    signature += "\n"
    
    signature += "📱 Подписывайся:\n"
    
    if SIGNATURE_VK:
        signature += "📘 [ВКонтакте]({0})\n".format(SIGNATURE_VK)
    
    if SIGNATURE_AVITO:
        signature += "🛒 [Авито]({0})\n".format(SIGNATURE_AVITO)
    
    if SIGNATURE_TELEGRAM:
        signature += "✈️ [Телеграм]({0})\n".format(SIGNATURE_TELEGRAM)
    
    if SIGNATURE_INSTAGRAM:
        signature += "📷 [Инстаграм]({0})".format(SIGNATURE_INSTAGRAM)
    
    return signature

def get_vk_signature(enabled: Optional[bool] = None, phone: Optional[str] = None) -> str:
    """
    Возвращает подпись для постов в ВКонтакте.
    
    Args:
        enabled: Включена ли подпись (если None, используется значение из .env)
        phone: Номер телефона для добавления в подпись (если None, используется значение из .env)
    
    Returns:
        str: Подпись с прямыми ссылками
    """
    if enabled is None:
        enabled = SIGNATURE_ENABLED
    
    if not enabled:
        return ""
    
    if phone is None:
        phone = SIGNATURE_PHONE
    
    signature = "\n\n"
    
    # Добавляем номер телефона в начало, если указан
    if phone:
        phone_cleaned = phone.strip()
        # Убираем префикс tel: если есть
        if phone_cleaned.lower().startswith("tel:"):
            phone_cleaned = phone_cleaned[4:]
        phone_cleaned = phone_cleaned.lstrip("/")
        if phone_cleaned:
            signature += f"📞 {phone_cleaned}\n\n"
    
    signature += "📱 Подписывайся:\n"
    
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