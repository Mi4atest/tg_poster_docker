from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import aiohttp
from datetime import datetime

from app.bot.keyboards.main_keyboard import get_main_keyboard, get_skip_back_keyboard
from app.config.settings import API_HOST, API_PORT

router = Router()

# Define states for post creation
class PostCreation(StatesGroup):
    waiting_for_text = State()
    waiting_for_photos = State()
    waiting_for_videos = State()
    confirmation = State()

# API client function
async def create_post_api(text, photos, videos):
    """Send post data to API."""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"http://{API_HOST}:{API_PORT}/api/posts/"
            data = {
                "text": text,
                "photos": photos if photos else [],
                "videos": videos if videos else []
            }
            print(f"Creating post via {url} with {len(photos)} photos and {len(videos)} videos")

            try:
                async with session.post(url, json=data) as response:
                    print(f"API response status: {response.status}")

                    if response.status == 201:
                        result = await response.json()
                        print(f"Post created successfully with ID: {result.get('id')}")
                        return result
                    else:
                        error_text = await response.text()
                        print(f"API Error: {response.status} - {error_text}")
                        return None
            except Exception as e:
                print(f"Error during API request: {str(e)}")
                return None
    except Exception as e:
        print(f"Error creating post: {str(e)}")
        return None

@router.message(PostCreation.waiting_for_text, F.text)
async def process_post_text(message: Message, state: FSMContext):
    """Process the post text."""
    if not message.text or len(message.text.strip()) == 0:
        await message.reply("❌ Текст не может быть пустым. Пожалуйста, отправьте текст для поста.")
        return

    # Save text to state
    await state.update_data(text=message.text)

    # Ask for photos
    await message.reply(
        "✅ Текст поста сохранен!\n\n"
        "📷 Теперь отправьте фотографии для поста (от 1 до 10 шт).\n\n"
        "Отправляйте по одной фотографии или группу фотографий. Порядок отправки будет сохранен.",
        reply_markup=get_skip_back_keyboard()
    )

    # Initialize empty photos list
    await state.update_data(photos=[])

    # Move to next state
    await state.set_state(PostCreation.waiting_for_photos)

@router.message(PostCreation.waiting_for_text, F.photo | F.media_group_id)
async def process_text_with_photo(message: Message, state: FSMContext):
    """Process a message with text and photo."""
    # Save text to state if present
    if message.caption and len(message.caption.strip()) > 0:
        await state.update_data(text=message.caption)
    else:
        # If no caption, ask for text
        await message.reply("❌ Текст не может быть пустым. Пожалуйста, отправьте текст для поста.")
        return

    # Initialize photos list
    photos = []
    media_group_id = message.media_group_id

    # Get the largest photo (best quality)
    photo = message.photo[-1]
    photos.append(photo.file_id)

    # Save photos to state
    await state.update_data(photos=photos)

    # If it's a media group, we'll handle additional photos in the process_photo handler
    if media_group_id:
        await state.update_data(
            current_media_group=media_group_id,
            processed_media_groups=[],
            status_message_id=None
        )

        # Send status message
        status_message_obj = await message.reply(
            f"✅ Текст поста сохранен!\n\n"
            f"✅ Загружено {len(photos)}/10 фото\n\n"
            f"Дождитесь загрузки всех фотографий из группы...",
            reply_markup=get_skip_back_keyboard()
        )
        await state.update_data(status_message_id=status_message_obj.message_id)
    else:
        # Send confirmation message
        await message.reply(
            f"✅ Текст поста сохранен!\n\n"
            f"✅ Загружено {len(photos)}/10 фото\n\n"
            f"Отправьте еще фотографии или используйте кнопки ниже:",
            reply_markup=get_skip_back_keyboard()
        )

    # Move to photos state to allow adding more photos
    await state.set_state(PostCreation.waiting_for_photos)

@router.message(PostCreation.waiting_for_text, F.video)
async def process_text_with_video(message: Message, state: FSMContext):
    """Process a message with text and video."""
    # Save text to state if present
    if message.caption and len(message.caption.strip()) > 0:
        await state.update_data(text=message.caption)
    else:
        # If no caption, ask for text
        await message.reply("❌ Текст не может быть пустым. Пожалуйста, отправьте текст для поста.")
        return

    # Initialize videos list
    videos = []

    # Add video to list
    video = message.video
    videos.append(video.file_id)

    # Save videos to state
    await state.update_data(videos=videos)

    # Send confirmation message
    await message.reply(
        f"✅ Текст поста сохранен!\n\n"
        f"✅ Загружено видео: {video.file_size / (1024*1024):.1f} МБ\n\n"
        f"Теперь вы можете добавить фотографии или пропустить этот шаг:",
        reply_markup=get_skip_back_keyboard()
    )

    # Initialize empty photos list
    await state.update_data(photos=[])

    # Move to photos state to allow adding photos
    await state.set_state(PostCreation.waiting_for_photos)

@router.callback_query(PostCreation.waiting_for_photos, F.data == "skip")
async def skip_photos(callback: CallbackQuery, state: FSMContext):
    """Skip adding photos."""
    await callback.message.edit_text(
        "📹 Теперь отправьте видео для поста (до 50 МБ, формат .mov).\n\n"
        "Отправляйте по одному видео за раз.",
        reply_markup=get_skip_back_keyboard()
    )

    # Initialize empty videos list
    await state.update_data(videos=[])

    # Move to next state
    await state.set_state(PostCreation.waiting_for_videos)

    await callback.answer()

@router.callback_query(PostCreation.waiting_for_photos, F.data == "back")
async def back_to_text(callback: CallbackQuery, state: FSMContext):
    """Go back to entering text."""
    await callback.message.edit_text(
        "📝 Отправьте текст для нового поста."
    )

    # Move back to previous state
    await state.set_state(PostCreation.waiting_for_text)

    await callback.answer()

@router.message(PostCreation.waiting_for_photos, F.photo | F.media_group_id)
async def process_photo(message: Message, state: FSMContext):
    """Process a photo for the post."""
    # Get current data
    data = await state.get_data()
    photos = data.get("photos", [])
    status_message_id = data.get("status_message_id")
    media_group_id = message.media_group_id

    # Если это медиа-группа, сохраняем ID группы в состоянии
    if media_group_id:
        # Проверяем, обрабатывали ли мы уже эту группу
        processed_groups = data.get("processed_media_groups", [])
        if media_group_id in processed_groups:
            # Эта группа уже обработана, пропускаем
            return

        # Если это новая группа, добавляем её ID в список обработанных
        if "current_media_group" not in data or data["current_media_group"] != media_group_id:
            await state.update_data(current_media_group=media_group_id)
            print(f"Processing new media group: {media_group_id}")

    # Check if we already have 10 photos
    if len(photos) >= 10:
        if not status_message_id:
            status_message_obj = await message.answer(
                "❌ Вы уже добавили максимальное количество фотографий (10).\n\n"
                "Нажмите 'Пропустить', чтобы перейти к добавлению видео, или 'Назад', чтобы вернуться к редактированию текста.",
                reply_markup=get_skip_back_keyboard()
            )
            await state.update_data(status_message_id=status_message_obj.message_id)
        return

    # Get the largest photo (best quality)
    photo = message.photo[-1]

    # Add photo file_id to list if it's not already there
    if photo.file_id not in photos:
        photos.append(photo.file_id)
        # Update state
        await state.update_data(photos=photos)
        print(f"Added photo {len(photos)}/{10}, file_id: {photo.file_id[:15]}...")

    # Если это медиа-группа, обновляем статус только после небольшой задержки
    # чтобы дать время всем фото из группы обработаться
    if media_group_id:
        # Сохраняем текущее время последнего обновления для этой группы
        await state.update_data(last_media_update=datetime.now().timestamp())

        # Проверяем, прошло ли достаточно времени с момента последнего обновления
        last_update = data.get("last_media_update", 0)
        current_time = datetime.now().timestamp()

        # Если прошло менее 1 секунды, не обновляем статус
        if current_time - last_update < 1:
            return

    # Update or send status message
    status_text = (
        f"✅ Загружено {len(photos)}/10 фото\n\n"
        "Отправьте еще фотографии или используйте кнопки ниже:"
    )

    if status_message_id:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=status_message_id,
                text=status_text,
                reply_markup=get_skip_back_keyboard()
            )
        except Exception as e:
            # If message can't be edited (too old or deleted), send a new one
            print(f"Error editing message: {str(e)}")
            status_message_obj = await message.answer(
                status_text,
                reply_markup=get_skip_back_keyboard()
            )
            await state.update_data(status_message_id=status_message_obj.message_id)
    else:
        status_message_obj = await message.answer(
            status_text,
            reply_markup=get_skip_back_keyboard()
        )
        await state.update_data(status_message_id=status_message_obj.message_id)

    # Если это медиа-группа, отмечаем её как обработанную после обновления статуса
    if media_group_id:
        processed_groups = data.get("processed_media_groups", [])
        if media_group_id not in processed_groups:
            processed_groups.append(media_group_id)
            await state.update_data(processed_media_groups=processed_groups)
            print(f"Marked media group {media_group_id} as processed")

@router.callback_query(PostCreation.waiting_for_videos, F.data == "skip")
async def skip_videos(callback: CallbackQuery, state: FSMContext):
    """Skip adding videos and proceed to create post."""
    # Get all data
    data = await state.get_data()
    text = data.get("text", "")
    photos = data.get("photos", [])
    videos = data.get("videos", [])

    # Send data to API
    await callback.message.edit_text("⏳ Создаю пост...")

    try:
        post = await create_post_api(text, photos, videos)

        if post:
            # Format success message
            photo_count = len(photos)
            video_count = len(videos)
            post_name = post.get("name", "")

            success_text = f"✅ Пост успешно создан!\n\n"
            success_text += f"📝 {post_name}\n\n"

            if photo_count > 0:
                success_text += f"📷 Фотографий: {photo_count}\n"

            if video_count > 0:
                success_text += f"📹 Видео: {video_count}\n"

            success_text += "\nПост добавлен в список отложенных. Теперь вы можете опубликовать его в социальных сетях."

            # Create keyboard for post actions
            buttons = [
                [InlineKeyboardButton(text="📋 Перейти к отложенным постам", callback_data="pending_posts")],
                [InlineKeyboardButton(text="🏠 Вернуться в главное меню", callback_data="back_to_main")]
            ]
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

            await callback.message.edit_text(success_text, reply_markup=keyboard)
        else:
            await callback.message.edit_text(
                "❌ Ошибка при создании поста. Пожалуйста, попробуйте еще раз.",
                reply_markup=get_main_keyboard()
            )
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Произошла ошибка: {str(e)}",
            reply_markup=get_main_keyboard()
        )

    # Reset state
    await state.clear()

    await callback.answer()

@router.callback_query(PostCreation.waiting_for_videos, F.data == "back")
async def back_to_photos(callback: CallbackQuery, state: FSMContext):
    """Go back to adding photos."""
    await callback.message.edit_text(
        "📷 Отправьте фотографии для поста (от 1 до 10 шт).\n\n"
        "Отправляйте по одной фотографии за раз. Порядок отправки будет сохранен.",
        reply_markup=get_skip_back_keyboard()
    )

    # Move back to previous state
    await state.set_state(PostCreation.waiting_for_photos)

    await callback.answer()

@router.message(PostCreation.waiting_for_videos, F.video)
async def process_video(message: Message, state: FSMContext):
    """Process a video for the post."""
    # Get current data
    data = await state.get_data()
    videos = data.get("videos", [])
    status_message_id = data.get("status_message_id")

    # Check file size (Telegram already limits to 50MB)
    video = message.video

    # Add video file_id to list
    videos.append(video.file_id)

    # Update state
    await state.update_data(videos=videos)

    # Update or send status message
    status_text = (
        f"✅ Загружено {len(videos)} видео\n"
        f"Последнее видео: {video.file_size / (1024*1024):.1f} МБ\n\n"
        "Отправьте еще видео или используйте кнопки ниже:"
    )

    if status_message_id:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=status_message_id,
                text=status_text,
                reply_markup=get_skip_back_keyboard()
            )
        except Exception as e:
            # If message can't be edited (too old or deleted), send a new one
            print(f"Error editing message: {str(e)}")
            status_message_obj = await message.answer(
                status_text,
                reply_markup=get_skip_back_keyboard()
            )
            await state.update_data(status_message_id=status_message_obj.message_id)
    else:
        status_message_obj = await message.answer(
            status_text,
            reply_markup=get_skip_back_keyboard()
        )
        await state.update_data(status_message_id=status_message_obj.message_id)

@router.callback_query(F.data == "pending_posts")
async def pending_posts_callback(callback: CallbackQuery, state: FSMContext):
    """Handle the 'Pending Posts' button."""
    # Clear any active state
    await state.clear()

    await callback.message.edit_text(
        "⏳ Загружаю список отложенных постов...\n\n"
        "Пожалуйста, подождите."
    )

    # Fetch posts from API and display them
    from app.bot.handlers.post_management import show_pending_posts
    try:
        await show_pending_posts(callback.message)
    except Exception as e:
        print(f"Error showing pending posts: {str(e)}")
        await callback.message.edit_text(
            f"❌ Ошибка при загрузке отложенных постов: {str(e)}",
            reply_markup=get_main_keyboard()
        )

    await callback.answer()

@router.callback_query(F.data == "back_to_main")
async def back_to_main_menu(callback: CallbackQuery, state: FSMContext):
    """Return to the main menu."""
    # Clear any active state
    await state.clear()

    await callback.message.edit_text(
        "👋 Добро пожаловать в систему автоматизированного постинга в соцсети!\n\n"
        "Используйте кнопки ниже для управления постами:",
        reply_markup=get_main_keyboard()
    )

    await callback.answer()
