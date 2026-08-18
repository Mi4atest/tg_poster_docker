from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest
import aiohttp
from datetime import datetime
import asyncio
import time
from typing import List, Optional, Tuple, Any
import json
import os

from app.bot.keyboards.main_keyboard import (
    post_actions_kb_for_user,
    get_publish_all_pending_confirmation_keyboard,
    get_delete_post_confirmation_keyboard,
)
from app.bot.keyboards.post_edit_keyboard import (
    get_edit_copy_delete_keyboard,
    get_edit_photo_manage_keyboard,
    get_edit_video_manage_keyboard,
)
from app.bot.utils.post_edit_flow import (
    ARCHIVE_CARD_TEXT_LIMIT,
    edit_draft_from_data,
    format_photo_manage_body,
    format_post_card_for_user,
    format_video_manage_body,
    refresh_edit_panel_message,
    show_edit_panel,
    show_edit_text_prompt,
)
from app.bot.utils.post_media import collect_album_media, format_media_summary
from app.bot.keyboards.post_avito_keyboard import (
    format_avito_publish_block,
    get_avito_publish_keyboard,
    next_body_level,
    next_screen_level,
)
from app.integrations.avito.condition_maps import clamp_avito_level
from app.integrations.avito.autoload_coordinator import get_coordinator
from app.scheduler.queue_manager import QueueManager
from app.config.settings import (
    API_HOST,
    API_PORT,
    TELEGRAM_CONTACT_USER_ID,
    MEDIA_DIR,
    DATABASE_URL,
)
import psycopg2
import logging
from app.services.settings_service import get_settings_service
from app.bot.utils.button_styles import ikb

logger = logging.getLogger(__name__)

# Определение состояний для поиска постов
class PostSearch(StatesGroup):
    waiting_for_query = State()

# Состояния панели редактирования поста
class PostEdit(StatesGroup):
    panel = State()
    waiting_for_text = State()
    manage_photos = State()
    manage_videos = State()


def _set_edit_mode(bot, user_id: int, enabled: bool) -> None:
    if not hasattr(bot, "user_data"):
        bot.user_data = {}
    if user_id not in bot.user_data:
        bot.user_data[user_id] = {}
    bot.user_data[user_id]["in_edit_mode"] = enabled


async def _init_edit_state(state: FSMContext, post: dict, post_id: str) -> None:
    await state.update_data(
        edit_post_id=post_id,
        edit_post_text=post.get("text", "") or "",
        edit_post_photos=list(post.get("photos") or []),
        edit_post_videos=list(post.get("videos") or []),
        original_post=post,
    )

router = Router()


def _post_actions_kb(bot, user_id: int):
    """Клавиатура действий с постом с учётом in_archive в user_data."""
    ud = bot.user_data.get(user_id, {}) if hasattr(bot, "user_data") else {}
    return post_actions_kb_for_user(ud)


def get_signature_state(bot) -> bool:
    """Возвращает текущее состояние переключателя подписи."""
    try:
        return get_settings_service().is_signature_enabled()
    except Exception:
        return getattr(bot, "signature_enabled", True)


def set_signature_state(bot, value: bool) -> None:
    """Обновляет глобальное состояние подписи."""
    setattr(bot, "signature_enabled", value)
    try:
        get_settings_service().set_signature_enabled(value)
    except Exception:
        pass


def build_signature_toggle_button_text(is_enabled: bool) -> str:
    """Возвращает текст для кнопки переключателя подписи с учетом состояния."""
    return ("🟢 " if is_enabled else "🔴 ") + "Подпись"


def get_vk_market_state(bot) -> bool:
    """Возвращает текущее состояние переключателя публикации в товары ВК."""
    try:
        return get_settings_service().is_vk_market_enabled()
    except Exception:
        return getattr(bot, "vk_market_enabled", True)


def set_vk_market_state(bot, value: bool) -> None:
    """Обновляет состояние переключателя публикации в товары ВК."""
    setattr(bot, "vk_market_enabled", value)
    try:
        get_settings_service().set_vk_market_enabled(value)
    except Exception:
        pass


def build_vk_market_toggle_button_text(is_enabled: bool) -> str:
    """Возвращает текст для кнопки переключателя публикации в товары ВК."""
    return ("🟢 " if is_enabled else "🔴 ") + "Товары ВК"

# Вспомогательная функция для безопасного редактирования сообщений
async def safe_edit_message(message, text, reply_markup=None):
    """Безопасно редактирует сообщение или отправляет новое, если редактирование невозможно."""
    try:
        await message.edit_text(text, reply_markup=reply_markup)
        return message
    except TelegramBadRequest as e:
        if "message can't be edited" in str(e):
            print(f"Message can't be edited, sending new message: {str(e)}")
            return await message.reply(text, reply_markup=reply_markup)
        else:
            raise e
    except Exception as e:
        print(f"Error editing message: {str(e)}")
        return await message.reply(text, reply_markup=reply_markup)

# API client functions
async def get_posts_api(is_archived=False, search_query=None):
    """Get posts from API with optional search query."""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"http://{API_HOST}:{API_PORT}/api/posts/"

            # Add search parameter if provided
            params = {}
            if search_query:
                params["search"] = search_query
                print(f"Searching posts with query: {search_query}")

            print(f"Fetching posts from {url}")

            try:
                async with session.get(url, params=params) as response:
                    print(f"API response status: {response.status}")

                    if response.status == 200:
                        data = await response.json()

                        # Check if data is None
                        if data is None:
                            print("Warning: API returned None")
                            posts = []
                        # Handle different response formats
                        elif isinstance(data, dict):
                            posts = data.get("posts", [])
                        elif hasattr(data, "posts"):
                            # If data is a Pydantic model
                            posts = data.posts if data.posts else []
                        elif isinstance(data, list):
                            # If API returns list directly
                            posts = data
                        else:
                            print(f"Warning: Unexpected data type from API: {type(data)}")
                            posts = []
                        
                        # Ensure posts is a list and filter out None values
                        if posts is None:
                            posts = []
                        if not isinstance(posts, list):
                            posts = []
                        
                        # Filter out None values and ensure each post is a dict
                        filtered_posts = []
                        for p in posts:
                            if p is None:
                                continue
                            # Convert Pydantic model to dict if needed
                            if hasattr(p, "dict"):
                                p = p.dict()
                            elif hasattr(p, "model_dump"):
                                p = p.model_dump()
                            elif not isinstance(p, dict):
                                print(f"Warning: Post is not a dict: {type(p)}")
                                continue
                            filtered_posts.append(p)
                        posts = filtered_posts
                        print(f"Received {len(posts)} posts from API")

                        # Отладочный вывод для поиска
                        if search_query:
                            print(f"Search results for '{search_query}':")
                            for i, post in enumerate(posts, 1):
                                if post and isinstance(post, dict):
                                    print(f"{i}. Post ID: {post.get('id')}, Name: {post.get('name')}")
                                    text = post.get('text', '') or ''
                                    print(f"   Text: {text[:100]}...")

                        # If searching, return all posts without filtering by archive status
                        if search_query:
                            return posts

                        # Filter posts based on archive status
                        if is_archived:
                            # В архиве показываем ВСЕ посты, независимо от статуса публикации
                            filtered_posts = posts
                        else:
                            # Черновики — через /api/posts/pending
                            filtered_posts = []

                        print(f"Filtered to {len(filtered_posts)} posts (is_archived={is_archived})")
                        return filtered_posts
                    else:
                        error_text = await response.text()
                        print(f"API Error: {response.status} - {error_text}")
                        return []
            except Exception as e:
                print(f"Error during API request: {str(e)}")
                return []
    except Exception as e:
        print(f"Error in get_posts_api: {str(e)}")
        return []

async def get_archive_summary_api():
    """Fetch lightweight archive summary directly from DB (no HTTP loopback)."""
    from app.db.database import run_db
    from app.services.archive_service import fetch_archive_summary

    try:
        return await run_db(fetch_archive_summary)
    except Exception as e:
        print(f"Error in get_archive_summary_api: {e}")
        return []


async def get_archive_day_api(year: int, month: int, day: int):
    """Fetch lightweight list of posts for a specific UTC day via run_db."""
    from app.db.database import run_db
    from app.services.archive_service import fetch_archive_day

    try:
        return await run_db(fetch_archive_day, year, month, day)
    except Exception as e:
        print(f"Error in get_archive_day_api: {e}")
        return []


async def get_pending_count_api() -> int:
    """Количество черновиков (лёгкий запрос)."""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"http://{API_HOST}:{API_PORT}/api/posts/pending/count"
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return int(data.get("count", 0)) if isinstance(data, dict) else 0
                print(f"Pending count API error: {response.status}")
                return 0
    except Exception as e:
        print(f"Error in get_pending_count_api: {e}")
        return 0


async def get_pending_posts_api():
    """Список черновиков (лёгкий запрос без загрузки всей БД)."""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"http://{API_HOST}:{API_PORT}/api/posts/pending"
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("posts", []) if isinstance(data, dict) else []
                print(f"Pending posts API error: {response.status}")
                return []
    except Exception as e:
        print(f"Error in get_pending_posts_api: {e}")
        return []


async def get_post_api(post_id):
    """Get a specific post from API."""
    t0 = time.perf_counter()
    try:
        async with aiohttp.ClientSession() as session:
            url = f"http://{API_HOST}:{API_PORT}/api/posts/{post_id}"
            async with session.get(url) as response:
                elapsed_ms = (time.perf_counter() - t0) * 1000
                if response.status == 200:
                    post_data = await response.json()
                    return post_data
                logger.warning(
                    "get_post_api failed post_id=%s status=%s elapsed_ms=%.0f",
                    post_id,
                    response.status,
                    elapsed_ms,
                )
                return None
    except Exception as e:
        logger.warning(
            "get_post_api failed post_id=%s after_ms=%.0f: %s",
            post_id,
            (time.perf_counter() - t0) * 1000,
            e,
        )
        return None


async def get_post_card_api(post_id: str, truncate: int = 1200) -> Optional[dict]:
    """Get a post optimized for card viewing (truncated text)."""
    t0 = time.perf_counter()
    try:
        async with aiohttp.ClientSession() as session:
            url = f"http://{API_HOST}:{API_PORT}/api/posts/{post_id}/card"
            params = {"truncate": truncate}
            async with session.get(url, params=params) as response:
                elapsed_ms = (time.perf_counter() - t0) * 1000
                if response.status == 200:
                    return await response.json()
                logger.warning(
                    "get_post_card_api failed post_id=%s status=%s elapsed_ms=%.0f",
                    post_id,
                    response.status,
                    elapsed_ms,
                )
                return None
    except Exception as e:
        logger.warning(
            "get_post_card_api failed post_id=%s after_ms=%.0f: %s",
            post_id,
            (time.perf_counter() - t0) * 1000,
            e,
        )
        return None


async def get_product_by_post_id_api(post_id: str) -> Optional[dict]:
    """Товар по post_id (если есть)."""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"http://{API_HOST}:{API_PORT}/api/products/post/{post_id}"
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.json()
                return None
    except Exception as e:
        logger.warning("get_product_by_post_id_api failed: %s", e)
        return None


async def resolve_avito_item_id_for_post(post: dict) -> Optional[str]:
    if not post:
        return None
    pid = post.get("avito_item_id")
    if pid:
        return str(pid).strip()
    post_id = post.get("id")
    if not post_id:
        return None
    prod = await get_product_by_post_id_api(post_id)
    if prod and prod.get("avito_item_id"):
        return str(prod["avito_item_id"]).strip()
    return None


async def delete_post_api(post_id):
    """Delete a post via API."""
    def _delete_post_via_psycopg_sync(target_post_id: str) -> bool:
        conn = None
        try:
            conn = psycopg2.connect(DATABASE_URL)
            conn.autocommit = False
            with conn.cursor() as cur:
                cur.execute("SELECT storage_path FROM posts WHERE id = %s LIMIT 1", (target_post_id,))
                row = cur.fetchone()
                if not row:
                    conn.rollback()
                    return False
                storage_path = row[0]
                cur.execute("DELETE FROM products WHERE post_id = %s", (target_post_id,))
                cur.execute("DELETE FROM posts WHERE id = %s", (target_post_id,))
            conn.commit()
            post_dir = MEDIA_DIR / storage_path
            if os.path.exists(post_dir):
                import shutil
                shutil.rmtree(post_dir)
            return True
        except Exception:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if conn is not None:
                conn.close()

    try:
        success = await asyncio.to_thread(_delete_post_via_psycopg_sync, post_id)
        return success
    except Exception as e:
        print(f"Error in delete_post_api: {type(e).__name__}: {str(e)}")
        return False

def _publish_api_error_detail(status: int, error_text: str) -> str:
    """Извлекает человекочитаемый detail из ответа FastAPI (JSON) или сырого текста."""
    raw = (error_text or "").strip()
    if not raw:
        return f"HTTP {status}"
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw[:3500]
    detail = data.get("detail")
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if isinstance(item, dict):
                parts.append(str(item.get("msg") or item))
            else:
                parts.append(str(item))
        return "; ".join(parts)[:3500]
    if detail is not None:
        return str(detail)[:3500]
    return raw[:3500]


async def publish_post_api(
    post_id, platform, signature_enabled: Optional[bool] = None
) -> Tuple[Optional[Any], Optional[str]]:
    """Публикация поста на платформу через API. Возвращает (result_json, None) или (None, detail)."""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"http://{API_HOST}:{API_PORT}/api/posts/{post_id}/publish/{platform}"
            print(f"Publishing post to {platform} via {url}")

            request_kwargs = {}
            if signature_enabled is not None:
                request_kwargs["json"] = {"signature_enabled": signature_enabled}

            try:
                async with session.post(url, **request_kwargs) as response:
                    print(f"API response status: {response.status}")

                    if response.status == 200:
                        result = await response.json()
                        return result, None
                    error_text = await response.text()
                    print(f"API Error: {response.status} - {error_text}")
                    return None, _publish_api_error_detail(response.status, error_text)
            except Exception as e:
                print(f"Error during API request: {str(e)}")
                return None, str(e)
    except Exception as e:
        print(f"Error in publish_post_api: {str(e)}")
        return None, str(e)

async def create_story_api(post_id, platform):
    """Create a story for a post via API."""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"http://{API_HOST}:{API_PORT}/api/stories/{post_id}/platform/{platform}"
            print(f"Creating story for platform {platform} via {url}")

            try:
                async with session.post(url) as response:
                    print(f"API response status: {response.status}")

                    if response.status == 201:
                        result = await response.json()
                        return result
                    else:
                        error_text = await response.text()
                        print(f"API Error: {response.status} - {error_text}")
                        return None
            except Exception as e:
                print(f"Error during API request: {str(e)}")
                return None
    except Exception as e:
        print(f"Error in create_story_api: {str(e)}")
        return None

async def publish_story_api(story_id):
    """Publish a story via API."""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"http://{API_HOST}:{API_PORT}/api/stories/{story_id}/publish"
            print(f"Publishing story via {url}")

            try:
                async with session.post(url) as response:
                    print(f"API response status: {response.status}")

                    if response.status == 200:
                        result = await response.json()
                        return result
                    else:
                        error_text = await response.text()
                        print(f"API Error: {response.status} - {error_text}")
                        return None
            except Exception as e:
                print(f"Error during API request: {str(e)}")
                return None
    except Exception as e:
        print(f"Error in publish_story_api: {str(e)}")
        return None

async def update_post_api(post_id, text=None, photos=None, videos=None, avito_draft=None):
    """Update a post via API."""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"http://{API_HOST}:{API_PORT}/api/posts/{post_id}"
            print(f"Updating post via {url}")

            # Подготовка данных для обновления
            data = {}
            if text is not None:
                data["text"] = text
            if photos is not None:
                data["photos"] = photos
            if videos is not None:
                data["videos"] = videos
            if avito_draft is not None:
                data["avito_draft"] = avito_draft

            print(f"Update data: {data}")

            # Так как в API нет метода PUT/PATCH, используем POST с дополнительным параметром
            data["_method"] = "update"

            try:
                async with session.post(url, json=data) as response:
                    print(f"API response status: {response.status}")

                    if response.status == 200:
                        result = await response.json()
                        return result
                    else:
                        error_text = await response.text()
                        print(f"API Error: {response.status} - {error_text}")
                        return None
            except Exception as e:
                print(f"Error during API request: {str(e)}")
                return None
    except Exception as e:
        print(f"Error in update_post_api: {str(e)}")
        return None

_RU_MONTH_NOM = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
}
_RU_MONTH_GEN = {
    1: "Января", 2: "Февраля", 3: "Марта", 4: "Апреля",
    5: "Мая", 6: "Июня", 7: "Июля", 8: "Августа",
    9: "Сентября", 10: "Октября", 11: "Ноября", 12: "Декабря",
}


def _fmt_date(value, fmt: str) -> str:
    """Format an ISO datetime string (or datetime) as `fmt` or '—'."""
    if not value:
        return "—"
    try:
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime(fmt)
        if hasattr(value, "strftime"):
            return value.strftime(fmt)
    except Exception:
        return "—"
    return "—"


async def _show_archived_posts_fast(message: Message, year=None, month=None, day=None, user_id: Optional[int] = None):
    """Render archive screens using lightweight summary + per-day endpoints."""
    # Get aggregate counts (one cheap SQL on the API side)
    buckets = await get_archive_summary_api()

    # Build counts for the navigation tree
    year_counts: dict = {}
    month_counts: dict = {}
    day_counts: dict = {}
    for b in buckets:
        y, m, d, c = b["year"], b["month"], b["day"], b["count"]
        year_counts[y] = year_counts.get(y, 0) + c
        month_counts[(y, m)] = month_counts.get((y, m), 0) + c
        day_counts[(y, m, d)] = day_counts.get((y, m, d), 0) + c

    if not year_counts and day is None:
        buttons = [[InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")]]
        await message.edit_text("📭 Архив пуст.", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        return

    today = datetime.now().date()
    buttons: list = []

    if year is None:
        # Root level: today's posts (lightweight) + year buttons
        response_text = "📁 Архив постов:\n\n"

        today_posts = await get_archive_day_api(today.year, today.month, today.day)
        if today_posts:
            response_text += f"📅 Сегодня ({today.strftime('%d.%m.%Y')}):\n\n"
            for i, post in enumerate(today_posts, 1):
                post_name = post.get("name") or "Без названия"
                photos = post.get("photos") or []
                videos = post.get("videos") or []
                photo_count = len(photos) if isinstance(photos, list) else 0
                video_count = len(videos) if isinstance(videos, list) else 0
                response_text += f"{i}. {post_name}\n"
                response_text += f"   Медиа: {photo_count}📷 {video_count}📹\n\n"
                button_text = f"{i}. {post_name[:30]}{'...' if len(post_name) > 30 else ''}"
                buttons.append([InlineKeyboardButton(text=button_text, callback_data=f"view_post_{post.get('id')}")])
            response_text += "📂 Архив по годам:\n\n"

        # Year buttons (skip current year if all its posts are 'today')
        years_sorted = sorted(year_counts.keys(), reverse=True)
        for y in years_sorted:
            cnt = year_counts[y]
            if y == today.year and today_posts:
                cnt -= len(today_posts)
                if cnt <= 0:
                    continue
            buttons.append([InlineKeyboardButton(
                text=f"📅 {y} ({cnt} постов)",
                callback_data=f"archive_year_{y}",
            )])

    elif month is None:
        response_text = f"📁 Архив постов за {year} год:\n\n"
        months = sorted({m for (y, m) in month_counts.keys() if y == year}, reverse=True)
        for m in months:
            cnt = month_counts[(year, m)]
            buttons.append([InlineKeyboardButton(
                text=f"📅 {_RU_MONTH_NOM.get(m, str(m))} ({cnt} постов)",
                callback_data=f"archive_month_{year}_{m}",
            )])
        buttons.append([InlineKeyboardButton(text="⬅️ Назад к годам", callback_data="archive_root")])

    elif day is None:
        month_gen = _RU_MONTH_GEN.get(month, str(month))
        response_text = f"📁 Архив постов за {month_gen} {year} года:\n\n"
        days = sorted({d for (y, m, d) in day_counts.keys() if y == year and m == month}, reverse=True)
        for d in days:
            cnt = day_counts[(year, month, d)]
            buttons.append([InlineKeyboardButton(
                text=f"📅 {d} {month_gen} ({cnt} постов)",
                callback_data=f"archive_day_{year}_{month}_{d}",
            )])
        buttons.append([InlineKeyboardButton(text="⬅️ Назад к месяцам", callback_data=f"archive_year_{year}")])

    else:
        month_gen = _RU_MONTH_GEN.get(month, str(month))
        response_text = f"📁 Архив постов за {day} {month_gen} {year} года:\n\n"
        day_posts = await get_archive_day_api(year, month, day)
        for i, post in enumerate(day_posts, 1):
            post_name = post.get("name") or "Без названия"
            created_at_str = post.get("created_at")
            try:
                created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00")) if created_at_str else None
            except Exception:
                created_at = None
            photos = post.get("photos") or []
            videos = post.get("videos") or []
            photo_count = len(photos) if isinstance(photos, list) else 0
            video_count = len(videos) if isinstance(videos, list) else 0
            vk_date = _fmt_date(post.get("published_vk_at"), "%d.%m.%Y")
            tg_date = _fmt_date(post.get("published_telegram_at"), "%d.%m.%Y")
            ig_date = _fmt_date(post.get("published_instagram_at"), "%d.%m.%Y")
            max_date = _fmt_date(post.get("published_max_at"), "%d.%m.%Y")
            response_text += f"{i}. {post_name}\n"
            if created_at:
                response_text += f"   Создан: {created_at.strftime('%H:%M')}\n"
            response_text += f"   Медиа: {photo_count}📷 {video_count}📹\n"
            response_text += f"   Опубликован: ВК ({vk_date}), ТГ ({tg_date}), IG ({ig_date}), MAX ({max_date})\n\n"
            button_text = f"{i}. {post_name[:30]}{'...' if len(post_name) > 30 else ''}"
            buttons.append([InlineKeyboardButton(text=button_text, callback_data=f"view_post_{post.get('id')}")])
        buttons.append([InlineKeyboardButton(text="⬅️ Назад к дням", callback_data=f"archive_month_{year}_{month}")])

    buttons.append([InlineKeyboardButton(text="🔍 Поиск", callback_data="search_posts")])
    buttons.append([InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")])

    # Persist navigation state and post IDs for post-detail/back navigation.
    # NOTE: message.from_user for callback.message refers to the BOT (sender),
    # not the user — must use the explicit user_id passed in by the callback
    # handler (or chat.id as a private-chat fallback).
    resolved_user_id = user_id
    if resolved_user_id is None and hasattr(message, "chat") and hasattr(message.chat, "id"):
        resolved_user_id = message.chat.id
    if resolved_user_id is not None and hasattr(message, "bot") and hasattr(message.bot, "user_data"):
        if resolved_user_id not in message.bot.user_data:
            message.bot.user_data[resolved_user_id] = {}
        message.bot.user_data[resolved_user_id]["archive_state"] = {
            "year": year, "month": month, "day": day,
        }
        # Mark that the user is currently inside the archive flow so that the
        # post-detail keyboard shows "⬅️ Назад к архиву" even on the root level
        # (where archive_state.year is None).
        message.bot.user_data[resolved_user_id]["in_archive"] = True
        if day is not None:
            posts_to_store = day_posts if "day_posts" in locals() else []
            message.bot.user_data[resolved_user_id].update(
                {f"post_{i}": p.get("id") for i, p in enumerate(posts_to_store, 1)}
            )
        elif year is None and "today_posts" in locals() and today_posts:
            message.bot.user_data[resolved_user_id].update(
                {f"post_{i}": p.get("id") for i, p in enumerate(today_posts, 1)}
            )

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.edit_text(response_text, reply_markup=keyboard)


async def show_archived_posts(message: Message, year=None, month=None, day=None, search_results=None, user_id: Optional[int] = None):
    """Show archived posts with date-based navigation.

    Args:
        message: The message to edit
        year: Optional year to filter posts
        month: Optional month to filter posts (requires year)
        day: Optional day to filter posts (requires year and month)
        search_results: Optional list of posts from search
    """
    try:
        # FAST PATH: when not showing search results, use lightweight archive endpoints
        # (aggregate counts + per-day list) instead of pulling the full posts list.
        if search_results is None:
            await _show_archived_posts_fast(message, year=year, month=month, day=day, user_id=user_id)
            return

        # Search-results branch keeps the legacy behaviour (full posts already passed in).
        posts = search_results
        print(f"show_archived_posts received search_results: {len(search_results)} posts")
        for i, post in enumerate(search_results, 1):
            print(f"  {i}. Post ID: {post.get('id')}, Name: {post.get('name')}")
            print(f"     Text: {post.get('text', '')[:50]}...")

        # Ensure posts is a list, not None
        if posts is None:
            posts = []

        if not posts:
            # Create back button
            buttons = [[InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")]]
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

            await message.edit_text(
                "📭 Архив пуст.",
                reply_markup=keyboard
            )
            return

        # Check if we're showing search results
        is_search_results = search_results is not None

        # Если это результаты поиска, просто отображаем их без группировки по датам
        if is_search_results:
            response_text = "🔍 Результаты поиска:\n\n"
            buttons = []

            for i, post in enumerate(posts, 1):
                if post is None:
                    continue
                post_name = post.get("name") or "Без названия"
                photos = post.get("photos") or []
                videos = post.get("videos") or []
                photo_count = len(photos) if isinstance(photos, list) else 0
                video_count = len(videos) if isinstance(videos, list) else 0
                text = post.get("text", "")[:100] + "..." if len(post.get("text", "")) > 100 else post.get("text", "")

                response_text += f"{i}. {post_name}\n"
                response_text += f"   Медиа: {photo_count}📷 {video_count}📹\n"
                response_text += f"   Текст: {text}\n\n"

                # Add button for this post
                safe_post_name = post_name or "Без названия"
                button_text = f"{i}. {safe_post_name[:30]}{'...' if len(safe_post_name) > 30 else ''}"
                buttons.append([InlineKeyboardButton(
                    text=button_text,
                    callback_data=f"view_post_{post.get('id')}"
                )])

            # Add search button
            buttons.append([InlineKeyboardButton(text="🔍 Новый поиск", callback_data="search_posts")])

            # Add main menu button
            buttons.append([InlineKeyboardButton(text="📁 Вернуться в архив", callback_data="archive_root")])
            buttons.append([InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")])

            # Create keyboard
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

            await message.edit_text(response_text, reply_markup=keyboard)
            return

        # Group posts by date for regular archive view
        today = datetime.now().date()
        posts_by_date = {}
        posts_today = []

        for post in posts:
            if post is None:
                continue
            if not isinstance(post, dict):
                continue
            try:
                created_at_str = post.get("created_at", "")
                if not created_at_str:
                    print(f"Warning: Post {post.get('id')} has no created_at date")
                    continue

                created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                post_date = created_at.date()
                post_year = post_date.year
                post_month = post_date.month
                post_day = post_date.day
            except Exception as e:
                print(f"Error processing post {post.get('id')}: {str(e)}")
                print(f"Post data: {post}")
                import traceback
                print(f"Traceback: {traceback.format_exc()}")
                continue

            # Check if post matches the filter criteria
            if year is not None and post_year != year:
                continue
            if month is not None and post_month != month:
                continue
            if day is not None and post_day != day:
                continue

            # Separate today's posts
            if post_date == today:
                posts_today.append(post)
                continue

            # Group by year, month, day
            if post_year not in posts_by_date:
                posts_by_date[post_year] = {}
            if post_month not in posts_by_date[post_year]:
                posts_by_date[post_year][post_month] = {}
            if post_day not in posts_by_date[post_year][post_month]:
                posts_by_date[post_year][post_month][post_day] = []

            posts_by_date[post_year][post_month][post_day].append(post)

        # Create buttons and response text based on navigation level
        buttons = []

        # Determine the title and content based on the navigation level
        if year is None:
            # Root level - show today's posts and years
            if is_search_results:
                response_text = "🔍 Результаты поиска:\n\n"
            else:
                response_text = "📁 Архив постов:\n\n"

            # Show today's posts first
            if posts_today:
                response_text += f"📅 Сегодня ({today.strftime('%d.%m.%Y')}):\n\n"
                for i, post in enumerate(posts_today, 1):
                    if post is None:
                        continue
                    post_name = post.get("name") or "Без названия"
                    photos = post.get("photos") or []
                    videos = post.get("videos") or []
                    photo_count = len(photos) if isinstance(photos, list) else 0
                    video_count = len(videos) if isinstance(videos, list) else 0

                    response_text += f"{i}. {post_name}\n"
                    response_text += f"   Медиа: {photo_count}📷 {video_count}📹\n\n"

                    # Add button for this post
                    safe_post_name = post_name or "Без названия"
                    button_text = f"{i}. {safe_post_name[:30]}{'...' if len(safe_post_name) > 30 else ''}"
                    buttons.append([InlineKeyboardButton(
                        text=button_text,
                        callback_data=f"view_post_{post.get('id')}"
                    )])

                response_text += "📂 Архив по годам:\n\n"

            # Add year buttons
            years = sorted(posts_by_date.keys(), reverse=True)
            for year_val in years:
                # Count posts in this year
                year_post_count = 0
                if year_val in posts_by_date:
                    for month_val in posts_by_date[year_val]:
                        if month_val in posts_by_date[year_val]:
                            for day_val in posts_by_date[year_val][month_val]:
                                if day_val in posts_by_date[year_val][month_val]:
                                    year_post_count += len(posts_by_date[year_val][month_val][day_val])

                buttons.append([InlineKeyboardButton(
                    text=f"📅 {year_val} ({year_post_count} постов)",
                    callback_data=f"archive_year_{year_val}"
                )])

        elif month is None:
            # Year level - show months
            response_text = f"📁 Архив постов за {year} год:\n\n"

            # Add month buttons
            if year not in posts_by_date:
                months = []
            else:
                months = sorted(posts_by_date[year].keys(), reverse=True)
            for month_val in months:
                # Count posts in this month
                month_post_count = 0
                if year in posts_by_date and month_val in posts_by_date[year]:
                    for day_val in posts_by_date[year][month_val]:
                        if day_val in posts_by_date[year][month_val]:
                            month_post_count += len(posts_by_date[year][month_val][day_val])

                # Get month name
                month_name = {
                    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
                    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
                    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
                }.get(month_val, str(month_val))

                buttons.append([InlineKeyboardButton(
                    text=f"📅 {month_name} ({month_post_count} постов)",
                    callback_data=f"archive_month_{year}_{month_val}"
                )])

            # Add back button
            buttons.append([InlineKeyboardButton(
                text="⬅️ Назад к годам",
                callback_data="archive_root"
            )])

        elif day is None:
            # Month level - show days
            month_name = {
                1: "Января", 2: "Февраля", 3: "Марта", 4: "Апреля",
                5: "Мая", 6: "Июня", 7: "Июля", 8: "Августа",
                9: "Сентября", 10: "Октября", 11: "Ноября", 12: "Декабря"
            }.get(month, str(month))

            response_text = f"📁 Архив постов за {month_name} {year} года:\n\n"

            # Add day buttons
            if year not in posts_by_date or month not in posts_by_date[year]:
                days = []
            else:
                days = sorted(posts_by_date[year][month].keys(), reverse=True)
            for day_val in days:
                # Count posts on this day
                if year in posts_by_date and month in posts_by_date[year] and day_val in posts_by_date[year][month]:
                    day_post_count = len(posts_by_date[year][month][day_val])
                else:
                    day_post_count = 0

                buttons.append([InlineKeyboardButton(
                    text=f"📅 {day_val} {month_name} ({day_post_count} постов)",
                    callback_data=f"archive_day_{year}_{month}_{day_val}"
                )])

            # Add back button
            buttons.append([InlineKeyboardButton(
                text="⬅️ Назад к месяцам",
                callback_data=f"archive_year_{year}"
            )])

        else:
            # Day level - show posts for this day
            month_name = {
                1: "Января", 2: "Февраля", 3: "Марта", 4: "Апреля",
                5: "Мая", 6: "Июня", 7: "Июля", 8: "Августа",
                9: "Сентября", 10: "Октября", 11: "Ноября", 12: "Декабря"
            }.get(month, str(month))

            response_text = f"📁 Архив постов за {day} {month_name} {year} года:\n\n"

            # Show posts for this day
            if year in posts_by_date and month in posts_by_date[year] and day in posts_by_date[year][month]:
                day_posts = posts_by_date[year][month][day]
            else:
                day_posts = []
            for i, post in enumerate(day_posts, 1):
                if post is None:
                    continue
                post_name = post.get("name") or "Без названия"
                created_at_str = post.get("created_at")
                if not created_at_str:
                    continue
                created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                photos = post.get("photos") or []
                videos = post.get("videos") or []
                photo_count = len(photos) if isinstance(photos, list) else 0
                video_count = len(videos) if isinstance(videos, list) else 0

                # Add platform status indicators
                vk_published_at = post.get("published_vk_at")
                tg_published_at = post.get("published_telegram_at")

                vk_date = "—"
                if vk_published_at:
                    try:
                        if isinstance(vk_published_at, str):
                            vk_date = datetime.fromisoformat(vk_published_at.replace("Z", "+00:00")).strftime("%d.%m.%Y")
                        else:
                            vk_date = vk_published_at.strftime("%d.%m.%Y") if hasattr(vk_published_at, 'strftime') else "—"
                    except:
                        vk_date = "—"
                
                tg_date = "—"
                if tg_published_at:
                    try:
                        if isinstance(tg_published_at, str):
                            tg_date = datetime.fromisoformat(tg_published_at.replace("Z", "+00:00")).strftime("%d.%m.%Y")
                        else:
                            tg_date = tg_published_at.strftime("%d.%m.%Y") if hasattr(tg_published_at, 'strftime') else "—"
                    except:
                        tg_date = "—"

                response_text += f"{i}. {post_name}\n"
                response_text += f"   Создан: {created_at.strftime('%H:%M')}\n"
                response_text += f"   Медиа: {photo_count}📷 {video_count}📹\n"

                # Добавляем статус Instagram
                ig_published_at = post.get("published_instagram_at")
                ig_date = "—"
                if ig_published_at:
                    try:
                        if isinstance(ig_published_at, str):
                            ig_date = datetime.fromisoformat(ig_published_at.replace("Z", "+00:00")).strftime("%d.%m.%Y")
                        else:
                            ig_date = ig_published_at.strftime("%d.%m.%Y") if hasattr(ig_published_at, 'strftime') else "—"
                    except:
                        ig_date = "—"

                max_published_at = post.get("published_max_at")
                max_date = "—"
                if max_published_at:
                    try:
                        if isinstance(max_published_at, str):
                            max_date = datetime.fromisoformat(max_published_at.replace("Z", "+00:00")).strftime("%d.%m.%Y")
                        else:
                            max_date = max_published_at.strftime("%d.%m.%Y") if hasattr(max_published_at, "strftime") else "—"
                    except Exception:
                        max_date = "—"
                response_text += f"   Опубликован: ВК ({vk_date}), ТГ ({tg_date}), IG ({ig_date}), MAX ({max_date})\n\n"

                # Add button for this post
                safe_post_name = post_name or "Без названия"
                button_text = f"{i}. {safe_post_name[:30]}{'...' if len(safe_post_name) > 30 else ''}"
                buttons.append([InlineKeyboardButton(
                    text=button_text,
                    callback_data=f"view_post_{post.get('id')}"
                )])

            # Add back button
            buttons.append([InlineKeyboardButton(
                text="⬅️ Назад к дням",
                callback_data=f"archive_month_{year}_{month}"
            )])

        # Add search button
        buttons.append([InlineKeyboardButton(text="🔍 Поиск", callback_data="search_posts")])

        # Add main menu button
        buttons.append([InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")])

        # Create keyboard
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        # Store post IDs in user data (for backward compatibility)
        if hasattr(message, 'bot') and hasattr(message.bot, 'user_data') and hasattr(message, 'from_user'):
            if message.from_user.id not in message.bot.user_data:
                message.bot.user_data[message.from_user.id] = {}

            # Store current archive navigation state
            message.bot.user_data[message.from_user.id]["archive_state"] = {
                "year": year,
                "month": month,
                "day": day
            }

            # Store post IDs if we're showing posts
            if day is not None:
                # Если мы на уровне дня, используем day_posts
                if year in posts_by_date and month in posts_by_date[year] and day in posts_by_date[year][month]:
                    posts_to_store = posts_by_date[year][month][day]
                else:
                    posts_to_store = []
                user_data = {f"post_{i}": post.get("id") for i, post in enumerate(posts_to_store, 1)}
                message.bot.user_data[message.from_user.id].update(user_data)
            elif posts_today:
                # Если мы на корневом уровне и есть посты за сегодня
                user_data = {f"post_{i}": post.get("id") for i, post in enumerate(posts_today, 1)}
                message.bot.user_data[message.from_user.id].update(user_data)

        await message.edit_text(response_text, reply_markup=keyboard)
    except Exception as e:
        logger.exception("show_archived_posts failed")
        buttons = [[InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")]]
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        await message.edit_text(
            f"❌ Ошибка при загрузке архива: {str(e)}",
            reply_markup=keyboard
        )

def _user_data_for(bot, user_id: int) -> dict:
    if not hasattr(bot, "user_data"):
        return {}
    if user_id not in bot.user_data:
        bot.user_data[user_id] = {}
    return bot.user_data[user_id]


def _is_from_archive(user_data: dict | None) -> bool:
    ud = user_data or {}
    archive_state = ud.get("archive_state") or {}
    return bool(ud.get("in_archive") or archive_state.get("year") is not None)


@router.callback_query(lambda c: c.data and c.data.startswith("view_post_"))
async def view_post_callback(callback: CallbackQuery):
    """Handle post selection via callback query."""
    await callback.answer()
    # Extract post_id from callback data
    post_id = callback.data.replace("view_post_", "")

    user_data = _user_data_for(callback.bot, callback.from_user.id)
    truncate = ARCHIVE_CARD_TEXT_LIMIT if _is_from_archive(user_data) else 1000
    post = await get_post_card_api(post_id, truncate=truncate)

    if not post:
        await callback.message.edit_text(
            "❌ Пост не найден. Возможно, он был удален.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")]
            ])
        )
        return

    user_data["selected_post"] = post_id
    response_text, parse_mode = format_post_card_for_user(post, user_data)

    await callback.message.edit_text(
        response_text,
        reply_markup=post_actions_kb_for_user(user_data),
        parse_mode=parse_mode,
    )

# Оставляем для обратной совместимости
@router.message(lambda message: message.text and message.text.isdigit() and
                (not hasattr(message, 'bot') or
                not hasattr(message.bot, 'user_data') or
                not message.bot.user_data.get(message.from_user.id, {}).get("in_search_mode", False)) and
                not message.bot.user_data.get(message.from_user.id, {}).get("in_edit_mode", False) and
                not message.bot.user_data.get(message.from_user.id, {}).get("in_menu_constructor_mode", False))
async def process_post_selection(message: Message, state: FSMContext):
    """Process post selection by number."""
    # Проверяем, не находится ли пользователь в состоянии редактирования цены товара или поиска товаров
    current_state = await state.get_state()
    if current_state:
        return
    
    post_number = int(message.text)

    # Get user data
    user_data = message.bot.user_data.get(message.from_user.id, {})
    post_id = user_data.get(f"post_{post_number}")

    if not post_id:
        await message.reply("❌ Неверный номер поста. Пожалуйста, выберите пост из списка.")
        return

    user_data = _user_data_for(message.bot, message.from_user.id)
    truncate = ARCHIVE_CARD_TEXT_LIMIT if _is_from_archive(user_data) else 1000
    post = await get_post_card_api(post_id, truncate=truncate)

    if not post:
        await message.reply("❌ Пост не найден. Возможно, он был удален.")
        return

    user_data["selected_post"] = post_id
    response_text, parse_mode = format_post_card_for_user(post, user_data)

    await message.reply(
        response_text,
        reply_markup=_post_actions_kb(message.bot, message.from_user.id),
        parse_mode=parse_mode,
    )

@router.callback_query(F.data == "publish_vk")
async def publish_to_vk(callback: CallbackQuery):
    """Add post to VK queue."""
    # Сразу отвечаем на callback, чтобы избежать ошибки "query is too old"
    await callback.answer("Добавляю пост в очередь ВК...")

    # Get selected post ID
    user_data = callback.bot.user_data.get(callback.from_user.id, {})
    post_id = user_data.get("selected_post")

    if not post_id:
        await callback.message.edit_text("❌ Пост не выбран.")
        return

    # Get post details
    post = await get_post_api(post_id)

    if not post:
        await callback.message.edit_text("❌ Пост не найден.")
        return

    # Check if already published
    if post.get("is_published_vk"):
        # Создаем клавиатуру с кнопками "Далее" и "Назад"
        buttons = [
            [
                InlineKeyboardButton(text="⏭️ Далее", callback_data="republish_vk"),
                InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_post")
            ]
        ]
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        # Используем edit_text вместо answer
        await callback.message.edit_text(
            f"{callback.message.text}\n\nОпубликовать повторно?", 
            reply_markup=keyboard
        )
        return

    try:
        from app.bot.handlers.scheduler import get_orchestrator
        orchestrator = get_orchestrator(callback.bot)
        added = orchestrator.add_post_to_queue(post_id, platforms=["vk"], priority=0)

        if added:
            await callback.message.edit_text(
                f"{callback.message.text}\n\n✅ Добавлено в очередь ВК. Публикация выполнится планировщиком.",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
            )
        else:
            await callback.message.edit_text(
                f"{callback.message.text}\n\n❌ Не удалось добавить в очередь ВК.",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
            )
    except Exception as e:
        await callback.message.edit_text(
            f"{callback.message.text}\n\n❌ Ошибка: {str(e)}",
            reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
        )

@router.callback_query(F.data == "publish_telegram")
async def publish_to_telegram(callback: CallbackQuery):
    """Add post to Telegram queue."""
    # Сразу отвечаем на callback, чтобы избежать ошибки "query is too old"
    await callback.answer("Добавляю пост в очередь Telegram...")

    # Get selected post ID
    user_data = callback.bot.user_data.get(callback.from_user.id, {})
    post_id = user_data.get("selected_post")
    if not post_id:
        await callback.message.edit_text("❌ Пост не выбран.")
        return

    # Get post details
    post = await get_post_api(post_id)

    if not post:
        await callback.message.edit_text("❌ Пост не найден.")
        return

    # Check if already published
    if post.get("is_published_telegram"):
        # Создаем клавиатуру с кнопками "Далее" и "Назад"
        buttons = [
            [
                InlineKeyboardButton(text="⏭️ Далее", callback_data="republish_telegram"),
                InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_post")
            ]
        ]
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        # Используем edit_text вместо answer
        await callback.message.edit_text(
            f"{callback.message.text}\n\nОпубликовать повторно?", 
            reply_markup=keyboard
        )
        return

    try:
        from app.bot.handlers.scheduler import get_orchestrator
        orchestrator = get_orchestrator(callback.bot)
        added = orchestrator.add_post_to_queue(post_id, platforms=["telegram"], priority=0)

        if added:
            await callback.message.edit_text(
                f"{callback.message.text}\n\n✅ Добавлено в очередь Telegram. Публикация выполнится планировщиком.",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
            )
        else:
            await callback.message.edit_text(
                f"{callback.message.text}\n\n❌ Не удалось добавить в очередь Telegram.",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
            )
    except Exception as e:
        await callback.message.edit_text(
            f"{callback.message.text}\n\n❌ Ошибка: {str(e)}",
            reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
        )

@router.callback_query(F.data == "publish_instagram")
async def publish_to_instagram(callback: CallbackQuery):
    """Add post to Instagram queue."""
    # Сразу отвечаем на callback, чтобы избежать ошибки "query is too old"
    await callback.answer("Добавляю пост в очередь Instagram...")

    # Get selected post ID
    user_data = callback.bot.user_data.get(callback.from_user.id, {})
    post_id = user_data.get("selected_post")
    if not post_id:
        await callback.message.edit_text("❌ Пост не выбран.")
        return

    # Get post details
    post = await get_post_api(post_id)

    if not post:
        await callback.message.edit_text("❌ Пост не найден.")
        return

    # Check if already published
    if post.get("is_published_instagram"):
        # Создаем клавиатуру с кнопками "Далее" и "Назад"
        buttons = [
            [
                InlineKeyboardButton(text="⏭️ Далее", callback_data="republish_instagram"),
                InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_post")
            ]
        ]
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        # Используем edit_text вместо answer
        await callback.message.edit_text(
            f"{callback.message.text}\n\nОпубликовать повторно?", 
            reply_markup=keyboard
        )
        return

    try:
        from app.bot.handlers.scheduler import get_orchestrator
        orchestrator = get_orchestrator(callback.bot)
        added = orchestrator.add_post_to_queue(
            post_id,
            platforms=["instagram"],
            priority=0,
            enforce_enabled_filter=False,
        )

        if added:
            await callback.message.edit_text(
                f"{callback.message.text}\n\n✅ Добавлено в очередь Instagram. Публикация выполнится планировщиком.",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
            )
        else:
            await callback.message.edit_text(
                f"{callback.message.text}\n\n❌ Не удалось добавить в очередь Instagram.",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
            )
    except Exception as e:
        await callback.message.edit_text(
            f"{callback.message.text}\n\n❌ Ошибка: {str(e)}",
            reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
        )


@router.callback_query(F.data == "publish_max")
async def publish_to_max(callback: CallbackQuery):
    """Add post to Max queue."""
    await callback.answer("Добавляю пост в очередь Max...")

    user_data = callback.bot.user_data.get(callback.from_user.id, {})
    post_id = user_data.get("selected_post")
    if not post_id:
        await callback.message.edit_text("❌ Пост не выбран.")
        return

    post = await get_post_api(post_id)
    if not post:
        await callback.message.edit_text("❌ Пост не найден.")
        return

    if post.get("is_published_max"):
        buttons = [[
            InlineKeyboardButton(text="⏭️ Далее", callback_data="republish_max"),
            InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_post")
        ]]
        await callback.message.edit_text(
            f"{callback.message.text}\n\nОпубликовать повторно?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
        return

    try:
        from app.bot.handlers.scheduler import get_orchestrator
        orchestrator = get_orchestrator(callback.bot)
        added = orchestrator.add_post_to_queue(post_id, platforms=["max"], priority=0)
        if added:
            await callback.message.edit_text(
                f"{callback.message.text}\n\n✅ Добавлено в очередь Max. Публикация выполнится планировщиком.",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id),
            )
        else:
            await callback.message.edit_text(
                f"{callback.message.text}\n\n❌ Не удалось добавить в очередь Max.",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id),
            )
    except Exception as e:
        await callback.message.edit_text(
            f"{callback.message.text}\n\n❌ Ошибка: {str(e)}",
            reply_markup=_post_actions_kb(callback.bot, callback.from_user.id),
        )


def _avito_pub_store(bot, user_id: int) -> dict:
    if not hasattr(bot, "user_data"):
        bot.user_data = {}
    if user_id not in bot.user_data:
        bot.user_data[user_id] = {}
    return bot.user_data[user_id]


def _draft_levels_from_post(post: dict) -> tuple[int, int]:
    draft = post.get("avito_draft") if isinstance(post.get("avito_draft"), dict) else {}
    sl = clamp_avito_level((draft or {}).get("screen_level", 1))
    bl = clamp_avito_level((draft or {}).get("body_level", 1))
    return sl, bl


async def _show_avito_publish_params(callback: CallbackQuery, post: dict, post_id: str) -> None:
    sl, bl = _draft_levels_from_post(post)
    store = _avito_pub_store(callback.bot, callback.from_user.id)
    store["avito_pub_post_id"] = post_id
    store["avito_pub_screen"] = sl
    store["avito_pub_body"] = bl
    store["avito_pub_base_text"] = callback.message.text or ""
    text = f"{store['avito_pub_base_text']}\n\n{format_avito_publish_block(sl, bl)}"
    await callback.message.edit_text(
        text,
        reply_markup=get_avito_publish_keyboard(sl, bl),
        parse_mode="HTML",
    )


def _avito_queue_status_hint() -> str:
    try:
        qm = QueueManager()
        n = qm.count_pending("avito")
        qm.close()
        return get_coordinator().format_eta_hint(n)
    except Exception:
        return "Авито: выгрузка по расписанию (до 1 раза в час, несколько постов за раз)."


async def _enqueue_avito_autoload(
    callback: CallbackQuery, post_id: str, base_text: str, *, priority: int = 100
) -> bool:
    from app.bot.handlers.scheduler import get_orchestrator

    orch = get_orchestrator(callback.bot)
    ok = orch.add_post_to_queue(
        post_id,
        platforms=["avito"],
        priority=priority,
        allow_avito_without_vk_market=True,
    )
    hint = _avito_queue_status_hint()
    if ok:
        await callback.message.edit_text(
            f"{base_text}\n\n✅ Добавлено в очередь Авито.\n{hint}",
            reply_markup=_post_actions_kb(callback.bot, callback.from_user.id),
        )
    else:
        await callback.message.edit_text(
            f"{base_text}\n\n❌ Не удалось добавить в очередь Авито.",
            reply_markup=_post_actions_kb(callback.bot, callback.from_user.id),
        )
    return ok


async def _execute_avito_publish(callback: CallbackQuery, post_id: str, base_text: str) -> None:
    await callback.message.edit_text(
        f"{base_text}\n\n⏳ Публикую в Авито...",
        reply_markup=_post_actions_kb(callback.bot, callback.from_user.id),
    )
    try:
        from app.bot.handlers.scheduler import get_orchestrator

        try:
            get_orchestrator(callback.bot).queue_manager.cancel_pending_jobs_for_post_platform(
                post_id, "avito"
            )
        except Exception:
            pass

        result, pub_err = await publish_post_api(
            post_id,
            "avito",
            signature_enabled=get_signature_state(callback.bot),
        )
        if result:
            post2 = await get_post_api(post_id)
            extra = ""
            if post2 and post2.get("avito_url"):
                extra = f"\n🔗 {post2['avito_url']}"
            await callback.message.edit_text(
                f"{base_text}\n\n✅ Опубликовано в Авито.{extra}",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id),
            )
        else:
            err = pub_err or "сервер API вернул ошибку — см. app/logs/avito_http.log"
            await callback.message.edit_text(
                f"{base_text}\n\n❌ Не удалось опубликовать в Авито.\n\n{err}"[:4090],
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id),
            )
    except Exception as e:
        await callback.message.edit_text(
            f"{base_text}\n\n❌ Ошибка: {str(e)}",
            reply_markup=_post_actions_kb(callback.bot, callback.from_user.id),
        )


@router.callback_query(F.data == "publish_avito")
async def publish_to_avito(callback: CallbackQuery):
    """Публикация в Авито: автозагрузка — сначала экран/корпус, иначе привязанное объявление."""

    user_data = callback.bot.user_data.get(callback.from_user.id, {})
    post_id = user_data.get("selected_post")
    if not post_id:
        await callback.answer("❌ Пост не выбран.", show_alert=True)
        try:
            await callback.message.edit_text("❌ Пост не выбран.")
        except TelegramBadRequest:
            pass
        return

    post = await get_post_api(post_id)
    if not post:
        await callback.answer("❌ Пост не найден.", show_alert=True)
        try:
            await callback.message.edit_text("❌ Пост не найден.")
        except TelegramBadRequest:
            pass
        return

    avito_platform_on = True
    try:
        avito_platform_on = bool(get_settings_service().is_avito_platform_only_enabled())
    except Exception:
        avito_platform_on = True
    if not avito_platform_on:
        msg = "Авито: включите площадку «Авито» в настройках публикации."
        await callback.answer(msg, show_alert=True)
        try:
            await callback.message.edit_text(
                f"{callback.message.text}\n\n⚠️ {msg}",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id),
            )
        except TelegramBadRequest:
            pass
        return

    avito_id = await resolve_avito_item_id_for_post(post)
    post_txt = post.get("text") or ""
    svc = get_settings_service()
    can_enqueue = bool(
        svc.can_enqueue_avito_without_linked_item(
            require_vk_market_for_pipeline=False,
            post_text=post_txt,
        )
    )
    if not avito_id and not can_enqueue:
        hint = svc.describe_avito_standalone_missing(post_txt)
        msg = (
            "Авито: у поста нет привязанного объявления. Либо привяжите avito_item_id к товару/посту, "
            f"либо настройте авто-создание: {hint}"
        )
        await callback.answer(msg, show_alert=True)
        try:
            await callback.message.edit_text(
                f"{callback.message.text}\n\n⚠️ {msg}",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id),
            )
        except TelegramBadRequest:
            pass
        return

    if not avito_id and can_enqueue:
        if not post.get("is_published_avito"):
            await _show_avito_publish_params(callback, post, post_id)
            await callback.answer()
            return
        await _enqueue_avito_autoload(
            callback, post_id, callback.message.text or "", priority=80
        )
        await callback.answer()
        return

    if post.get("is_published_avito"):
        buttons = [[
            InlineKeyboardButton(text="⏭️ Далее", callback_data="republish_avito"),
            InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_post"),
        ]]
        await callback.answer()
        await callback.message.edit_text(
            f"{callback.message.text}\n\nОпубликовать повторно в Авито?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
        return

    await _execute_avito_publish(callback, post_id, callback.message.text or "")
    await callback.answer()


@router.callback_query(F.data == "avito_pub_scr")
async def avito_pub_cycle_screen(callback: CallbackQuery):
    store = _avito_pub_store(callback.bot, callback.from_user.id)
    if not store.get("avito_pub_post_id"):
        await callback.answer("❌ Сессия устарела.", show_alert=True)
        return
    sl = next_screen_level(store.get("avito_pub_screen", 1))
    store["avito_pub_screen"] = sl
    bl = clamp_avito_level(store.get("avito_pub_body", 1))
    base = store.get("avito_pub_base_text", "")
    await callback.message.edit_text(
        f"{base}\n\n{format_avito_publish_block(sl, bl)}",
        reply_markup=get_avito_publish_keyboard(sl, bl),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "avito_pub_bod")
async def avito_pub_cycle_body(callback: CallbackQuery):
    store = _avito_pub_store(callback.bot, callback.from_user.id)
    if not store.get("avito_pub_post_id"):
        await callback.answer("❌ Сессия устарела.", show_alert=True)
        return
    bl = next_body_level(store.get("avito_pub_body", 1))
    store["avito_pub_body"] = bl
    sl = clamp_avito_level(store.get("avito_pub_screen", 1))
    base = store.get("avito_pub_base_text", "")
    await callback.message.edit_text(
        f"{base}\n\n{format_avito_publish_block(sl, bl)}",
        reply_markup=get_avito_publish_keyboard(sl, bl),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "avito_pub_go")
async def avito_pub_confirm(callback: CallbackQuery):
    store = _avito_pub_store(callback.bot, callback.from_user.id)
    post_id = store.get("avito_pub_post_id")
    if not post_id:
        await callback.answer("❌ Сессия устарела.", show_alert=True)
        return
    sl = clamp_avito_level(store.get("avito_pub_screen", 1))
    bl = clamp_avito_level(store.get("avito_pub_body", 1))
    draft = {"screen_level": sl, "body_level": bl}
    base = store.get("avito_pub_base_text", callback.message.text or "")

    updated = await update_post_api(post_id, avito_draft=draft)
    if not updated:
        await callback.answer("❌ Не удалось сохранить параметры.", show_alert=True)
        return

    for key in ("avito_pub_post_id", "avito_pub_screen", "avito_pub_body", "avito_pub_base_text"):
        store.pop(key, None)

    await callback.answer()
    await _enqueue_avito_autoload(callback, post_id, base)


@router.callback_query(F.data == "stories_menu")
async def show_stories_menu(callback: CallbackQuery):
    """Show stories menu."""
    # Сразу отвечаем на callback, чтобы избежать ошибки "query is too old"
    await callback.answer("Меню сторис")

    # Get selected post ID
    user_data = callback.bot.user_data.get(callback.from_user.id, {})
    post_id = user_data.get("selected_post")

    if not post_id:
        await callback.message.answer("❌ Пост не выбран.")
        return

    # Get post details
    post = await get_post_api(post_id)

    if not post:
        await callback.message.answer("❌ Пост не найден.")
        return

    # Create keyboard for stories
    buttons = [
        [InlineKeyboardButton(text="📱 Опубликовать сторис в ВК", callback_data="story_vk")],
        [InlineKeyboardButton(text="📢 Опубликовать сторис в ТГ", callback_data="story_telegram")],
        [InlineKeyboardButton(text="📸 Опубликовать сторис в IG", callback_data="story_instagram")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_post")]
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    # Show stories menu
    await callback.message.edit_text(
        f"{callback.message.text}\n\n📱 Выберите платформу для публикации сторис:",
        reply_markup=keyboard
    )

@router.callback_query(F.data == "back_to_post")
async def back_to_post(callback: CallbackQuery):
    """Go back to post view."""
    # Get selected post ID
    user_data = callback.bot.user_data.get(callback.from_user.id, {})
    post_id = user_data.get("selected_post")

    if not post_id:
        await callback.message.answer("❌ Пост не выбран.")
        return

    # Get post details
    post = await get_post_api(post_id)

    if not post:
        await callback.message.answer("❌ Пост не найден.")
        return

    # Show post details
    await callback.message.edit_text(
        callback.message.text.split("\n\n📱 Выберите платформу")[0],
        reply_markup=post_actions_kb_for_user(user_data)
    )

    await callback.answer()

@router.callback_query(F.data == "story_vk")
async def publish_story_to_vk(callback: CallbackQuery):
    """Publish story to VK."""
    # Сразу отвечаем на callback, чтобы избежать ошибки "query is too old"
    await callback.answer("Публикую сторис в ВК...")

    # Get selected post ID
    user_data = callback.bot.user_data.get(callback.from_user.id, {})
    post_id = user_data.get("selected_post")

    if not post_id:
        await callback.message.answer("❌ Пост не выбран.")
        return

    # Get post details
    post = await get_post_api(post_id)

    if not post:
        await callback.message.answer("❌ Пост не найден.")
        return

    # Check if post has photos
    if not post.get("photos"):
        await callback.message.answer("❌ Пост не содержит фотографий для сторис.")
        return

    # Publish story
    status_message = await callback.message.edit_text(f"{callback.message.text}\n\n⏳ Публикую сторис в ВК...")

    try:
        # Create story
        story = await create_story_api(post_id, "vk")

        if not story:
            await status_message.edit_text(
                f"{status_message.text.split('⏳')[0]}\n\n❌ Ошибка при создании сторис для ВК.",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
            )
            return

        # Publish story
        result = await publish_story_api(story.get("id"))

        if result:
            await status_message.edit_text(
                f"{status_message.text.split('⏳')[0]}\n\n✅ Сторис опубликован в ВК!",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
            )
        else:
            await status_message.edit_text(
                f"{status_message.text.split('⏳')[0]}\n\n❌ Ошибка при публикации сторис в ВК.",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
            )
    except Exception as e:
        await status_message.edit_text(
            f"{status_message.text.split('⏳')[0]}\n\n❌ Ошибка: {str(e)}",
            reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
        )

@router.callback_query(F.data == "story_telegram")
async def publish_story_to_telegram(callback: CallbackQuery):
    """Publish story to Telegram."""
    # Сразу отвечаем на callback, чтобы избежать ошибки "query is too old"
    await callback.answer("Публикую сторис в Telegram...")

    # Get selected post ID
    user_data = callback.bot.user_data.get(callback.from_user.id, {})
    post_id = user_data.get("selected_post")

    if not post_id:
        await callback.message.answer("❌ Пост не выбран.")
        return

    # Get post details
    post = await get_post_api(post_id)

    if not post:
        await callback.message.answer("❌ Пост не найден.")
        return

    # Check if post has photos
    if not post.get("photos"):
        await callback.message.answer("❌ Пост не содержит фотографий для сторис.")
        return

    # Publish story
    status_message = await callback.message.edit_text(f"{callback.message.text}\n\n⏳ Публикую сторис в Telegram...")

    try:
        # Create story
        story = await create_story_api(post_id, "telegram")

        if not story:
            await status_message.edit_text(
                f"{status_message.text.split('⏳')[0]}\n\n❌ Ошибка при создании сторис для Telegram.",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
            )
            return

        # Publish story
        result = await publish_story_api(story.get("id"))

        if result:
            await status_message.edit_text(
                f"{status_message.text.split('⏳')[0]}\n\n✅ Сторис опубликован в Telegram!",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
            )
        else:
            await status_message.edit_text(
                f"{status_message.text.split('⏳')[0]}\n\n❌ Ошибка при публикации сторис в Telegram.",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
            )
    except Exception as e:
        await status_message.edit_text(
            f"{status_message.text.split('⏳')[0]}\n\n❌ Ошибка: {str(e)}",
            reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
        )

@router.callback_query(F.data == "story_instagram")
async def publish_story_to_instagram(callback: CallbackQuery):
    """Publish story to Instagram."""
    # Сразу отвечаем на callback, чтобы избежать ошибки "query is too old"
    await callback.answer("Публикую сторис в Instagram...")

    # Get selected post ID
    user_data = callback.bot.user_data.get(callback.from_user.id, {})
    post_id = user_data.get("selected_post")

    if not post_id:
        await callback.message.answer("❌ Пост не выбран.")
        return

    # Get post details
    post = await get_post_api(post_id)

    if not post:
        await callback.message.answer("❌ Пост не найден.")
        return

    # Check if post has photos
    if not post.get("photos"):
        await callback.message.answer("❌ Пост не содержит фотографий для сторис.")
        return

    # Publish story
    status_message = await callback.message.edit_text(f"{callback.message.text}\n\n⏳ Публикую сторис в Instagram...")

    try:
        # Create story
        story = await create_story_api(post_id, "instagram")

        if not story:
            await status_message.edit_text(
                f"{status_message.text.split('⏳')[0]}\n\n❌ Ошибка при создании сторис для Instagram.",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
            )
            return

        # Publish story
        result = await publish_story_api(story.get("id"))

        if result:
            await status_message.edit_text(
                f"{status_message.text.split('⏳')[0]}\n\n✅ Сторис опубликован в Instagram!",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
            )
        else:
            await status_message.edit_text(
                f"{status_message.text.split('⏳')[0]}\n\n❌ Ошибка при публикации сторис в Instagram.",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
            )
    except Exception as e:
        await status_message.edit_text(
            f"{status_message.text.split('⏳')[0]}\n\n❌ Ошибка: {str(e)}",
            reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
        )

@router.callback_query(F.data == "publish_vk_product")
async def publish_to_vk_product(callback: CallbackQuery):
    """Publish product to VK Market only."""
    await callback.answer("Публикую товар в ВК...")
    
    # Get selected post ID
    user_data = callback.bot.user_data.get(callback.from_user.id, {})
    post_id = user_data.get("selected_post")
    
    if not post_id:
        await callback.message.edit_text("❌ Пост не выбран.")
        return
    
    # Get post details
    post = await get_post_api(post_id)
    
    if not post:
        await callback.message.edit_text("❌ Пост не найден.")
        return
    
    status_message = await callback.message.edit_text(f"{callback.message.text}\n\n⏳ Публикую товар в ВК...")
    
    try:
        # Публикуем только товар в ВК Market
        from app.workers.vk.product_publisher import publish_product_to_vk
        
        success = await publish_product_to_vk(post_id)
        
        if success:
            await status_message.edit_text(
                f"{status_message.text.split('⏳')[0]}\n\n✅ Товар опубликован в ВК Market!",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
            )
        else:
            await status_message.edit_text(
                f"{status_message.text.split('⏳')[0]}\n\n❌ Ошибка при публикации товара в ВК Market.",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
            )
    except Exception as e:
        await status_message.edit_text(
            f"{status_message.text.split('⏳')[0]}\n\n❌ Ошибка: {str(e)}",
            reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
        )


@router.callback_query(F.data == "publish_all")
async def publish_to_all(callback: CallbackQuery):
    """Add post to queue for all unpublished platforms."""
    # Get selected post ID
    user_data = callback.bot.user_data.get(callback.from_user.id, {})
    post_id = user_data.get("selected_post")

    if not post_id:
        await callback.answer("❌ Пост не выбран.", show_alert=True)
        return

    # Get post details
    post = await get_post_api(post_id)

    if not post:
        await callback.answer("❌ Пост не найден.", show_alert=True)
        return

    # Check if already published in any platform
    if (
        post.get("is_published_vk")
        or post.get("is_published_telegram")
        or post.get("is_published_instagram")
        or post.get("is_published_max")
        or post.get("is_published_avito")
    ):
        # Создаем клавиатуру с кнопками "Далее" и "Назад"
        buttons = [
            [
                InlineKeyboardButton(text="⏭️ Далее", callback_data="republish_all"),
                InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_post")
            ]
        ]
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        # Используем edit_text вместо answer
        await callback.message.edit_text(
            f"{callback.message.text}\n\nОпубликовать повторно?", 
            reply_markup=keyboard
        )
        return

    try:
        target_platforms = []
        if not post.get("is_published_vk"):
            target_platforms.append("vk")
        if not post.get("is_published_telegram"):
            target_platforms.append("telegram")
        if not post.get("is_published_instagram"):
            target_platforms.append("instagram")
        if not post.get("is_published_max"):
            target_platforms.append("max")
        if not post.get("is_published_avito"):
            if await resolve_avito_item_id_for_post(post) or get_settings_service().can_enqueue_avito_without_linked_item(
                post_text=post.get("text") or "",
            ):
                target_platforms.append("avito")

        if not target_platforms:
            await callback.message.edit_text(
                f"{callback.message.text}\n\nℹ️ Пост уже опубликован во всех соцсетях.",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
            )
            await callback.answer()
            return

        from app.bot.handlers.scheduler import get_orchestrator
        orchestrator = get_orchestrator(callback.bot)
        added = orchestrator.add_post_to_queue(
            post_id,
            platforms=target_platforms,
            priority=0,
            allow_avito_without_vk_market="avito" in target_platforms,
        )

        if added:
            pretty = ", ".join(
                [
                    "ВК" if p == "vk"
                    else "Telegram" if p == "telegram"
                    else "Instagram" if p == "instagram"
                    else "Max" if p == "max"
                    else "Авито"
                    for p in target_platforms
                ]
            )
            extra = ""
            if "avito" in target_platforms:
                extra = f"\n{_avito_queue_status_hint()}"
            await callback.message.edit_text(
                f"{callback.message.text}\n\n✅ Добавлено в очередь: {pretty}.\n"
                f"Публикация выполнится планировщиком.{extra}",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id),
            )
        else:
            await callback.message.edit_text(
                f"{callback.message.text}\n\n❌ Не удалось добавить пост в очередь.",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
            )
    except Exception as e:
        await callback.message.edit_text(
            f"{callback.message.text}\n\n❌ Ошибка: {str(e)}",
            reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
        )

    await callback.answer()

@router.callback_query(F.data == "republish_all")
async def republish_to_all(callback: CallbackQuery):
    """Republish post to all platforms."""
    # Get selected post ID
    user_data = callback.bot.user_data.get(callback.from_user.id, {})
    post_id = user_data.get("selected_post")

    if not post_id:
        await callback.answer("❌ Пост не выбран.", show_alert=True)
        return

    # Get post details
    post = await get_post_api(post_id)

    if not post:
        await callback.answer("❌ Пост не найден.", show_alert=True)
        return

    # Publish post to all platforms
    await callback.message.edit_text(f"⏳ Публикую во все соцсети повторно...")

    try:
        results = []

        # Publish to all platforms regardless of previous publication status
        vk_result, _ = await publish_post_api(
            post_id,
            "vk",
            signature_enabled=get_signature_state(callback.bot),
        )
        results.append(("ВК", vk_result is not None))

        tg_result, _ = await publish_post_api(
            post_id,
            "telegram",
            signature_enabled=get_signature_state(callback.bot),
        )
        results.append(("Telegram", tg_result is not None))

        ig_result, _ = await publish_post_api(post_id, "instagram")
        results.append(("Instagram", ig_result is not None))
        max_result, _ = await publish_post_api(
            post_id,
            "max",
            signature_enabled=get_signature_state(callback.bot),
        )
        results.append(("Max", max_result is not None))

        avito_id = None
        avito_result = None
        avito_tried = False
        if get_settings_service().is_avito_queue_allowed():
            avito_id = await resolve_avito_item_id_for_post(post)
            if avito_id or get_settings_service().can_enqueue_avito_without_linked_item(
                post_text=post.get("text") or "",
            ):
                avito_tried = True
                avito_result, _ = await publish_post_api(post_id, "avito")
        if avito_tried:
            results.append(("Avito", avito_result is not None))

        # Format results
        result_text = "\n\n📤 Результаты публикации:\n"
        for platform, success in results:
            status = "✅" if success else "❌"
            result_text += f"{platform}: {status}\n"

        await callback.message.edit_text(
            f"✅ Опубликовано повторно!{result_text}",
            reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
        )
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Ошибка: {str(e)}",
            reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
        )

    await callback.answer()

@router.callback_query(F.data == "delete")
async def confirm_delete_post(callback: CallbackQuery):
    """Ask for confirmation before deleting a post."""
    keyboard = get_delete_post_confirmation_keyboard()

    await callback.message.edit_text(
        f"{callback.message.text}\n\n⚠️ Вы уверены, что хотите удалить этот пост?",
        reply_markup=keyboard
    )

    await callback.answer()

@router.callback_query(F.data == "confirm_delete")
async def delete_post(callback: CallbackQuery):
    """Delete the post after confirmation."""
    # Get selected post ID
    user_data = callback.bot.user_data.get(callback.from_user.id, {})
    post_id = user_data.get("selected_post")

    if not post_id:
        await callback.answer("❌ Пост не выбран.", show_alert=True)
        return

    await callback.answer()

    # Delete post
    await callback.message.edit_text(f"{callback.message.text.split('⚠️')[0]}\n\n⏳ Удаляю пост...")

    try:
        success = await delete_post_api(post_id)

        if success:
            from app.bot.utils.main_menu import build_main_keyboard
            await callback.message.edit_text("✅ Пост успешно удален.")
            await callback.message.answer(
                "Выберите действие:",
                reply_markup=await build_main_keyboard(callback.bot),
            )
        else:
            await callback.message.edit_text(
                f"{callback.message.text.split('⏳')[0]}\n\n❌ Ошибка при удалении поста.",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
            )
    except Exception as e:
        await callback.message.edit_text(
            f"{callback.message.text.split('⏳')[0]}\n\n❌ Ошибка: {str(e)}",
            reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
        )

@router.callback_query(F.data == "republish_vk")
async def republish_to_vk(callback: CallbackQuery):
    """Republish post to VK."""
    # Get selected post ID
    user_data = callback.bot.user_data.get(callback.from_user.id, {})
    post_id = user_data.get("selected_post")

    if not post_id:
        await callback.message.answer("❌ Пост не выбран.")
        return

    # Get post details
    post = await get_post_api(post_id)

    if not post:
        await callback.message.answer("❌ Пост не найден.")
        return

    # Publish post
    status_message = await callback.message.edit_text(f"⏳ Публикую в ВК повторно...")

    try:
        result, _ = await publish_post_api(
            post_id,
            "vk",
            signature_enabled=get_signature_state(callback.bot),
        )

        if result:
            await status_message.edit_text(
                f"✅ Опубликовано в ВК!",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
            )
        else:
            await status_message.edit_text(
                f"❌ Ошибка при публикации в ВК.",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
            )
    except Exception as e:
        await status_message.edit_text(
            f"❌ Ошибка: {str(e)}",
            reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
        )

@router.callback_query(F.data == "republish_telegram")
async def republish_to_telegram(callback: CallbackQuery):
    """Republish post to Telegram."""
    # Get selected post ID
    user_data = callback.bot.user_data.get(callback.from_user.id, {})
    post_id = user_data.get("selected_post")

    if not post_id:
        await callback.message.answer("❌ Пост не выбран.")
        return

    # Get post details
    post = await get_post_api(post_id)

    if not post:
        await callback.message.answer("❌ Пост не найден.")
        return

    # Publish post
    status_message = await callback.message.edit_text(f"⏳ Публикую в Telegram повторно...")

    try:
        result, _ = await publish_post_api(
            post_id,
            "telegram",
            signature_enabled=get_signature_state(callback.bot),
        )

        if result:
            await status_message.edit_text(
                f"✅ Опубликовано в Telegram!",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
            )
        else:
            await status_message.edit_text(
                f"❌ Ошибка при публикации в Telegram.",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
            )
    except Exception as e:
        await status_message.edit_text(
            f"❌ Ошибка: {str(e)}",
            reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
        )

@router.callback_query(F.data == "republish_instagram")
async def republish_to_instagram(callback: CallbackQuery):
    """Republish post to Instagram."""
    # Get selected post ID
    user_data = callback.bot.user_data.get(callback.from_user.id, {})
    post_id = user_data.get("selected_post")

    if not post_id:
        await callback.message.answer("❌ Пост не выбран.")
        return

    # Get post details
    post = await get_post_api(post_id)

    if not post:
        await callback.message.answer("❌ Пост не найден.")
        return

    # Publish post
    status_message = await callback.message.edit_text(f"⏳ Публикую в Instagram повторно...")

    try:
        result, _ = await publish_post_api(post_id, "instagram")

        if result:
            await status_message.edit_text(
                f"✅ Опубликовано в Instagram!",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
            )
        else:
            await status_message.edit_text(
                f"❌ Ошибка при публикации в Instagram.",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
            )
    except Exception as e:
        await status_message.edit_text(
            f"❌ Ошибка: {str(e)}",
            reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
        )

@router.callback_query(F.data == "cancel_delete")
async def cancel_delete(callback: CallbackQuery):
    """Cancel post deletion."""
    await callback.message.edit_text(
        callback.message.text.split("⚠️")[0],
        reply_markup=_post_actions_kb(callback.bot, callback.from_user.id)
    )

    await callback.answer()


@router.callback_query(F.data == "republish_max")
async def republish_to_max(callback: CallbackQuery):
    """Republish post to Max."""
    user_data = callback.bot.user_data.get(callback.from_user.id, {})
    post_id = user_data.get("selected_post")
    if not post_id:
        await callback.answer("❌ Пост не выбран.", show_alert=True)
        return

    post = await get_post_api(post_id)
    if not post:
        await callback.answer("❌ Пост не найден.", show_alert=True)
        return

    await callback.message.edit_text("⏳ Публикую в Max повторно...")
    try:
        result, _ = await publish_post_api(
            post_id,
            "max",
            signature_enabled=get_signature_state(callback.bot),
        )
        if result:
            await callback.message.edit_text(
                "✅ Опубликовано в Max повторно!",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id),
            )
        else:
            await callback.message.edit_text(
                "❌ Ошибка при публикации в Max.",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id),
            )
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Ошибка: {str(e)}",
            reply_markup=_post_actions_kb(callback.bot, callback.from_user.id),
        )
    await callback.answer()


@router.callback_query(F.data == "republish_avito")
async def republish_to_avito(callback: CallbackQuery):
    """Повторная публикация: для автозагрузки — снова экран/корпус."""
    user_data = callback.bot.user_data.get(callback.from_user.id, {})
    post_id = user_data.get("selected_post")
    if not post_id:
        await callback.answer("❌ Пост не выбран.", show_alert=True)
        return

    post = await get_post_api(post_id)
    if not post:
        await callback.answer("❌ Пост не найден.", show_alert=True)
        return

    avito_id = await resolve_avito_item_id_for_post(post)
    post_txt = post.get("text") or ""
    svc = get_settings_service()
    can_enqueue = bool(
        svc.can_enqueue_avito_without_linked_item(
            require_vk_market_for_pipeline=False,
            post_text=post_txt,
        )
    )
    if not avito_id and can_enqueue:
        await _show_avito_publish_params(callback, post, post_id)
        await callback.answer()
        return

    base = callback.message.text or ""
    await _execute_avito_publish(callback, post_id, base)
    await callback.answer()


@router.callback_query(F.data == "back_to_posts")
async def back_to_posts(callback: CallbackQuery):
    """Go back to the drafts list."""
    if hasattr(callback.bot, "user_data"):
        ud = callback.bot.user_data.get(callback.from_user.id)
        if isinstance(ud, dict):
            ud["in_archive"] = False
    await callback.message.edit_text("⏳ Возвращаюсь к черновикам...")
    await show_pending_posts(callback.message)
    await callback.answer()


@router.callback_query(F.data == "archive_root")
async def archive_root(callback: CallbackQuery):
    """Show the root archive view."""
    await callback.answer()
    await callback.message.edit_text("⏳ Загружаю архив постов...")
    await show_archived_posts(callback.message, user_id=callback.from_user.id)


@router.callback_query(lambda c: c.data and c.data.startswith("archive_year_"))
async def archive_year(callback: CallbackQuery):
    """Show archive for a specific year."""
    await callback.answer()
    year = int(callback.data.replace("archive_year_", ""))
    await callback.message.edit_text(f"⏳ Загружаю архив постов за {year} год...")
    await show_archived_posts(callback.message, year=year, user_id=callback.from_user.id)


@router.callback_query(lambda c: c.data and c.data.startswith("archive_month_"))
async def archive_month(callback: CallbackQuery):
    """Show archive for a specific month."""
    await callback.answer()
    parts = callback.data.replace("archive_month_", "").split("_")
    year = int(parts[0])
    month = int(parts[1])
    month_name = _RU_MONTH_NOM.get(month, str(month))
    await callback.message.edit_text(f"⏳ Загружаю архив постов за {month_name} {year} года...")
    await show_archived_posts(
        callback.message, year=year, month=month, user_id=callback.from_user.id
    )


@router.callback_query(lambda c: c.data and c.data.startswith("archive_day_"))
async def archive_day(callback: CallbackQuery):
    """Show archive for a specific day."""
    await callback.answer()
    parts = callback.data.replace("archive_day_", "").split("_")
    year = int(parts[0])
    month = int(parts[1])
    day = int(parts[2])
    month_name = _RU_MONTH_GEN.get(month, str(month))
    await callback.message.edit_text(
        f"⏳ Загружаю архив постов за {day} {month_name} {year} года..."
    )
    await show_archived_posts(
        callback.message, year=year, month=month, day=day, user_id=callback.from_user.id
    )


@router.callback_query(F.data == "back_to_archive")
async def back_to_archive(callback: CallbackQuery):
    """Return to the archive screen stored in user_data."""
    await callback.answer()
    user_id = callback.from_user.id
    user_data = (
        callback.bot.user_data.get(user_id, {})
        if hasattr(callback.bot, "user_data")
        else {}
    )
    archive_state = user_data.get("archive_state") or {}
    year = archive_state.get("year")
    month = archive_state.get("month")
    day = archive_state.get("day")

    if day is not None:
        await callback.message.edit_text("⏳ Возвращаюсь к постам за день...")
    elif month is not None:
        await callback.message.edit_text("⏳ Возвращаюсь к дням...")
    elif year is not None:
        await callback.message.edit_text("⏳ Возвращаюсь к месяцам...")
    else:
        await callback.message.edit_text("⏳ Возвращаюсь к архиву...")

    await show_archived_posts(
        callback.message, year=year, month=month, day=day, user_id=user_id
    )


@router.callback_query(F.data == "search_posts")
async def search_posts_callback(callback: CallbackQuery, state: FSMContext):
    """Handle the 'Search' button in archive."""
    buttons = [[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_search")]]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(
        "🔍 Введите текст для поиска по постам в архиве:\n\n"
        "Вы можете искать по тексту поста или по дате (например, 2023, 0623, 06.23 и т.д.)",
        reply_markup=keyboard,
    )

    await state.set_state(PostSearch.waiting_for_query)

    if not hasattr(callback.bot, "user_data"):
        callback.bot.user_data = {}
    if callback.from_user.id not in callback.bot.user_data:
        callback.bot.user_data[callback.from_user.id] = {}
    callback.bot.user_data[callback.from_user.id]["in_search_mode"] = True

    await callback.answer()


@router.callback_query(PostSearch.waiting_for_query, F.data == "cancel_search")
async def cancel_search(callback: CallbackQuery, state: FSMContext):
    """Cancel search and return to archive."""
    await state.clear()

    if hasattr(callback.bot, "user_data") and callback.from_user.id in callback.bot.user_data:
        callback.bot.user_data[callback.from_user.id]["in_search_mode"] = False

    await callback.message.edit_text("⏳ Возвращаюсь к архиву постов...")
    await archive_root(callback)


@router.message(PostSearch.waiting_for_query)
async def process_search_query(message: Message, state: FSMContext):
    """Process search query in archive."""
    search_query = (message.text or "").strip()

    if not search_query:
        await message.reply(
            "❌ Поисковый запрос не может быть пустым. Пожалуйста, введите текст для поиска или нажмите 'Отмена'.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_search")]
                ]
            ),
        )
        return

    await state.clear()

    if hasattr(message.bot, "user_data") and message.from_user.id in message.bot.user_data:
        message.bot.user_data[message.from_user.id]["in_search_mode"] = False

    status_message = await message.reply(f'🔍 Ищу посты по запросу: "{search_query}"...')

    search_results = await get_posts_api(is_archived=True, search_query=search_query)

    valid_results = [
        post
        for post in search_results
        if post and "id" in post and post.get("created_at")
    ]

    if not valid_results:
        await status_message.edit_text(
            f'🔍 По запросу "{search_query}" ничего не найдено.',
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🔍 Новый поиск", callback_data="search_posts")],
                    [InlineKeyboardButton(text="📁 Вернуться в архив", callback_data="archive_root")],
                    [InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")],
                ]
            ),
        )
        return

    response_text = f'🔍 Результаты поиска по запросу "{search_query}":\n\n'
    buttons = []

    for i, post in enumerate(valid_results, 1):
        post_name = post.get("name") or "Без названия"
        photos = post.get("photos") or []
        videos = post.get("videos") or []
        photo_count = len(photos) if isinstance(photos, list) else 0
        video_count = len(videos) if isinstance(videos, list) else 0
        text = post.get("text", "") or ""
        text = text[:100] + "..." if len(text) > 100 else text

        response_text += f"{i}. {post_name}\n"
        response_text += f"   Медиа: {photo_count}📷 {video_count}📹\n"
        response_text += f"   Текст: {text}\n\n"

        safe_post_name = post_name or "Без названия"
        button_text = f"{i}. {safe_post_name[:30]}{'...' if len(safe_post_name) > 30 else ''}"
        buttons.append(
            [InlineKeyboardButton(text=button_text, callback_data=f"view_post_{post.get('id')}")]
        )

    buttons.append([InlineKeyboardButton(text="🔍 Новый поиск", callback_data="search_posts")])
    buttons.append([InlineKeyboardButton(text="📁 Вернуться в архив", callback_data="archive_root")])
    buttons.append([InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await status_message.edit_text(response_text, reply_markup=keyboard)


@router.callback_query(F.data == "edit")
async def edit_post(callback: CallbackQuery, state: FSMContext):
    """Open the editing panel for the selected post."""
    post_id = callback.bot.user_data.get(callback.from_user.id, {}).get("selected_post")
    if not post_id:
        await callback.answer("❌ Пост не выбран.", show_alert=True)
        return

    post = await get_post_api(post_id)
    if not post:
        await callback.answer("❌ Пост не найден.", show_alert=True)
        return

    _set_edit_mode(callback.bot, callback.from_user.id, True)
    await _init_edit_state(state, post, post_id)
    await state.set_state(PostEdit.panel)
    await show_edit_panel(callback, state)
    await callback.answer()


@router.callback_query(StateFilter(PostEdit), F.data == "edit_change_text")
async def edit_change_text(callback: CallbackQuery, state: FSMContext):
    await state.set_state(PostEdit.waiting_for_text)
    await show_edit_text_prompt(callback, state)
    await callback.answer()


@router.callback_query(StateFilter(PostEdit), F.data == "edit_back_panel")
async def edit_back_panel(callback: CallbackQuery, state: FSMContext):
    await state.set_state(PostEdit.panel)
    await show_edit_panel(callback, state)
    await callback.answer()


@router.callback_query(StateFilter(PostEdit), F.data == "edit_copy_text")
async def edit_copy_text(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    post_text = data.get("edit_post_text") or ""
    await callback.message.reply(
        post_text,
        reply_markup=get_edit_copy_delete_keyboard(),
    )
    await callback.answer("Текст отправлен отдельным сообщением")


@router.callback_query(F.data == "edit_delete_copy")
async def edit_delete_copy(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer()


@router.callback_query(StateFilter(PostEdit), F.data == "edit_cancel")
async def edit_cancel(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    post_id = data.get("edit_post_id")
    _set_edit_mode(callback.bot, callback.from_user.id, False)
    await state.clear()

    post = await get_post_api(post_id) if post_id else None
    if not post:
        await callback.message.edit_text(
            "❌ Пост не найден.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_main")]
                ]
            ),
        )
    else:
        user_data = _user_data_for(callback.bot, callback.from_user.id)
        body, parse_mode = format_post_card_for_user(post, user_data)
        await callback.message.edit_text(
            body,
            reply_markup=_post_actions_kb(callback.bot, callback.from_user.id),
            parse_mode=parse_mode,
        )
    await callback.answer()


@router.callback_query(StateFilter(PostEdit), F.data == "edit_save")
async def edit_save(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    post_id = data.get("edit_post_id")
    draft = edit_draft_from_data(data)

    await callback.message.edit_text(
        "⏳ Сохраняю изменения…\n\n"
        f"{format_media_summary(draft['photos'], draft['videos'])}\n"
        f"Текст: {len(draft['text'])} симв."
    )

    try:
        updated_post = await update_post_api(
            post_id,
            text=draft["text"],
            photos=draft["photos"],
            videos=draft["videos"],
        )
        _set_edit_mode(callback.bot, callback.from_user.id, False)
        await state.clear()

        if updated_post:
            user_data = _user_data_for(callback.bot, callback.from_user.id)
            body, parse_mode = format_post_card_for_user(
                updated_post, user_data, success_prefix="✅ Пост сохранён."
            )
            await callback.message.edit_text(
                body,
                parse_mode=parse_mode,
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id),
            )
        else:
            await callback.message.edit_text(
                "❌ Не удалось сохранить пост. Попробуйте ещё раз.",
                reply_markup=_post_actions_kb(callback.bot, callback.from_user.id),
            )
    except Exception as e:
        _set_edit_mode(callback.bot, callback.from_user.id, False)
        await state.clear()
        await callback.message.edit_text(
            f"❌ Ошибка при сохранении: {e}",
            reply_markup=_post_actions_kb(callback.bot, callback.from_user.id),
        )
    await callback.answer()


@router.message(PostEdit.waiting_for_text, F.text)
async def edit_receive_text(message: Message, state: FSMContext):
    new_text = (message.text or "").strip()
    if not new_text:
        await message.reply("❌ Текст не может быть пустым.")
        return

    await state.update_data(edit_post_text=new_text)
    await state.set_state(PostEdit.panel)
    hint = "✅ Текст обновлён (нажмите «Сохранить» для записи в базу)."
    data = await state.get_data()
    if data.get("edit_panel_message_id"):
        await refresh_edit_panel_message(
            message.bot, message.chat.id, state, media_hint=hint
        )
    else:
        await show_edit_panel(message, state, media_hint=hint)


@router.message(PostEdit.panel, F.photo | F.video | F.media_group_id)
async def edit_panel_add_media(
    message: Message,
    state: FSMContext,
    album: Optional[List[Message]] = None,
):
    """Add photos/videos from a single message or album while on the panel."""
    data = await state.get_data()
    photos = list(data.get("edit_post_photos") or [])
    videos = list(data.get("edit_post_videos") or [])
    messages = album if album else [message]

    photos_added, videos_added, photo_limit_hit = collect_album_media(
        messages, photos, videos
    )
    if photos_added == 0 and videos_added == 0 and not photo_limit_hit:
        return

    await state.update_data(edit_post_photos=photos, edit_post_videos=videos)

    hint_parts = []
    if photos_added or videos_added:
        hint_parts.append(
            f"✅ Добавлено: {photos_added} фото, {videos_added} видео."
        )
    if photo_limit_hit:
        hint_parts.append("⚠️ Лимит 10 фото — часть файлов не добавлена.")
    hint = " ".join(hint_parts)

    await refresh_edit_panel_message(
        message.bot, message.chat.id, state, media_hint=hint
    )


@router.callback_query(PostEdit.panel, F.data == "edit_manage_photos")
async def edit_manage_photos(callback: CallbackQuery, state: FSMContext):
    photos = list((await state.get_data()).get("edit_post_photos") or [])
    await callback.message.edit_text(
        format_photo_manage_body(photos),
        reply_markup=get_edit_photo_manage_keyboard(photos),
        parse_mode="Markdown",
    )
    await state.set_state(PostEdit.manage_photos)
    await callback.answer()


@router.callback_query(PostEdit.panel, F.data == "edit_manage_videos")
async def edit_manage_videos(callback: CallbackQuery, state: FSMContext):
    videos = list((await state.get_data()).get("edit_post_videos") or [])
    await callback.message.edit_text(
        format_video_manage_body(videos),
        reply_markup=get_edit_video_manage_keyboard(videos),
        parse_mode="Markdown",
    )
    await state.set_state(PostEdit.manage_videos)
    await callback.answer()


@router.callback_query(PostEdit.manage_photos, F.data.startswith("edit_del_photo_"))
async def edit_delete_photo(callback: CallbackQuery, state: FSMContext):
    index = int(callback.data.replace("edit_del_photo_", ""))
    data = await state.get_data()
    photos = list(data.get("edit_post_photos") or [])
    if 0 <= index < len(photos):
        photos.pop(index)
        await state.update_data(edit_post_photos=photos)
        await callback.message.edit_text(
            format_photo_manage_body(photos),
            reply_markup=get_edit_photo_manage_keyboard(photos),
            parse_mode="Markdown",
        )
    else:
        await callback.answer("Фото не найдено", show_alert=True)
        return
    await callback.answer()


@router.callback_query(PostEdit.manage_videos, F.data.startswith("edit_del_video_"))
async def edit_delete_video(callback: CallbackQuery, state: FSMContext):
    index = int(callback.data.replace("edit_del_video_", ""))
    data = await state.get_data()
    videos = list(data.get("edit_post_videos") or [])
    if 0 <= index < len(videos):
        videos.pop(index)
        await state.update_data(edit_post_videos=videos)
        await callback.message.edit_text(
            format_video_manage_body(videos),
            reply_markup=get_edit_video_manage_keyboard(videos),
            parse_mode="Markdown",
        )
    else:
        await callback.answer("Видео не найдено", show_alert=True)
        return
    await callback.answer()


@router.callback_query(PostEdit.panel, F.data == "edit_clear_photos")
async def edit_clear_photos(callback: CallbackQuery, state: FSMContext):
    await state.update_data(edit_post_photos=[])
    await show_edit_panel(callback, state, media_hint="🗑 Все фото удалены из черновика.")
    await callback.answer()


@router.callback_query(PostEdit.panel, F.data == "edit_clear_videos")
async def edit_clear_videos(callback: CallbackQuery, state: FSMContext):
    await state.update_data(edit_post_videos=[])
    await show_edit_panel(callback, state, media_hint="🗑 Все видео удалены из черновика.")
    await callback.answer()


@router.callback_query(F.data == "publish_all_pending")
async def publish_all_pending_posts(callback: CallbackQuery):
    """Show confirmation for adding all drafts to queue."""
    current_drafts = await get_pending_posts_api()

    if not current_drafts:
        await callback.answer("❌ Нет черновиков для публикации.", show_alert=True)
        return

    pending_post_ids = [post.get("id") for post in current_drafts if post.get("id")]
    if not pending_post_ids:
        await callback.answer("❌ Нет черновиков для публикации.", show_alert=True)
        return

    if not hasattr(callback.bot, "user_data"):
        callback.bot.user_data = {}
    if callback.from_user.id not in callback.bot.user_data:
        callback.bot.user_data[callback.from_user.id] = {}

    callback.bot.user_data[callback.from_user.id]["pending_post_ids"] = pending_post_ids

    count = len(pending_post_ids)
    post_word = "пост" if count % 10 == 1 and count % 100 != 11 else (
        "поста" if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14) else "постов"
    )
    await safe_edit_message(
        callback.message,
        f"📤 Добавить {count} {post_word} в очередь?\n\n"
        "Интервалы публикации — в «Настройки → Публикация и интервалы».",
        reply_markup=get_publish_all_pending_confirmation_keyboard(),
    )
    await callback.answer()


# Подтверждение массового добавления черновиков в очередь

@router.callback_query(F.data == "confirm_publish_all_pending")
async def confirm_publish_all_pending(callback: CallbackQuery):
    """Add all drafts to queue."""
    user_data = callback.bot.user_data.get(callback.from_user.id, {})
    pending_post_ids = user_data.get("pending_post_ids", [])

    if not pending_post_ids:
        current_drafts = await get_pending_posts_api()
        pending_post_ids = [
            post.get("id") for post in current_drafts if post.get("id")
        ]

    if not pending_post_ids:
        await callback.answer("❌ Нет черновиков для публикации.", show_alert=True)
        return

    from app.bot.handlers.scheduler import get_orchestrator
    orchestrator = get_orchestrator(callback.bot)

    added_count = 0
    for post_id in pending_post_ids:
        ok = orchestrator.add_post_to_queue(
            post_id,
            platforms=["vk", "telegram", "instagram", "max", "avito"],
            priority=0,
        )
        if ok:
            added_count += 1


    if callback.from_user.id in callback.bot.user_data:
        callback.bot.user_data[callback.from_user.id]["pending_post_ids"] = []

    if added_count > 0:
        await callback.answer(f"✅ {added_count} постов добавлено в очередь")
        from app.bot.utils.main_menu import build_main_keyboard
        await safe_edit_message(
            callback.message,
            f"✅ {added_count} постов добавлено в очередь публикации.\n\n"
            "Публикация выполнится планировщиком с интервалами из "
            "«Настройки → Публикация и интервалы».\n\n"
            "Управлять очередью можно через меню «В очереди».",
            reply_markup=await build_main_keyboard(callback.bot),
        )
    else:
        await callback.answer("❌ Ошибка при добавлении постов в очередь", show_alert=True)


@router.callback_query(F.data == "cancel_publish_all_pending")
async def cancel_publish_all_pending(callback: CallbackQuery):
    """Cancel mass publishing of pending posts."""
    if callback.from_user.id in callback.bot.user_data:
        callback.bot.user_data[callback.from_user.id]["pending_post_ids"] = []

    await show_pending_posts(callback.message)
    await callback.answer("❌ Публикация отменена")

@router.callback_query(F.data == "toggle_signature")
async def toggle_signature(callback: CallbackQuery):
    """Toggle signature for future publications."""
    new_state = not get_signature_state(callback.bot)
    set_signature_state(callback.bot, new_state)

    await callback.answer(
        "Подпись: включена" if new_state else "Подпись: выключена"
    )

    await show_pending_posts(callback.message)


@router.callback_query(F.data == "toggle_vk_market")
async def toggle_vk_market(callback: CallbackQuery):
    """Toggle VK Market publishing for future publications."""
    new_state = not get_vk_market_state(callback.bot)
    set_vk_market_state(callback.bot, new_state)

    await callback.answer(
        "Товары ВК: включены" if new_state else "Товары ВК: выключены"
    )

    await show_pending_posts(callback.message)

async def show_pending_posts(message: Message):
    """Show draft posts (не опубликованы и не в очереди)."""
    try:
        if hasattr(message, "bot") and hasattr(message.bot, "user_data") and hasattr(message, "from_user"):
            if message.from_user.id not in message.bot.user_data:
                message.bot.user_data[message.from_user.id] = {}
            message.bot.user_data[message.from_user.id]["in_archive"] = False

        print("Fetching drafts...")
        posts = await get_pending_posts_api()
        print(f"Fetched {len(posts)} drafts")

        if not posts:
            buttons = [
                [InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")],
                [InlineKeyboardButton(text="⚙️ Настройки", callback_data="open_settings")],
            ]
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

            await message.edit_text(
                "📭 Черновиков нет.",
                reply_markup=keyboard
            )

            if hasattr(message, "bot") and hasattr(message.bot, "user_data") and hasattr(message, "from_user"):
                if message.from_user.id in message.bot.user_data:
                    message.bot.user_data[message.from_user.id]["pending_post_ids"] = []
            return

        max_text_len = 3900
        response_text = "📝 Черновики:\n\n"
        response_text += "⚙️ Настройки подписей и интервалов — в разделе «Настройки».\n\n"
        shown_posts_count = 0

        buttons = []

        buttons.append([ikb("📤 Добавить все черновики в очередь", "publish_all_pending")])

        # Add buttons for each post
        for i, post in enumerate(posts, 1):
            post_name = post.get("name") or f"Пост {i}"
            post_id = post.get("id")
            
            # Truncate long names
            if post_name and len(post_name) > 30:
                post_name = post_name[:27] + "..."
            elif not post_name:
                post_name = f"Пост {i}"
                
            # Add post info to response
            photos = post.get("photos") or [] if post else []
            videos = post.get("videos") or [] if post else []
            photo_count = len(photos) if isinstance(photos, list) else 0
            video_count = len(videos) if isinstance(videos, list) else 0
            
            post_text_block = f"{i}. {post_name}\n" + f"   Медиа: {photo_count}📷 {video_count}📹\n\n"
            if len(response_text) + len(post_text_block) > max_text_len:
                break
            response_text += post_text_block
            shown_posts_count += 1
            
            # Add button for this post
            buttons.append([InlineKeyboardButton(
                text=f"{i}. {post_name}",
                callback_data=f"view_post_{post_id}"
            )])

        # Add back and toggle buttons
        buttons.append([InlineKeyboardButton(text="⚙️ Настройки", callback_data="open_settings")])
        buttons.append([InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")])

        # Create keyboard
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        # Store post IDs in user data
        if hasattr(message, 'bot') and hasattr(message.bot, 'user_data') and hasattr(message, 'from_user'):
            try:
                # Создаем или получаем словарь для пользователя
                if message.from_user.id not in message.bot.user_data:
                    message.bot.user_data[message.from_user.id] = {}
                
                # Сохраняем ID постов в user_data
                user_data = message.bot.user_data[message.from_user.id]
                
                # Сохраняем отдельные посты (для обратной совместимости)
                for i, post in enumerate(posts, 1):
                    if post.get("id"):
                        user_data[f"post_{i}"] = post.get("id")
                
                # Сохраняем список ID черновиков для массовой публикации
                pending_post_ids = [post.get("id") for post in posts if post.get("id")]
                user_data["pending_post_ids"] = pending_post_ids

                print(f"Saved {len(pending_post_ids)} draft post IDs: {pending_post_ids}")
            except Exception as e:
                print(f"Error updating user_data: {str(e)}")

        hidden_posts_count = max(0, len(posts) - shown_posts_count)
        if hidden_posts_count:
            response_text += f"… и еще {hidden_posts_count} пост(ов). Откройте пост через кнопки ниже.\n"

        await message.edit_text(response_text, reply_markup=keyboard)
    except Exception as e:
        print(f"Error in show_pending_posts: {str(e)}")
        # Create back button
        buttons = [[InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")]]
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        await message.edit_text(
            f"❌ Ошибка при загрузке постов: {str(e)}",
            reply_markup=keyboard
        )

