import os
from typing import Optional
from app.services.settings_service import get_settings_service
from app.utils.telegram_post_markup import is_valid_catalog_button_url

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

def _read_runtime_settings():
    try:
        cfg = get_settings_service().get_all()
        return cfg.get("signatures", {}), cfg.get("contacts", {})
    except Exception:
        return {}, {}


def get_telegram_used_catalog_quote(enabled: Optional[bool] = None) -> str:
    """Возвращает Telegram MarkdownV2-цитату с CTA на каталог б/у.

    Блок включается только если:
    - флаг telegram_used_catalog_button_enabled = True
    - URL каталога задан и валиден (http/https)
    """
    signatures, _ = _read_runtime_settings()
    if enabled is None:
        enabled = bool(signatures.get("telegram_used_catalog_button_enabled", True))
    if not enabled:
        return ""

    catalog_url = str(signatures.get("telegram_used_catalog_url") or "").strip()
    if not is_valid_catalog_button_url(catalog_url):
        return ""

    return (
        "\n> *🔄 Не подошла эта модель?*\n"
        f"> Удобный выбор товаров в [нашем каталоге]({catalog_url})!\n"
    )


def get_telegram_signature(enabled: Optional[bool] = None, phone: Optional[str] = None) -> str:
    """
    Возвращает подпись для постов в Telegram.
    
    Args:
        enabled: Включена ли подпись (если None, используется значение из .env)
        phone: Номер телефона для добавления в подпись (если None, используется значение из .env)
    
    Returns:
        str: Подпись с эмодзи и гиперссылками
    """
    signatures, contacts = _read_runtime_settings()

    if enabled is None:
        enabled = signatures.get("enabled", SIGNATURE_ENABLED)
    
    if not enabled:
        return ""
    
    if phone is None:
        phone = signatures.get("phone") or SIGNATURE_PHONE
    
    signature = "\n\n"
    
    # Добавляем контактные ссылки (две строки: Купить с ссылкой, номер телефона без ссылки)
    
    # Ссылка на чат с менеджером (используем https://t.me/ для прямого открытия чата)
    tg_username = contacts.get("telegram_username") or TELEGRAM_CONTACT_USERNAME
    tg_user_id = contacts.get("telegram_user_id") or TELEGRAM_CONTACT_USER_ID
    if tg_username:
        manager_url = f"https://t.me/{tg_username}"
        signature += f"[🛍️ Купить]({manager_url})\n"
    elif tg_user_id:
        manager_url = f"tg://user?id={tg_user_id}"
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
    
    if signatures.get("vk") or SIGNATURE_VK:
        signature += "📘 [ВКонтакте]({0})\n".format(signatures.get("vk") or SIGNATURE_VK)
    
    if signatures.get("avito") or SIGNATURE_AVITO:
        signature += "🛒 [Авито]({0})\n".format(signatures.get("avito") or SIGNATURE_AVITO)
    
    if signatures.get("telegram") or SIGNATURE_TELEGRAM:
        signature += "✈️ [Телеграм]({0})\n".format(signatures.get("telegram") or SIGNATURE_TELEGRAM)
    
    if signatures.get("instagram") or SIGNATURE_INSTAGRAM:
        signature += "📷 [Инстаграм]({0})".format(signatures.get("instagram") or SIGNATURE_INSTAGRAM)
    
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
    signatures, _ = _read_runtime_settings()
    if enabled is None:
        enabled = signatures.get("enabled", SIGNATURE_ENABLED)
    
    if not enabled:
        return ""
    
    if phone is None:
        phone = signatures.get("phone") or SIGNATURE_PHONE
    
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
    
    if signatures.get("vk_short_avito") or SIGNATURE_VK_SHORT_AVITO:
        signature += "🛒 Авито: {0}\n".format(signatures.get("vk_short_avito") or SIGNATURE_VK_SHORT_AVITO)
    
    if signatures.get("vk_short_telegram") or SIGNATURE_VK_SHORT_TELEGRAM:
        signature += "✈️ Телеграм: {0}".format(signatures.get("vk_short_telegram") or SIGNATURE_VK_SHORT_TELEGRAM)
    
    return signature

def get_instagram_signature() -> str:
    """
    Возвращает подпись для постов в Instagram.
    
    Returns:
        str: Простая подпись без ссылок
    """
    signatures, _ = _read_runtime_settings()
    if not signatures.get("enabled", SIGNATURE_ENABLED):
        return ""
    
    signature = "\n\n📱 Подписывайся:\n"
    
    if signatures.get("vk") or SIGNATURE_VK:
        signature += "📘 ВКонтакте: {0}\n".format(signatures.get("vk") or SIGNATURE_VK)
    
    if signatures.get("telegram") or SIGNATURE_TELEGRAM:
        signature += "✈️ Телеграм: {0}".format(signatures.get("telegram") or SIGNATURE_TELEGRAM)
    
    return signature