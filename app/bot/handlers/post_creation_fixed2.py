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
