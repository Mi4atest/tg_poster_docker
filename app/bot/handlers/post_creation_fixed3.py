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
