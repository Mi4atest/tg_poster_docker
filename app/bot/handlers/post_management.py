from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import aiohttp
import json
from datetime import datetime

from app.bot.keyboards.main_keyboard import (
    get_main_keyboard, get_post_actions_keyboard, get_skip_back_keyboard,
    get_media_management_keyboard, get_photo_management_keyboard, get_video_management_keyboard
)
from app.config.settings import API_HOST, API_PORT

# Определение состояний для поиска постов
class PostSearch(StatesGroup):
    waiting_for_query = State()

# Определение состояний для редактирования поста
class PostEdit(StatesGroup):
    waiting_for_text = State()
    waiting_for_photos = State()
    waiting_for_videos = State()
    confirmation = State()
    manage_photos = State()
    waiting_for_photo_to_delete = State()
    manage_videos = State()
    waiting_for_video_to_delete = State()

router = Router()

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
                        posts = data.get("posts", [])
                        print(f"Received {len(posts)} posts from API")

                        # Отладочный вывод для поиска
                        if search_query:
                            print(f"Search results for '{search_query}':")
                            for i, post in enumerate(posts, 1):
                                print(f"{i}. Post ID: {post.get('id')}, Name: {post.get('name')}")
                                text = post.get('text', '')
                                print(f"   Text: {text[:100]}...")

                        # If searching, return all posts without filtering by archive status
                        if search_query:
                            return posts

                        # Filter posts based on archive status
                        if is_archived:
                            # Consider a post archived if it's published to all platforms
                            filtered_posts = [p for p in posts if p.get("is_published_vk") and p.get("is_published_telegram")]
                        else:
                            # Pending posts are those not published to at least one platform
                            filtered_posts = [p for p in posts if not (p.get("is_published_vk") and p.get("is_published_telegram"))]

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

async def get_post_api(post_id):
    """Get a specific post from API."""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"http://{API_HOST}:{API_PORT}/api/posts/{post_id}"
            print(f"Fetching post from {url}")

            try:
                async with session.get(url) as response:
                    print(f"API response status: {response.status}")

                    if response.status == 200:
                        post_data = await response.json()
                        return post_data
                    else:
                        error_text = await response.text()
                        print(f"API Error: {response.status} - {error_text}")
                        return None
            except Exception as e:
                print(f"Error during API request: {str(e)}")
                return None
    except Exception as e:
        print(f"Error in get_post_api: {str(e)}")
        return None

async def delete_post_api(post_id):
    """Delete a post via API."""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"http://{API_HOST}:{API_PORT}/api/posts/{post_id}"
            print(f"Deleting post via {url}")

            try:
                async with session.delete(url) as response:
                    print(f"API response status: {response.status}")

                    if response.status == 204:
                        return True
                    else:
                        error_text = await response.text()
                        print(f"API Error: {response.status} - {error_text}")
                        return False
            except Exception as e:
                print(f"Error during API request: {str(e)}")
                return False
    except Exception as e:
        print(f"Error in delete_post_api: {str(e)}")
        return False

async def publish_post_api(post_id, platform):
    """Publish a post to a specific platform via API."""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"http://{API_HOST}:{API_PORT}/api/posts/{post_id}/publish/{platform}"
            print(f"Publishing post to {platform} via {url}")

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
        print(f"Error in publish_post_api: {str(e)}")
        return None

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

async def update_post_api(post_id, text=None, photos=None, videos=None):
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

async def show_pending_posts(message: Message):
    """Show pending posts."""
    try:
        print("Fetching pending posts...")
        posts = await get_posts_api(is_archived=False)
        print(f"Fetched {len(posts)} pending posts")

        if not posts:
            # Create back button
            buttons = [[InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")]]
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

            await message.edit_text(
                "📭 Отложенных постов нет.",
                reply_markup=keyboard
            )
            return

        # Send list of posts
        response_text = "📋 Отложенные посты:\n\n"

        # Create buttons for each post
        buttons = []

        for i, post in enumerate(posts, 1):
            # Format post info
            post_name = post.get("name", "Без названия")
            created_at_str = post.get("created_at", "")

            # Безопасное преобразование даты
            try:
                if created_at_str:
                    created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                    created_at_formatted = created_at.strftime('%d.%m.%Y %H:%M')
                else:
                    created_at_formatted = "Неизвестно"
            except Exception as e:
                print(f"Error parsing date: {str(e)}")
                created_at_formatted = "Неизвестно"

            photo_count = len(post.get("photos", []))
            video_count = len(post.get("videos", []))

            # Add platform status indicators
            vk_status = "✅" if post.get("is_published_vk") else "❌"
            tg_status = "✅" if post.get("is_published_telegram") else "❌"

            response_text += f"{i}. {post_name}\n"
            response_text += f"   Создан: {created_at_formatted}\n"
            response_text += f"   Медиа: {photo_count}📷 {video_count}📹\n"

            # Добавляем статус Instagram
            ig_status = "✅" if post.get("is_published_instagram") else "❌"

            response_text += f"   ВК: {vk_status}, ТГ: {tg_status}, IG: {ig_status}\n\n"

            # Add button for this post
            post_id = post.get('id')
            if post_id:
                buttons.append([InlineKeyboardButton(
                    text=f"{i}. {post_name[:30]}{'...' if len(post_name) > 30 else ''}",
                    callback_data=f"view_post_{post_id}"
                )])

        # Add back button
        buttons.append([InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")])

        # Create keyboard
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        # Store post IDs in user data (for backward compatibility)
        if hasattr(message, 'bot') and hasattr(message.bot, 'user_data') and hasattr(message, 'from_user'):
            try:
                user_data = {f"post_{i}": post.get("id") for i, post in enumerate(posts, 1) if post.get("id")}
                if message.from_user.id not in message.bot.user_data:
                    message.bot.user_data[message.from_user.id] = {}
                message.bot.user_data[message.from_user.id].update(user_data)
            except Exception as e:
                print(f"Error updating user_data: {str(e)}")

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

async def show_archived_posts(message: Message, year=None, month=None, day=None, search_results=None):
    """Show archived posts with date-based navigation.

    Args:
        message: The message to edit
        year: Optional year to filter posts
        month: Optional month to filter posts (requires year)
        day: Optional day to filter posts (requires year and month)
        search_results: Optional list of posts from search
    """
    try:
        # If search results are provided, use them instead of fetching from API
        posts = search_results if search_results is not None else await get_posts_api(is_archived=True)

        # Отладочный вывод для search_results
        if search_results is not None:
            print(f"show_archived_posts received search_results: {len(search_results)} posts")
            for i, post in enumerate(search_results, 1):
                print(f"  {i}. Post ID: {post.get('id')}, Name: {post.get('name')}")
                print(f"     Text: {post.get('text', '')[:50]}...")

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
                post_name = post.get("name", "Без названия")
                photo_count = len(post.get("photos", []))
                video_count = len(post.get("videos", []))
                text = post.get("text", "")[:100] + "..." if len(post.get("text", "")) > 100 else post.get("text", "")

                response_text += f"{i}. {post_name}\n"
                response_text += f"   Медиа: {photo_count}📷 {video_count}📹\n"
                response_text += f"   Текст: {text}\n\n"

                # Add button for this post
                buttons.append([InlineKeyboardButton(
                    text=f"{i}. {post_name[:30]}{'...' if len(post_name) > 30 else ''}",
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
                    post_name = post.get("name", "Без названия")
                    photo_count = len(post.get("photos", []))
                    video_count = len(post.get("videos", []))

                    response_text += f"{i}. {post_name}\n"
                    response_text += f"   Медиа: {photo_count}📷 {video_count}📹\n\n"

                    # Add button for this post
                    buttons.append([InlineKeyboardButton(
                        text=f"{i}. {post_name[:30]}{'...' if len(post_name) > 30 else ''}",
                        callback_data=f"view_post_{post.get('id')}"
                    )])

                response_text += "📂 Архив по годам:\n\n"

            # Add year buttons
            years = sorted(posts_by_date.keys(), reverse=True)
            for year in years:
                # Count posts in this year
                year_post_count = sum(
                    len(posts_by_date[year][month][day])
                    for month in posts_by_date[year]
                    for day in posts_by_date[year][month]
                )

                buttons.append([InlineKeyboardButton(
                    text=f"📅 {year} ({year_post_count} постов)",
                    callback_data=f"archive_year_{year}"
                )])

        elif month is None:
            # Year level - show months
            response_text = f"📁 Архив постов за {year} год:\n\n"

            # Add month buttons
            months = sorted(posts_by_date[year].keys(), reverse=True)
            for month in months:
                # Count posts in this month
                month_post_count = sum(
                    len(posts_by_date[year][month][day])
                    for day in posts_by_date[year][month]
                )

                # Get month name
                month_name = {
                    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
                    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
                    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
                }.get(month, str(month))

                buttons.append([InlineKeyboardButton(
                    text=f"📅 {month_name} ({month_post_count} постов)",
                    callback_data=f"archive_month_{year}_{month}"
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
            days = sorted(posts_by_date[year][month].keys(), reverse=True)
            for day in days:
                # Count posts on this day
                day_post_count = len(posts_by_date[year][month][day])

                buttons.append([InlineKeyboardButton(
                    text=f"📅 {day} {month_name} ({day_post_count} постов)",
                    callback_data=f"archive_day_{year}_{month}_{day}"
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
            day_posts = posts_by_date[year][month][day]
            for i, post in enumerate(day_posts, 1):
                post_name = post.get("name", "Без названия")
                created_at = datetime.fromisoformat(post.get("created_at").replace("Z", "+00:00"))
                photo_count = len(post.get("photos", []))
                video_count = len(post.get("videos", []))

                # Add platform status indicators
                vk_published_at = post.get("published_vk_at")
                tg_published_at = post.get("published_telegram_at")

                vk_date = datetime.fromisoformat(vk_published_at.replace("Z", "+00:00")).strftime("%d.%m.%Y") if vk_published_at else "—"
                tg_date = datetime.fromisoformat(tg_published_at.replace("Z", "+00:00")).strftime("%d.%m.%Y") if tg_published_at else "—"

                response_text += f"{i}. {post_name}\n"
                response_text += f"   Создан: {created_at.strftime('%H:%M')}\n"
                response_text += f"   Медиа: {photo_count}📷 {video_count}📹\n"

                # Добавляем статус Instagram
                ig_published_at = post.get("published_instagram_at")
                ig_date = datetime.fromisoformat(ig_published_at.replace("Z", "+00:00")).strftime("%d.%m.%Y") if ig_published_at else "—"

                response_text += f"   Опубликован: ВК ({vk_date}), ТГ ({tg_date}), IG ({ig_date})\n\n"

                # Add button for this post
                buttons.append([InlineKeyboardButton(
                    text=f"{i}. {post_name[:30]}{'...' if len(post_name) > 30 else ''}",
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
                posts_to_store = posts_by_date[year][month][day]
                user_data = {f"post_{i}": post.get("id") for i, post in enumerate(posts_to_store, 1)}
                message.bot.user_data[message.from_user.id].update(user_data)
            elif posts_today:
                # Если мы на корневом уровне и есть посты за сегодня
                user_data = {f"post_{i}": post.get("id") for i, post in enumerate(posts_today, 1)}
                message.bot.user_data[message.from_user.id].update(user_data)

        await message.edit_text(response_text, reply_markup=keyboard)
    except Exception as e:
        # Create back button
        buttons = [[InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")]]
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        await message.edit_text(
            f"❌ Ошибка при загрузке архива: {str(e)}",
            reply_markup=keyboard
        )

@router.callback_query(lambda c: c.data and c.data.startswith("view_post_"))
async def view_post_callback(callback: CallbackQuery):
    """Handle post selection via callback query."""
    # Extract post_id from callback data
    post_id = callback.data.replace("view_post_", "")

    # Get post details
    post = await get_post_api(post_id)

    if not post:
        await callback.message.edit_text(
            "❌ Пост не найден. Возможно, он был удален.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")]
            ])
        )
        await callback.answer()
        return

    # Format post details
    post_name = post.get("name", "Без названия")
    text = post.get("text", "")
    photo_count = len(post.get("photos", []))
    video_count = len(post.get("videos", []))

    # Truncate text if too long
    if len(text) > 1000:
        text = text[:997] + "..."

    response_text = f"📝 {post_name}\n\n"
    response_text += f"{text}\n\n"
    response_text += f"📷 {photo_count} фото\n"
    response_text += f"📹 {video_count} видео\n\n"

    # Add platform status
    vk_status = "✅" if post.get("is_published_vk") else "❌"
    tg_status = "✅" if post.get("is_published_telegram") else "❌"
    ig_status = "✅" if post.get("is_published_instagram") else "❌"

    response_text += f"ВК: {vk_status}, ТГ: {tg_status}, IG: {ig_status}"

    # Store selected post ID and determine if we're coming from archive
    if hasattr(callback.bot, 'user_data'):
        if callback.from_user.id not in callback.bot.user_data:
            callback.bot.user_data[callback.from_user.id] = {}

        # Store the post ID
        callback.bot.user_data[callback.from_user.id]["selected_post"] = post_id

        # Determine if we're viewing from archive based on stored state
        from_archive = False
        archive_state = callback.bot.user_data[callback.from_user.id].get("archive_state", {})
        if archive_state.get("year") is not None:
            from_archive = True

    # Send post details with action keyboard
    await callback.message.edit_text(
        response_text,
        reply_markup=get_post_actions_keyboard(from_archive=from_archive)
    )

    await callback.answer()

# Оставляем для обратной совместимости
@router.message(lambda message: message.text and message.text.isdigit() and
                (not hasattr(message, 'bot') or
                not hasattr(message.bot, 'user_data') or
                not message.bot.user_data.get(message.from_user.id, {}).get("in_search_mode", False)) and
                not message.bot.user_data.get(message.from_user.id, {}).get("in_edit_mode", False))
async def process_post_selection(message: Message):
    """Process post selection by number."""
    post_number = int(message.text)

    # Get user data
    user_data = message.bot.user_data.get(message.from_user.id, {})
    post_id = user_data.get(f"post_{post_number}")

    if not post_id:
        await message.reply("❌ Неверный номер поста. Пожалуйста, выберите пост из списка.")
        return

    # Get post details
    post = await get_post_api(post_id)

    if not post:
        await message.reply("❌ Пост не найден. Возможно, он был удален.")
        return

    # Format post details
    post_name = post.get("name", "Без названия")
    text = post.get("text", "")
    photo_count = len(post.get("photos", []))
    video_count = len(post.get("videos", []))

    # Truncate text if too long
    if len(text) > 1000:
        text = text[:997] + "..."

    response_text = f"📝 {post_name}\n\n"
    response_text += f"{text}\n\n"
    response_text += f"📷 {photo_count} фото\n"
    response_text += f"📹 {video_count} видео\n\n"

    # Add platform status
    vk_status = "✅" if post.get("is_published_vk") else "❌"
    tg_status = "✅" if post.get("is_published_telegram") else "❌"
    ig_status = "✅" if post.get("is_published_instagram") else "❌"

    response_text += f"ВК: {vk_status}, ТГ: {tg_status}, IG: {ig_status}"

    # Store selected post ID
    message.bot.user_data[message.from_user.id]["selected_post"] = post_id

    # Send post details with action keyboard
    await message.reply(
        response_text,
        reply_markup=get_post_actions_keyboard()
    )

@router.callback_query(F.data == "publish_vk")
async def publish_to_vk(callback: CallbackQuery):
    """Publish post to VK."""
    # Сразу отвечаем на callback, чтобы избежать ошибки "query is too old"
    await callback.answer("Публикую пост в ВК...")

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

    # Check if already published
    if post.get("is_published_vk"):
        await callback.message.answer("❌ Пост уже опубликован в ВК.")
        return

    # Publish post
    status_message = await callback.message.edit_text(f"{callback.message.text}\n\n⏳ Публикую в ВК...")

    try:
        result = await publish_post_api(post_id, "vk")

        if result:
            await status_message.edit_text(
                f"{status_message.text.split('⏳')[0]}\n\n✅ Опубликовано в ВК!",
                reply_markup=get_post_actions_keyboard()
            )
        else:
            await status_message.edit_text(
                f"{status_message.text.split('⏳')[0]}\n\n❌ Ошибка при публикации в ВК.",
                reply_markup=get_post_actions_keyboard()
            )
    except Exception as e:
        await status_message.edit_text(
            f"{status_message.text.split('⏳')[0]}\n\n❌ Ошибка: {str(e)}",
            reply_markup=get_post_actions_keyboard()
        )

@router.callback_query(F.data == "publish_telegram")
async def publish_to_telegram(callback: CallbackQuery):
    """Publish post to Telegram."""
    # Сразу отвечаем на callback, чтобы избежать ошибки "query is too old"
    await callback.answer("Публикую пост в Telegram...")

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

    # Check if already published
    if post.get("is_published_telegram"):
        await callback.message.answer("❌ Пост уже опубликован в Telegram.")
        return

    # Publish post
    status_message = await callback.message.edit_text(f"{callback.message.text}\n\n⏳ Публикую в Telegram...")

    try:
        result = await publish_post_api(post_id, "telegram")

        if result:
            await status_message.edit_text(
                f"{status_message.text.split('⏳')[0]}\n\n✅ Опубликовано в Telegram!",
                reply_markup=get_post_actions_keyboard()
            )
        else:
            await status_message.edit_text(
                f"{status_message.text.split('⏳')[0]}\n\n❌ Ошибка при публикации в Telegram.",
                reply_markup=get_post_actions_keyboard()
            )
    except Exception as e:
        await status_message.edit_text(
            f"{status_message.text.split('⏳')[0]}\n\n❌ Ошибка: {str(e)}",
            reply_markup=get_post_actions_keyboard()
        )

@router.callback_query(F.data == "publish_instagram")
async def publish_to_instagram(callback: CallbackQuery):
    """Publish post to Instagram."""
    # Сразу отвечаем на callback, чтобы избежать ошибки "query is too old"
    await callback.answer("Публикую пост в Instagram...")

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

    # Check if already published
    if post.get("is_published_instagram"):
        await callback.message.answer("❌ Пост уже опубликован в Instagram.")
        return

    # Publish post
    status_message = await callback.message.edit_text(f"{callback.message.text}\n\n⏳ Публикую в Instagram...")

    try:
        result = await publish_post_api(post_id, "instagram")

        if result:
            await status_message.edit_text(
                f"{status_message.text.split('⏳')[0]}\n\n✅ Опубликовано в Instagram!",
                reply_markup=get_post_actions_keyboard()
            )
        else:
            await status_message.edit_text(
                f"{status_message.text.split('⏳')[0]}\n\n❌ Ошибка при публикации в Instagram.",
                reply_markup=get_post_actions_keyboard()
            )
    except Exception as e:
        await status_message.edit_text(
            f"{status_message.text.split('⏳')[0]}\n\n❌ Ошибка: {str(e)}",
            reply_markup=get_post_actions_keyboard()
        )

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

    # Determine if we're coming from archive
    from_archive = False
    archive_state = user_data.get("archive_state", {})
    if archive_state.get("year") is not None:
        from_archive = True

    # Show post details
    await callback.message.edit_text(
        callback.message.text.split("\n\n📱 Выберите платформу")[0],
        reply_markup=get_post_actions_keyboard(from_archive=from_archive)
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
                reply_markup=get_post_actions_keyboard()
            )
            return

        # Publish story
        result = await publish_story_api(story.get("id"))

        if result:
            await status_message.edit_text(
                f"{status_message.text.split('⏳')[0]}\n\n✅ Сторис опубликован в ВК!",
                reply_markup=get_post_actions_keyboard()
            )
        else:
            await status_message.edit_text(
                f"{status_message.text.split('⏳')[0]}\n\n❌ Ошибка при публикации сторис в ВК.",
                reply_markup=get_post_actions_keyboard()
            )
    except Exception as e:
        await status_message.edit_text(
            f"{status_message.text.split('⏳')[0]}\n\n❌ Ошибка: {str(e)}",
            reply_markup=get_post_actions_keyboard()
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
                reply_markup=get_post_actions_keyboard()
            )
            return

        # Publish story
        result = await publish_story_api(story.get("id"))

        if result:
            await status_message.edit_text(
                f"{status_message.text.split('⏳')[0]}\n\n✅ Сторис опубликован в Telegram!",
                reply_markup=get_post_actions_keyboard()
            )
        else:
            await status_message.edit_text(
                f"{status_message.text.split('⏳')[0]}\n\n❌ Ошибка при публикации сторис в Telegram.",
                reply_markup=get_post_actions_keyboard()
            )
    except Exception as e:
        await status_message.edit_text(
            f"{status_message.text.split('⏳')[0]}\n\n❌ Ошибка: {str(e)}",
            reply_markup=get_post_actions_keyboard()
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
                reply_markup=get_post_actions_keyboard()
            )
            return

        # Publish story
        result = await publish_story_api(story.get("id"))

        if result:
            await status_message.edit_text(
                f"{status_message.text.split('⏳')[0]}\n\n✅ Сторис опубликован в Instagram!",
                reply_markup=get_post_actions_keyboard()
            )
        else:
            await status_message.edit_text(
                f"{status_message.text.split('⏳')[0]}\n\n❌ Ошибка при публикации сторис в Instagram.",
                reply_markup=get_post_actions_keyboard()
            )
    except Exception as e:
        await status_message.edit_text(
            f"{status_message.text.split('⏳')[0]}\n\n❌ Ошибка: {str(e)}",
            reply_markup=get_post_actions_keyboard()
        )

@router.callback_query(F.data == "publish_all")
async def publish_to_all(callback: CallbackQuery):
    """Publish post to all platforms."""
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
    await callback.message.edit_text(f"{callback.message.text}\n\n⏳ Публикую во все соцсети...")

    try:
        results = []

        # Publish to VK if not already published
        if not post.get("is_published_vk"):
            vk_result = await publish_post_api(post_id, "vk")
            results.append(("ВК", vk_result is not None))

        # Publish to Telegram if not already published
        if not post.get("is_published_telegram"):
            tg_result = await publish_post_api(post_id, "telegram")
            results.append(("Telegram", tg_result is not None))

        # Publish to Instagram if not already published
        if not post.get("is_published_instagram"):
            ig_result = await publish_post_api(post_id, "instagram")
            results.append(("Instagram", ig_result is not None))

        # Format results
        result_text = "\n\n📤 Результаты публикации:\n"
        for platform, success in results:
            status = "✅" if success else "❌"
            result_text += f"{platform}: {status}\n"

        await callback.message.edit_text(
            f"{callback.message.text.split('⏳')[0]}{result_text}",
            reply_markup=get_post_actions_keyboard()
        )
    except Exception as e:
        await callback.message.edit_text(
            f"{callback.message.text.split('⏳')[0]}\n\n❌ Ошибка: {str(e)}",
            reply_markup=get_post_actions_keyboard()
        )

    await callback.answer()

@router.callback_query(F.data == "delete")
async def confirm_delete_post(callback: CallbackQuery):
    """Ask for confirmation before deleting a post."""
    # Create confirmation keyboard
    buttons = [
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data="confirm_delete"),
            InlineKeyboardButton(text="❌ Нет, отмена", callback_data="cancel_delete")
        ]
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

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

    # Delete post
    await callback.message.edit_text(f"{callback.message.text.split('⚠️')[0]}\n\n⏳ Удаляю пост...")

    try:
        success = await delete_post_api(post_id)

        if success:
            await callback.message.edit_text("✅ Пост успешно удален.")
            await callback.message.answer("Выберите действие:", reply_markup=get_main_keyboard())
        else:
            await callback.message.edit_text(
                f"{callback.message.text.split('⏳')[0]}\n\n❌ Ошибка при удалении поста.",
                reply_markup=get_post_actions_keyboard()
            )
    except Exception as e:
        await callback.message.edit_text(
            f"{callback.message.text.split('⏳')[0]}\n\n❌ Ошибка: {str(e)}",
            reply_markup=get_post_actions_keyboard()
        )

    await callback.answer()

@router.callback_query(F.data == "cancel_delete")
async def cancel_delete(callback: CallbackQuery):
    """Cancel post deletion."""
    await callback.message.edit_text(
        callback.message.text.split("⚠️")[0],
        reply_markup=get_post_actions_keyboard()
    )

    await callback.answer()

@router.callback_query(F.data == "edit")
async def edit_post(callback: CallbackQuery, state: FSMContext):
    """Start editing a post."""
    # Получаем ID выбранного поста
    user_data = callback.bot.user_data.get(callback.from_user.id, {})
    post_id = user_data.get("selected_post")

    if not post_id:
        await callback.answer("❌ Пост не выбран.", show_alert=True)
        return

    # Получаем данные поста
    post = await get_post_api(post_id)

    if not post:
        await callback.answer("❌ Пост не найден.", show_alert=True)
        return

    # Устанавливаем флаг редактирования
    if not hasattr(callback.bot, 'user_data'):
        callback.bot.user_data = {}
    if callback.from_user.id not in callback.bot.user_data:
        callback.bot.user_data[callback.from_user.id] = {}
    callback.bot.user_data[callback.from_user.id]["in_edit_mode"] = True

    # Сохраняем данные поста в состоянии
    await state.update_data(
        edit_post_id=post_id,
        edit_post_text=post.get("text", ""),
        edit_post_photos=post.get("photos", []),
        edit_post_videos=post.get("videos", []),
        original_post=post
    )

    # Отправляем сообщение с предложением отредактировать текст
    await callback.message.edit_text(
        f"✏️ *РЕДАКТИРОВАНИЕ ПОСТА*\n\n"
        f"*Текущий текст поста:*\n\n"
        f"{post.get('text', '')}\n\n"
        f"*Инструкция:* _Отправьте новый текст для поста или нажмите 'Далее', чтобы оставить текущий текст._",
        reply_markup=get_skip_back_keyboard(),
        parse_mode="Markdown"
    )

    # Устанавливаем состояние ожидания нового текста
    await state.set_state(PostEdit.waiting_for_text)

    await callback.answer()

@router.callback_query(F.data == "back_to_posts")
async def back_to_posts(callback: CallbackQuery):
    """Go back to the posts list."""
    await callback.message.edit_text("⏳ Возвращаюсь к списку постов...")
    await show_pending_posts(callback.message)

    await callback.answer()

@router.callback_query(F.data == "archive_root")
async def archive_root(callback: CallbackQuery):
    """Show the root archive view."""
    await callback.message.edit_text("⏳ Загружаю архив постов...")
    await show_archived_posts(callback.message)
    await callback.answer()

@router.callback_query(lambda c: c.data and c.data.startswith("archive_year_"))
async def archive_year(callback: CallbackQuery):
    """Show archive for a specific year."""
    year = int(callback.data.replace("archive_year_", ""))
    await callback.message.edit_text(f"⏳ Загружаю архив постов за {year} год...")
    await show_archived_posts(callback.message, year=year)
    await callback.answer()

@router.callback_query(lambda c: c.data and c.data.startswith("archive_month_"))
async def archive_month(callback: CallbackQuery):
    """Show archive for a specific month."""
    # Format: archive_month_YEAR_MONTH
    parts = callback.data.replace("archive_month_", "").split("_")
    year = int(parts[0])
    month = int(parts[1])

    month_name = {
        1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
        5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
        9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
    }.get(month, str(month))

    await callback.message.edit_text(f"⏳ Загружаю архив постов за {month_name} {year} года...")
    await show_archived_posts(callback.message, year=year, month=month)
    await callback.answer()

@router.callback_query(lambda c: c.data and c.data.startswith("archive_day_"))
async def archive_day(callback: CallbackQuery):
    """Show archive for a specific day."""
    # Format: archive_day_YEAR_MONTH_DAY
    parts = callback.data.replace("archive_day_", "").split("_")
    year = int(parts[0])
    month = int(parts[1])
    day = int(parts[2])

    month_name = {
        1: "Января", 2: "Февраля", 3: "Марта", 4: "Апреля",
        5: "Мая", 6: "Июня", 7: "Июля", 8: "Августа",
        9: "Сентября", 10: "Октября", 11: "Ноября", 12: "Декабря"
    }.get(month, str(month))

    await callback.message.edit_text(f"⏳ Загружаю архив постов за {day} {month_name} {year} года...")
    await show_archived_posts(callback.message, year=year, month=month, day=day)
    await callback.answer()

@router.callback_query(F.data == "back_to_archive")
async def back_to_archive(callback: CallbackQuery):
    """Go back to the archive based on stored state."""
    if hasattr(callback.message, 'bot') and hasattr(callback.message.bot, 'user_data') and hasattr(callback.message, 'from_user'):
        user_data = callback.message.bot.user_data.get(callback.message.from_user.id, {})
        archive_state = user_data.get("archive_state", {})

        year = archive_state.get("year")
        month = archive_state.get("month")
        day = archive_state.get("day")

        # Determine which level to go back to
        if day is not None:
            # Go back to month view
            await archive_month(callback)
        elif month is not None:
            # Go back to year view
            await archive_year(callback)
        elif year is not None:
            # Go back to root view
            await archive_root(callback)
        else:
            # Default to root view
            await archive_root(callback)
    else:
        # Default to root view
        await archive_root(callback)

@router.callback_query(F.data == "search_posts")
async def search_posts_callback(callback: CallbackQuery, state: FSMContext):
    """Handle the 'Search' button."""
    # Create keyboard with cancel button
    buttons = [[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_search")]]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(
        "🔍 Введите текст для поиска по постам в архиве:\n\n"
        "Вы можете искать по тексту поста или по дате (например, 2023, 0623, 06.23 и т.д.)",
        reply_markup=keyboard
    )

    # Set state to waiting for search query
    await state.set_state(PostSearch.waiting_for_query)

    # Set flag to indicate we're in search mode
    if not hasattr(callback.bot, 'user_data'):
        callback.bot.user_data = {}
    if callback.from_user.id not in callback.bot.user_data:
        callback.bot.user_data[callback.from_user.id] = {}

    callback.bot.user_data[callback.from_user.id]["in_search_mode"] = True

    await callback.answer()

@router.callback_query(PostSearch.waiting_for_query, F.data == "cancel_search")
async def cancel_search(callback: CallbackQuery, state: FSMContext):
    """Cancel search and return to archive."""
    # Clear search state
    await state.clear()

    # Reset search mode flag
    if hasattr(callback.bot, 'user_data') and callback.from_user.id in callback.bot.user_data:
        callback.bot.user_data[callback.from_user.id]["in_search_mode"] = False

    # Return to archive
    await callback.message.edit_text("⏳ Возвращаюсь к архиву постов...")
    await archive_root(callback)

@router.message(PostSearch.waiting_for_query)
async def process_search_query(message: Message, state: FSMContext):
    """Process search query."""
    # Get search query
    search_query = message.text.strip()

    if not search_query:
        # If query is empty, ask again
        await message.reply(
            "❌ Поисковый запрос не может быть пустым. Пожалуйста, введите текст для поиска или нажмите 'Отмена'.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_search")]
            ])
        )
        return

    # Clear state
    await state.clear()

    # Reset search mode flag after processing
    if hasattr(message.bot, 'user_data') and message.from_user.id in message.bot.user_data:
        message.bot.user_data[message.from_user.id]["in_search_mode"] = False

    # Show loading message
    status_message = await message.reply(f"🔍 Ищу посты по запросу: \"{search_query}\"...")

    # Search posts
    search_results = await get_posts_api(is_archived=True, search_query=search_query)

    # Отладочный вывод
    print(f"process_search_query: received {len(search_results)} results for query '{search_query}'")

    # Проверяем, что все посты имеют необходимые поля
    valid_results = []
    for post in search_results:
        if "id" in post and "created_at" in post and post.get("created_at"):
            valid_results.append(post)
        else:
            print(f"Warning: Invalid post data: {post}")

    print(f"Valid results: {len(valid_results)} out of {len(search_results)}")
    search_results = valid_results

    if not search_results:
        # If no results, show message
        await status_message.edit_text(
            f"🔍 По запросу \"{search_query}\" ничего не найдено.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔍 Новый поиск", callback_data="search_posts")],
                [InlineKeyboardButton(text="📁 Вернуться в архив", callback_data="archive_root")],
                [InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")]
            ])
        )
        return

    # Отладочный вывод
    print(f"Displaying search results: {len(search_results)} posts found")
    for i, post in enumerate(search_results, 1):
        print(f"{i}. Post ID: {post.get('id')}, Name: {post.get('name')}")

    # Display search results directly
    response_text = f"🔍 Результаты поиска по запросу \"{search_query}\":\n\n"
    buttons = []

    for i, post in enumerate(search_results, 1):
        post_name = post.get("name", "Без названия")
        photo_count = len(post.get("photos", []))
        video_count = len(post.get("videos", []))
        text = post.get("text", "")[:100] + "..." if len(post.get("text", "")) > 100 else post.get("text", "")

        response_text += f"{i}. {post_name}\n"
        response_text += f"   Медиа: {photo_count}📷 {video_count}📹\n"
        response_text += f"   Текст: {text}\n\n"

        # Add button for this post
        buttons.append([InlineKeyboardButton(
            text=f"{i}. {post_name[:30]}{'...' if len(post_name) > 30 else ''}",
            callback_data=f"view_post_{post.get('id')}"
        )])

    # Add search button
    buttons.append([InlineKeyboardButton(text="🔍 Новый поиск", callback_data="search_posts")])

    # Add main menu button
    buttons.append([InlineKeyboardButton(text="📁 Вернуться в архив", callback_data="archive_root")])
    buttons.append([InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")])

    # Create keyboard
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await status_message.edit_text(response_text, reply_markup=keyboard)

# Обработчики для редактирования поста

@router.message(PostEdit.waiting_for_text)
async def process_edit_text(message: Message, state: FSMContext):
    """Process edited post text."""
    # Получаем текст из сообщения
    new_text = message.text.strip()

    if not new_text:
        await message.reply("❌ Текст не может быть пустым. Пожалуйста, отправьте текст для поста.")
        return

    # Сохраняем новый текст в состоянии
    await state.update_data(edit_post_text=new_text)

    # Получаем данные из состояния
    data = await state.get_data()
    post_id = data.get("edit_post_id")
    photos = data.get("edit_post_photos", [])
    videos = data.get("edit_post_videos", [])

    # Отправляем сообщение о переходе к редактированию фото
    await message.reply(
        f"✅ *Текст поста обновлен!*\n\n"
        f"*Текущие медиафайлы:* {len(photos)} фото, {len(videos)} видео\n\n"
        f"*Инструкция:* _Отправьте новые фотографии, чтобы добавить их к посту, или выберите действие:_",
        reply_markup=get_media_management_keyboard(),
        parse_mode="Markdown"
    )

    # Переходим к состоянию ожидания фотографий
    await state.set_state(PostEdit.waiting_for_photos)

@router.callback_query(PostEdit.waiting_for_text, F.data == "skip")
async def skip_edit_text(callback: CallbackQuery, state: FSMContext):
    """Skip editing text."""
    # Получаем данные из состояния
    data = await state.get_data()
    photos = data.get("edit_post_photos", [])
    videos = data.get("edit_post_videos", [])

    # Отправляем сообщение о переходе к редактированию фото
    await callback.message.edit_text(
        f"✏️ *РЕДАКТИРОВАНИЕ ПОСТА*\n\n"
        f"*Текущие медиафайлы:* {len(photos)} фото, {len(videos)} видео\n\n"
        f"*Инструкция:* _Отправьте новые фотографии, чтобы добавить их к посту, или выберите действие:_",
        reply_markup=get_media_management_keyboard(),
        parse_mode="Markdown"
    )

    # Переходим к состоянию ожидания фотографий
    await state.set_state(PostEdit.waiting_for_photos)

    await callback.answer()

@router.callback_query(PostEdit.waiting_for_text, F.data == "back")
async def back_from_edit(callback: CallbackQuery, state: FSMContext):
    """Cancel editing and go back to post view."""
    # Получаем ID поста
    data = await state.get_data()
    post_id = data.get("edit_post_id")

    # Сбрасываем флаг редактирования
    if hasattr(callback.bot, 'user_data') and callback.from_user.id in callback.bot.user_data:
        callback.bot.user_data[callback.from_user.id]["in_edit_mode"] = False

    # Очищаем состояние
    await state.clear()

    # Получаем данные поста
    post = await get_post_api(post_id)

    if not post:
        await callback.message.edit_text(
            "❌ Пост не найден. Возможно, он был удален.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")]
            ])
        )
        await callback.answer()
        return

    # Форматируем данные поста
    post_name = post.get("name", "Без названия")
    text = post.get("text", "")
    photo_count = len(post.get("photos", []))
    video_count = len(post.get("videos", []))

    # Обрезаем текст, если он слишком длинный
    if len(text) > 1000:
        text = text[:997] + "..."

    response_text = f"📝 {post_name}\n\n"
    response_text += f"{text}\n\n"
    response_text += f"📷 {photo_count} фото\n"
    response_text += f"📹 {video_count} видео\n\n"

    # Добавляем статус публикации
    vk_status = "✅" if post.get("is_published_vk") else "❌"
    tg_status = "✅" if post.get("is_published_telegram") else "❌"
    ig_status = "✅" if post.get("is_published_instagram") else "❌"

    response_text += f"ВК: {vk_status}, ТГ: {tg_status}, IG: {ig_status}"

    # Отправляем сообщение с данными поста
    await callback.message.edit_text(
        response_text,
        reply_markup=get_post_actions_keyboard()
    )

    await callback.answer()

@router.message(PostEdit.waiting_for_photos, F.photo)
async def process_edit_photo(message: Message, state: FSMContext):
    """Process photo for editing post."""
    # Получаем данные из состояния
    data = await state.get_data()
    photos = data.get("edit_post_photos", [])

    # Проверяем, не превышено ли максимальное количество фотографий
    if len(photos) >= 10:
        await message.reply(
            "❌ Достигнуто максимальное количество фотографий (10). "
            "Нажмите 'Далее', чтобы перейти к видео."
        )
        return

    # Получаем file_id фотографии
    photo = message.photo[-1]
    file_id = photo.file_id

    # Добавляем фотографию в список, если её там ещё нет
    if file_id not in photos:
        photos.append(file_id)
        await state.update_data(edit_post_photos=photos)

    # Отправляем сообщение о добавлении фотографии
    await message.reply(
        f"✅ *Фотография добавлена!* Всего: {len(photos)}/10\n\n"
        f"*Инструкция:* _Отправьте ещё фотографии или нажмите 'Далее', чтобы перейти к видео._",
        reply_markup=get_skip_back_keyboard(),
        parse_mode="Markdown"
    )

@router.callback_query(PostEdit.waiting_for_photos, F.data == "skip")
async def skip_edit_photos(callback: CallbackQuery, state: FSMContext):
    """Skip editing photos."""
    # Получаем данные из состояния
    data = await state.get_data()
    photos = data.get("edit_post_photos", [])
    videos = data.get("edit_post_videos", [])

    # Отправляем сообщение о переходе к редактированию видео
    await callback.message.edit_text(
        f"✏️ *РЕДАКТИРОВАНИЕ ПОСТА*\n\n"
        f"*Текущие медиафайлы:* {len(photos)} фото, {len(videos)} видео\n\n"
        f"*Инструкция:* _Отправьте новые видео, чтобы добавить их к посту, или нажмите 'Далее', чтобы сохранить изменения._",
        reply_markup=get_skip_back_keyboard(),
        parse_mode="Markdown"
    )

    # Переходим к состоянию ожидания видео
    await state.set_state(PostEdit.waiting_for_videos)

    await callback.answer()

@router.callback_query(PostEdit.waiting_for_photos, F.data == "back")
async def back_to_edit_text(callback: CallbackQuery, state: FSMContext):
    """Go back to editing text."""
    # Получаем данные из состояния
    data = await state.get_data()
    text = data.get("edit_post_text", "")

    # Отправляем сообщение о возврате к редактированию текста
    await callback.message.edit_text(
        f"✏️ Редактирование поста\n\n"
        f"Текущий текст поста:\n\n"
        f"{text}\n\n"
        f"Отправьте новый текст для поста или нажмите 'Пропустить', чтобы оставить текущий текст.",
        reply_markup=get_skip_back_keyboard()
    )

    # Возвращаемся к состоянию ожидания текста
    await state.set_state(PostEdit.waiting_for_text)

    await callback.answer()

@router.message(PostEdit.waiting_for_videos, F.video)
async def process_edit_video(message: Message, state: FSMContext):
    """Process video for editing post."""
    # Получаем данные из состояния
    data = await state.get_data()
    videos = data.get("edit_post_videos", [])

    # Проверяем, не превышено ли максимальное количество видео
    if len(videos) >= 5:
        await message.reply(
            "❌ Достигнуто максимальное количество видео (5). "
            "Нажмите 'Далее', чтобы сохранить изменения."
        )
        return

    # Получаем file_id видео
    video = message.video
    file_id = video.file_id

    # Добавляем видео в список, если его там ещё нет
    if file_id not in videos:
        videos.append(file_id)
        await state.update_data(edit_post_videos=videos)

    # Отправляем сообщение о добавлении видео
    await message.reply(
        f"✅ *Видео добавлено!* Всего: {len(videos)}/5\n\n"
        f"*Инструкция:* _Отправьте ещё видео или нажмите 'Далее', чтобы сохранить изменения._",
        reply_markup=get_skip_back_keyboard(),
        parse_mode="Markdown"
    )

@router.callback_query(PostEdit.waiting_for_videos, F.data == "skip")
async def save_edit_post(callback: CallbackQuery, state: FSMContext):
    """Save edited post."""
    # Получаем данные из состояния
    data = await state.get_data()
    post_id = data.get("edit_post_id")
    text = data.get("edit_post_text")
    photos = data.get("edit_post_photos", [])
    videos = data.get("edit_post_videos", [])

    # Отправляем сообщение о сохранении изменений
    await callback.message.edit_text(
        f"⏳ Сохраняю изменения...\n\n"
        f"Текст: {len(text)} символов\n"
        f"Фото: {len(photos)}\n"
        f"Видео: {len(videos)}"
    )

    # Обновляем пост через API
    try:
        updated_post = await update_post_api(post_id, text=text, photos=photos, videos=videos)

        if updated_post:
            # Форматируем данные обновленного поста
            post_name = updated_post.get("name", "Без названия")
            updated_text = updated_post.get("text", "")
            photo_count = len(updated_post.get("photos", []))
            video_count = len(updated_post.get("videos", []))

            # Обрезаем текст, если он слишком длинный
            if len(updated_text) > 1000:
                updated_text = updated_text[:997] + "..."

            response_text = f"✅ Пост успешно обновлен!\n\n"
            response_text += f"📝 {post_name}\n\n"
            response_text += f"{updated_text}\n\n"
            response_text += f"📷 {photo_count} фото\n"
            response_text += f"📹 {video_count} видео\n\n"

            # Добавляем статус публикации
            vk_status = "✅" if updated_post.get("is_published_vk") else "❌"
            tg_status = "✅" if updated_post.get("is_published_telegram") else "❌"
            ig_status = "✅" if updated_post.get("is_published_instagram") else "❌"

            response_text += f"ВК: {vk_status}, ТГ: {tg_status}, IG: {ig_status}"

            # Отправляем сообщение с данными обновленного поста
            await callback.message.edit_text(
                response_text,
                reply_markup=get_post_actions_keyboard()
            )
        else:
            # Если не удалось обновить пост, отправляем сообщение об ошибке
            await callback.message.edit_text(
                "❌ Не удалось обновить пост. Пожалуйста, попробуйте еще раз.",
                reply_markup=get_post_actions_keyboard()
            )
    except Exception as e:
        # Если произошла ошибка, отправляем сообщение с ошибкой
        await callback.message.edit_text(
            f"❌ Ошибка при обновлении поста: {str(e)}",
            reply_markup=get_post_actions_keyboard()
        )

    # Сбрасываем флаг редактирования
    if hasattr(callback.bot, 'user_data') and callback.from_user.id in callback.bot.user_data:
        callback.bot.user_data[callback.from_user.id]["in_edit_mode"] = False

    # Очищаем состояние
    await state.clear()

    await callback.answer()

@router.callback_query(PostEdit.waiting_for_videos, F.data == "back")
async def back_to_edit_photos(callback: CallbackQuery, state: FSMContext):
    """Go back to editing photos."""
    # Получаем данные из состояния
    data = await state.get_data()
    photos = data.get("edit_post_photos", [])
    videos = data.get("edit_post_videos", [])

    # Отправляем сообщение о возврате к редактированию фото
    await callback.message.edit_text(
        f"✏️ *РЕДАКТИРОВАНИЕ ПОСТА*\n\n"
        f"*Текущие медиафайлы:* {len(photos)} фото, {len(videos)} видео\n\n"
        f"*Инструкция:* _Отправьте новые фотографии, чтобы добавить их к посту, или выберите действие:_",
        reply_markup=get_media_management_keyboard(),
        parse_mode="Markdown"
    )

    # Возвращаемся к состоянию ожидания фотографий
    await state.set_state(PostEdit.waiting_for_photos)

    await callback.answer()

@router.callback_query(PostEdit.waiting_for_photos, F.data == "manage_photos")
async def manage_photos(callback: CallbackQuery, state: FSMContext):
    """Show photo management interface."""
    # Получаем данные из состояния
    data = await state.get_data()
    photos = data.get("edit_post_photos", [])

    # Создаем сообщение с превью фотографий
    message_text = f"📷 *УПРАВЛЕНИЕ ФОТОГРАФИЯМИ*\n\n"

    if not photos:
        message_text += f"*В посте нет фотографий.*\n\n"
        message_text += f"*Инструкция:* _Нажмите 'Добавить фото', чтобы добавить фотографии к посту._"
    else:
        message_text += f"В посте {len(photos)} фото. Выберите фото для удаления:\n\n"

        for i, _ in enumerate(photos, 1):
            message_text += f"*Фото #{i}*\n"

    # Отправляем сообщение с клавиатурой для управления фотографиями
    await callback.message.edit_text(
        message_text,
        reply_markup=get_photo_management_keyboard(photos),
        parse_mode="Markdown"
    )

    # Устанавливаем состояние управления фотографиями
    await state.set_state(PostEdit.manage_photos)

    await callback.answer()

@router.callback_query(PostEdit.waiting_for_photos, F.data == "manage_videos")
async def manage_videos(callback: CallbackQuery, state: FSMContext):
    """Show video management interface."""
    # Получаем данные из состояния
    data = await state.get_data()
    videos = data.get("edit_post_videos", [])

    # Создаем сообщение с информацией о видео
    message_text = f"📹 *УПРАВЛЕНИЕ ВИДЕО*\n\n"

    if not videos:
        message_text += f"*В посте нет видео.*\n\n"
        message_text += f"*Инструкция:* _Нажмите 'Добавить видео', чтобы добавить видео к посту._"
    else:
        message_text += f"В посте {len(videos)} видео. Выберите видео для удаления:\n\n"

        for i, _ in enumerate(videos, 1):
            message_text += f"*Видео #{i}*\n"

    # Отправляем сообщение с клавиатурой для управления видео
    await callback.message.edit_text(
        message_text,
        reply_markup=get_video_management_keyboard(videos),
        parse_mode="Markdown"
    )

    # Устанавливаем состояние управления видео
    await state.set_state(PostEdit.manage_videos)

    await callback.answer()

@router.callback_query(PostEdit.manage_photos, F.data.startswith("delete_photo_"))
async def delete_photo(callback: CallbackQuery, state: FSMContext):
    """Delete a photo from the post."""
    # Получаем индекс фотографии для удаления
    photo_index = int(callback.data.replace("delete_photo_", ""))

    # Получаем данные из состояния
    data = await state.get_data()
    photos = data.get("edit_post_photos", [])

    if 0 <= photo_index < len(photos):
        # Удаляем фотографию из списка
        photos.pop(photo_index)
        await state.update_data(edit_post_photos=photos)

        # Обновляем сообщение
        if photos:
            message_text = f"✅ *Фото #{photo_index+1} удалено!*\n\n"
            message_text += f"📷 *УПРАВЛЕНИЕ ФОТОГРАФИЯМИ*\n\n"
            message_text += f"В посте осталось {len(photos)} фото. Выберите фото для удаления:\n\n"

            for i, _ in enumerate(photos, 1):
                message_text += f"*Фото #{i}*\n"

            await callback.message.edit_text(
                message_text,
                reply_markup=get_photo_management_keyboard(photos),
                parse_mode="Markdown"
            )
        else:
            # Если фотографий больше нет, возвращаемся к редактированию
            await callback.message.edit_text(
                f"✅ *Все фотографии удалены!*\n\n"
                f"*Инструкция:* _Отправьте новые фотографии, чтобы добавить их к посту, или выберите действие:_",
                reply_markup=get_media_management_keyboard(),
                parse_mode="Markdown"
            )
            await state.set_state(PostEdit.waiting_for_photos)
    else:
        await callback.answer("Ошибка: фотография не найдена", show_alert=True)

    await callback.answer()

@router.callback_query(PostEdit.manage_videos, F.data.startswith("delete_video_"))
async def delete_video(callback: CallbackQuery, state: FSMContext):
    """Delete a video from the post."""
    # Получаем индекс видео для удаления
    video_index = int(callback.data.replace("delete_video_", ""))

    # Получаем данные из состояния
    data = await state.get_data()
    videos = data.get("edit_post_videos", [])

    if 0 <= video_index < len(videos):
        # Удаляем видео из списка
        videos.pop(video_index)
        await state.update_data(edit_post_videos=videos)

        # Обновляем сообщение
        if videos:
            message_text = f"✅ *Видео #{video_index+1} удалено!*\n\n"
            message_text += f"📹 *УПРАВЛЕНИЕ ВИДЕО*\n\n"
            message_text += f"В посте осталось {len(videos)} видео. Выберите видео для удаления:\n\n"

            for i, _ in enumerate(videos, 1):
                message_text += f"*Видео #{i}*\n"

            await callback.message.edit_text(
                message_text,
                reply_markup=get_video_management_keyboard(videos),
                parse_mode="Markdown"
            )
        else:
            # Если видео больше нет, возвращаемся к редактированию
            await callback.message.edit_text(
                f"✅ *Все видео удалены!*\n\n"
                f"*Инструкция:* _Отправьте новые фотографии, чтобы добавить их к посту, или выберите действие:_",
                reply_markup=get_media_management_keyboard(),
                parse_mode="Markdown"
            )
            await state.set_state(PostEdit.waiting_for_photos)
    else:
        await callback.answer("Ошибка: видео не найдено", show_alert=True)

    await callback.answer()

@router.callback_query(PostEdit.manage_photos, F.data == "add_photos")
async def add_photos(callback: CallbackQuery, state: FSMContext):
    """Go back to adding photos."""
    # Получаем данные из состояния
    data = await state.get_data()
    photos = data.get("edit_post_photos", [])
    videos = data.get("edit_post_videos", [])

    # Отправляем сообщение о возврате к добавлению фотографий
    await callback.message.edit_text(
        f"✏️ *РЕДАКТИРОВАНИЕ ПОСТА*\n\n"
        f"*Текущие медиафайлы:* {len(photos)} фото, {len(videos)} видео\n\n"
        f"*Инструкция:* _Отправьте новые фотографии, чтобы добавить их к посту, или выберите действие:_",
        reply_markup=get_media_management_keyboard(),
        parse_mode="Markdown"
    )

    # Возвращаемся к состоянию ожидания фотографий
    await state.set_state(PostEdit.waiting_for_photos)

    await callback.answer()

@router.callback_query(PostEdit.manage_videos, F.data == "add_videos")
async def add_videos(callback: CallbackQuery, state: FSMContext):
    """Go to adding videos."""
    # Получаем данные из состояния
    data = await state.get_data()
    photos = data.get("edit_post_photos", [])
    videos = data.get("edit_post_videos", [])

    # Отправляем сообщение о переходе к добавлению видео
    await callback.message.edit_text(
        f"✏️ *РЕДАКТИРОВАНИЕ ПОСТА*\n\n"
        f"*Текущие медиафайлы:* {len(photos)} фото, {len(videos)} видео\n\n"
        f"*Инструкция:* _Отправьте новые видео, чтобы добавить их к посту, или нажмите 'Далее', чтобы сохранить изменения._",
        reply_markup=get_skip_back_keyboard(),
        parse_mode="Markdown"
    )

    # Переходим к состоянию ожидания видео
    await state.set_state(PostEdit.waiting_for_videos)

    await callback.answer()

@router.callback_query(PostEdit.manage_photos, F.data == "back_to_media_management")
@router.callback_query(PostEdit.manage_videos, F.data == "back_to_media_management")
async def back_to_media_management(callback: CallbackQuery, state: FSMContext):
    """Go back to media management."""
    # Получаем данные из состояния
    data = await state.get_data()
    photos = data.get("edit_post_photos", [])
    videos = data.get("edit_post_videos", [])

    # Отправляем сообщение о возврате к управлению медиафайлами
    await callback.message.edit_text(
        f"✏️ *РЕДАКТИРОВАНИЕ ПОСТА*\n\n"
        f"*Текущие медиафайлы:* {len(photos)} фото, {len(videos)} видео\n\n"
        f"*Инструкция:* _Отправьте новые фотографии, чтобы добавить их к посту, или выберите действие:_",
        reply_markup=get_media_management_keyboard(),
        parse_mode="Markdown"
    )

    # Возвращаемся к состоянию ожидания фотографий
    await state.set_state(PostEdit.waiting_for_photos)

    await callback.answer()
