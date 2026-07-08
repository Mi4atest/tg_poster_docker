from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
import random

from app.bot.keyboards.main_keyboard import get_create_post_entry_keyboard
from app.bot.keyboards.post_avito_keyboard import format_post_creation_text_prompt
from app.bot.handlers.post_creation import PostCreation
from app.bot.utils.spoiler_phrases import SPOILER_PHRASES
from app.bot.utils.platform_status import get_platform_status_hint_text
from app.bot.utils.main_menu import build_main_keyboard
from app.services.settings_service import get_settings_service

router = Router()

@router.message(Command("start"))
async def cmd_start(message: Message):
    """Handle the /start command."""
    spoiler_phrase = random.choice(SPOILER_PHRASES)

    await message.answer(
        "Приветы! Нажимай \"Создать пост\"\n\n"
        f"<tg-spoiler>{spoiler_phrase}</tg-spoiler>",
        parse_mode="HTML",
        reply_markup=await build_main_keyboard(message.bot),
    )

@router.callback_query(F.data == "create_post")
async def create_post_callback(callback: CallbackQuery, state: FSMContext):
    """Handle the 'Create Post' button."""
    service = get_settings_service()
    vk_market_enabled = service.is_vk_market_enabled()
    avito_enabled = service.is_platform_enabled("avito")
    await state.update_data(avito_screen_level=1, avito_body_level=1, photos=[], videos=[])
    keyboard = get_create_post_entry_keyboard(
        vk_market_enabled,
        avito_enabled=avito_enabled,
        avito_screen_level=1,
        avito_body_level=1,
    )
    status_hint = get_platform_status_hint_text()

    await callback.message.edit_text(
        format_post_creation_text_prompt(status_hint, 1, 1, avito_enabled=avito_enabled),
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await state.set_state(PostCreation.waiting_for_text)
    await callback.answer()


@router.callback_query(PostCreation.waiting_for_text, F.data == "create_post_toggle_vk_market")
async def toggle_vk_market_from_create_post(callback: CallbackQuery, state: FSMContext):
    """Toggle VK market publication flag directly from create post screen."""
    service = get_settings_service()
    next_state = not service.is_vk_market_enabled()
    service.set_vk_market_enabled(next_state)
    avito_enabled = service.is_platform_enabled("avito")
    data = await state.get_data()
    from app.integrations.avito.condition_maps import clamp_avito_level

    sl = clamp_avito_level(data.get("avito_screen_level", 1))
    bl = clamp_avito_level(data.get("avito_body_level", 1))
    keyboard = get_create_post_entry_keyboard(
        next_state,
        avito_enabled=avito_enabled,
        avito_screen_level=sl,
        avito_body_level=bl,
    )
    status_hint = get_platform_status_hint_text()
    await callback.message.edit_text(
        format_post_creation_text_prompt(status_hint, sl, bl, avito_enabled=avito_enabled),
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await state.set_state(PostCreation.waiting_for_text)
    await callback.answer(
        "Товары ВК: включены" if next_state else "Товары ВК: выключены"
    )

@router.callback_query(F.data == "pending_posts")
async def pending_posts_callback(callback: CallbackQuery, state: FSMContext):
    """Handle the 'Drafts' button."""
    await state.clear()
    if hasattr(callback.bot, "user_data"):
        ud = callback.bot.user_data.get(callback.from_user.id)
        if isinstance(ud, dict):
            ud["in_archive"] = False
    await callback.message.edit_text(
        "⏳ Загружаю черновики...\n\n"
        "Пожалуйста, подождите."
    )
    from app.bot.handlers.post_management import show_pending_posts
    await show_pending_posts(callback.message)
    await callback.answer()

@router.callback_query(F.data == "archive_posts")
async def archive_posts_callback(callback: CallbackQuery):
    """Handle the 'Archive' button."""
    # Сразу отвечаем на callback, чтобы Telegram не истёк таймаут query.
    await callback.answer()
    try:
        await callback.message.edit_text(
            "📁 Загружаю архив постов...\n\n"
            "Пожалуйста, подождите."
        )
        from app.bot.handlers.post_management import show_archived_posts
        await show_archived_posts(callback.message, user_id=callback.from_user.id)
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Ошибка при загрузке архива: {str(e)}"
        )

@router.callback_query(F.data == "back_to_main")
async def back_to_main_callback(callback: CallbackQuery, state: FSMContext):
    """Handle the 'Back to Main Menu' button."""
    await state.clear()
    if hasattr(callback.bot, "user_data"):
        ud = callback.bot.user_data.get(callback.from_user.id)
        if isinstance(ud, dict):
            ud["in_archive"] = False
            ud.pop("archive_state", None)
    spoiler_phrase = random.choice(SPOILER_PHRASES)
    await callback.message.edit_text(
        "Приветы! Нажимай \"Создать пост\"\n\n"
        f"<tg-spoiler>{spoiler_phrase}</tg-spoiler>",
        parse_mode="HTML",
        reply_markup=await build_main_keyboard(callback.bot),
    )
    await callback.answer()
