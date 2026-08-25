from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.exceptions import TelegramBadRequest
from html import escape
from datetime import datetime, timezone
import re

from app.bot.keyboards.settings_keyboard import (
    get_platform_interval_keyboard,
    get_input_cancel_keyboard,
    get_integration_platform_keyboard,
    get_settings_backup_keyboard,
    get_settings_price_tags_keyboard,
    get_settings_channels_keyboard,
    get_settings_integrations_keyboard,
    get_settings_intervals_keyboard,
    get_settings_reports_keyboard,
    get_settings_root_keyboard,
    get_settings_update_keyboard,
    get_signature_platform_fields_keyboard,
    get_settings_signatures_edit_keyboard,
    get_settings_signatures_keyboard,
    get_vk_channel_price_keyboard,
    get_max_catalog_reserve_count_keyboard,
    get_max_catalog_reserve_confirm_keyboard,
)
from app.services.settings_service import get_settings_service
from app.utils.signatures import get_instagram_signature, get_telegram_signature, get_vk_signature
from app.utils.telegram_post_markup import is_valid_catalog_button_url
from app.config import settings as env_settings
from app.workers.instagram.token_manager import InstagramGraphTokenManager
from app.bot.utils.admin_auth import (
    deny_unless_admin_callback,
    deny_unless_admin_message,
    is_admin_user,
)

router = Router()

# Поля интеграций, которые являются секретами → шифруются в encrypted_secrets (set_secret),
# а не лежат открытым текстом в config["integrations"].
SECRET_INTEGRATION_FIELDS = {
    "vk_access_token",
    "vk_market_access_token",
    "vk_app_secret",
    "telegram_bot_token",
    "max_bot_token",
    "instagram_graph_access_token",
    "instagram_graph_app_secret",
    "instagram_password",
    "avito_client_secret",
}

# Поля «ID канала» сохраняем в секцию channels (её читает резолвер SettingsService),
# чтобы изменения из меню реально влияли на публикацию.
CHANNEL_INTEGRATION_FIELDS = {"telegram_channel_id", "max_channel_id"}


class SettingsState(StatesGroup):
    waiting_for_secret_value = State()
    waiting_for_contact_value = State()
    waiting_for_signature_value = State()
    waiting_for_custom_interval = State()
    waiting_for_integration_value = State()
    waiting_for_channel_value = State()
    waiting_for_report_value = State()
    waiting_for_max_catalog_slot_count = State()
    waiting_for_backup_value = State()
    waiting_for_backup_schedule = State()
    waiting_for_price_tag_value = State()
    waiting_for_vk_price_link = State()
    waiting_for_vk_price_markers = State()
    waiting_for_vk_price_template = State()


def _status_text() -> str:
    service = get_settings_service()
    lines = service.get_publication_status_lines()
    return "⚙️ Настройки\n\n" + "\n".join(lines)


def _build_signatures_contacts_text() -> str:
    service = get_settings_service()
    data = service.get_all()
    contacts = data["contacts"]
    signatures = data["signatures"]
    from app.workers.vk.story_composer import story_style_label

    return (
        "📇 Контакты и подписи\n\n"
        f"Подпись: {'включена' if service.is_signature_enabled() else 'выключена'}\n"
        f"Товары ВК: {'включены' if service.is_vk_market_enabled() else 'выключены'}\n"
        f"Сторис (авто): {'включены' if service.is_stories_auto_enabled() else 'выключены'}"
        f" · стиль: {story_style_label(service.get_vk_stories_style())}\n"
        f"Блок «Каталог б/у» в постах: {'включен' if service.is_telegram_used_catalog_button_enabled() else 'выключен'}\n\n"
        "Текущие контакты Telegram:\n"
        f"- username: {contacts.get('telegram_username') or 'не задан'}\n"
        f"- user_id: {contacts.get('telegram_user_id') or 'не задан'}\n"
        f"- phone: {contacts.get('phone') or 'не задан'}\n\n"
        "Текущие ссылки подписей:\n"
        f"- VK: {signatures.get('vk') or 'не задано'}\n"
        f"- Avito: {signatures.get('avito') or 'не задано'}\n"
        f"- Telegram: {signatures.get('telegram') or 'не задано'}\n"
        f"- Instagram: {signatures.get('instagram') or 'не задано'}\n"
        f"- Каталог б/у (Telegram): {signatures.get('telegram_used_catalog_url') or 'не задано'}\n"
        f"- Каталог б/у (VK): {signatures.get('vk_used_catalog_url') or 'не задано'}\n"
        f"- Каталог б/у (Max): {signatures.get('max_used_catalog_url') or 'не задано'}\n"
    )


def _format_preview_quote(title: str, content: str) -> str:
    body = content.strip() or "Подпись пока пустая."
    quoted = "\n".join(escape(line) for line in body.splitlines())
    return f"{title}\n\n<blockquote>{quoted}</blockquote>"


def _get_signature_field_current_value(field: str) -> str:
    signatures = get_settings_service().get_all()["signatures"]
    return str(signatures.get(field) or "не задано")


def _get_contact_field_current_value(field: str) -> str:
    contacts = get_settings_service().get_all()["contacts"]
    return str(contacts.get(field) or "не задано")


def _get_integration_field_current_value(field: str) -> str:
    service = get_settings_service()
    if field in SECRET_INTEGRATION_FIELDS:
        return "задан" if service.get_secret(field) else "не задано"
    if field in CHANNEL_INTEGRATION_FIELDS:
        v = service.get_all().get("channels", {}).get(field)
        return str(v) if v else "не задано"
    integrations = service.get_all().get("integrations", {})
    v = integrations.get(field)
    if v is None or v == "":
        return "не задано"
    return str(v)


def _get_instagram_integration_field_prompt(field: str) -> str:
    prompts = {
        "instagram_graph_access_token": (
            "Введите Токен Instagram Graph.\n\n"
            "Где получить:\n"
            "1) [Meta Graph API Explorer](https://developers.facebook.com/tools/explorer)\n"
            "2) Выберите ваше приложение и получите User Access Token с правами:\n"
            "- instagram_basic\n"
            "- instagram_content_publish\n"
            "- pages_show_list\n"
            "- pages_read_engagement\n"
            "3) Вставьте токен сюда."
        ),
        "instagram_graph_app_id": (
            "Введите App ID (Meta).\n\n"
            "Где взять:\n"
            "1) [Meta for Developers](https://developers.facebook.com/apps/)\n"
            "2) Откройте нужное приложение\n"
            "3) Скопируйте App ID из Dashboard."
        ),
        "instagram_graph_app_secret": (
            "Введите App Secret (Meta).\n\n"
            "Где взять:\n"
            "1) [Meta for Developers](https://developers.facebook.com/apps/)\n"
            "2) Settings -> Basic\n"
            "3) Нажмите Show напротив App Secret и скопируйте значение."
        ),
        "instagram_graph_user_id": (
            "Введите Graph User ID Instagram-аккаунта (куда публикуем).\n\n"
            "Как получить:\n"
            "1) Убедитесь, что IG Professional привязан к Facebook Page\n"
            "2) Получите Page ID и запросите:\n"
            "`GET /{page-id}?fields=instagram_business_account`\n"
            "3) Используйте `instagram_business_account.id`."
        ),
    }
    return prompts.get(field, "")


def _platform_label(platform: str) -> str:
    labels = {
        "vk": "VK",
        "telegram": "Telegram",
        "instagram": "Instagram",
        "max": "Max",
        "avito": "Авито",
    }
    return labels.get(platform, platform.upper())


async def _render_platform_interval_screen(callback: CallbackQuery, platform: str):
    service = get_settings_service()
    enabled = service.is_platform_enabled(platform)
    minutes = service.get_platform_interval_minutes(platform)
    lines = [
        f"⏱ {_platform_label(platform)}",
        "",
        f"Текущий интервал: {minutes} мин",
        f"Статус: {'включено' if enabled else 'выключено'}",
    ]
    vk_upload_strict = None
    vk_wall_requires_market = None
    if platform == "vk":
        vk_upload_strict = service.is_vk_upload_strict_mode()
        vk_wall_requires_market = service.is_vk_wall_requires_market()
        lines.extend(
            [
                "",
                "Политика публикации VK:",
                "",
                "• Полное медиа — если выкл, пост/товар уйдёт "
                "даже при потере 1–2 фото/видео (аварийный режим).",
                "",
                "• Лента только с товаром — если выкл, при сбое "
                "Товаров ВК лента всё равно опубликуется.",
            ]
        )
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=get_platform_interval_keyboard(
            platform,
            enabled,
            vk_upload_strict=vk_upload_strict,
            vk_wall_requires_market=vk_wall_requires_market,
        ),
    )
    await callback.answer()


def _build_integration_platform_text(platform: str) -> str:
    service = get_settings_service()
    integrations = service.get_all().get("integrations", {})
    if platform == "vk":
        return (
            "🔐 VK интеграция\n\n"
            f"Токен: {'задан' if service.get_secret('vk_access_token') else 'не задан'}\n"
            f"ID группы (куда постим): {integrations.get('vk_group_id') or env_settings.VK_GROUP_ID or 'не задан'}"
        )
    if platform == "telegram":
        return (
            "🔐 Telegram интеграция\n\n"
            f"Токен бота: {'задан' if service.get_secret('telegram_bot_token') else 'не задан'}\n"
            f"ID канала (куда постим): {service.get_telegram_channel_id() or 'не задан'}"
        )
    if platform == "max":
        return (
            "🔐 Max интеграция\n\n"
            f"Токен: {'задан' if service.get_secret('max_bot_token') else 'не задан'}\n"
            f"ID канала (куда постим): {service.get_max_channel_id() or 'не задан'}\n"
            f"Base URL API: {integrations.get('max_api_base_url') or env_settings.MAX_API_BASE_URL or 'не задан'}"
        )
    if platform == "avito":
        sec = get_settings_service().get_secret("avito_client_secret")
        return (
            "🔐 Авито API\n\n"
            f"Client ID: {integrations.get('avito_client_id') or 'не задан'}\n"
            f"Client Secret: {'задан' if sec else 'не задан'}\n"
            f"User ID (кэш API): {integrations.get('avito_user_id') or '—'}\n"
            f"Category ID (Авито, фаза B): {integrations.get('avito_category_id') or 'не задан'}\n"
            f"Fallback из .env (смартфоны): AVITO_DEFAULT_CATEGORY_SMARTPHONES="
            f"{getattr(env_settings, 'AVITO_DEFAULT_CATEGORY_SMARTPHONES', None) or '—'}\n"
            f"Location ID (опц.): {integrations.get('avito_location_id') or '—'}\n"
            f"Авто-создание объявления из поста: {'да' if integrations.get('avito_auto_create_from_post', True) else 'нет'}\n"
            f"Вид объявления: {integrations.get('avito_listing_kind', 'resale')}\n"
            f"Мультиобъявление: {'да' if integrations.get('avito_multi_listing', True) else 'нет'}\n"
            f"Доставка: {integrations.get('avito_delivery_mode', 'pickup_only')}"
        )
    if platform != "instagram":
        return f"🔐 Интеграция {platform}\n\n(нет текста для этой платформы)"

    token_expires_at_raw = str(integrations.get("instagram_graph_token_expires_at") or "").strip()
    token_last_error = str(integrations.get("instagram_graph_token_last_error") or "").strip()

    def _token_ttl_text() -> str:
        if not token_expires_at_raw:
            return "неизвестно"
        try:
            expires_at = datetime.fromisoformat(token_expires_at_raw)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            seconds_left = int((expires_at.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds())
            if seconds_left <= 0:
                return "истек"
            days = seconds_left // 86400
            hours = (seconds_left % 86400) // 3600
            if days > 0:
                return f"{days} дн. {hours} ч."
            return f"{hours} ч."
        except Exception:
            return "неизвестно"

    app_id = integrations.get("instagram_graph_app_id") or getattr(env_settings, "INSTAGRAM_GRAPH_APP_ID", None)
    app_secret = service.get_secret("instagram_graph_app_secret") or getattr(env_settings, "INSTAGRAM_GRAPH_APP_SECRET", None)
    token_present = bool(service.get_secret("instagram_graph_access_token") or getattr(env_settings, "INSTAGRAM_GRAPH_ACCESS_TOKEN", ""))
    user_id_present = bool(
        integrations.get("instagram_graph_user_id") or getattr(env_settings, "INSTAGRAM_GRAPH_USER_ID", None)
    )

    if token_last_error:
        status_line = f"🔴 Ошибка токена: {token_last_error[:120]}"
    elif token_present and _token_ttl_text() not in ("истек", "неизвестно"):
        status_line = f"🟢 Instagram token OK (осталось {_token_ttl_text()})"
    elif token_present and _token_ttl_text() == "истек":
        status_line = "🔴 Instagram token истек"
    elif token_present:
        status_line = "🟡 Instagram token задан (TTL пока неизвестен)"
    else:
        status_line = "🔴 Instagram token не задан"

    return (
        "🔐 Instagram интеграция\n\n"
        f"{status_line}\n\n"
        f"Режим: {integrations.get('instagram_mode', 'graph')}\n"
        f"Graph token: {'задан' if token_present else 'не задан'}\n"
        f"Graph token TTL: {_token_ttl_text()}\n"
        f"Graph app_id: {'задан' if app_id else 'не задан'}\n"
        f"Graph app_secret: {'задан' if app_secret else 'не задан'}\n"
        f"Graph user_id (куда постим): {integrations.get('instagram_graph_user_id') or getattr(env_settings, 'INSTAGRAM_GRAPH_USER_ID', None) or 'не задан'}\n"
        f"Legacy username: {integrations.get('instagram_username') or 'не задан'}\n"
        f"Legacy password: {'задан' if service.get_secret('instagram_password') else 'не задан'}\n"
        f"Последняя ошибка refresh/check: {token_last_error or 'нет'}"
    )


@router.callback_query(F.data == "open_settings")
@router.callback_query(F.data == "settings_root")
async def open_settings(callback: CallbackQuery):
    uid = callback.from_user.id if callback.from_user else None
    await callback.message.edit_text(
        _status_text(),
        reply_markup=get_settings_root_keyboard(is_admin=is_admin_user(uid)),
    )
    await callback.answer()


@router.callback_query(F.data == "settings_signatures")
async def open_signatures(callback: CallbackQuery):
    service = get_settings_service()
    from app.workers.vk.story_composer import story_style_label

    await callback.message.edit_text(
        _build_signatures_contacts_text(),
        reply_markup=get_settings_signatures_keyboard(
            enabled=service.is_signature_enabled(),
            vk_market_enabled=service.is_vk_market_enabled(),
            catalog_enabled=service.is_telegram_used_catalog_button_enabled(),
            vk_stories_auto_enabled=service.is_vk_stories_auto_enabled(),
            vk_stories_style_label=story_style_label(service.get_vk_stories_style()),
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "settings_toggle_signature")
async def toggle_signature(callback: CallbackQuery):
    service = get_settings_service()
    next_state = not service.is_signature_enabled()
    service.set_signature_enabled(next_state)
    await open_signatures(callback)


@router.callback_query(F.data == "settings_toggle_vk_market")
async def toggle_vk_market(callback: CallbackQuery):
    service = get_settings_service()
    next_state = not service.is_vk_market_enabled()
    service.set_vk_market_enabled(next_state)
    await open_signatures(callback)


@router.callback_query(F.data == "settings_toggle_vk_stories_auto")
async def toggle_vk_stories_auto(callback: CallbackQuery):
    service = get_settings_service()
    next_state = not service.is_vk_stories_auto_enabled()
    service.set_vk_stories_auto_enabled(next_state)
    await open_signatures(callback)


@router.callback_query(F.data == "settings_cycle_vk_stories_style")
async def cycle_vk_stories_style(callback: CallbackQuery):
    get_settings_service().cycle_vk_stories_style()
    await open_signatures(callback)


@router.callback_query(F.data == "settings_toggle_telegram_used_catalog")
async def toggle_telegram_used_catalog(callback: CallbackQuery):
    service = get_settings_service()
    next_state = not service.is_telegram_used_catalog_button_enabled()
    service.set_telegram_used_catalog_button_enabled(next_state)
    await open_signatures(callback)


@router.callback_query(F.data == "settings_edit_tg_catalog_url")
async def request_tg_catalog_url(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsState.waiting_for_signature_value)
    await state.update_data(
        signature_field="telegram_used_catalog_url",
        input_return_callback="settings_signatures",
    )
    current = _get_signature_field_current_value("telegram_used_catalog_url")
    await callback.message.edit_text(
        f"Текущая ссылка каталога б/у для Telegram:\n{current}\n\n"
        "Введите новый URL (https://… или http://…):",
        reply_markup=get_input_cancel_keyboard("settings_signatures"),
    )
    await callback.answer()


@router.callback_query(F.data == "settings_edit_vk_catalog_url")
async def request_vk_catalog_url(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsState.waiting_for_signature_value)
    await state.update_data(
        signature_field="vk_used_catalog_url",
        input_return_callback="settings_signatures",
    )
    current = _get_signature_field_current_value("vk_used_catalog_url")
    await callback.message.edit_text(
        f"Текущая ссылка каталога б/у для VK:\n{current}\n\n"
        "Введите новый URL (https://… или http://…):",
        reply_markup=get_input_cancel_keyboard("settings_signatures"),
    )
    await callback.answer()


@router.callback_query(F.data == "settings_edit_max_catalog_url")
async def request_max_catalog_url(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsState.waiting_for_signature_value)
    await state.update_data(
        signature_field="max_used_catalog_url",
        input_return_callback="settings_signatures",
    )
    current = _get_signature_field_current_value("max_used_catalog_url")
    await callback.message.edit_text(
        f"Текущая ссылка каталога б/у для Max:\n{current}\n\n"
        "Введите новый URL (https://max.ru/c/…):",
        reply_markup=get_input_cancel_keyboard("settings_signatures"),
    )
    await callback.answer()


@router.callback_query(F.data == "settings_intervals")
async def open_intervals(callback: CallbackQuery):
    service = get_settings_service()
    data = service.get_all()
    await callback.message.edit_text(
        "⏱ Публикация и интервалы\n\nВыберите платформу:",
        reply_markup=get_settings_intervals_keyboard(data),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings_platform_"))
async def open_platform_interval(callback: CallbackQuery):
    platform = callback.data.replace("settings_platform_", "")
    await _render_platform_interval_screen(callback, platform)


@router.callback_query(F.data.startswith("settings_toggle_platform_"))
async def toggle_platform(callback: CallbackQuery):
    platform = callback.data.replace("settings_toggle_platform_", "")
    service = get_settings_service()
    enabled = service.is_platform_enabled(platform)
    service.set_platform_enabled(platform, not enabled)
    await _render_platform_interval_screen(callback, platform)


@router.callback_query(F.data == "settings_toggle_vk_upload_strict")
async def toggle_vk_upload_strict(callback: CallbackQuery):
    service = get_settings_service()
    service.set_vk_upload_strict_mode(not service.is_vk_upload_strict_mode())
    await _render_platform_interval_screen(callback, "vk")


@router.callback_query(F.data == "settings_toggle_vk_wall_requires_market")
async def toggle_vk_wall_requires_market(callback: CallbackQuery):
    service = get_settings_service()
    service.set_vk_wall_requires_market(not service.is_vk_wall_requires_market())
    await _render_platform_interval_screen(callback, "vk")


@router.callback_query(F.data.startswith("settings_interval_custom_"))
async def request_custom_interval(callback: CallbackQuery, state: FSMContext):
    platform = callback.data.replace("settings_interval_custom_", "")
    await state.set_state(SettingsState.waiting_for_custom_interval)
    await state.update_data(
        custom_interval_platform=platform,
        input_return_callback=f"settings_platform_{platform}",
    )
    await callback.message.edit_text(
        f"✏️ Введите свой интервал для {platform.upper()} в минутах (1-1440).",
        reply_markup=get_input_cancel_keyboard(f"settings_platform_{platform}"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings_interval_"))
async def set_platform_interval(callback: CallbackQuery):
    _, _, platform, minutes = callback.data.split("_", 3)
    service = get_settings_service()
    service.set_platform_interval_minutes(platform, int(minutes))
    await _render_platform_interval_screen(callback, platform)


@router.callback_query(F.data == "settings_integrations")
async def open_integrations(callback: CallbackQuery):
    await callback.message.edit_text(
        "🔐 Интеграции и токены\n\n"
        "Выберите соцсеть. Внутри есть токены и ID/каналы/группы назначения.",
        reply_markup=get_settings_integrations_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings_integration_platform_"))
async def open_integration_platform(callback: CallbackQuery):
    platform = callback.data.replace("settings_integration_platform_", "")
    await callback.message.edit_text(
        _build_integration_platform_text(platform),
        reply_markup=get_integration_platform_keyboard(platform),
    )
    await callback.answer()


@router.callback_query(F.data == "settings_edit_signatures_menu")
async def open_signatures_edit(callback: CallbackQuery):
    await callback.message.edit_text(
        "✏️ Превью подписи по платформам\n\nВыберите платформу:",
        reply_markup=get_settings_signatures_edit_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings_preview_"))
async def show_platform_preview(callback: CallbackQuery):
    platform = callback.data.replace("settings_preview_", "")
    if platform == "vk":
        text = _format_preview_quote("📘 Превью подписи VK", get_vk_signature(enabled=True))
    elif platform == "instagram":
        text = _format_preview_quote("📷 Превью подписи Instagram", get_instagram_signature())
    elif platform == "max":
        text = _format_preview_quote("💬 Превью подписи Max", get_telegram_signature(enabled=True))
    else:
        text = _format_preview_quote("✈️ Превью подписи Telegram", get_telegram_signature(enabled=True))

    await callback.message.edit_text(
        text + "\n\nВыберите поле для изменения:",
        reply_markup=get_signature_platform_fields_keyboard(platform),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings_edit_contact_"))
async def request_contact_value(callback: CallbackQuery, state: FSMContext):
    field = callback.data.replace("settings_edit_contact_", "")
    await state.set_state(SettingsState.waiting_for_contact_value)
    await state.update_data(contact_field=field, input_return_callback="settings_signatures")
    current = _get_contact_field_current_value(field)
    await callback.message.edit_text(
        f"Текущее значение `{field}`:\n{current}\n\nВведите новое значение:",
        reply_markup=get_input_cancel_keyboard("settings_signatures"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings_edit_signature_"))
async def request_signature_value(callback: CallbackQuery, state: FSMContext):
    field = callback.data.replace("settings_edit_signature_", "")
    await state.set_state(SettingsState.waiting_for_signature_value)
    await state.update_data(signature_field=field, input_return_callback="settings_edit_signatures_menu")
    current = _get_signature_field_current_value(field)
    await callback.message.edit_text(
        f"Текущее значение `{field}`:\n{current}\n\nВведите новое значение:",
        reply_markup=get_input_cancel_keyboard("settings_edit_signatures_menu"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings_secret_"))
async def request_secret_value(callback: CallbackQuery, state: FSMContext):
    if not await deny_unless_admin_callback(callback):
        return
    secret_name = callback.data.replace("settings_secret_", "")
    await state.set_state(SettingsState.waiting_for_secret_value)
    await state.update_data(secret_name=secret_name, input_return_callback="settings_integrations")
    await callback.message.edit_text(
        f"🔐 Введите новое значение для `{secret_name}`.\n\n"
        "Сообщение с вашим вводом будет обработано и удалено из чата вручную при необходимости.\n\n"
        "Вы просили пока без шифрования/MASTER_KEY — поэтому хранение оставляем в env-потоке до отдельного этапа.",
        reply_markup=get_input_cancel_keyboard("settings_integrations"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings_edit_integration_"))
async def request_integration_value(callback: CallbackQuery, state: FSMContext):
    field = callback.data.replace("settings_edit_integration_", "")
    if field in SECRET_INTEGRATION_FIELDS and not await deny_unless_admin_callback(callback):
        return
    return_callback = "settings_integrations"
    if field.startswith("vk_"):
        return_callback = "settings_integration_platform_vk"
    elif field.startswith("telegram_"):
        return_callback = "settings_integration_platform_telegram"
    elif field.startswith("max_"):
        return_callback = "settings_integration_platform_max"
    elif field.startswith("instagram_"):
        return_callback = "settings_integration_platform_instagram"
    elif field.startswith("avito_"):
        return_callback = "settings_integration_platform_avito"

    await state.set_state(SettingsState.waiting_for_integration_value)
    await state.update_data(
        integration_field=field,
        input_return_callback=return_callback,
        integration_restore_chat_id=callback.message.chat.id,
        integration_restore_message_id=callback.message.message_id,
    )
    current = _get_integration_field_current_value(field)
    instruction = _get_instagram_integration_field_prompt(field)
    instruction_block = f"\n\n{instruction}" if instruction else ""
    await callback.message.edit_text(
        f"Текущее значение `{field}`:\n{current}{instruction_block}\n\nВведите новое значение:",
        reply_markup=get_input_cancel_keyboard(return_callback),
        disable_web_page_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data == "settings_toggle_instagram_mode")
async def toggle_instagram_mode(callback: CallbackQuery):
    service = get_settings_service()
    current = service.get_all()["integrations"].get("instagram_mode", "graph")
    new_mode = "legacy" if current == "graph" else "graph"
    service.update({"integrations": {"instagram_mode": new_mode}})
    await open_integrations(callback)


@router.callback_query(F.data == "settings_instagram_exchange_long_lived")
async def exchange_instagram_long_lived(callback: CallbackQuery):
    if not await deny_unless_admin_callback(callback):
        return
    manager = InstagramGraphTokenManager()
    token = manager.get_access_token()
    if not token:
        await callback.answer("❌ Не задан instagram_graph_access_token", show_alert=True)
        return

    ok, err, new_token, new_expires_at = await manager.exchange_to_long_lived_token(token)
    if not ok or not new_token:
        await callback.answer(f"❌ Не удалось обменять token в long-lived: {(err or 'unknown')[:160]}", show_alert=True)
        return

    meta = {
        "instagram_graph_token_last_error": "",
        "instagram_graph_token_last_check_at": datetime.now(timezone.utc).isoformat(),
    }
    if new_expires_at:
        meta["instagram_graph_token_expires_at"] = new_expires_at.isoformat()
    manager._store_access_token(new_token, meta)

    await callback.answer("✅ Long-lived token обновлен")
    await callback.message.edit_text(
        _build_integration_platform_text("instagram"),
        reply_markup=get_integration_platform_keyboard("instagram"),
    )


@router.message(SettingsState.waiting_for_secret_value)
async def save_secret_value(message: Message, state: FSMContext):
    if not await deny_unless_admin_message(message):
        return
    data = await state.get_data()
    secret_name = data.get("secret_name")
    if not secret_name:
        await state.clear()
        await message.answer("❌ Не удалось определить тип секрета. Откройте Настройки заново.")
        return

    service = get_settings_service()
    service.set_secret(secret_name, (message.text or "").strip())
    await state.clear()
    await message.answer("✅ Значение сохранено (зашифровано).")


@router.message(SettingsState.waiting_for_contact_value)
async def save_contact_value(message: Message, state: FSMContext):
    data = await state.get_data()
    field = data.get("contact_field")
    if not field:
        await state.clear()
        await message.answer("❌ Не удалось определить поле контакта.")
        return
    value: str | int = (message.text or "").strip()
    if field == "telegram_user_id" and value:
        try:
            value = int(value)
        except ValueError:
            await message.answer("❌ user_id должен быть числом.")
            return
    service = get_settings_service()
    service.update({"contacts": {field: value}})
    await state.clear()
    await message.answer("✅ Контакт обновлен.")


@router.message(SettingsState.waiting_for_signature_value)
async def save_signature_value(message: Message, state: FSMContext):
    data = await state.get_data()
    field = data.get("signature_field")
    if not field:
        await state.clear()
        await message.answer("❌ Не удалось определить поле подписи.")
        return
    raw = (message.text or "").strip()
    if field in ("telegram_used_catalog_url", "vk_used_catalog_url", "max_used_catalog_url"):
        if not is_valid_catalog_button_url(raw):
            await message.answer(
                "❌ Укажите корректный URL с протоколом https:// или http:// (например https://t.me/…)."
            )
            return
    service = get_settings_service()
    service.update({"signatures": {field: raw}})
    await state.clear()
    await message.answer("✅ Поле подписи обновлено.")


@router.message(SettingsState.waiting_for_custom_interval)
async def save_custom_interval(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if not value.isdigit():
        await message.answer("❌ Введите число в минутах, например: 12")
        return

    minutes = int(value)
    if minutes < 1 or minutes > 1440:
        await message.answer("❌ Допустимый диапазон: 1-1440 минут.")
        return

    data = await state.get_data()
    platform = data.get("custom_interval_platform")
    if not platform:
        await state.clear()
        await message.answer("❌ Не удалось определить платформу.")
        return

    get_settings_service().set_platform_interval_minutes(platform, minutes)
    await state.clear()
    await message.answer(f"✅ Интервал для {platform.upper()} сохранен: {minutes} мин.")


@router.message(SettingsState.waiting_for_integration_value)
async def save_integration_value(message: Message, state: FSMContext):
    data = await state.get_data()
    field = data.get("integration_field")
    if not field:
        await state.clear()
        await message.answer("❌ Не удалось определить поле интеграции.")
        return
    if field in SECRET_INTEGRATION_FIELDS and not await deny_unless_admin_message(message):
        return
    raw = (message.text or "").strip()
    return_cb = str(data.get("input_return_callback") or "")
    restore_chat = data.get("integration_restore_chat_id")
    restore_mid = data.get("integration_restore_message_id")

    if field in SECRET_INTEGRATION_FIELDS:
        service = get_settings_service()
        service.set_secret(field, raw)
        # Одно поле «Токен VK» должно покрывать и стену, и market.edit.
        if field == "vk_access_token" and raw:
            service.set_secret("vk_market_access_token", raw)
        # Убираем устаревшую plaintext-копию токена из integrations —
        # иначе token_manager мог бы читать старый invalid token вместо secret.
        if field == "instagram_graph_access_token":
            service.update(
                {
                    "integrations": {
                        "instagram_graph_access_token": "",
                        "instagram_graph_token_last_error": "",
                    }
                }
            )
        if field == "avito_client_secret":
            try:
                from app.integrations.avito.actions import invalidate_token_cache
                invalidate_token_cache()
            except Exception:
                pass
    elif field in CHANNEL_INTEGRATION_FIELDS:
        get_settings_service().update({"channels": {field: raw}})
    elif field in ("avito_category_id", "avito_location_id"):
        if not raw or raw.lower() in ("-", "none", "нет", "сброс"):
            get_settings_service().update({"integrations": {field: None}})
        else:
            try:
                get_settings_service().update({"integrations": {field: int(raw)}})
            except ValueError:
                await message.answer("❌ Укажите целое число или пусто для сброса.")
                return
    else:
        get_settings_service().update({"integrations": {field: raw}})
    await state.clear()

    if (
        restore_chat
        and restore_mid
        and return_cb.startswith("settings_integration_platform_")
    ):
        platform = return_cb.replace("settings_integration_platform_", "")
        try:
            await message.bot.edit_message_text(
                chat_id=int(restore_chat),
                message_id=int(restore_mid),
                text=_build_integration_platform_text(platform),
                reply_markup=get_integration_platform_keyboard(platform),
                disable_web_page_preview=True,
            )
        except TelegramBadRequest:
            pass

    if field == "avito_client_secret":
        await message.answer("✅ Client Secret сохранён. Экран «Авито» выше обновлён (секрет в чат не повторяем).")
    else:
        await message.answer("✅ Поле интеграции обновлено. Экран выше обновлён, если сообщение ещё доступно.")


@router.callback_query(F.data == "settings_cancel_input")
async def cancel_input(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    return_callback = data.get("input_return_callback", "settings_root")
    await state.clear()

    if return_callback == "settings_signatures":
        await open_signatures(callback)
    elif return_callback == "settings_integrations":
        await open_integrations(callback)
    elif return_callback == "settings_channels":
        await open_channels(callback)
    elif return_callback == "settings_reports":
        await open_reports(callback)
    elif return_callback == "settings_backup":
        await open_backup(callback)
    elif return_callback == "settings_price_tags":
        await open_price_tags(callback)
    elif return_callback == "settings_edit_signatures_menu":
        await open_signatures_edit(callback)
    elif return_callback.startswith("settings_platform_"):
        platform = return_callback.replace("settings_platform_", "")
        await _render_platform_interval_screen(callback, platform)
    elif return_callback.startswith("settings_integration_platform_"):
        platform = return_callback.replace("settings_integration_platform_", "")
        await callback.message.edit_text(
            _build_integration_platform_text(platform),
            reply_markup=get_integration_platform_keyboard(platform),
        )
        await callback.answer()
    else:
        await open_settings(callback)


@router.callback_query(F.data == "settings_avito_toggle_auto_create")
async def avito_toggle_auto_create(callback: CallbackQuery):
    svc = get_settings_service()
    cur = bool(svc.get_all().get("integrations", {}).get("avito_auto_create_from_post", True))
    svc.update({"integrations": {"avito_auto_create_from_post": not cur}})
    await callback.message.edit_text(
        _build_integration_platform_text("avito"),
        reply_markup=get_integration_platform_keyboard("avito"),
    )
    await callback.answer("Авто-создание: " + ("выкл" if cur else "вкл"))


@router.callback_query(F.data == "settings_avito_toggle_listing_kind")
async def avito_toggle_listing_kind(callback: CallbackQuery):
    svc = get_settings_service()
    cur = str(svc.get_all().get("integrations", {}).get("avito_listing_kind", "resale"))
    nxt = "own" if cur != "own" else "resale"
    svc.update({"integrations": {"avito_listing_kind": nxt}})
    await callback.message.edit_text(
        _build_integration_platform_text("avito"),
        reply_markup=get_integration_platform_keyboard("avito"),
    )
    await callback.answer(f"Вид объявления: {nxt}")


@router.callback_query(F.data == "settings_avito_toggle_multi")
async def avito_toggle_multi(callback: CallbackQuery):
    svc = get_settings_service()
    cur = bool(svc.get_all().get("integrations", {}).get("avito_multi_listing", True))
    svc.update({"integrations": {"avito_multi_listing": not cur}})
    await callback.message.edit_text(
        _build_integration_platform_text("avito"),
        reply_markup=get_integration_platform_keyboard("avito"),
    )
    await callback.answer("Мультиобъявление: " + ("выкл" if cur else "вкл"))


@router.callback_query(F.data == "settings_avito_cycle_delivery")
async def avito_cycle_delivery(callback: CallbackQuery):
    order = ("pickup_only", "seller", "avito_partners")
    svc = get_settings_service()
    cur = str(svc.get_all().get("integrations", {}).get("avito_delivery_mode", "pickup_only"))
    try:
        i = order.index(cur)
    except ValueError:
        i = 0
    nxt = order[(i + 1) % len(order)]
    svc.update({"integrations": {"avito_delivery_mode": nxt}})
    await callback.message.edit_text(
        _build_integration_platform_text("avito"),
        reply_markup=get_integration_platform_keyboard("avito"),
    )
    await callback.answer(f"Доставка: {nxt}")


# ===================== Каналы публикации =====================

def _build_channels_text() -> str:
    svc = get_settings_service()
    return (
        "📣 Каналы публикации\n\n"
        f"Telegram-канал: {svc.get_telegram_channel_id() or 'не задан'}\n"
        f"Max-канал: {svc.get_max_channel_id() or 'не задан'}\n\n"
        "ID можно указывать как @username или числовой -100…"
    )


@router.callback_query(F.data == "settings_channels")
async def open_channels(callback: CallbackQuery):
    await callback.message.edit_text(_build_channels_text(), reply_markup=get_settings_channels_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("settings_edit_channel_"))
async def request_channel_value(callback: CallbackQuery, state: FSMContext):
    field = callback.data.replace("settings_edit_channel_", "")
    await state.set_state(SettingsState.waiting_for_channel_value)
    await state.update_data(channel_field=field, input_return_callback="settings_channels")
    current = (
        get_settings_service().get_telegram_channel_id()
        if field == "telegram_channel_id"
        else get_settings_service().get_max_channel_id()
    )
    await callback.message.edit_text(
        f"Текущее значение `{field}`:\n{current or 'не задано'}\n\nВведите новое значение:",
        reply_markup=get_input_cancel_keyboard("settings_channels"),
    )
    await callback.answer()


@router.message(SettingsState.waiting_for_channel_value)
async def save_channel_value(message: Message, state: FSMContext):
    data = await state.get_data()
    field = data.get("channel_field")
    if field not in ("telegram_channel_id", "max_channel_id"):
        await state.clear()
        await message.answer("❌ Не удалось определить поле канала.")
        return
    get_settings_service().update({"channels": {field: (message.text or "").strip()}})
    await state.clear()
    await message.answer("✅ Канал обновлён.")


# ===================== Отчёты и списки =====================

def _build_reports_text() -> str:
    svc = get_settings_service()
    vk_ids = ", ".join(str(x) for x in svc.get_vk_report_user_ids()) or "не заданы"
    avail = ", ".join(str(x) for x in svc.get_availability_message_ids()) or "не заданы"
    used = ", ".join(str(x) for x in svc.get_used_products_list_message_ids()) or "не заданы"
    used_max_ids = list(svc.get_max_used_products_list_message_ids() or [])
    used_max = ", ".join(used_max_ids) or "не заданы"
    return (
        "🗂 Отчёты и списки\n\n"
        f"VK получатели отчёта: {escape(vk_ids)}\n"
        f"ID сообщений «Наличие» (полный прайс ТГ): {escape(avail)}\n"
        f"ID сообщений «Список б/у» (ТГ): {escape(used)}\n"
        f"ID сообщений «Список б/у» (Max): {escape(used_max)}\n\n"
        "Telegram: целые <code>message_id</code> через запятую, по порядку сверху вниз.\n"
        "Пример ТГ: <code>11728,11729,11730,11731</code>\n\n"
        "Max: mid вводить не нужно — кнопка «Создать каталог б/у в Max» сама "
        "отправит посты в <b>канал этого сервера</b> и запомнит ID. "
        "Чужой канал того же бота не используется.\n"
        "Пример Max (если вставляете вручную): "
        "<code>mid.ffffbf41cd8a8a9701a010b89a821a2b, "
        "mid.ffffbf41cd8a8a9701a010b89d561a2b</code>\n\n"
        "Бот только редактирует запомненные сообщения, новые посты в каталог сам не плодит."
    )


_REPORT_INT_FIELDS = {
    "vk_report_user_ids",
    "availability_message_ids",
    "used_products_list_message_ids",
}
_REPORT_STR_FIELDS = {
    "max_used_products_list_message_ids",
}
_REPORT_FIELDS = _REPORT_INT_FIELDS | _REPORT_STR_FIELDS


@router.callback_query(F.data == "settings_reports")
async def open_reports(callback: CallbackQuery, state: FSMContext | None = None):
    if state is not None:
        await state.clear()
    await callback.message.edit_text(
        _build_reports_text(),
        reply_markup=get_settings_reports_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "settings_max_catalog_reserve")
async def start_max_catalog_reserve(callback: CallbackQuery, state: FSMContext):
    svc = get_settings_service()
    chat_id = (svc.get_max_channel_id() or "").strip()
    if not chat_id:
        await callback.answer("Сначала задайте ID канала Max в «Каналы публикации».", show_alert=True)
        return
    await state.set_state(SettingsState.waiting_for_max_catalog_slot_count)
    await state.update_data(input_return_callback="settings_reports")
    existing = list(svc.get_max_used_products_list_message_ids() or [])
    existing_line = (
        f"Сейчас уже зарезервировано: <b>{len(existing)}</b> сообщений.\n"
        if existing
        else "Слотов пока нет — mid вводить не нужно, бот создаст их сам.\n"
    )
    await callback.message.edit_text(
        "💬 Создать каталог б/у в Max\n\n"
        f"Посты уйдут в канал <b>этого</b> сервера:\n<code>{escape(chat_id)}</code>\n\n"
        f"{existing_line}\n"
        "Сколько сообщений создать? Обычно <b>15</b>.\n"
        "Можно нажать кнопку или прислать число от 1 до 30.",
        reply_markup=get_max_catalog_reserve_count_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


async def _show_max_catalog_confirm(callback_or_message, state: FSMContext, count: int) -> None:
    svc = get_settings_service()
    existing = list(svc.get_max_used_products_list_message_ids() or [])
    from app.bot.utils.used_products_max_channel_updater import fetch_max_catalog_target

    target = await fetch_max_catalog_target()
    chat_id = target.get("chat_id") or (svc.get_max_channel_id() or "").strip()
    title = target.get("title") or ""
    title_line = f"Название: <b>{escape(title)}</b>\n" if title else ""
    await state.update_data(
        max_catalog_slot_count=count,
        input_return_callback="settings_reports",
    )
    await state.set_state(SettingsState.waiting_for_max_catalog_slot_count)

    if existing:
        extra = (
            f"Уже сохранено слотов: <b>{len(existing)}</b>.\n"
            "«Добавить» допишет новые посты в конец каталога.\n"
            "«Создать новые» забудет старые ID (посты в канале останутся как есть) "
            "и начнёт список заново.\n\n"
        )
    else:
        extra = ""

    text = (
        "Подтверждение\n\n"
        f"Канал Max этого сервера:\n<code>{escape(chat_id)}</code>\n"
        f"{title_line}\n"
        f"В канал уйдёт <b>{count}</b> постов «зарезервировано ⬇️», "
        "потом в них запишется актуальный список б/у.\n\n"
        f"{extra}"
        "Чужой канал того же бота не используется — только ID из настроек этого проекта.\n"
        "Продолжить?"
    )
    markup = get_max_catalog_reserve_confirm_keyboard(has_existing=bool(existing))
    if isinstance(callback_or_message, CallbackQuery):
        await callback_or_message.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        await callback_or_message.answer()
    else:
        await callback_or_message.answer(text, reply_markup=markup, parse_mode="HTML")


@router.callback_query(F.data.startswith("settings_max_catalog_count_"))
async def pick_max_catalog_count(callback: CallbackQuery, state: FSMContext):
    raw = callback.data.replace("settings_max_catalog_count_", "")
    try:
        count = int(raw)
    except ValueError:
        await callback.answer("Неверное число", show_alert=True)
        return
    if count < 1 or count > 30:
        await callback.answer("Допустимо 1–30", show_alert=True)
        return
    await _show_max_catalog_confirm(callback, state, count)


@router.message(SettingsState.waiting_for_max_catalog_slot_count)
async def typed_max_catalog_count(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Введите число от 1 до 30 или нажмите кнопку.")
        return
    count = int(raw)
    if count < 1 or count > 30:
        await message.answer("Допустимо от 1 до 30.")
        return
    await _show_max_catalog_confirm(message, state, count)


@router.callback_query(F.data.in_({
    "settings_max_catalog_do_new",
    "settings_max_catalog_do_append",
    "settings_max_catalog_do_replace",
}))
async def run_max_catalog_reserve(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    count = int(data.get("max_catalog_slot_count") or 0)
    if count < 1 or count > 30:
        await callback.answer("Сначала выберите число слотов.", show_alert=True)
        return
    mode = callback.data.replace("settings_max_catalog_do_", "")
    append = mode == "append"
    force = mode == "replace"
    svc = get_settings_service()
    chat_id = (svc.get_max_channel_id() or "").strip()
    await callback.answer()
    await callback.message.edit_text(
        f"Создаю {count} постов в канале Max <code>{escape(chat_id)}</code>…\n"
        "Это займёт около минуты, mid подставятся сами.",
        parse_mode="HTML",
    )
    try:
        from app.bot.utils.used_products_max_channel_updater import (
            reserve_and_fill_max_used_catalog,
        )

        ids, ok = await reserve_and_fill_max_used_catalog(
            count, force=force, append=append
        )
    except Exception as exc:
        await state.clear()
        await callback.message.edit_text(
            f"❌ Не удалось создать каталог Max:\n{escape(str(exc))}",
            reply_markup=get_settings_reports_keyboard(),
            parse_mode="HTML",
        )
        return

    await state.clear()
    catalog_url = svc.get_max_used_catalog_url() or "не задан"
    status = "список записан" if ok else "слоты созданы, но список не записался"
    await callback.message.edit_text(
        f"✅ Каталог б/у в Max: {status}.\n\n"
        f"Канал: <code>{escape(chat_id)}</code>\n"
        f"Слотов в настройках: <b>{len(ids)}</b>\n"
        f"Ссылка на первый пост: {escape(catalog_url)}\n\n"
        "Mid сохранены на этом сервере. Другой проект с тем же ботом их не подхватит.",
        reply_markup=get_settings_reports_keyboard(),
        parse_mode="HTML",
    )


_REPORT_FIELD_HINTS = {
    "vk_report_user_ids": (
        "VK user_id получателей отчёта — целые числа через запятую.\n"
        "Пример: <code>123456789,987654321</code>\n"
        "«-» — очистить."
    ),
    "availability_message_ids": (
        "ID сообщений прайса «Наличие» в Telegram-канале.\n"
        "Только целые <code>message_id</code> через запятую, сверху вниз.\n"
        "Пример: <code>11728,11729,11730,11731</code>\n"
        "Править можно только сообщения этого бота. «-» — очистить."
    ),
    "used_products_list_message_ids": (
        "ID сообщений каталога б/у в Telegram-канале.\n"
        "Только целые <code>message_id</code> через запятую, сверху вниз.\n"
        "Пример: <code>12185,12186,12187,12188</code>\n"
        "Править можно только сообщения этого бота. «-» — очистить."
    ),
    "max_used_products_list_message_ids": (
        "mid сообщений каталога б/у в канале Max.\n"
        "Строки вида <code>mid.xxxxxxxx</code> через запятую (или с новой строки), "
        "в том же порядке, что посты в канале.\n"
        "Пример: <code>mid.ffffbf41cd8a8a9701a010b89a821a2b, "
        "mid.ffffbf41cd8a8a9701a010b89d561a2b</code>\n\n"
        "Это не числа Telegram. Для другого проекта/бота Max эти mid не подойдут — "
        "нужны слоты, которые отправил бот того проекта. «-» — очистить."
    ),
}


@router.callback_query(F.data.startswith("settings_edit_report_"))
async def request_report_value(callback: CallbackQuery, state: FSMContext):
    field = callback.data.replace("settings_edit_report_", "")
    if field not in _REPORT_FIELDS:
        await callback.answer("Неизвестное поле", show_alert=True)
        return
    await state.set_state(SettingsState.waiting_for_report_value)
    await state.update_data(report_field=field, input_return_callback="settings_reports")
    svc = get_settings_service()
    if field == "max_used_products_list_message_ids":
        current = ", ".join(str(x) for x in svc.get_max_used_products_list_message_ids()) or "не заданы"
    elif field == "used_products_list_message_ids":
        current = ", ".join(str(x) for x in svc.get_used_products_list_message_ids()) or "не заданы"
    elif field == "availability_message_ids":
        current = ", ".join(str(x) for x in svc.get_availability_message_ids()) or "не заданы"
    else:
        current = ", ".join(str(x) for x in svc.get_vk_report_user_ids()) or "не заданы"
    hint = _REPORT_FIELD_HINTS.get(field, "Введите значения через запятую или «-» для очистки.")
    await callback.message.edit_text(
        f"Текущее значение:\n<code>{escape(current)}</code>\n\n{hint}",
        reply_markup=get_input_cancel_keyboard("settings_reports"),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(SettingsState.waiting_for_report_value)
async def save_report_value(message: Message, state: FSMContext):
    data = await state.get_data()
    field = data.get("report_field")
    if field not in _REPORT_FIELDS:
        await state.clear()
        await message.answer("❌ Не удалось определить поле.")
        return
    raw = (message.text or "").strip()
    if raw in ("-", "none", "нет", "сброс", ""):
        ids: list = []
    elif field in _REPORT_STR_FIELDS:
        ids = [part.strip() for part in re.split(r"[\s,]+", raw) if part.strip()]
        if not ids:
            await message.answer("❌ Укажите mid через запятую или «-» для очистки.")
            return
        bad = [x for x in ids if not re.match(r"^mid\.[A-Za-z0-9._-]+$", x)]
        if bad:
            await message.answer(
                "❌ Для Max нужны mid вида mid.xxxx через запятую.\n"
                f"Не похоже на mid: {bad[0]}"
            )
            return
    else:
        ids = []
        for part in re.split(r"[\s,]+", raw):
            if not part:
                continue
            if not part.lstrip("-").isdigit():
                await message.answer("❌ Допустимы только целые числа через запятую.")
                return
            ids.append(int(part))
    get_settings_service().update({"reports": {field: ids}})
    await state.clear()
    await message.answer("✅ Сохранено.")


# ===================== Прайс VK-канала =====================

def _build_vk_channel_price_text() -> str:
    cfg = get_settings_service().get_vk_channel_price_config()
    avail = get_settings_service().get_availability_message_ids()
    avail_s = ", ".join(str(x) for x in avail) or "не заданы"
    has_tpl = bool(cfg.get("template"))
    return (
        "📣 Прайс (Telegram)\n\n"
        "Полный прайс с наличием публикуется в ТГ-канал через "
        "ID сообщений «Наличие» (только edit, без новых постов).\n\n"
        f"ID сообщений прайса ТГ: {avail_s}\n"
        f"Маркеры: {cfg.get('marker_in_stock')} / {cfg.get('marker_on_order')}\n"
        f"Ссылки на Market: {'вкл' if cfg.get('links_enabled') else 'выкл'}\n"
        f"Шаблон: {'свой (из настроек)' if has_tpl else 'файловый/канонический'}\n\n"
        "Задайте ID в «Отчёты и списки» → ID сообщений «Наличие» "
        "(например 11728,11729,11730,11731).\n"
        "VK Channel через API редактировать нельзя — прайс там вручную."
    )


async def _show_vk_channel_price(callback: CallbackQuery) -> None:
    cfg = get_settings_service().get_vk_channel_price_config()
    await callback.message.edit_text(
        _build_vk_channel_price_text(),
        reply_markup=get_vk_channel_price_keyboard(links_enabled=bool(cfg.get("links_enabled"))),
    )


@router.callback_query(F.data == "settings_vk_channel_price")
async def open_vk_channel_price(callback: CallbackQuery):
    await _show_vk_channel_price(callback)
    await callback.answer()


@router.callback_query(F.data == "settings_vk_price_link")
async def request_vk_price_link(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsState.waiting_for_vk_price_link)
    await callback.message.edit_text(
        "Пришлите ссылку на сообщение прайса в VK-канале, например:\n"
        "`https://vk.ru/im/channels/-235526445?cmid=1`\n\n"
        "Несколько сообщений — по одной ссылке на строку "
        "(или `peer,cmid1,cmid2`).\n"
        "«-» — сбросить привязку.",
        reply_markup=get_input_cancel_keyboard("settings_vk_channel_price"),
    )
    await callback.answer()


@router.message(SettingsState.waiting_for_vk_price_link)
async def save_vk_price_link(message: Message, state: FSMContext):
    from app.utils.vk_channel_link import parse_vk_channel_message_links

    raw = (message.text or "").strip()
    svc = get_settings_service()
    if raw in ("-", "none", "нет", "сброс"):
        svc.clear_vk_channel_price_binding()
        await state.clear()
        await message.answer("✅ Привязка сброшена.")
        return
    refs = parse_vk_channel_message_links(raw)
    if not refs:
        await message.answer("❌ Не удалось разобрать ссылку. Пример: https://vk.ru/im/channels/-235526445?cmid=1")
        return
    peer_ids = {r.peer_id for r in refs}
    if len(peer_ids) > 1:
        await message.answer("❌ Все сообщения должны быть из одного канала (один peer_id).")
        return
    peer_id = refs[0].peer_id
    cmids = [r.cmid for r in refs]
    svc.set_vk_channel_price_binding(peer_id, cmids)
    await state.clear()
    await message.answer(f"✅ Привязка сохранена: peer={peer_id}, cmid={', '.join(map(str, cmids))}")


@router.callback_query(F.data == "settings_vk_price_markers")
async def request_vk_price_markers(callback: CallbackQuery, state: FSMContext):
    cfg = get_settings_service().get_vk_channel_price_config()
    await state.set_state(SettingsState.waiting_for_vk_price_markers)
    await callback.message.edit_text(
        "Введите два маркера через пробел или запятую:\n"
        "1) в наличии  2) на заказ\n\n"
        f"Сейчас: `{cfg.get('marker_in_stock')}` / `{cfg.get('marker_on_order')}`\n\n"
        "Примеры: `● ○`  или  `✓ ↻`  или  `◆ ◇`",
        reply_markup=get_input_cancel_keyboard("settings_vk_channel_price"),
    )
    await callback.answer()


@router.message(SettingsState.waiting_for_vk_price_markers)
async def save_vk_price_markers(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    parts = [p for p in re.split(r"[\s,;]+", raw) if p]
    if len(parts) < 2:
        await message.answer("❌ Нужно два символа: наличие и заказ, например: ◆ ◇")
        return
    get_settings_service().set_vk_channel_price_markers(parts[0], parts[1])
    await state.clear()
    await message.answer(f"✅ Маркеры: {parts[0]} / {parts[1]}")
    # Триггерим обновление прайса
    try:
        from app.services.price_sync_service import get_price_sync_service

        get_price_sync_service().schedule_vk_channel_price_refresh()
    except Exception:
        pass


@router.callback_query(F.data == "settings_vk_price_template")
async def request_vk_price_template(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsState.waiting_for_vk_price_template)
    await callback.message.edit_text(
        "Пришлите полный текст прайса в каноническом формате "
        "(с секциями и строками ●/○ … — цена).\n\n"
        "«-» — сбросить свой шаблон и вернуться к файловому/каноническому.",
        reply_markup=get_input_cancel_keyboard("settings_vk_channel_price"),
    )
    await callback.answer()


@router.message(SettingsState.waiting_for_vk_price_template)
async def save_vk_price_template(message: Message, state: FSMContext):
    from app.utils.vk_channel_price_template import parse_price_list_template

    raw = (message.text or "").strip()
    svc = get_settings_service()
    if raw in ("-", "none", "нет", "сброс"):
        svc.set_vk_channel_price_template(None)
        await state.clear()
        await message.answer("✅ Свой шаблон сброшен.")
        return
    tpl = parse_price_list_template(raw)
    if not tpl.sections or not tpl.all_slots():
        await message.answer("❌ Не удалось разобрать шаблон: нет секций/слотов.")
        return
    svc.set_vk_channel_price_template(tpl.to_dict())
    await state.clear()
    await message.answer(
        f"✅ Шаблон сохранён: секций {len(tpl.sections)}, слотов {len(tpl.all_slots())}."
    )
    try:
        from app.services.price_sync_service import get_price_sync_service

        get_price_sync_service().schedule_vk_channel_price_refresh()
    except Exception:
        pass


@router.callback_query(F.data == "settings_vk_price_toggle_links")
async def toggle_vk_price_links(callback: CallbackQuery):
    svc = get_settings_service()
    cfg = svc.get_vk_channel_price_config()
    new_val = not bool(cfg.get("links_enabled"))
    svc.set_vk_channel_price_links_enabled(new_val)
    await _show_vk_channel_price(callback)
    await callback.answer("Ссылки " + ("вкл" if new_val else "выкл"))
    try:
        from app.services.price_sync_service import get_price_sync_service

        get_price_sync_service().schedule_vk_channel_price_refresh()
    except Exception:
        pass


@router.callback_query(F.data == "settings_vk_price_clear")
async def clear_vk_price_binding(callback: CallbackQuery):
    get_settings_service().clear_vk_channel_price_binding()
    await _show_vk_channel_price(callback)
    await callback.answer("Привязка сброшена")


@router.callback_query(F.data == "settings_vk_price_refresh")
async def refresh_vk_price_now(callback: CallbackQuery):
    await callback.answer("Обновляю…")
    try:
        from app.bot.utils.channel_updater import update_availability_message

        ok = await update_availability_message(callback.bot)
        if ok:
            await callback.message.answer("✅ Прайс в ТГ-канале обновлён (edit сообщений «Наличие»).")
        else:
            await callback.message.answer(
                "❌ Не удалось обновить. Проверьте TELEGRAM_CHANNEL_ID и "
                "ID сообщений «Наличие» (11728,11729,…)."
            )
    except Exception as exc:
        await callback.message.answer(f"❌ Ошибка обновления: {exc}")


# ===================== Резервное копирование =====================

def _build_backup_text() -> str:
    svc = get_settings_service()
    cfg = svc.get_backup_config()
    token_set = "задан" if svc.get_backup_bot_token() else "не задан"
    return (
        "💾 Резервное копирование\n\n"
        f"Автобэкап: {'включён' if cfg['enabled'] else 'выключен'}\n"
        f"Время запуска: {cfg['hour']:02d}:{cfg['minute']:02d}\n"
        f"Токен бота: {token_set}\n"
        f"Chat ID: {cfg['chat_id'] or 'не задан'}\n"
        f"Имя проекта: {cfg['project_name']}\n"
        f"Бэкап медиа: {'да' if cfg['media'] else 'нет'}\n"
        f"Хранить дней: {cfg['keep_days']}\n\n"
        "Дамп БД создаётся внутри приложения и отправляется в Telegram (хостовый cron не нужен)."
    )


async def _refresh_backup_screen(callback: CallbackQuery):
    cfg = get_settings_service().get_backup_config()
    await callback.message.edit_text(
        _build_backup_text(),
        reply_markup=get_settings_backup_keyboard(cfg["enabled"], cfg["media"]),
    )


@router.callback_query(F.data == "settings_backup")
async def open_backup(callback: CallbackQuery):
    await _refresh_backup_screen(callback)
    await callback.answer()


@router.callback_query(F.data == "settings_backup_toggle_enabled")
async def backup_toggle_enabled(callback: CallbackQuery):
    svc = get_settings_service()
    cur = bool(svc.get_backup_config()["enabled"])
    svc.update({"backup": {"enabled": not cur}})
    await _refresh_backup_screen(callback)
    await callback.answer("Автобэкап: " + ("выкл" if cur else "вкл"))


@router.callback_query(F.data == "settings_backup_toggle_media")
async def backup_toggle_media(callback: CallbackQuery):
    svc = get_settings_service()
    cur = bool(svc.get_backup_config()["media"])
    svc.update({"backup": {"media": not cur}})
    await _refresh_backup_screen(callback)
    await callback.answer("Медиа в бэкапе: " + ("выкл" if cur else "вкл"))


@router.callback_query(F.data == "settings_backup_edit_token")
async def backup_edit_token(callback: CallbackQuery, state: FSMContext):
    if not await deny_unless_admin_callback(callback):
        return
    await state.set_state(SettingsState.waiting_for_backup_value)
    await state.update_data(backup_field="token", input_return_callback="settings_backup")
    await callback.message.edit_text(
        "🔑 Введите токен Telegram-бота для отправки бэкапов (хранится зашифрованным):",
        reply_markup=get_input_cancel_keyboard("settings_backup"),
    )
    await callback.answer()


@router.callback_query(F.data == "settings_backup_edit_chat_id")
async def backup_edit_chat_id(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsState.waiting_for_backup_value)
    await state.update_data(backup_field="chat_id", input_return_callback="settings_backup")
    await callback.message.edit_text(
        f"💬 Текущий chat_id: {get_settings_service().get_backup_config()['chat_id'] or 'не задан'}\n\n"
        "Введите chat_id получателя (например -1002645220676):",
        reply_markup=get_input_cancel_keyboard("settings_backup"),
    )
    await callback.answer()


@router.callback_query(F.data == "settings_backup_edit_project_name")
async def backup_edit_project_name(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsState.waiting_for_backup_value)
    await state.update_data(backup_field="project_name", input_return_callback="settings_backup")
    await callback.message.edit_text(
        f"🏷 Текущее имя проекта: {get_settings_service().get_backup_config()['project_name']}\n\n"
        "Введите имя проекta (используется в имени файла бэкапа):",
        reply_markup=get_input_cancel_keyboard("settings_backup"),
    )
    await callback.answer()


@router.callback_query(F.data == "settings_backup_edit_schedule")
async def backup_edit_schedule(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsState.waiting_for_backup_schedule)
    await state.update_data(input_return_callback="settings_backup")
    cfg = get_settings_service().get_backup_config()
    await callback.message.edit_text(
        f"🕒 Текущее время: {cfg['hour']:02d}:{cfg['minute']:02d}\n\n"
        "Введите время в формате ЧЧ:ММ (например 03:30):",
        reply_markup=get_input_cancel_keyboard("settings_backup"),
    )
    await callback.answer()


@router.callback_query(F.data == "settings_backup_run_now")
async def backup_run_now(callback: CallbackQuery):
    await callback.answer("Запускаю бэкап…")
    from app.services.backup_service import run_backup
    ok, msg = await run_backup("manual")
    await callback.message.answer(("✅ " if ok else "❌ ") + msg)


@router.message(SettingsState.waiting_for_backup_value)
async def save_backup_value(message: Message, state: FSMContext):
    data = await state.get_data()
    field = data.get("backup_field")
    if field == "token" and not await deny_unless_admin_message(message):
        return
    raw = (message.text or "").strip()
    svc = get_settings_service()
    if field == "token":
        svc.set_secret("backup_bot_token", raw)
    elif field == "chat_id":
        svc.update({"backup": {"chat_id": raw}})
    elif field == "project_name":
        svc.update({"backup": {"project_name": raw or "tg_poster"}})
    else:
        await state.clear()
        await message.answer("❌ Не удалось определить поле.")
        return
    await state.clear()
    await message.answer("✅ Сохранено.")


@router.message(SettingsState.waiting_for_backup_schedule)
async def save_backup_schedule(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    parts = raw.replace(".", ":").split(":")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        await message.answer("❌ Формат времени ЧЧ:ММ, например 03:30")
        return
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        await message.answer("❌ Часы 0–23, минуты 0–59.")
        return
    get_settings_service().update({"backup": {"hour": hour, "minute": minute}})
    await state.clear()
    await message.answer(f"✅ Время бэкапа: {hour:02d}:{minute:02d}")


@router.callback_query(F.data == "settings_update_project")
async def settings_update_project(callback: CallbackQuery):
    if not await deny_unless_admin_callback(callback):
        return
    from app.services.project_update_service import build_update_screen

    await callback.answer("Проверяю GitHub…")
    text, check, running = await build_update_screen(refresh=True)
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_settings_update_keyboard(
                running=running,
                up_to_date=bool(check.fetch_ok and check.up_to_date),
                has_update=bool(check.fetch_ok and not check.up_to_date and check.behind > 0),
                fetch_ok=check.fetch_ok,
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise

@router.callback_query(F.data == "settings_update_project_confirm")
async def settings_update_project_confirm(callback: CallbackQuery):
    if not await deny_unless_admin_callback(callback):
        return
    from app.services.project_update_service import build_update_screen, start_project_update

    uid = callback.from_user.id if callback.from_user else None
    ok, msg = await start_project_update(force=False, requested_by=uid)
    await callback.answer("Запускаю обновление…" if ok else "Не запущено")
    _, check, running = await build_update_screen(refresh=False)
    if ok:
        running = True
    try:
        await callback.message.edit_text(
            msg,
            reply_markup=get_settings_update_keyboard(
                running=running,
                up_to_date=bool(check.fetch_ok and check.up_to_date),
                has_update=bool(check.fetch_ok and not check.up_to_date and check.behind > 0),
                fetch_ok=check.fetch_ok,
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


@router.callback_query(F.data == "settings_update_project_force")
async def settings_update_project_force(callback: CallbackQuery):
    if not await deny_unless_admin_callback(callback):
        return
    from app.services.project_update_service import build_update_screen, start_project_update

    uid = callback.from_user.id if callback.from_user else None
    ok, msg = await start_project_update(force=True, requested_by=uid)
    await callback.answer("Принудительная пересборка…" if ok else "Не запущено")
    _, check, running = await build_update_screen(refresh=False)
    if ok:
        running = True
    try:
        await callback.message.edit_text(
            msg,
            reply_markup=get_settings_update_keyboard(
                running=running,
                up_to_date=bool(check.fetch_ok and check.up_to_date),
                has_update=bool(check.fetch_ok and not check.up_to_date and check.behind > 0),
                fetch_ok=check.fetch_ok,
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


@router.callback_query(F.data == "settings_update_project_status")
async def settings_update_project_status(callback: CallbackQuery):
    if not await deny_unless_admin_callback(callback):
        return
    await callback.answer()
    from app.services.project_update_service import (
        build_update_screen,
        get_update_details_message,
    )

    text = await get_update_details_message()
    _, check, running = await build_update_screen(refresh=False)
    try:
        await callback.message.edit_text(
            f"🔄 <b>Подробности обновления</b>\n\n{text}",
            reply_markup=get_settings_update_keyboard(
                running=running,
                up_to_date=bool(check.fetch_ok and check.up_to_date),
                has_update=bool(check.fetch_ok and not check.up_to_date and check.behind > 0),
                fetch_ok=check.fetch_ok,
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


@router.callback_query(F.data == "settings_update_project_prune")
async def settings_update_project_prune(callback: CallbackQuery):
    """Очистка неиспользуемых Docker-образов (без volumes / медиа / бэкапов)."""
    if not await deny_unless_admin_callback(callback):
        return
    from app.services.project_update_service import (
        build_update_screen,
        free_docker_disk_space,
    )

    await callback.answer("Очищаю Docker-кэш…")
    uid = callback.from_user.id if callback.from_user else None
    _, msg = await free_docker_disk_space(requested_by=uid)
    _, check, running = await build_update_screen(refresh=False)
    try:
        await callback.message.edit_text(
            msg,
            reply_markup=get_settings_update_keyboard(
                running=running,
                up_to_date=bool(check.fetch_ok and check.up_to_date),
                has_update=bool(check.fetch_ok and not check.up_to_date and check.behind > 0),
                fetch_ok=check.fetch_ok,
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


def _build_price_tags_text() -> str:
    cfg = get_settings_service().get_price_tags_settings()
    pct = cfg.get("strike_markup_percent", 5)
    defs = cfg.get("default_descriptions") or {}
    iphone = (defs.get("iPhone новые") or "")[:80]
    ipad = (defs.get("iPad") or "")[:80]
    footer = (cfg.get("fixed_footer_text") or "")[:80]
    return (
        "🏷️ <b>Ценники</b>\n\n"
        f"Наценка «цена без скидки»: <b>+{pct}%</b> (округление до 100₽ вверх)\n\n"
        f"iPhone (по умолчанию): {iphone or '—'}{'…' if len(defs.get('iPhone новые') or '') > 80 else ''}\n"
        f"iPad (по умолчанию): {ipad or '—'}{'…' if len(defs.get('iPad') or '') > 80 else ''}\n"
        f"Fallback: {footer or '—'}{'…' if len(cfg.get('fixed_footer_text') or '') > 80 else ''}"
    )


async def _refresh_price_tags_screen(callback: CallbackQuery):
    pct = get_settings_service().get_price_tag_strike_markup_percent()
    await callback.message.edit_text(
        _build_price_tags_text(),
        reply_markup=get_settings_price_tags_keyboard(pct),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "settings_price_tags")
async def open_price_tags(callback: CallbackQuery):
    await _refresh_price_tags_screen(callback)
    await callback.answer()


@router.callback_query(F.data == "settings_price_tags_pct_5")
async def price_tags_set_pct_5(callback: CallbackQuery):
    get_settings_service().update({"price_tags": {"strike_markup_percent": 5}})
    await _refresh_price_tags_screen(callback)
    await callback.answer("Наценка: +5%")


@router.callback_query(F.data == "settings_price_tags_pct_10")
async def price_tags_set_pct_10(callback: CallbackQuery):
    get_settings_service().update({"price_tags": {"strike_markup_percent": 10}})
    await _refresh_price_tags_screen(callback)
    await callback.answer("Наценка: +10%")


@router.callback_query(F.data.startswith("settings_price_tags_edit_"))
async def price_tags_edit_field(callback: CallbackQuery, state: FSMContext):
    field = callback.data.replace("settings_price_tags_edit_", "")
    prompts = {
        "iphone": ("iPhone новые", "текст описания для iPhone"),
        "ipad": ("iPad", "текст описания для iPad"),
        "footer": ("fixed_footer_text", "общий fallback-текст"),
    }
    if field not in prompts:
        await callback.answer("Ошибка", show_alert=True)
        return
    key, label = prompts[field]
    await state.set_state(SettingsState.waiting_for_price_tag_value)
    await state.update_data(price_tag_field=key, input_return_callback="settings_price_tags")
    await callback.message.edit_text(
        f"📝 Введите {label}.\nОтправьте <code>-</code> чтобы очистить.",
        parse_mode="HTML",
        reply_markup=get_input_cancel_keyboard("settings_price_tags"),
    )
    await callback.answer()


@router.message(SettingsState.waiting_for_price_tag_value)
async def save_price_tag_value(message: Message, state: FSMContext):
    data = await state.get_data()
    field = data.get("price_tag_field")
    raw = (message.text or "").strip()
    val = "" if raw == "-" else raw
    svc = get_settings_service()
    if field == "fixed_footer_text":
        svc.update({"price_tags": {"fixed_footer_text": val}})
    elif field in ("iPhone новые", "iPad"):
        cur = svc.get_price_tags_settings()
        defs = dict(cur.get("default_descriptions") or {})
        defs[field] = val
        svc.update({"price_tags": {"default_descriptions": defs}})
    else:
        await state.clear()
        await message.answer("❌ Не удалось определить поле.")
        return
    await state.clear()
    await message.answer("✅ Сохранено.")

