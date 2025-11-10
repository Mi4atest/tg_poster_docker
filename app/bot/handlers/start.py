from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
import random

from app.bot.keyboards.main_keyboard import get_main_keyboard
from app.bot.handlers.post_creation import PostCreation
from app.bot.utils.spoiler_phrases import SPOILER_PHRASES

router = Router()

@router.message(Command("start"))
async def cmd_start(message: Message):
    """Handle the /start command."""
    # Выбираем случайную фразу из списка
    spoiler_phrase = random.choice(SPOILER_PHRASES)

    # Удаляем клавиатуру справа от ввода текста и показываем основное меню
    await message.answer(
        "Приветы! Нажимай \"Создать пост\"\n\n"
        f"<tg-spoiler>{spoiler_phrase}</tg-spoiler>",
        parse_mode="HTML",
        reply_markup=get_main_keyboard()
    )

@router.callback_query(F.data == "create_post")
async def create_post_callback(callback: CallbackQuery, state: FSMContext):
    """Handle the 'Create Post' button."""
    # Создаем клавиатуру с кнопкой "Назад"
    buttons = [[InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(
        "📝 Отправьте текст для нового поста.\n\n"
        "После этого вы сможете добавить фотографии и видео.",
        reply_markup=keyboard
    )
    # Set state to waiting for post text
    await state.set_state(PostCreation.waiting_for_text)
    await callback.answer()

@router.callback_query(F.data == "pending_posts")
async def pending_posts_callback(callback: CallbackQuery):
    """Handle the 'Pending Posts' button."""
    await callback.message.edit_text(
        "⏳ Загружаю список отложенных постов...\n\n"
        "Пожалуйста, подождите."
    )
    # Fetch posts from API and display them
    # Вызываем функцию из модуля post_management
    from app.bot.handlers.post_management import show_pending_posts
    await show_pending_posts(callback.message)
    await callback.answer()

@router.callback_query(F.data == "archive_posts")
async def archive_posts_callback(callback: CallbackQuery):
    """Handle the 'Archive' button."""
    await callback.message.edit_text(
        "📁 Загружаю архив постов...\n\n"
        "Пожалуйста, подождите."
    )
    # Fetch archived posts from API and display them
    # Вызываем функцию из модуля post_management
    from app.bot.handlers.post_management import show_archived_posts
    await show_archived_posts(callback.message)
    await callback.answer()
