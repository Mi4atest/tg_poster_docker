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
async def delete_previous_messages(message, state):
    """Delete previous bot messages to keep the interface clean."""
    data = await state.get_data()
    bot_message_ids = data.get("bot_message_ids", [])

    # Удаляем предыдущие сообщения бота
    for msg_id in bot_message_ids:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
        except Exception as e:
            print(f"Error deleting message: {str(e)}")

    # Инициализируем новый список сообщений бота
    await state.update_data(bot_message_ids=[])

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
            print(f"DEBUG: Photos data: {photos}")
            print(f"DEBUG: Videos data: {videos}")
            print(f"DEBUG: Full data being sent: {data}")

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

# Этот обработчик перенесен в start.py и заменен на callback_query
# @router.message(F.text == "🆕 Создать пост")
# async def start_post_creation(message: Message, state: FSMContext):
#     """Start the post creation process."""
#     await message.answer(
#         "📝 Отправьте текст для нового поста."
#     )
#     await state.set_state(PostCreation.waiting_for_text)

@router.message(PostCreation.waiting_for_text, F.text)
async def process_post_text(message: Message, state: FSMContext):
    """Process the post text."""
    if not message.text or len(message.text.strip()) == 0:
        await message.reply("❌ Текст не может быть пустым. Пожалуйста, отправьте текст для поста.")
        return

    # Save text to state
    await state.update_data(text=message.text)

    # Удаляем предыдущие сообщения бота
    await delete_previous_messages(message, state)

    # Ask for photos
    status_message = await message.reply(
        "Теперь фото. ОБЯЗАТЕЛЬНО отправляй (значок фото) БЕЗ ГРУППИРОВКИ (3 точки в правом верхнем углу, когда будешь выбирать фото)",
        reply_markup=get_skip_back_keyboard()
    )

    # Сохраняем ID сообщения бота
    await state.update_data(bot_message_ids=[status_message.message_id], status_message_id=status_message.message_id)

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
        # Проверяем, обрабатывали ли мы уже эту группу и эту конкретную фотографию
        processed_photos = data.get("processed_photos", [])
        if message.photo[-1].file_id in processed_photos:
            # Эта фотография уже обработана, пропускаем
            return

        # Добавляем фото в список обработанных
        processed_photos = data.get("processed_photos", [])
        if message.photo[-1].file_id not in processed_photos:
            processed_photos.append(message.photo[-1].file_id)
            await state.update_data(processed_photos=processed_photos)

        # Если это новая группа, инициализируем временное хранилище для фотографий
        if "current_media_group" not in data or data["current_media_group"] != media_group_id:
            # Создаем временное хранилище для фотографий из этой группы
            # Но не очищаем temp_photos, если они уже есть
            temp_photos = data.get("temp_photos", [])
            await state.update_data(
                current_media_group=media_group_id,
                processed_media_groups=[]
            )
            print(f"Processing new media group: {media_group_id}")

        # Выводим отладочную информацию
        print(f"DEBUG: Processing photo: file_id={message.photo[-1].file_id}")

        # Сохраняем фотографию во временное хранилище
        temp_photos = data.get("temp_photos", [])

        # Получаем актуальные данные из состояния
        updated_data = await state.get_data()
        temp_photos = updated_data.get("temp_photos", [])

        # Проверяем, есть ли уже такой file_id в temp_photos
        file_id_exists = False
        for photo in temp_photos:
            if photo.get("file_id") == message.photo[-1].file_id:
                file_id_exists = True
                break

        if not file_id_exists:
            # Сохраняем не только file_id, но и информацию о фотографии для отображения
            temp_photos.append({
                "file_id": message.photo[-1].file_id,
                "width": message.photo[-1].width,
                "height": message.photo[-1].height,
                "file_size": message.photo[-1].file_size
            })
            # Update state
            await state.update_data(temp_photos=temp_photos)
            print(f"Added photo to temp storage: {len(temp_photos)}/{10}, file_id: {message.photo[-1].file_id[:15]}...")

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

    # Если это не медиа-группа, добавляем фото в список photos
    if not media_group_id:
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

    # Для одиночных фотографий (не в группе) обновляем статусное сообщение только при первой фотографии
    if not media_group_id and len(photos) == 1:
        # Удаляем предыдущие сообщения бота, кроме статусного
        bot_message_ids = data.get("bot_message_ids", [])
        if status_message_id and status_message_id in bot_message_ids:
            bot_message_ids.remove(status_message_id)

        for msg_id in bot_message_ids:
            try:
                await message.bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
            except Exception as e:
                print(f"Error deleting message: {str(e)}")

        # Отправляем статусное сообщение только при первой фотографии
        status_text = (
            "📷 Отправьте еще фотографии или видео, либо нажмите 'Далее'."
        )

        if status_message_id:
            try:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=status_message_id,
                    text=status_text,
                    reply_markup=get_skip_back_keyboard()
                )
                # Обновляем список сообщений бота
                await state.update_data(bot_message_ids=[status_message_id])
            except Exception as e:
                # If message can't be edited (too old or deleted), send a new one
                print(f"Error editing message: {str(e)}")
                status_message_obj = await message.answer(
                    status_text,
                    reply_markup=get_skip_back_keyboard()
                )
                await state.update_data(status_message_id=status_message_obj.message_id, bot_message_ids=[status_message_obj.message_id])
        else:
            status_message_obj = await message.answer(
                status_text,
                reply_markup=get_skip_back_keyboard()
            )
            await state.update_data(status_message_id=status_message_obj.message_id, bot_message_ids=[status_message_obj.message_id])

    # Если это медиа-группа, отмечаем её как обработанную после обновления статуса
    if media_group_id:
        processed_groups = data.get("processed_media_groups", [])
        if media_group_id not in processed_groups:
            processed_groups.append(media_group_id)
            await state.update_data(processed_media_groups=processed_groups)
            print(f"Marked media group {media_group_id} as processed")

            # Удаляем предыдущие сообщения бота, кроме статусного
            bot_message_ids = data.get("bot_message_ids", [])
            if status_message_id and status_message_id in bot_message_ids:
                bot_message_ids.remove(status_message_id)

            for msg_id in bot_message_ids:
                try:
                    await message.bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
                except Exception as e:
                    print(f"Error deleting message: {str(e)}")

            # Обновляем статусное сообщение, предлагая перейти к видео
            status_text = (
                "📷 Отправьте еще фотографии или видео, либо нажмите 'Далее'."
            )

            if status_message_id:
                try:
                    await message.bot.edit_message_text(
                        chat_id=message.chat.id,
                        message_id=status_message_id,
                        text=status_text,
                        reply_markup=get_skip_back_keyboard()
                    )
                    # Обновляем список сообщений бота
                    await state.update_data(bot_message_ids=[status_message_id])
                except Exception as e:
                    print(f"Error updating status message: {str(e)}")
                    status_message_obj = await message.answer(
                        status_text,
                        reply_markup=get_skip_back_keyboard()
                    )
                    await state.update_data(status_message_id=status_message_obj.message_id, bot_message_ids=[status_message_obj.message_id])
            else:
                status_message_obj = await message.answer(
                    status_text,
                    reply_markup=get_skip_back_keyboard()
                )
                await state.update_data(status_message_id=status_message_obj.message_id, bot_message_ids=[status_message_obj.message_id])

@router.callback_query(PostCreation.waiting_for_photos, F.data == "skip")
async def skip_photos(callback: CallbackQuery, state: FSMContext):
    """Skip adding photos and proceed to creating post."""
    # Get current data
    data = await state.get_data()
    temp_photos = data.get("temp_photos", [])
    videos = data.get("videos", [])

    # Если есть временные фотографии, сохраняем их в основное хранилище
    if temp_photos:
        photos = [photo["file_id"] for photo in temp_photos]
        await state.update_data(photos=photos)
    else:
        # If no photos were added, initialize empty list
        await state.update_data(photos=[])

    # Получаем количество загруженных фотографий и видео
    photos = data.get("photos", [])

    # Формируем сообщение с информацией о загруженных медиа
    media_info = ""
    if photos:
        media_info += f"📷 Фото: {len(photos)}\n"
    if videos:
        media_info += f"📹 Видео: {len(videos)}\n"

    # Если уже есть видео, переходим сразу к созданию поста
    if videos:
        # Формируем сообщение с информацией о загруженных медиа
        await callback.message.edit_text(
            f"⏳ Создаю пост...\n\n{media_info}"
        )

        # Получаем текст поста
        text = data.get("text", "")

        # Создаем пост
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
    else:
        # Если видео нет, переходим к добавлению видео
        # Отправляем сообщение о переходе к добавлению видео с информацией о загруженных фото
        await callback.message.edit_text(
            f"{media_info}\n"
            "📹 Отправьте видео для поста (от 1 до 5 шт).\n"
            "Отправляйте по одному видео. Порядок отправки будет сохранен.",
            reply_markup=get_skip_back_keyboard()
        )

        # Переходим к добавлению видео
        await state.set_state(PostCreation.waiting_for_videos)

    await callback.answer()

@router.callback_query(PostCreation.waiting_for_videos, F.data == "skip")
async def skip_videos(callback: CallbackQuery, state: FSMContext):
    """Skip adding videos and proceed to create post."""
    # Get all data
    data = await state.get_data()
    text = data.get("text", "")
    photos = data.get("photos", [])
    videos = data.get("videos", [])

    # Выводим отладочную информацию
    print(f"DEBUG: skip_videos - photos: {photos}")
    print(f"DEBUG: skip_videos - videos: {videos}")

    # Формируем сообщение с информацией о загруженных медиа
    media_info = ""
    if photos:
        media_info += f"📷 Фото: {len(photos)}\n"
    if videos:
        media_info += f"📹 Видео: {len(videos)}\n"

    # Send data to API
    await callback.message.edit_text(f"⏳ Создаю пост...\n\n{media_info}")

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

@router.message(PostCreation.waiting_for_photos, F.video)
async def process_video_in_photos(message: Message, state: FSMContext):
    """Process a video during the photo selection stage."""
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

    # Удаляем предыдущие сообщения бота, кроме статусного
    bot_message_ids = data.get("bot_message_ids", [])
    if status_message_id and status_message_id in bot_message_ids:
        bot_message_ids.remove(status_message_id)

    for msg_id in bot_message_ids:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
        except Exception as e:
            print(f"Error deleting message: {str(e)}")

    # Обновляем статусное сообщение только при первом видео
    if len(videos) == 1:
        status_text = (
            "📹 Отправьте еще фотографии или видео, либо нажмите 'Далее'."
        )

        if status_message_id:
            try:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=status_message_id,
                    text=status_text,
                    reply_markup=get_skip_back_keyboard()
                )
                # Обновляем список сообщений бота
                await state.update_data(bot_message_ids=[status_message_id])
            except Exception as e:
                # If message can't be edited (too old or deleted), send a new one
                print(f"Error editing message: {str(e)}")
                status_message_obj = await message.answer(
                    status_text,
                    reply_markup=get_skip_back_keyboard()
                )
                await state.update_data(status_message_id=status_message_obj.message_id, bot_message_ids=[status_message_obj.message_id])
        else:
            status_message_obj = await message.answer(
                status_text,
                reply_markup=get_skip_back_keyboard()
            )
            await state.update_data(status_message_id=status_message_obj.message_id, bot_message_ids=[status_message_obj.message_id])

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

    # Обновляем статусное сообщение только при первом видео
    if len(videos) == 1:
        # Update or send status message
        status_text = (
            "📹 Отправьте еще видео или нажмите 'Далее'."
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

@router.callback_query(PostCreation.confirmation, F.data == "confirm_create")
async def confirm_create_post(callback: CallbackQuery, state: FSMContext):
    """Create the post after confirmation."""
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

            # Reset state since we're done with post creation
            await state.clear()
            return
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

@router.callback_query(PostCreation.confirmation, F.data == "back_to_videos")
async def back_to_videos_from_confirmation(callback: CallbackQuery, state: FSMContext):
    """Go back to adding videos from confirmation."""
    await callback.message.edit_text(
        "📹 Вернемся к добавлению видео для поста (до 50 МБ).\n\n"
        "Отправляйте по одному видео за раз.",
        reply_markup=get_skip_back_keyboard()
    )

    # Move back to videos state
    await state.set_state(PostCreation.waiting_for_videos)

    await callback.answer()

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

@router.callback_query(PostCreation.confirmation, F.data == "cancel_create")
async def cancel_create_post(callback: CallbackQuery, state: FSMContext):
    """Cancel post creation."""
    await callback.message.edit_text(
        "❌ Создание поста отменено.\n\n"
        "Выберите действие:",
        reply_markup=get_main_keyboard()
    )

    # Reset state
    await state.clear()

    await callback.answer()
