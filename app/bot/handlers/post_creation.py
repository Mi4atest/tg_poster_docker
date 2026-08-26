from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import aiohttp
import logging
import random
import time
from datetime import datetime
from typing import List, Optional

from app.bot.keyboards.main_keyboard import (
    get_skip_back_keyboard,
    get_text_only_confirm_keyboard,
    get_create_post_entry_keyboard,
)
from app.bot.utils.main_menu import build_main_keyboard
from app.bot.keyboards.post_avito_keyboard import format_post_creation_text_prompt
from app.bot.keyboards.product_keyboard import get_category_selection_keyboard, get_collection_selection_keyboard
from app.config.settings import API_HOST, API_PORT
from app.bot.utils.spoiler_phrases import SPOILER_PHRASES
from app.bot.utils.platform_status import get_platform_status_hint_text
from app.services.settings_service import get_settings_service
from app.integrations.avito.condition_maps import clamp_avito_level
from app.bot.keyboards.post_avito_keyboard import next_screen_level, next_body_level
from app.bot.utils.button_styles import ikb
from app.bot.utils.post_media import collect_album_media, format_media_summary
from app.bot.utils.post_success_ui import (
    build_post_ready_keyboard,
    build_post_ready_text,
    post_media_counts,
)

router = Router()
logger = logging.getLogger(__name__)

# Define states for post creation
class PostCreation(StatesGroup):
    waiting_for_text = State()
    waiting_for_photos = State()
    selecting_category = State()
    selecting_collection = State()

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

async def create_post_api(text, photos, videos, avito_draft=None):
    """Send post data to API."""
    t0 = time.perf_counter()
    try:
        async with aiohttp.ClientSession() as session:
            url = f"http://{API_HOST}:{API_PORT}/api/posts/"
            data = {
                "text": text,
                "photos": photos if photos else [],
                "videos": videos if videos else [],
            }
            if isinstance(avito_draft, dict):
                data["avito_draft"] = avito_draft

            try:
                async with session.post(url, json=data) as response:
                    elapsed_ms = (time.perf_counter() - t0) * 1000

                    if response.status == 201:
                        result = await response.json()
                        logger.info(
                            "create_post_api ok post_id=%s total_ms=%.0f",
                            result.get("id"),
                            (time.perf_counter() - t0) * 1000,
                        )
                        return result
                    else:
                        error_text = await response.text()
                        logger.warning(
                            "create_post_api failed status=%s elapsed_ms=%.0f body=%s",
                            response.status,
                            elapsed_ms,
                            error_text[:500],
                        )
                        return None
            except Exception as e:
                logger.warning(
                    "create_post_api request error after_ms=%.0f: %s",
                    (time.perf_counter() - t0) * 1000,
                    e,
                )
                return None
    except Exception as e:
        logger.warning("create_post_api error: %s", e)
        return None


async def _finalize_post_creation(callback: CallbackQuery, state: FSMContext, avito_draft: dict):
    """Создание поста в API и экран успеха (после шага черновика Авито)."""
    data = await state.get_data()
    text = data.get("text", "")
    photos = list(data.get("photos", []))
    videos = list(data.get("videos", []))

    media_info = ""
    if photos:
        media_info += f"📷 Фото: {len(photos)}\n"
    if videos:
        media_info += f"📹 Видео: {len(videos)}\n"

    await callback.message.edit_text(f"⏳ Создаю пост...\n\n{media_info}")

    try:
        post = await create_post_api(text, photos, videos, avito_draft=avito_draft)

        if post:
            photo_count = len(photos)
            video_count = len(videos)
            post_name = post.get("name", "")
            post_id = post.get("id")

            success_text = build_post_ready_text(post_name, photo_count, video_count)
            keyboard = build_post_ready_keyboard(post_id)

            await callback.message.edit_text(success_text, reply_markup=keyboard, parse_mode="HTML")
            await state.update_data(post_id=post_id)
        else:
            await callback.message.edit_text(
                "❌ Ошибка при создании поста. Пожалуйста, попробуйте еще раз.",
                reply_markup=await build_main_keyboard(callback.message.bot),
            )
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Произошла ошибка: {str(e)}",
            reply_markup=await build_main_keyboard(callback.message.bot),
        )

    await state.clear()


def _avito_draft_from_state(data: dict) -> dict:
    return {
        "screen_level": clamp_avito_level(data.get("avito_screen_level", 1)),
        "body_level": clamp_avito_level(data.get("avito_body_level", 1)),
    }


def _has_media(photos: list, videos: list) -> bool:
    return bool(photos or videos)


def _media_step_keyboard(photos: list, videos: list) -> InlineKeyboardMarkup:
    return get_skip_back_keyboard()


MEDIA_UPLOAD_HINT = (
    "📷 Отправь фото обычным альбомом — порядок сохранится.\n"
    "При желании можно как раньше — «Отправить без группировки»."
)


def _build_initial_media_prompt(intro_phrase: str) -> str:
    return f"{intro_phrase}\n\n{MEDIA_UPLOAD_HINT}"


def _build_media_status_text(photos: list, videos: list, *, photo_limit_hit: bool = False) -> str:
    summary = _format_media_summary(photos, videos)
    if photo_limit_hit and not _has_media(photos, videos):
        return (
            "❌ Уже загружено максимум 10 фото.\n"
            f"✅ {summary}\n\n"
            "Нажми «Далее», чтобы продолжить, или «Назад» к редактированию текста."
        )
    if photo_limit_hit:
        return (
            "⚠️ Часть фото не добавлена — лимит 10.\n"
            f"✅ {summary}\n\n"
            "Отправь ещё фото/видео либо нажми «Далее»."
        )
    return f"✅ {summary}\n\nОтправь ещё фото/видео либо нажми «Далее»."


async def _restore_media_step(callback: CallbackQuery, state: FSMContext) -> None:
    """Return to the media upload screen after cancelling text-only confirmation."""
    data = await state.get_data()
    photos = list(data.get("photos", []))
    videos = list(data.get("videos", []))
    if _has_media(photos, videos):
        text = _build_media_status_text(photos, videos)
    else:
        intro = data.get("media_intro_phrase") or "Отправь фото и видео для поста."
        text = _build_initial_media_prompt(intro)
    await callback.message.edit_text(
        text,
        reply_markup=_media_step_keyboard(photos, videos),
    )


@router.callback_query(PostCreation.waiting_for_photos, F.data == "skip")
async def skip_photos(callback: CallbackQuery, state: FSMContext):
    """Finish media step: create post or ask to confirm text-only."""
    data = await state.get_data()
    photos = list(data.get("photos", []))
    videos = list(data.get("videos", []))

    if _has_media(photos, videos):
        await callback.answer()
        await _finalize_post_creation(callback, state, _avito_draft_from_state(data))
        return

    await callback.message.edit_text(
        "⚠️ Без фото?\n\n"
        "Медиа не добавлены. Обычно пост публикуют с фото или видео.\n"
        "Создать пост только с текстом?",
        reply_markup=get_text_only_confirm_keyboard(),
    )
    await callback.answer()


@router.callback_query(PostCreation.waiting_for_photos, F.data == "pc_text_only_yes")
async def confirm_text_only_post(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await callback.answer()
    await _finalize_post_creation(callback, state, _avito_draft_from_state(data))


@router.callback_query(PostCreation.waiting_for_photos, F.data == "pc_text_only_no")
async def cancel_text_only_post(callback: CallbackQuery, state: FSMContext):
    await _restore_media_step(callback, state)
    await callback.answer()


async def _render_post_creation_text_entry(message, state: FSMContext) -> None:
    service = get_settings_service()
    vk_market_enabled = service.is_vk_market_enabled()
    avito_enabled = service.is_platform_enabled("avito")
    data = await state.get_data()
    sl = clamp_avito_level(data.get("avito_screen_level", 1))
    bl = clamp_avito_level(data.get("avito_body_level", 1))
    text = format_post_creation_text_prompt(
        get_platform_status_hint_text(), sl, bl, avito_enabled=avito_enabled
    )
    kb = get_create_post_entry_keyboard(
        vk_market_enabled,
        avito_enabled=avito_enabled,
        avito_screen_level=sl,
        avito_body_level=bl,
        vk_stories_button_text=service.stories_mode_button_label(),
    )
    await message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(PostCreation.waiting_for_text, F.data == "pc_text_avito_cycle_scr")
async def pc_text_avito_cycle_screen(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    sl = next_screen_level(data.get("avito_screen_level", 1))
    await state.update_data(avito_screen_level=sl)
    await _render_post_creation_text_entry(callback.message, state)
    await callback.answer()


@router.callback_query(PostCreation.waiting_for_text, F.data == "pc_text_avito_cycle_bod")
async def pc_text_avito_cycle_body(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    bl = next_body_level(data.get("avito_body_level", 1))
    await state.update_data(avito_body_level=bl)
    await _render_post_creation_text_entry(callback.message, state)
    await callback.answer()


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

    # Список фраз для сообщения о загрузке фото
    photo_phrases = [
        "Печатал как будто в темноте 🤯",
        "Снова 'как новый', 'в идеале', 'без торга'? Да ты легенда 🔥",
        "Это описание или шаблон из 2015-го? 📄",
        "А можно чуть больше оригинальности? 🧠",
        "Ты вообще читаешь, что пишешь? 🤨",
        "Текст такой же, как у всех — говно без приправы 🍽️💩",
        "Кажется, ты забыл выключить автозамену 🤭",
        "Это не текст — это клиническая скука 😴",
        "Снова 'отличное состояние'? Ага, конечно 🙃",
        "Неужели нельзя было написать чуть лучше? 🖊️",
        "Ты там ещё не научился форматировать? 📝",
        "Это описание или спам-письмо? 🚫",
        "Где логика? Где структура? 🧩",
        "Такой текст мог бы написать мой кот 🐱",
        "Ты просто скопировал и всё? Ну браво 👏",
        "А где эмодзи? Или мы уже взрослые? 😏",
        "Это называется 'текст' или 'куча букв'? 📚",
        "Опять ошибки? Да ты талантливый 😂",
        "Прям как в школе — списал и не понял 🤷‍♂️",
        "Ты вообще проверил перед отправкой? 🧐",
        "Снова повторяешь одно и то же? 🔄",
        "Это описание или мемуары? 📖",
        "А где ключевые слова? SEO, помнишь? 🔑",
        "Это не продающий текст — это отписка 📋",
        "Смотри, опять буквы не того регистра 🧐",
        "Это называется 'работа' или 'копирование'? 🤔",
        "Ты реально думаешь, что так продают? 🤭",
        "А можно чуть меньше 'фиг знает чего'? 💩",
        "Это текст или поток сознания? 🧠",
        "Ты не описываешь — ты обобществляешь 🤷‍♂️",
        "Снова 'цена договорная'? Да ты мастер интриги 🎭",
        "Это описание или загадка с подвохом? ❓",
        "А где детали? Разве так показывают? 🕵️‍♂️",
        "Ты вообще знаешь, что продаёшь? 🤯",
        "Это текст или список покупок? 🛒",
        "Снова 'без дефектов'? Я уже глаз замылился 😵‍💫",
        "Ты не описываешь товар — ты его прячешь 🙈",
        "Это не контент — это тест на терпение 🧘‍♂️",
        "А можно чуть больше конкретики? 🎯",
        "Это не описание — это пустышка 🧸",
        "Ты не создаешь текст — ты его плодишь 🤢",
        "Это не стиль — это беспорядок 🧹",
        "А где эмоции? Разве так цепляют? 😐",
        "Это не реклама — это давление 🚨",
        "Ты не маркетолог — ты копирщик 📋",
        "Это не текст — это набор слов 🧩",
        "Ты не админ, ты мусорный бот 🗑️",
        "Это не описание — это каша 🍲",
        "Ты реально думаешь, что это красиво? 🤭",
        "Это не продажа — это рассылка спама 🚫",
        "А можно чуть больше внимания к деталям? 🔍",
        "Ты не создаешь контент — ты его копируешь 📋",
        "Это не пост — это стенограмма мыслей 🧠",
        "А можно чуть меньше белиберды? 🤷‍♂️",
        "Это не продающий текст — это сон в ухо 🥱",
        "А можно чуть больше души? ❤️",
        "Это не реклама — это экзорцизм 🙏",
        "Пост после тебя — как головная боль 💆‍♂️",
        "Ты там ещё не свихнулся от всего этого? 🤪",
        "Как будто кот на клаве 🐱",
        "Ты реально каждый раз так стараешься? 🙄",
        "С каждым постом мне становится страшнее 🥶",
        "Ты не сотрудник, ты контент-террорист 🚨",
        "Так и хочется сказать: 'Нет, спасибо' ❌",
        "Снова дублируешь? Да ты профи 🔁",
        "Это банально, как вторник 🥱",
        "Может, стоит обновить контент? 🧼",
        "Ты не выкладываешь — ты закапываешь 🪦",
        "Каждый пост — как клик по ссылке с вирусом 🦠",
        "А можно чуть больше мозга в текст? 🧠",
        "Это не реклама — это трэш 🚮",
        "Ты реально думаешь, что это цепляет? 🤨",
        "А где оригинальность? 🤷‍♂️",
        "Это не уникальное предложение — это шаблон 📄",
        "Ты мог бы и получше написать 🖊️",
        "Ты вообще интересуешься продуктом? 🧐",
        "А где эмоции? Разве так продают? 😐",
        "Это описание или список покупок? 🛒",
        "Ты не продаёшь — ты отпугиваешь 🚫",
        "Это не продающий текст — это сон в ухо 🥱",
        "Это не описание — это каша 🍲",
        "Ты реально думаешь, что это красиво? 🤭",
        "Это не стиль — это беспорядок 🧹",
        "Ты не дизайнер, ты хаос в формате текста 🧸",
        "А можно чуть больше смысла? 🧠",
        "Это не контент — это тест на терпение 🧘‍♂️",
        "Ты не создаешь, ты перекрашиваешь 🎨",
        "Это не креатив — это повтор 🔄",
        "А можно чуть больше усилий? 💪",
        "Это не работа — это автоматизм 🤖",
        "Ты не специалист — ты копирка 📋",
        "Это не пост — это копия 📄",
        "Ты не продаёшь — ты отталкиваешь 🤷‍♂️",
        "Это не текст — это бессмысленный набор букв 📝",
        "А можно чуть больше профессионализма? 🧑‍💼",
        "Это не контент — это мука для глаз 🥴",
        "Ты не работник — ты автопилот 🚀",
        "Это не успех — это рутинный ад 🧱🔥",
        "Это не описание — это 'на коленке за 5 минут' 🧑‍💻",
        "Ты не описываешь товар — ты его хоронишь 🪦"
    ]

    # Выбираем случайную фразу из списка
    photo_phrase = random.choice(photo_phrases)

    # Ask for photos
    status_message = await message.reply(
        _build_initial_media_prompt(photo_phrase),
        reply_markup=_media_step_keyboard([], []),
    )

    # Сохраняем ID сообщения бота
    await state.update_data(
        bot_message_ids=[status_message.message_id],
        status_message_id=status_message.message_id,
        media_intro_phrase=photo_phrase,
    )

    await state.update_data(photos=[], videos=[])

    # Move to next state
    await state.set_state(PostCreation.waiting_for_photos)

def _format_media_summary(photos: list, videos: list) -> str:
    return format_media_summary(photos, videos)


@router.message(PostCreation.waiting_for_text, F.photo | F.video | F.document | F.media_group_id)
async def process_text_with_media(
    message: Message,
    state: FSMContext,
    album: Optional[List[Message]] = None,
):
    """Process a message (or whole album) with caption + photo(s)/video(s).

    Telegram albums may mix photos and videos. AlbumMiddleware buffers them
    and passes the whole group sorted by message_id. The caption lives on
    the first item of the album.
    """
    messages = album if album else [message]
    first = messages[0]

    if not first.caption or not first.caption.strip():
        await message.reply("❌ Текст не может быть пустым. Пожалуйста, отправьте текст для поста.")
        return

    await state.update_data(text=first.caption)

    photos: list[str] = []
    videos: list[str] = []
    _, _, photo_limit_hit = collect_album_media(messages, photos, videos)

    await state.update_data(photos=photos, videos=videos)

    summary = _format_media_summary(photos, videos)
    warning = "⚠️ Часть фото не добавлена — лимит 10.\n" if photo_limit_hit else ""

    status_message_obj = await message.reply(
        "✅ Текст поста сохранён!\n\n"
        f"{warning}✅ {summary}\n\n"
        "Отправь ещё фото/видео либо нажми «Далее».",
        reply_markup=_media_step_keyboard(photos, videos),
    )
    await state.update_data(
        status_message_id=status_message_obj.message_id,
        bot_message_ids=[status_message_obj.message_id],
    )

    await state.set_state(PostCreation.waiting_for_photos)

@router.callback_query(PostCreation.waiting_for_photos, F.data == "back")
async def back_to_text(callback: CallbackQuery, state: FSMContext):
    """Go back to entering text."""
    data = await state.get_data()
    await state.update_data(photos=[], videos=[])
    service = get_settings_service()
    vk_market_enabled = service.is_vk_market_enabled()
    avito_enabled = service.is_platform_enabled("avito")
    sl = clamp_avito_level(data.get("avito_screen_level", 1))
    bl = clamp_avito_level(data.get("avito_body_level", 1))
    await callback.message.edit_text(
        format_post_creation_text_prompt(
            get_platform_status_hint_text(), sl, bl, avito_enabled=avito_enabled
        ),
        reply_markup=get_create_post_entry_keyboard(
            vk_market_enabled,
            avito_enabled=avito_enabled,
            avito_screen_level=sl,
            avito_body_level=bl,
            vk_stories_button_text=service.stories_mode_button_label(),
        ),
        parse_mode="HTML",
    )

    # Move back to previous state
    await state.set_state(PostCreation.waiting_for_text)

    await callback.answer()

async def _update_photos_status(message: Message, state: FSMContext, status_text: str) -> None:
    """Edit the existing status message in-place, or send a new one if missing."""
    data = await state.get_data()
    status_message_id = data.get("status_message_id")
    bot_message_ids = data.get("bot_message_ids", [])

    # Drop any leftover bot messages except the active status one.
    for msg_id in bot_message_ids:
        if msg_id == status_message_id:
            continue
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
        except Exception as e:
            print(f"Error deleting message: {str(e)}")

    if status_message_id:
        try:
            photos = list(data.get("photos", []))
            videos = list(data.get("videos", []))
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=status_message_id,
                text=status_text,
                reply_markup=_media_step_keyboard(photos, videos),
            )
            await state.update_data(bot_message_ids=[status_message_id])
            return
        except Exception as e:
            print(f"Error editing status message: {str(e)}")

    photos = list(data.get("photos", []))
    videos = list(data.get("videos", []))
    status_message_obj = await message.answer(
        status_text,
        reply_markup=_media_step_keyboard(photos, videos),
    )
    await state.update_data(
        status_message_id=status_message_obj.message_id,
        bot_message_ids=[status_message_obj.message_id],
    )


@router.message(PostCreation.waiting_for_photos, F.photo | F.video | F.document | F.media_group_id)
async def process_media_in_photos(
    message: Message,
    state: FSMContext,
    album: Optional[List[Message]] = None,
):
    """Add photos and/or videos from a single message or a whole album.

    The album may mix photos and videos — order is preserved within each
    type (publishers list photos first, then videos).
    """
    data = await state.get_data()
    photos: list[str] = list(data.get("photos", []))
    videos: list[str] = list(data.get("videos", []))

    messages = album if album else [message]
    photos_added, videos_added, photo_limit_hit = collect_album_media(
        messages, photos, videos
    )

    if photos_added == 0 and videos_added == 0 and not photo_limit_hit:
        return

    await state.update_data(photos=photos, videos=videos)

    status_text = _build_media_status_text(photos, videos, photo_limit_hit=photo_limit_hit)
    await _update_photos_status(message, state, status_text)


@router.callback_query(F.data == "back_to_main")
async def back_to_main_menu(callback: CallbackQuery, state: FSMContext):
    """Return to the main menu."""
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


@router.callback_query(F.data.startswith("setup_product_"))
async def setup_product(callback: CallbackQuery, state: FSMContext):
    """Начать настройку товара (выбор категории и подборки)."""
    try:
        post_id = callback.data.replace("setup_product_", "")
        await state.update_data(post_id=post_id)
        
        text = "📦 Настройка товара\n\nВыберите категорию товара:"
        from app.bot.keyboards.product_keyboard import get_category_selection_keyboard
        await callback.message.edit_text(
            text,
            reply_markup=get_category_selection_keyboard()
        )
        await state.set_state(PostCreation.selecting_category)
        await callback.answer()
    except Exception as e:
        await callback.answer(f"Ошибка: {str(e)}", show_alert=True)


@router.callback_query(PostCreation.selecting_category, F.data.startswith("category_"))
async def select_category(callback: CallbackQuery, state: FSMContext):
    """Обработать выбор категории."""
    category = callback.data.replace("category_", "")
    
    if category == "skip":
        # Пропускаем выбор категории
        await state.update_data(category=None)
        from app.bot.keyboards.product_keyboard import get_collection_selection_keyboard
        text = "📦 Настройка товара\n\nВыберите подборку товара (или пропустите):"
        await callback.message.edit_text(
            text,
            reply_markup=get_collection_selection_keyboard()
        )
        await state.set_state(PostCreation.selecting_collection)
        await callback.answer()
        return
    
    await state.update_data(category=category)
    
    from app.bot.keyboards.product_keyboard import get_collection_selection_keyboard
    text = f"✅ Категория выбрана: {category}\n\nВыберите подборку товара (или пропустите):"
    await callback.message.edit_text(
        text,
        reply_markup=get_collection_selection_keyboard(category)
    )
    await state.set_state(PostCreation.selecting_collection)
    await callback.answer()


@router.callback_query(PostCreation.selecting_collection, F.data.startswith("collection_"))
async def select_collection(callback: CallbackQuery, state: FSMContext):
    """Обработать выбор подборки и завершить настройку."""
    collection = callback.data.replace("collection_", "")
    data = await state.get_data()
    post_id = data.get("post_id")
    category = data.get("category")
    
    if collection == "skip":
        collection = None
    
    # Сохраняем выбранные категорию и подборку (можно сохранить в отдельную таблицу или использовать при публикации)
    # Пока просто показываем сообщение об успешной настройке
    text = "✅ Настройка товара завершена!\n\n"
    if category:
        text += f"📂 Категория: {category}\n"
    if collection:
        text += f"📁 Подборка: {collection}\n"
    text += "\nЭти настройки будут использованы при публикации товара в ВК."

    from app.bot.handlers.post_management import get_post_api

    post = await get_post_api(post_id) if post_id else None
    if post:
        photo_count, video_count = post_media_counts(post)
        text = build_post_ready_text(post.get("name", ""), photo_count, video_count)
        keyboard = build_post_ready_keyboard(post_id)
    else:
        keyboard = build_post_ready_keyboard(post_id or "")

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await state.clear()
    await callback.answer()
