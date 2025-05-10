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
