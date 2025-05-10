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
