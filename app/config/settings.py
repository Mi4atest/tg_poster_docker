import os
import re
from typing import List, Optional
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables from .env file (override image ENV defaults)
load_dotenv(override=True)

# Base directory
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Telegram Bot settings
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_IDS = [int(user_id) for user_id in os.getenv("ALLOWED_USER_IDS", "").split(",") if user_id]
# Подмножество ALLOWED: обновление, prune, секреты. Пусто → все из ALLOWED (обратная совместимость).
_admin_ids_raw = os.getenv("ADMIN_USER_IDS", "").strip()
ADMIN_USER_IDS: List[int] = (
    [int(user_id) for user_id in _admin_ids_raw.split(",") if user_id]
    if _admin_ids_raw
    else list(ALLOWED_USER_IDS)
)

# VK API settings
VK_APP_ID = os.getenv("VK_APP_ID")
VK_APP_SECRET = os.getenv("VK_APP_SECRET")
VK_ACCESS_TOKEN = os.getenv("VK_ACCESS_TOKEN")
# User OAuth token for market.* (market.edit/get/add). Community key returns error 27.
VK_MARKET_ACCESS_TOKEN = (os.getenv("VK_MARKET_ACCESS_TOKEN") or "").strip()
VK_GROUP_ID = os.getenv("VK_GROUP_ID")
INSTAGRAM_GRAPH_ACCESS_TOKEN = os.getenv("INSTAGRAM_GRAPH_ACCESS_TOKEN", "")
INSTAGRAM_GRAPH_USER_ID = os.getenv("INSTAGRAM_GRAPH_USER_ID", "")
INSTAGRAM_GRAPH_API_VERSION = os.getenv("INSTAGRAM_GRAPH_API_VERSION", "v19.0")
INSTAGRAM_GRAPH_MEDIA_BASE_URL = os.getenv("INSTAGRAM_GRAPH_MEDIA_BASE_URL", "")
INSTAGRAM_GRAPH_TIMEOUT_SECONDS = int(os.getenv("INSTAGRAM_GRAPH_TIMEOUT_SECONDS", "60"))
INSTAGRAM_GRAPH_APP_ID = os.getenv("INSTAGRAM_GRAPH_APP_ID", "")
INSTAGRAM_GRAPH_APP_SECRET = os.getenv("INSTAGRAM_GRAPH_APP_SECRET", "")
INSTAGRAM_GRAPH_REFRESH_BEFORE_DAYS = int(os.getenv("INSTAGRAM_GRAPH_REFRESH_BEFORE_DAYS", "7"))
INSTAGRAM_GRAPH_TOKEN_DAILY_CHECK_INTERVAL_SECONDS = int(
    os.getenv("INSTAGRAM_GRAPH_TOKEN_DAILY_CHECK_INTERVAL_SECONDS", "86400")
)

# Telegram Channel settings
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
MAX_BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
MAX_CHANNEL_ID = os.getenv("MAX_CHANNEL_ID")
MAX_API_BASE_URL = os.getenv("MAX_API_BASE_URL", "https://botapi.max.ru")
# Fallback, если в ответе API нет публичного url (как в приложении: https://max.ru/c/<chat>/<id>)
MAX_SHARE_FALLBACK_PREFIX = os.getenv("MAX_SHARE_FALLBACK_PREFIX", "https://max.ru/c").strip() or "https://max.ru/c"
# ID сообщений в канале для списка наличия (редактируются, не создаются новые). Пример: "100" или "100,101,102"
_avail_ids_str = os.getenv("AVAILABILITY_MESSAGE_IDS", "").strip()
AVAILABILITY_MESSAGE_IDS: List[int] = []
if _avail_ids_str:
    for part in _avail_ids_str.replace(" ", "").split(","):
        try:
            AVAILABILITY_MESSAGE_IDS.append(int(part))
        except ValueError:
            pass
# Редактировать подписи к фото/медиа (True), а не текст сообщений. Для медиагрупп укажите id первого сообщения каждой группы (95, 101, 107…).
AVAILABILITY_USE_CAPTION = os.getenv("AVAILABILITY_USE_CAPTION", "").strip().lower() in ("1", "true", "yes")
# ID сообщений в канале для списка б/у товаров (редактируются, новые не создаются). Пример: 11728,11729,11730,11731
_used_list_ids_str = os.getenv("USED_PRODUCTS_LIST_MESSAGE_IDS", "").strip()
USED_PRODUCTS_LIST_MESSAGE_IDS: List[int] = []
if _used_list_ids_str:
    for part in _used_list_ids_str.replace(" ", "").split(","):
        try:
            USED_PRODUCTS_LIST_MESSAGE_IDS.append(int(part))
        except ValueError:
            pass
_max_used_list_ids_str = os.getenv("MAX_USED_PRODUCTS_LIST_MESSAGE_IDS", "").strip()
MAX_USED_PRODUCTS_LIST_MESSAGE_IDS: List[str] = []
if _max_used_list_ids_str:
    for part in _max_used_list_ids_str.replace(" ", "").split(","):
        if part:
            MAX_USED_PRODUCTS_LIST_MESSAGE_IDS.append(part)
TELEGRAM_CONTACT_USER_ID = os.getenv("TELEGRAM_CONTACT_USER_ID")
if TELEGRAM_CONTACT_USER_ID:
    try:
        TELEGRAM_CONTACT_USER_ID = int(TELEGRAM_CONTACT_USER_ID)
    except ValueError:
        TELEGRAM_CONTACT_USER_ID = None
else:
    TELEGRAM_CONTACT_USER_ID = None

TELEGRAM_CONTACT_USERNAME = os.getenv("TELEGRAM_CONTACT_USERNAME", "").strip().lstrip("@")
TELEGRAM_CONTACT_PHONE = os.getenv("TELEGRAM_CONTACT_PHONE", "")

# Database settings
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")

# Security settings (Fernet для секретов в БД; JWT не используется)
MASTER_KEY = os.getenv("MASTER_KEY", "")

# API settings
API_HOST = os.getenv("API_HOST", "localhost")
API_PORT = int(os.getenv("API_PORT", "8002"))

# Media storage settings
MEDIA_DIR = BASE_DIR / "media"
MEDIA_STRUCTURE = "{year}/{month}/{day}/{post_name}"

# Ensure media directory exists
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

# Логи приложения (в Docker: /app/app/logs относительно образа)
APP_LOG_DIR = BASE_DIR / "app" / "logs"
APP_LOG_DIR.mkdir(parents=True, exist_ok=True)

# Авито: fallback category_id для смартфонов/телефонов, если в БД не задан integrations.avito_category_id.
# ID берите из дерева категорий вашего приложения Авито (Swagger / кабинет разработчика). Подставляется
# только если текст поста по эвристике похож на телефон (iPhone, смартфон, Galaxy и т.д.).
_s_avito_phone_cat = os.getenv("AVITO_DEFAULT_CATEGORY_SMARTPHONES", "").strip()
AVITO_DEFAULT_CATEGORY_SMARTPHONES: Optional[int] = None
if _s_avito_phone_cat:
    try:
        _v_avito_cat = int(_s_avito_phone_cat)
        AVITO_DEFAULT_CATEGORY_SMARTPHONES = _v_avito_cat if _v_avito_cat > 0 else None
    except ValueError:
        AVITO_DEFAULT_CATEGORY_SMARTPHONES = None

# Публичный URL для фида и фото в автозагрузке (HTTPS).
AVITO_FEED_PUBLIC_BASE_URL = (os.getenv("AVITO_FEED_PUBLIC_BASE_URL") or "https://appleshop.ap43.ru").strip().rstrip("/")
AVITO_AUTOLOAD_ADDRESS = (os.getenv("AVITO_AUTOLOAD_ADDRESS") or "Киров").strip()
_avito_phone = (os.getenv("AVITO_AUTOLOAD_CONTACT_PHONE") or os.getenv("TELEGRAM_CONTACT_PHONE") or "").strip()
AVITO_AUTOLOAD_CONTACT_PHONE = re.sub(r"\D", "", _avito_phone) if _avito_phone else ""
# Пауза после последнего поста в очереди Авито перед одной выгрузкой (сек)
AVITO_AUTOLOAD_BATCH_QUIET_SEC = int(os.getenv("AVITO_AUTOLOAD_BATCH_QUIET_SEC", "120"))
# Минимум между POST /autoload/v1/upload (сек; лимит API ~1 раз в час)
AVITO_AUTOLOAD_MIN_UPLOAD_INTERVAL_SEC = int(
    os.getenv("AVITO_AUTOLOAD_MIN_UPLOAD_INTERVAL_SEC", "3600")
)
AVITO_AUTOLOAD_MAX_ADS_PER_BATCH = int(os.getenv("AVITO_AUTOLOAD_MAX_ADS_PER_BATCH", "30"))
# True: файл на Авито только по кнопке «Отправить файл» в меню очереди (1 раз/час)
AVITO_MANUAL_FEED_UPLOAD = os.getenv("AVITO_MANUAL_FEED_UPLOAD", "true").lower() in (
    "1",
    "true",
    "yes",
)
# Цена просмотра (CPA): Promo=Manual, PromoManualOptions=|цена| — без региона, без лимита в день.
# Для б/у в Кирове минимум ~1.2 ₽: |1.2|  (лимит не указываем — по правилам Авито на минимуме он не действует).
AVITO_PROMO_ENABLED = os.getenv("AVITO_PROMO_ENABLED", "true").lower() in ("1", "true", "yes")
AVITO_PROMO_MANUAL_OPTIONS = (os.getenv("AVITO_PROMO_MANUAL_OPTIONS") or "|1.2|").strip()

# Оценка рынка по одной публичной странице Avito. Ограничения намеренно консервативны:
# защита Avito не обходится, а повторные одинаковые запросы обслуживаются из БД.
AVITO_MARKET_ENABLED = os.getenv("AVITO_MARKET_ENABLED", "true").lower() in ("1", "true", "yes")
AVITO_MARKET_REGION = (os.getenv("AVITO_MARKET_REGION") or "Россия").strip()
# Публичные идентификаторы: вся Россия + «Смартфоны» (для рыночной вилки шире города).
AVITO_MARKET_LOCATION_ID = int(os.getenv("AVITO_MARKET_LOCATION_ID", "660311"))
AVITO_MARKET_CATEGORY_ID = int(os.getenv("AVITO_MARKET_CATEGORY_ID", "84"))
AVITO_MARKET_CACHE_TTL_SEC = int(os.getenv("AVITO_MARKET_CACHE_TTL_SEC", "43200"))
# Между живыми запросами к Avito: 6 мин.
# По runtime: ~4 мин → 439, серии с интервалом 6:16–6:20 → HTTP 200.
AVITO_MARKET_MIN_REQUEST_INTERVAL_SEC = int(
    os.getenv("AVITO_MARKET_MIN_REQUEST_INTERVAL_SEC", "360")
)
AVITO_MARKET_DAILY_REQUEST_LIMIT = int(os.getenv("AVITO_MARKET_DAILY_REQUEST_LIMIT", "40"))
# После HTTP 439 — общая пауза на все модели (не жечь новый cookie сразу).
AVITO_MARKET_BLOCK_COOLDOWN_SEC = int(os.getenv("AVITO_MARKET_BLOCK_COOLDOWN_SEC", "3600"))
AVITO_MARKET_TIMEOUT_SEC = int(os.getenv("AVITO_MARKET_TIMEOUT_SEC", "20"))
# «Надёжная» выборка; ниже — мягкий ориентир (см. SOFT).
AVITO_MARKET_MIN_SAMPLE_SIZE = int(os.getenv("AVITO_MARKET_MIN_SAMPLE_SIZE", "10"))
AVITO_MARKET_SOFT_SAMPLE_SIZE = int(os.getenv("AVITO_MARKET_SOFT_SAMPLE_SIZE", "3"))
AVITO_MARKET_MIN_SELLER_GROUP_SIZE = int(
    os.getenv("AVITO_MARKET_MIN_SELLER_GROUP_SIZE", "5")
)
# Watchlist: тик worker, пауза между живыми запросами, интервалы tier.
AVITO_MARKET_WL_TICK_SEC = int(os.getenv("AVITO_MARKET_WL_TICK_SEC", "60"))
AVITO_MARKET_WL_LIVE_INTERVAL_SEC = int(
    os.getenv("AVITO_MARKET_WL_LIVE_INTERVAL_SEC", "1800")
)
AVITO_MARKET_WL_TIER_DAILY_SEC = int(
    os.getenv("AVITO_MARKET_WL_TIER_DAILY_SEC", "86400")
)
AVITO_MARKET_WL_TIER_SLOW_SEC = int(
    os.getenv("AVITO_MARKET_WL_TIER_SLOW_SEC", "259200")
)
AVITO_MARKET_WL_BLOCK_PAUSE_SEC = int(
    os.getenv("AVITO_MARKET_WL_BLOCK_PAUSE_SEC", "7200")
)
# SPFA / прокси для оценки рынка: основной источник — Настройки бота (БД).
# Переменные ниже — только одноразовый bootstrap в encrypted_secrets при старте.
SPFA_API_KEY = (os.getenv("SPFA_API_KEY") or "").strip()
AVITO_MARKET_PROXY = (os.getenv("AVITO_MARKET_PROXY") or "").strip()
MOBILEPROXY_API_TOKEN = (os.getenv("MOBILEPROXY_API_TOKEN") or "").strip()
# Пакет residential: 1 ГБ на 3 месяца (если кабинет не отдал остаток явно).
AVITO_MARKET_RESIDENTIAL_PACKAGE_MB = int(
    os.getenv("AVITO_MARKET_RESIDENTIAL_PACKAGE_MB", "1024")
)
AVITO_MARKET_RESIDENTIAL_PACKAGE_DAYS = int(
    os.getenv("AVITO_MARKET_RESIDENTIAL_PACKAGE_DAYS", "90")
)
AVITO_MARKET_USE_SPFA = os.getenv("AVITO_MARKET_USE_SPFA", "true").lower() in (
    "1",
    "true",
    "yes",
)

# Настройки подписей для социальных сетей
SIGNATURE_ENABLED = os.getenv("SIGNATURE_ENABLED", "true").lower() == "true"
SIGNATURE_VK = os.getenv("SIGNATURE_VK", "")
SIGNATURE_AVITO = os.getenv("SIGNATURE_AVITO", "")
SIGNATURE_TELEGRAM = os.getenv("SIGNATURE_TELEGRAM", "")
SIGNATURE_INSTAGRAM = os.getenv("SIGNATURE_INSTAGRAM", "")
SIGNATURE_VK_SHORT_AVITO = os.getenv("SIGNATURE_VK_SHORT_AVITO", "")
SIGNATURE_VK_SHORT_TELEGRAM = os.getenv("SIGNATURE_VK_SHORT_TELEGRAM", "")
SIGNATURE_PHONE = os.getenv("SIGNATURE_PHONE", "")

# Блок «Каталог б/у» в постах (единый переключатель)
TELEGRAM_USED_CATALOG_BUTTON_ENABLED = os.getenv("TELEGRAM_USED_CATALOG_BUTTON_ENABLED", "true").lower() == "true"
TELEGRAM_USED_CATALOG_URL = (os.getenv("TELEGRAM_USED_CATALOG_URL") or "https://t.me/AppleShop43/12185").strip()
VK_USED_CATALOG_URL = (os.getenv("VK_USED_CATALOG_URL") or "").strip()
MAX_USED_CATALOG_URL = (os.getenv("MAX_USED_CATALOG_URL") or "").strip()

# VK upload: неполная публикация медиа запрещена по умолчанию.
# Аварийный откат: VK_UPLOAD_STRICT_MODE=false — опубликовать то, что удалось загрузить.
VK_UPLOAD_STRICT_MODE = os.getenv("VK_UPLOAD_STRICT_MODE", "true").lower() in ("1", "true", "yes")

# VK Market settings
VK_MARKET_ENABLED = os.getenv("VK_MARKET_ENABLED", "true").lower() == "true"
VK_MARKET_AUTO_CATEGORY = os.getenv("VK_MARKET_AUTO_CATEGORY", "true").lower() == "true"
VK_MARKET_AUTO_COLLECTION = os.getenv("VK_MARKET_AUTO_COLLECTION", "true").lower() == "true"

# Прикреплять карточку товара (market-вложение) к посту в ленте VK — даёт кнопку
# «Смотреть товары». Флаг отката: при false пост публикуется без market-вложения.
VK_WALL_ATTACH_MARKET = os.getenv("VK_WALL_ATTACH_MARKET", "true").lower() in ("1", "true", "yes")

# Если Товары ВК включены: при true сбой Market блокирует и ленту.
# Аварийный откат: VK_WALL_REQUIRES_MARKET=false — лента уйдёт без товара.
VK_WALL_REQUIRES_MARKET = os.getenv("VK_WALL_REQUIRES_MARKET", "true").lower() in (
    "1",
    "true",
    "yes",
)

# VK Report settings (для отправки отчетов о продажах)
VK_REPORT_USER_IDS = [int(user_id) for user_id in os.getenv("VK_REPORT_USER_IDS", "").split(",") if user_id]

# Backup settings (fallback-слой; основное хранилище — БД/меню «Настройки → Бэкап»)
BACKUP_BOT_TOKEN = os.getenv("BACKUP_BOT_TOKEN", "")
BACKUP_CHAT_ID = os.getenv("BACKUP_CHAT_ID", "")
BACKUP_PROJECT_NAME = os.getenv("BACKUP_PROJECT_NAME", "")
BACKUP_MEDIA = os.getenv("BACKUP_MEDIA", "false").strip().lower() in ("1", "true", "yes")

# Застой по цене (б/у): бейдж на кнопке в архиве — товары без смены цены ≥ N дней
STALE_BADGE_MIN_DAYS = int(os.getenv("STALE_BADGE_MIN_DAYS", "60"))
