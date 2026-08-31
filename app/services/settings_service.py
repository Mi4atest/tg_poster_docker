import logging
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, Optional

from app.api.models.post import AppSettings
from app.config import settings as env_settings
from app.db.database import SessionLocal
from app.utils.security import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)

# Подсказки в тексте поста для fallback-категории «мобильные телефоны» (см. AVITO_DEFAULT_CATEGORY_SMARTPHONES).
_AVITO_PHONE_TEXT_HINTS = (
    "iphone",
    "айфон",
    "смартфон",
    "smartphone",
    "телефон",
    "galaxy",
    "pixel",
    "xiaomi",
    "samsung",
    "huawei",
    "honor",
    "realme",
    "oppo",
    "vivo",
    "oneplus",
    "мобильн",
    "android",
    "андроид",
)


DEFAULT_SETTINGS: Dict[str, Any] = {
    "contacts": {
        "telegram_username": env_settings.TELEGRAM_CONTACT_USERNAME or "",
        "telegram_user_id": env_settings.TELEGRAM_CONTACT_USER_ID,
        "phone": env_settings.TELEGRAM_CONTACT_PHONE or "",
    },
    "signatures": {
        "enabled": env_settings.SIGNATURE_ENABLED,
        "vk": env_settings.SIGNATURE_VK or "",
        "avito": env_settings.SIGNATURE_AVITO or "",
        "telegram": env_settings.SIGNATURE_TELEGRAM or "",
        "instagram": env_settings.SIGNATURE_INSTAGRAM or "",
        "vk_short_avito": env_settings.SIGNATURE_VK_SHORT_AVITO or "",
        "vk_short_telegram": env_settings.SIGNATURE_VK_SHORT_TELEGRAM or "",
        "phone": env_settings.SIGNATURE_PHONE or "",
        "telegram_used_catalog_button_enabled": env_settings.TELEGRAM_USED_CATALOG_BUTTON_ENABLED,
        "telegram_used_catalog_url": env_settings.TELEGRAM_USED_CATALOG_URL or "https://t.me/AppleShop43/12185",
        "vk_used_catalog_url": env_settings.VK_USED_CATALOG_URL or "",
        "max_used_catalog_url": env_settings.MAX_USED_CATALOG_URL or "",
    },
    "publishing": {
        "interval_minutes": {
            "vk": 3,
            "telegram": 3,
            "instagram": 30,
            "max": 3,
            "avito": 60,
        },
        "enabled": {
            "vk": True,
            "telegram": True,
            "instagram": True,
            "max": True,
            "avito": True,
        },
        # Сохраняется в БД: переживает перезапуск бота (в отличие от in-memory флага оркестратора).
        "global_pause": False,
        "platform_pause": {
            "vk": False,
            "telegram": False,
            "instagram": False,
            "max": False,
            "avito": False,
        },
    },
    "features": {
        "vk_market_enabled": env_settings.VK_MARKET_ENABLED,
        # Автопубликация сторис ВК после успешного wall.post (независимо от Товаров ВК).
        "vk_stories_auto_enabled": False,
        # Визуал сторис: bubble (крупный бабл) | social (компактный IG-like).
        "vk_stories_style": "bubble",
        # Default из .env при первом сиде; дальше — тумблеры в меню Настройки → VK.
        "vk_upload_strict_mode": bool(
            getattr(env_settings, "VK_UPLOAD_STRICT_MODE", True)
        ),
        "vk_wall_requires_market": bool(
            getattr(env_settings, "VK_WALL_REQUIRES_MARKET", True)
        ),
    },
    "channels": {
        "telegram_channel_id": env_settings.TELEGRAM_CHANNEL_ID or "",
        "max_channel_id": env_settings.MAX_CHANNEL_ID or "",
    },
    "reports": {
        "vk_report_user_ids": list(env_settings.VK_REPORT_USER_IDS or []),
        "availability_message_ids": list(env_settings.AVAILABILITY_MESSAGE_IDS or []),
        "used_products_list_message_ids": list(env_settings.USED_PRODUCTS_LIST_MESSAGE_IDS or []),
        "max_used_products_list_message_ids": list(
            getattr(env_settings, "MAX_USED_PRODUCTS_LIST_MESSAGE_IDS", None) or []
        ),
    },
    # Прайс в VK-канале (edit одного/нескольких сообщений по шаблону слотов).
    "vk_channel_price": {
        "peer_id": None,
        "message_cmids": [],
        "marker_in_stock": "●",
        "marker_on_order": "○",
        "links_enabled": True,
        "template": None,
    },
    "backup": {
        # enabled управляет автоматическим бэкапом внутри приложения (без хостового cron)
        "enabled": bool(getattr(env_settings, "BACKUP_BOT_TOKEN", "")) and bool(getattr(env_settings, "BACKUP_CHAT_ID", "")),
        "chat_id": getattr(env_settings, "BACKUP_CHAT_ID", "") or "",
        "project_name": getattr(env_settings, "BACKUP_PROJECT_NAME", "") or "tg_poster",
        "media": bool(getattr(env_settings, "BACKUP_MEDIA", False)),
        "hour": 3,
        "minute": 0,
        "keep_days": 30,
    },
    "new_menu_constructor": {
        "hidden_keys": [],
        "label_overrides": {},
    },
    "price_tags": {
        "strike_markup_percent": 5,
        "default_subtitle": "",
        "default_descriptions": {
            "iPhone новые": (
                "Товар бывший в употреблении, оригинал, комплект полный, "
                "не активирован, гарантия 14 дней"
            ),
            "iPad": (
                "Товар бывший в употреблении, оригинал, комплект полный, "
                "не активирован, гарантия 14 дней"
            ),
            "Airpods": "",
            "Apple Watch": "",
            "custom": "",
        },
        "fixed_footer_text": (
            "Товар бывший в употреблении, оригинал, комплект полный, "
            "не активирован, без RuStore, гарантия 14 дней"
        ),
    },
    "integrations": {
        "vk_group_id": str(getattr(env_settings, "VK_GROUP_ID", "") or ""),
        "instagram_graph_access_token": "",
        "instagram_graph_app_id": getattr(env_settings, "INSTAGRAM_GRAPH_APP_ID", "") or "",
        "instagram_graph_app_secret": getattr(env_settings, "INSTAGRAM_GRAPH_APP_SECRET", "") or "",
        "instagram_graph_user_id": getattr(env_settings, "INSTAGRAM_GRAPH_USER_ID", "") or "",
        "instagram_graph_media_base_url": getattr(env_settings, "INSTAGRAM_GRAPH_MEDIA_BASE_URL", "") or "",
        "instagram_graph_token_expires_at": "",
        "instagram_graph_token_last_check_at": "",
        "instagram_graph_token_last_error": "",
        "avito_client_id": "",
        "avito_user_id": "",
        # listing_kind: own | resale (товар приобретён на продажу)
        "avito_listing_kind": "resale",
        "avito_multi_listing": True,
        # delivery: avito_partners | seller | pickup_only (настройка «Авито доставка»)
        "avito_delivery_mode": "pickup_only",
        # Фаза B: числовой category_id в дереве Авито (не id ВК); location_id — при необходимости API
        "avito_category_id": None,
        "avito_location_id": None,
        "avito_auto_create_from_post": True,
        # Оценка рынка б/у iPhone по выдаче Avito (SPFA cookies + прокси).
        "avito_market_enabled": True,
        "avito_market_use_spfa": True,
        "avito_market_proxy_change_url": "",
        "avito_market_watchlist_enabled": True,
        "avito_market_watchlist_pause_until": "",
    },
}


ENV_SECRET_DEFAULTS: Dict[str, str] = {
    "telegram_bot_token": env_settings.TELEGRAM_BOT_TOKEN or "",
    "vk_access_token": env_settings.VK_ACCESS_TOKEN or "",
    "vk_market_access_token": getattr(env_settings, "VK_MARKET_ACCESS_TOKEN", "") or "",
    "vk_app_secret": getattr(env_settings, "VK_APP_SECRET", "") or "",
    "max_bot_token": env_settings.MAX_BOT_TOKEN or "",
    "instagram_graph_access_token": getattr(env_settings, "INSTAGRAM_GRAPH_ACCESS_TOKEN", "") or "",
    "instagram_graph_app_secret": getattr(env_settings, "INSTAGRAM_GRAPH_APP_SECRET", "") or "",
    "backup_bot_token": getattr(env_settings, "BACKUP_BOT_TOKEN", "") or "",
    "spfa_api_key": getattr(env_settings, "SPFA_API_KEY", "") or "",
    "avito_market_proxy": getattr(env_settings, "AVITO_MARKET_PROXY", "") or "",
    "mobileproxy_api_token": getattr(env_settings, "MOBILEPROXY_API_TOKEN", "") or "",
}


def _normalize_int_list(value) -> list:
    """Принимает list | "1,2,3" | "" → list[int] (мусор отбрасывается)."""
    result: list = []
    if value is None:
        return result
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = str(value).replace(" ", "").split(",")
    for item in items:
        if item in ("", None):
            continue
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def _normalize_str_list(value) -> list:
    """Принимает list | "a,b,c" | "" → list[str] без пустых."""
    result: list = []
    if value is None:
        return result
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = str(value).split(",")
    for item in items:
        s = str(item).strip() if item is not None else ""
        if s:
            result.append(s)
    return result


class SettingsService:
    def _ensure_row(self, db) -> AppSettings:
        row = db.query(AppSettings).filter(AppSettings.id == 1).first()
        if row:
            return row

        row = AppSettings(
            id=1,
            config=deepcopy(DEFAULT_SETTINGS),
            encrypted_secrets={},
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def _deep_merge(self, base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
        result = deepcopy(base)
        for key, value in (patch or {}).items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    def get_all(self) -> Dict[str, Any]:
        db = SessionLocal()
        try:
            row = self._ensure_row(db)
            return self._deep_merge(DEFAULT_SETTINGS, row.config or {})
        finally:
            db.close()

    def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        db = SessionLocal()
        try:
            row = self._ensure_row(db)
            current = self._deep_merge(DEFAULT_SETTINGS, row.config or {})
            row.config = self._deep_merge(current, patch)
            db.commit()
            db.refresh(row)
            return self._deep_merge(DEFAULT_SETTINGS, row.config or {})
        finally:
            db.close()

    def bootstrap_from_env_once(self) -> None:
        """Единоразовый перенос значений из «грязного» .env в БД.

        1) Секреты → encrypted_secrets (зашифрованы MASTER_KEY, иначе devplain).
        2) Конфиг (каналы, отчёты, бэкап, подписи, контакты) → config один раз,
           чтобы значения пережили последующую очистку .env.
        Идемпотентно: повторно ничего не перетирает (флаг config["_env_bootstrapped"]).
        """
        db = SessionLocal()
        try:
            row = self._ensure_row(db)
            changed = False

            secrets = dict(row.encrypted_secrets or {})
            for key, value in ENV_SECRET_DEFAULTS.items():
                if not value:
                    continue
                if secrets.get(key):
                    continue
                try:
                    secrets[key] = encrypt_secret(value)
                    changed = True
                except Exception as exc:
                    logger.warning("Could not encrypt bootstrap secret '%s': %s", key, exc)
            if changed:
                row.encrypted_secrets = secrets

            cfg = dict(row.config or {})
            if not cfg.get("_env_bootstrapped"):
                # DEFAULT_SETTINGS уже засеян значениями из .env (каналы/отчёты/бэкап/
                # подписи/контакты). Сливаем поверх пользовательские правки из БД и
                # фиксируем результат, чтобы он не зависел от наличия .env в дальнейшем.
                merged = self._deep_merge(DEFAULT_SETTINGS, cfg)
                merged["_env_bootstrapped"] = True
                row.config = merged
                cfg = merged
                changed = True
                logger.info("Конфиг из .env перенесён в БД (единоразовый bootstrap)")

            # vk_group_id раньше не входил в DEFAULT_SETTINGS — после очистки .env
            # публикация в VK падала, хотя токен уже был в БД.
            env_gid = str(getattr(env_settings, "VK_GROUP_ID", "") or "").strip()
            integ = dict((cfg.get("integrations") or {}))
            if env_gid and not str(integ.get("vk_group_id") or "").strip():
                integ["vk_group_id"] = env_gid
                cfg["integrations"] = integ
                row.config = cfg
                changed = True

            if changed:
                db.commit()
        finally:
            db.close()

    def set_secret(self, name: str, value: str) -> None:
        db = SessionLocal()
        try:
            row = self._ensure_row(db)
            secrets = dict(row.encrypted_secrets or {})
            secrets[name] = encrypt_secret(value) if value else ""
            row.encrypted_secrets = secrets
            db.commit()
        finally:
            db.close()

    def get_secret(self, name: str) -> str:
        db = SessionLocal()
        try:
            row = self._ensure_row(db)
            value = (row.encrypted_secrets or {}).get(name) or ""
            if not value:
                return ""
            return decrypt_secret(value)
        except Exception as exc:
            logger.warning("Could not decrypt secret '%s': %s", name, exc)
            return ""
        finally:
            db.close()

    def get_platform_interval_minutes(self, platform: str) -> int:
        data = self.get_all()
        interval = data["publishing"]["interval_minutes"].get(platform)
        return int(interval) if interval else DEFAULT_SETTINGS["publishing"]["interval_minutes"].get(platform, 3)

    def set_platform_interval_minutes(self, platform: str, minutes: int) -> None:
        self.update({"publishing": {"interval_minutes": {platform: int(minutes)}}})

    def is_platform_enabled(self, platform: str) -> bool:
        data = self.get_all()
        return bool(data["publishing"]["enabled"].get(platform, True))

    def set_platform_enabled(self, platform: str, enabled: bool) -> None:
        self.update({"publishing": {"enabled": {platform: bool(enabled)}}})

    _PUBLICATION_PLATFORMS = ("vk", "telegram", "instagram", "max", "avito")

    def is_global_publication_pause(self) -> bool:
        return bool(self.get_all().get("publishing", {}).get("global_pause", False))

    def set_global_publication_pause(self, paused: bool) -> None:
        self.update({"publishing": {"global_pause": bool(paused)}})

    def get_platform_publication_pauses(self) -> Dict[str, bool]:
        raw = self.get_all().get("publishing", {}).get("platform_pause") or {}
        return {p: bool(raw.get(p, False)) for p in self._PUBLICATION_PLATFORMS}

    def is_platform_publication_pause(self, platform: str) -> bool:
        return bool(self.get_platform_publication_pauses().get(platform, False))

    def set_platform_publication_pause(self, platform: str, paused: bool) -> None:
        if platform not in self._PUBLICATION_PLATFORMS:
            return
        self.update({"publishing": {"platform_pause": {platform: bool(paused)}}})

    def is_publishing_paused(self, platform: Optional[str] = None) -> bool:
        """Глобальная пауза или пауза конкретной платформы."""
        if self.is_global_publication_pause():
            return True
        if platform:
            return self.is_platform_publication_pause(platform)
        return False

    def is_signature_enabled(self) -> bool:
        return bool(self.get_all()["signatures"]["enabled"])

    def set_signature_enabled(self, enabled: bool) -> None:
        self.update({"signatures": {"enabled": bool(enabled)}})

    # --- Каналы (DB → .env fallback) ---
    def get_telegram_channel_id(self) -> str:
        v = str(self.get_all().get("channels", {}).get("telegram_channel_id") or "").strip()
        return v or (env_settings.TELEGRAM_CHANNEL_ID or "")

    def get_max_channel_id(self) -> str:
        v = str(self.get_all().get("channels", {}).get("max_channel_id") or "").strip()
        return v or (env_settings.MAX_CHANNEL_ID or "")

    # --- Отчёты и списки (DB → .env fallback) ---
    def get_vk_report_user_ids(self) -> list:
        ids = _normalize_int_list(self.get_all().get("reports", {}).get("vk_report_user_ids"))
        return ids or list(env_settings.VK_REPORT_USER_IDS or [])

    def get_availability_message_ids(self) -> list:
        ids = _normalize_int_list(self.get_all().get("reports", {}).get("availability_message_ids"))
        return ids or list(env_settings.AVAILABILITY_MESSAGE_IDS or [])

    def get_used_products_list_message_ids(self) -> list:
        ids = _normalize_int_list(self.get_all().get("reports", {}).get("used_products_list_message_ids"))
        return ids or list(env_settings.USED_PRODUCTS_LIST_MESSAGE_IDS or [])

    def get_max_used_products_list_message_ids(self) -> list:
        ids = _normalize_str_list(
            self.get_all().get("reports", {}).get("max_used_products_list_message_ids")
        )
        return ids or list(getattr(env_settings, "MAX_USED_PRODUCTS_LIST_MESSAGE_IDS", None) or [])

    def set_max_used_products_list_message_ids(self, ids: list) -> None:
        self.update({"reports": {"max_used_products_list_message_ids": _normalize_str_list(ids)}})

    def get_vk_channel_price_config(self) -> Dict[str, Any]:
        data = dict(self.get_all().get("vk_channel_price") or {})
        defaults = DEFAULT_SETTINGS["vk_channel_price"]
        peer_raw = data.get("peer_id", defaults.get("peer_id"))
        try:
            peer_id = int(peer_raw) if peer_raw is not None and str(peer_raw).strip() != "" else None
        except (TypeError, ValueError):
            peer_id = None
        cmids = _normalize_int_list(data.get("message_cmids"))
        marker_in = str(data.get("marker_in_stock") or defaults["marker_in_stock"]).strip() or "●"
        marker_on = str(data.get("marker_on_order") or defaults["marker_on_order"]).strip() or "○"
        # Ограничение длины маркера (emoji могут быть >1 codepoint)
        if len(marker_in) > 8:
            marker_in = marker_in[:8]
        if len(marker_on) > 8:
            marker_on = marker_on[:8]
        template = data.get("template")
        if template is not None and not isinstance(template, dict):
            template = None
        return {
            "peer_id": peer_id,
            "message_cmids": cmids,
            "marker_in_stock": marker_in,
            "marker_on_order": marker_on,
            "links_enabled": bool(data.get("links_enabled", defaults["links_enabled"])),
            "template": template,
        }

    def set_vk_channel_price_binding(self, peer_id: int, cmids: list) -> None:
        self.update(
            {
                "vk_channel_price": {
                    "peer_id": int(peer_id),
                    "message_cmids": [int(x) for x in cmids],
                }
            }
        )

    def clear_vk_channel_price_binding(self) -> None:
        self.update({"vk_channel_price": {"peer_id": None, "message_cmids": []}})

    def set_vk_channel_price_markers(self, in_stock: str, on_order: str) -> None:
        self.update(
            {
                "vk_channel_price": {
                    "marker_in_stock": (in_stock or "●").strip()[:8] or "●",
                    "marker_on_order": (on_order or "○").strip()[:8] or "○",
                }
            }
        )

    def set_vk_channel_price_template(self, template: Optional[Dict[str, Any]]) -> None:
        self.update({"vk_channel_price": {"template": template}})

    def set_vk_channel_price_links_enabled(self, enabled: bool) -> None:
        self.update({"vk_channel_price": {"links_enabled": bool(enabled)}})

    # --- Контакты (единый источник телефона) ---
    def get_contact_phone(self) -> str:
        v = str(self.get_all().get("contacts", {}).get("phone") or "").strip()
        return v or (env_settings.TELEGRAM_CONTACT_PHONE or env_settings.SIGNATURE_PHONE or "")

    # --- Бэкап (DB → .env fallback) ---
    def get_backup_config(self) -> Dict[str, Any]:
        data = self.get_all().get("backup", {}) or {}
        return {
            "enabled": bool(data.get("enabled", False)),
            "chat_id": str(data.get("chat_id") or getattr(env_settings, "BACKUP_CHAT_ID", "") or "").strip(),
            "project_name": str(data.get("project_name") or getattr(env_settings, "BACKUP_PROJECT_NAME", "") or "tg_poster").strip(),
            "media": bool(data.get("media", getattr(env_settings, "BACKUP_MEDIA", False))),
            "hour": int(data.get("hour", 3)),
            "minute": int(data.get("minute", 0)),
            "keep_days": int(data.get("keep_days", 30)),
        }

    def get_backup_bot_token(self) -> str:
        token = self.get_secret("backup_bot_token")
        return token or (getattr(env_settings, "BACKUP_BOT_TOKEN", "") or "")

    def is_vk_market_enabled(self) -> bool:
        return bool(self.get_all()["features"]["vk_market_enabled"])

    def set_vk_market_enabled(self, enabled: bool) -> None:
        self.update({"features": {"vk_market_enabled": bool(enabled)}})

    def is_vk_stories_auto_enabled(self) -> bool:
        """Автосторис (ВК после стены + IG после ленты). Ключ БД прежний для совместимости."""
        features = self.get_all().get("features", {})
        return bool(features.get("vk_stories_auto_enabled", False))

    def is_stories_auto_enabled(self) -> bool:
        """Алиас: автопубликация сторис (ВК + Instagram)."""
        return self.is_vk_stories_auto_enabled()

    def set_vk_stories_auto_enabled(self, enabled: bool) -> None:
        self.update({"features": {"vk_stories_auto_enabled": bool(enabled)}})

    def set_stories_auto_enabled(self, enabled: bool) -> None:
        self.set_vk_stories_auto_enabled(enabled)

    def get_vk_stories_style(self) -> str:
        from app.workers.vk.story_composer import normalize_story_style

        features = self.get_all().get("features", {})
        return normalize_story_style(features.get("vk_stories_style"))

    def set_vk_stories_style(self, style: str) -> str:
        from app.workers.vk.story_composer import normalize_story_style

        value = normalize_story_style(style)
        self.update({"features": {"vk_stories_style": value}})
        return value

    def get_stories_auto_mode(self) -> str:
        """Режим автосторис: off | social | bubble."""
        from app.workers.vk.story_composer import STORIES_MODE_OFF

        if not self.is_vk_stories_auto_enabled():
            return STORIES_MODE_OFF
        return self.get_vk_stories_style()

    def cycle_stories_auto_mode(self) -> str:
        """Цикл: выкл → компакт (social) → карточка (bubble) → выкл."""
        from app.workers.vk.story_composer import (
            STORIES_MODE_OFF,
            STORY_STYLE_BUBBLE,
            STORY_STYLE_SOCIAL,
        )

        current = self.get_stories_auto_mode()
        if current == STORIES_MODE_OFF:
            self.update(
                {
                    "features": {
                        "vk_stories_auto_enabled": True,
                        "vk_stories_style": STORY_STYLE_SOCIAL,
                    }
                }
            )
            return STORY_STYLE_SOCIAL
        if current == STORY_STYLE_SOCIAL:
            self.update(
                {
                    "features": {
                        "vk_stories_auto_enabled": True,
                        "vk_stories_style": STORY_STYLE_BUBBLE,
                    }
                }
            )
            return STORY_STYLE_BUBBLE
        self.set_vk_stories_auto_enabled(False)
        return STORIES_MODE_OFF

    def stories_mode_button_label(self) -> str:
        from app.workers.vk.story_composer import stories_mode_button_label

        return stories_mode_button_label(
            enabled=self.is_vk_stories_auto_enabled(),
            style=self.get_vk_stories_style(),
        )

    def cycle_vk_stories_style(self) -> str:
        """Только стиль при включённом авто (legacy). Предпочтительно cycle_stories_auto_mode."""
        from app.workers.vk.story_composer import STORY_STYLE_BUBBLE, STORY_STYLE_SOCIAL

        current = self.get_vk_stories_style()
        nxt = STORY_STYLE_SOCIAL if current == STORY_STYLE_BUBBLE else STORY_STYLE_BUBBLE
        return self.set_vk_stories_style(nxt)

    def is_vk_upload_strict_mode(self) -> bool:
        """Не публиковать в VK/Market, если не все фото/видео загрузились."""
        features = self.get_all().get("features", {})
        if "vk_upload_strict_mode" in features:
            return bool(features["vk_upload_strict_mode"])
        return bool(getattr(env_settings, "VK_UPLOAD_STRICT_MODE", True))

    def set_vk_upload_strict_mode(self, enabled: bool) -> None:
        self.update({"features": {"vk_upload_strict_mode": bool(enabled)}})

    def is_vk_wall_requires_market(self) -> bool:
        """При включённых Товарах ВК: сбой Market блокирует и ленту."""
        features = self.get_all().get("features", {})
        if "vk_wall_requires_market" in features:
            return bool(features["vk_wall_requires_market"])
        return bool(getattr(env_settings, "VK_WALL_REQUIRES_MARKET", True))

    def set_vk_wall_requires_market(self, enabled: bool) -> None:
        self.update({"features": {"vk_wall_requires_market": bool(enabled)}})

    def is_telegram_used_catalog_button_enabled(self) -> bool:
        return bool(self.get_all()["signatures"].get("telegram_used_catalog_button_enabled", True))

    def set_telegram_used_catalog_button_enabled(self, enabled: bool) -> None:
        self.update({"signatures": {"telegram_used_catalog_button_enabled": bool(enabled)}})

    def get_telegram_used_catalog_url(self) -> str:
        return str(self.get_all()["signatures"].get("telegram_used_catalog_url") or "").strip()

    def get_vk_used_catalog_url(self) -> str:
        from app.utils.vk_urls import rewrite_vk_com_to_ru

        raw = str(self.get_all()["signatures"].get("vk_used_catalog_url") or "").strip()
        return (rewrite_vk_com_to_ru(raw) or "").strip()

    def get_max_used_catalog_url(self) -> str:
        return str(self.get_all()["signatures"].get("max_used_catalog_url") or "").strip()

    def is_vk_market_publish_allowed(self) -> bool:
        """Публикация товара в VK Market: мастер-флаг VK_MARKET_ENABLED и переключатель в БД."""
        if not bool(getattr(env_settings, "VK_MARKET_ENABLED", False)):
            return False
        try:
            return bool(self.is_vk_market_enabled())
        except Exception:
            return True

    def is_avito_queue_allowed(self) -> bool:
        """Авито в связке с б/у (ТЗ): площадка Авито + «Товары ВК» / VK Market.

        Для массовых очередей и отложенной публикации «как товар» — без включённых
        товаров ВК Авито в общий набор не подмешивается.
        """

        if not self.is_platform_enabled("avito"):
            return False
        try:
            return bool(self.is_vk_market_publish_allowed())
        except Exception:
            return bool(self.is_vk_market_enabled())

    def is_avito_platform_only_enabled(self) -> bool:
        """Только площадка «Авито» в настройках (без требования «Товары ВК»).

        Нужна для явных действий вроде кнопки «в Авито» из архива / карточки поста.
        """
        try:
            return bool(self.is_platform_enabled("avito"))
        except Exception:
            return False

    def get_avito_category_id(self) -> Optional[int]:
        raw = self.get_all().get("integrations", {}).get("avito_category_id")
        if raw in (None, "", False):
            return None
        try:
            v = int(raw)
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None

    def resolved_avito_category_id_for_text(self, post_text: Optional[str]) -> Optional[int]:
        """Числовой category_id для API Авито: сначала из настроек, иначе эвристика по тексту + .env fallback."""
        explicit = self.get_avito_category_id()
        if explicit:
            return explicit
        fb = getattr(env_settings, "AVITO_DEFAULT_CATEGORY_SMARTPHONES", None)
        if fb is None or not (post_text or "").strip():
            return None
        low = post_text.lower()
        if any(h in low for h in _AVITO_PHONE_TEXT_HINTS):
            try:
                v = int(fb)
                return v if v > 0 else None
            except (TypeError, ValueError):
                return None
        return None

    def get_avito_location_id(self) -> Optional[int]:
        raw = self.get_all().get("integrations", {}).get("avito_location_id")
        if raw in (None, "", False):
            return None
        try:
            v = int(raw)
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None

    def is_avito_auto_create_from_post_enabled(self) -> bool:
        return bool(self.get_all().get("integrations", {}).get("avito_auto_create_from_post", True))

    def is_avito_market_enabled(self) -> bool:
        return bool(self.get_all().get("integrations", {}).get("avito_market_enabled", True))

    def is_avito_market_spfa_enabled(self) -> bool:
        return bool(self.get_all().get("integrations", {}).get("avito_market_use_spfa", True))

    def get_spfa_api_key(self) -> str:
        return str(self.get_secret("spfa_api_key") or "").strip()

    def get_avito_market_proxy(self) -> str:
        return str(self.get_secret("avito_market_proxy") or "").strip()

    def get_mobileproxy_api_token(self) -> str:
        return str(self.get_secret("mobileproxy_api_token") or "").strip()

    def get_avito_market_proxy_change_url(self) -> str:
        return str(
            self.get_all().get("integrations", {}).get("avito_market_proxy_change_url") or ""
        ).strip()

    def is_avito_market_watchlist_enabled(self) -> bool:
        integ = self.get_all().get("integrations", {})
        if not bool(integ.get("avito_market_enabled", True)):
            return False
        return bool(integ.get("avito_market_watchlist_enabled", True))

    def get_avito_market_watchlist_pause_until(self) -> Optional[datetime]:
        raw = str(
            self.get_all().get("integrations", {}).get("avito_market_watchlist_pause_until") or ""
        ).strip()
        if not raw:
            return None
        try:
            value = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if value.tzinfo is not None:
            value = value.replace(tzinfo=None)
        return value

    def set_avito_market_watchlist_pause_until(self, until: Optional[datetime]) -> None:
        value = until.isoformat(timespec="seconds") if until else ""
        self.update({"integrations": {"avito_market_watchlist_pause_until": value}})

    def can_enqueue_avito_without_linked_item(
        self,
        *,
        require_vk_market_for_pipeline: bool = True,
        post_text: Optional[str] = None,
    ) -> bool:
        """Авто-создание объявления без привязанного avito_item_id.

        По умолчанию требует связку с «Товары ВК» (как для б/у). Для ручной
        публикации в Авито передайте require_vk_market_for_pipeline=False.
        post_text — для подстановки category_id из эвристики + AVITO_DEFAULT_CATEGORY_SMARTPHONES.
        """
        if require_vk_market_for_pipeline:
            if not self.is_avito_queue_allowed():
                return False
        else:
            if not self.is_avito_platform_only_enabled():
                return False
        if not self.is_avito_auto_create_from_post_enabled():
            return False
        if not self.resolved_avito_category_id_for_text(post_text):
            return False
        cid = str(self.get_all().get("integrations", {}).get("avito_client_id") or "").strip()
        if not cid:
            return False
        if not str(self.get_secret("avito_client_secret") or "").strip():
            return False
        return True

    def get_avito_standalone_enqueue_diagnostics(self, post_text: Optional[str] = None) -> dict:
        """Булевы флаги: почему нельзя авто-создать объявление без avito_item_id (без «Товары ВК»).

        Секреты не возвращаются, только факт наличия.
        """
        rid = self.resolved_avito_category_id_for_text(post_text)
        explicit = bool(self.get_avito_category_id())
        return {
            "platform_avito": bool(self.is_avito_platform_only_enabled()),
            "auto_create_on": bool(self.is_avito_auto_create_from_post_enabled()),
            "has_explicit_category_id": explicit,
            "has_resolved_category_id": bool(rid),
            "used_phone_text_fallback": bool(rid and not explicit),
            "has_client_id": bool(
                str(self.get_all().get("integrations", {}).get("avito_client_id") or "").strip()
            ),
            "has_client_secret": bool(str(self.get_secret("avito_client_secret") or "").strip()),
        }

    def describe_avito_standalone_missing(self, post_text: Optional[str] = None) -> str:
        """Краткий текст для алерта: что не настроено для авто-создания без привязки."""
        d = self.get_avito_standalone_enqueue_diagnostics(post_text)
        parts = []
        if not d["auto_create_on"]:
            parts.append("авто-создание из поста выключено в настройках")
        if not d["has_resolved_category_id"]:
            parts.append(
                "не задан category_id Авито: в настройках «Category ID» или в .env "
                "AVITO_DEFAULT_CATEGORY_SMARTPHONES (число из дерева категорий Авито для смартфонов)"
            )
        if not d["has_client_id"]:
            parts.append("не задан avito_client_id")
        if not d["has_client_secret"]:
            parts.append("не задан avito_client_secret")
        if not parts:
            return "проверьте интеграцию Авито в настройках"
        return "; ".join(parts)

    def get_price_tags_settings(self) -> Dict[str, Any]:
        return dict(self.get_all().get("price_tags") or DEFAULT_SETTINGS["price_tags"])

    def get_price_tag_strike_markup_percent(self) -> int:
        raw = self.get_price_tags_settings().get("strike_markup_percent", 5)
        try:
            v = int(raw)
        except (TypeError, ValueError):
            v = 5
        return v if v in (5, 10) else 5

    def get_publication_status_lines(self) -> list[str]:
        data = self.get_all()
        lines = []
        labels = {
            "vk": "VK",
            "telegram": "Telegram",
            "instagram": "Instagram",
            "max": "Max",
            "avito": "Авито",
        }
        for platform in ("vk", "telegram", "instagram", "max", "avito"):
            enabled = bool(data["publishing"]["enabled"].get(platform, True))
            if enabled:
                minutes = int(data["publishing"]["interval_minutes"].get(platform, 3))
                lines.append(f"🟢 {labels[platform]} — {minutes} мин")
            else:
                lines.append(f"🔴 {labels[platform]} — отключено")
        vk_market_enabled = bool(data.get("features", {}).get("vk_market_enabled", True))
        lines.append("🟢 Товары ВК" if vk_market_enabled else "🔴 Товары ВК")
        from app.workers.vk.story_composer import stories_mode_status_line

        lines.append(
            stories_mode_status_line(
                enabled=bool(data.get("features", {}).get("vk_stories_auto_enabled", False)),
                style=data.get("features", {}).get("vk_stories_style", "bubble"),
            )
        )
        return lines


_service: Optional[SettingsService] = None


def get_settings_service() -> SettingsService:
    global _service
    if _service is None:
        _service = SettingsService()
    return _service
