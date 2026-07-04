import logging
from typing import Optional

import html

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext

from app.bot.keyboards.scheduler_keyboard import (
    get_queue_menu_keyboard,
    get_platform_queue_keyboard,
    get_post_queue_actions_keyboard
)
from app.bot.keyboards.main_keyboard import get_create_post_entry_keyboard
from app.bot.keyboards.post_avito_keyboard import format_post_creation_text_prompt
from app.bot.utils.platform_status import get_platform_status_hint_text
from app.services.settings_service import get_settings_service
from app.bot.handlers.queue_avito_ui import (
    build_avito_platform_text,
    build_queue_menu_text,
)
from app.bot.keyboards.scheduler_keyboard import get_avito_platform_keyboard
from app.integrations.avito.archive_queue import (
    list_pending as list_archive_pending,
    reconcile_pending_with_avito,
)
from app.integrations.avito.avito_feed_dispatcher import get_queue_summary
from app.scheduler.queue_ui import queue_item_display_name

logger = logging.getLogger(__name__)

router = Router()

_QUEUE_PUBLISH_PREFIX = "queue_publish_now_"
_QUEUE_PUBLISH_PLATFORMS = ("instagram", "telegram", "avito", "max", "vk")


def _parse_queue_publish_now_callback(data: str) -> tuple[Optional[str], str]:
    """Возвращает (platform, post_id). platform=None — старый формат callback только с post_id."""
    if not data.startswith(_QUEUE_PUBLISH_PREFIX):
        return None, ""
    rest = data[len(_QUEUE_PUBLISH_PREFIX) :]
    for plat in sorted(_QUEUE_PUBLISH_PLATFORMS, key=len, reverse=True):
        marker = plat + "_"
        if rest.startswith(marker):
            return plat, rest[len(marker) :]
    return None, rest


async def safe_edit_message(message, text, reply_markup=None, parse_mode=None):
    """Безопасно редактирует сообщение или отправляет новое."""
    edit_kw = {"reply_markup": reply_markup}
    reply_kw = {"reply_markup": reply_markup}
    if parse_mode is not None:
        edit_kw["parse_mode"] = parse_mode
        reply_kw["parse_mode"] = parse_mode
    try:
        await message.edit_text(text, **edit_kw)
        return message
    except TelegramBadRequest as e:
        if "message can't be edited" in str(e):
            return await message.reply(text, **reply_kw)
        else:
            raise e
    except Exception as e:
        logger.error(f"Error editing message: {str(e)}")
        return await message.reply(text, **reply_kw)


def get_orchestrator(bot):
    """Получить оркестратор из бота."""
    if not hasattr(bot, 'orchestrator') or bot.orchestrator is None:
        from app.scheduler.orchestrator import PublicationOrchestrator
        bot.orchestrator = PublicationOrchestrator(signature_enabled=getattr(bot, 'signature_enabled', True))
        bot.orchestrator.start()
        # Запускаем workers, если event loop уже работает
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(bot.orchestrator.start_workers())
        except Exception as e:
            logger.warning(f"Could not start workers immediately: {str(e)}")
    return bot.orchestrator


@router.callback_query(F.data == "queue_menu")
async def show_queue_menu(callback: CallbackQuery):
    """Показать меню очереди."""
    try:
        orchestrator = get_orchestrator(callback.bot)
        await reconcile_pending_with_avito()
        stats = orchestrator.get_queue_stats()
        text = build_queue_menu_text(stats, orchestrator.queue_manager)
        keyboard = get_queue_menu_keyboard(stats)
        await safe_edit_message(
            callback.message, text, reply_markup=keyboard, parse_mode="HTML"
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error showing queue menu: {str(e)}")
        await callback.answer("❌ Ошибка при загрузке очереди", show_alert=True)


@router.callback_query(F.data.startswith("queue_platform_"))
async def show_platform_queue(callback: CallbackQuery):
    """Показать очередь для конкретной платформы."""
    import time as _time

    _t0 = _time.time()
    try:
        platform = callback.data.replace("queue_platform_", "")
        orchestrator = get_orchestrator(callback.bot)

        if platform == "avito":
            await reconcile_pending_with_avito()
            summary = get_queue_summary(orchestrator.queue_manager)
            publish_items = orchestrator.get_queue_for_platform("avito")
            archive_items = list_archive_pending()
            text = build_avito_platform_text(summary, publish_items, archive_items)
            keyboard = get_avito_platform_keyboard(
                can_upload=summary.can_upload_now,
                has_work=bool(publish_items or archive_items),
            )
            await safe_edit_message(callback.message, text, reply_markup=keyboard, parse_mode="HTML")
            await callback.answer()
            return

        queue_items = orchestrator.get_queue_for_platform(platform)
        
        platform_names = {
            "vk": "ВКонтакте",
            "telegram": "Telegram",
            "instagram": "Instagram",
            "max": "Max",
            "avito": "Авито",
        }
        platform_name = platform_names.get(platform, platform)
        
        text = f"📋 Очередь публикаций: {platform_name}\n\n"
        
        if not queue_items:
            text += "Очередь пуста."
        else:
            text += f"Всего постов: {len(queue_items)}\n\n"
            for i, item in enumerate(queue_items[:10], 1):
                post_name = queue_item_display_name(item)
                status_icon = "⏸" if item.status == "paused" else "⏳" if item.status == "pending" else "🔄"
                text += f"{i}. {status_icon} {post_name}\n"
            
            if len(queue_items) > 10:
                text += f"\n... и еще {len(queue_items) - 10} постов"
        
        keyboard = get_platform_queue_keyboard(platform, queue_items)
        await safe_edit_message(callback.message, text, reply_markup=keyboard)
        await callback.answer()
    except Exception as e:
        logger.error(f"Error showing platform queue: {str(e)}")
        await callback.answer("❌ Ошибка при загрузке очереди", show_alert=True)


@router.callback_query(F.data == "queue_avito_upload_feed")
async def queue_avito_upload_feed(callback: CallbackQuery):
    """Ручная отправка XML-фида на Авито (публикация + снятие)."""
    try:
        from app.db.database import SessionLocal
        from app.integrations.avito.avito_feed_dispatcher import execute_feed_upload

        orchestrator = get_orchestrator(callback.bot)
        db = SessionLocal()
        try:
            ok, msg = await execute_feed_upload(
                db, orchestrator.queue_manager, manual=True
            )
        finally:
            db.close()
        alert = ("✅ " if ok else "⚠️ ") + msg[:200]
        if ok:
            await callback.answer("Файл отправлен на Авито")
        else:
            await callback.answer(alert[:200], show_alert=True)
        await reconcile_pending_with_avito()
        summary = get_queue_summary(orchestrator.queue_manager)
        publish_items = orchestrator.get_queue_for_platform("avito")
        archive_items = list_archive_pending()
        text = build_avito_platform_text(summary, publish_items, archive_items)
        text += f"\n\n<b>{html.escape(alert)}</b>"
        keyboard = get_avito_platform_keyboard(
            can_upload=summary.can_upload_now,
            has_work=bool(publish_items or archive_items),
        )
        await safe_edit_message(
            callback.message, text, reply_markup=keyboard, parse_mode="HTML"
        )
    except Exception as e:
        logger.error("queue_avito_upload_feed: %s", e, exc_info=True)
        await callback.answer("❌ Ошибка отправки файла", show_alert=True)


@router.callback_query(F.data.startswith("queue_post_"))
async def show_post_queue_actions(callback: CallbackQuery):
    """Показать действия с постом в очереди."""
    try:
        queue_item_id = int(callback.data.replace("queue_post_", ""))
        orchestrator = get_orchestrator(callback.bot)

        queue_item = orchestrator.queue_manager.fetch_queue_item(queue_item_id)
        if not queue_item:
            await callback.answer("❌ Запись не найдена", show_alert=True)
            return

        platform = queue_item.platform

        platform_names = {
            "vk": "ВКонтакте",
            "telegram": "Telegram",
            "instagram": "Instagram",
            "max": "Max",
            "avito": "Авито",
        }
        platform_name = platform_names.get(platform, platform)

        status_names = {
            "pending": "⏳ Ожидает",
            "publishing": "🔄 Публикуется",
            "paused": "⏸ На паузе",
            "completed": "✅ Завершено",
            "failed": "❌ Ошибка",
        }
        status_name = status_names.get(queue_item.status, queue_item.status)

        text = f"📋 Пост в очереди\n\n"
        text += f"📝 {queue_item_display_name(queue_item)}\n"
        text += f"📱 Платформа: {platform_name}\n"
        text += f"📊 Статус: {status_name}\n"

        if queue_item.error_message:
            text += f"\n❌ Ошибка: {queue_item.error_message}\n"

        keyboard = get_post_queue_actions_keyboard(queue_item_id, queue_item.post_id, platform)
        await safe_edit_message(callback.message, text, reply_markup=keyboard)
        await callback.answer()
    except Exception as e:
        logger.error(f"Error showing post queue actions: {str(e)}")
        await callback.answer("❌ Ошибка", show_alert=True)


@router.callback_query(F.data == "queue_pause_global")
async def pause_global(callback: CallbackQuery):
    """Приостановить все публикации."""
    try:
        orchestrator = get_orchestrator(callback.bot)
        orchestrator.pause_global()
        await callback.answer("⏸ Все публикации приостановлены")
        # Обновляем меню
        await show_queue_menu(callback)
    except Exception as e:
        logger.error(f"Error pausing global: {str(e)}")
        await callback.answer("❌ Ошибка", show_alert=True)


@router.callback_query(F.data == "queue_resume_global")
async def resume_global(callback: CallbackQuery):
    """Возобновить все публикации."""
    try:
        orchestrator = get_orchestrator(callback.bot)
        orchestrator.resume_global()
        await callback.answer("▶️ Все публикации возобновлены")
        # Обновляем меню
        await show_queue_menu(callback)
    except Exception as e:
        logger.error(f"Error resuming global: {str(e)}")
        await callback.answer("❌ Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("queue_pause_platform_"))
async def pause_platform(callback: CallbackQuery):
    """Приостановить публикации для платформы."""
    try:
        platform = callback.data.replace("queue_pause_platform_", "")
        orchestrator = get_orchestrator(callback.bot)
        orchestrator.pause_platform(platform)
        
        platform_names = {
            "vk": "ВКонтакте",
            "telegram": "Telegram",
            "instagram": "Instagram",
            "max": "Max",
            "avito": "Авито",
        }
        platform_name = platform_names.get(platform, platform)
        await callback.answer(f"⏸ Публикации в {platform_name} приостановлены")
        # Обновляем очередь платформы
        await show_platform_queue(callback)
    except Exception as e:
        logger.error(f"Error pausing platform: {str(e)}")
        await callback.answer("❌ Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("queue_resume_platform_"))
async def resume_platform(callback: CallbackQuery):
    """Возобновить публикации для платформы."""
    try:
        platform = callback.data.replace("queue_resume_platform_", "")
        orchestrator = get_orchestrator(callback.bot)
        if orchestrator.global_pause:
            await callback.answer(
                "Включена пауза всех публикаций. Сначала в меню очереди нажмите «Возобновить все» — "
                "иначе воркеры платформы не начнут работу.",
                show_alert=True,
            )
            await show_platform_queue(callback)
            return
        orchestrator.resume_platform(platform)

        platform_names = {
            "vk": "ВКонтакте",
            "telegram": "Telegram",
            "instagram": "Instagram",
            "max": "Max",
            "avito": "Авито",
        }
        platform_name = platform_names.get(platform, platform)
        await callback.answer(f"▶️ Публикации в {platform_name} возобновлены")
        # Обновляем очередь платформы
        await show_platform_queue(callback)
    except Exception as e:
        logger.error(f"Error resuming platform: {str(e)}")
        await callback.answer("❌ Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("queue_pause_post_"))
async def pause_post(callback: CallbackQuery):
    """Приостановить публикацию поста."""
    try:
        queue_item_id = int(callback.data.replace("queue_pause_post_", ""))
        orchestrator = get_orchestrator(callback.bot)

        queue_item = orchestrator.queue_manager.fetch_queue_item(queue_item_id)
        if not queue_item:
            await callback.answer("❌ Запись не найдена", show_alert=True)
            return

        orchestrator.pause_post(queue_item.post_id)
        await callback.answer("⏸ Публикация поста приостановлена")
        await show_post_queue_actions(callback)
    except Exception as e:
        logger.error(f"Error pausing post: {str(e)}")
        await callback.answer("❌ Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("queue_resume_post_"))
async def resume_post(callback: CallbackQuery):
    """Возобновить публикацию поста."""
    try:
        queue_item_id = int(callback.data.replace("queue_resume_post_", ""))
        orchestrator = get_orchestrator(callback.bot)

        queue_item = orchestrator.queue_manager.fetch_queue_item(queue_item_id)
        if not queue_item:
            await callback.answer("❌ Запись не найдена", show_alert=True)
            return

        orchestrator.resume_post(queue_item.post_id)
        await callback.answer("▶️ Публикация поста возобновлена")
        await show_post_queue_actions(callback)
    except Exception as e:
        logger.error(f"Error resuming post: {str(e)}")
        await callback.answer("❌ Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("queue_cancel_post_"))
async def cancel_post(callback: CallbackQuery):
    """Отменить публикацию поста (вернуть в черновики)."""
    try:
        queue_item_id = int(callback.data.replace("queue_cancel_post_", ""))
        orchestrator = get_orchestrator(callback.bot)

        queue_item = orchestrator.queue_manager.fetch_queue_item(queue_item_id)
        if not queue_item:
            await callback.answer("❌ Запись не найдена", show_alert=True)
            return

        orchestrator.cancel_post(queue_item.post_id)
        await callback.answer("✅ Пост возвращён в черновики")

        platform = queue_item.platform
        callback.data = f"queue_platform_{platform}"
        await show_platform_queue(callback)
    except Exception as e:
        logger.error(f"Error cancelling post: {str(e)}")
        await callback.answer("❌ Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("queue_publish_now_"))
async def publish_now(callback: CallbackQuery):
    """Опубликовать пост вне очереди."""
    try:
        platform, post_id = _parse_queue_publish_now_callback(callback.data)
        if not post_id:
            await callback.answer("❌ Некорректные данные кнопки", show_alert=True)
            return
        orchestrator = get_orchestrator(callback.bot)
        platforms = [platform] if platform else None
        if platforms is None:
            # Старый callback без платформы: не добавлять все соцсети — только ускорить уже висящие задачи
            success = orchestrator.bump_queued_publication_priority(post_id, priority=999)
            ok_text = "⏫ Приоритет очереди обновлён"
        else:
            success = orchestrator.publish_now(post_id, platforms=platforms)
            ok_text = "🚀 Пост добавлен в очередь с высоким приоритетом"

        if success:
            if get_settings_service().is_global_publication_pause():
                await callback.answer(
                    f"{ok_text}. Глобальная пауза включена — публикация начнётся после «Возобновить все».",
                    show_alert=True,
                )
            else:
                await callback.answer(ok_text)
        else:
            await callback.answer(
                "❌ Нет новых записей в очереди (или нечего ускорять). Откройте пост заново из меню очереди.",
                show_alert=True,
            )
    except Exception as e:
        logger.error(f"Error publishing now: {str(e)}")
        await callback.answer("❌ Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("add_to_queue_and_create_"))
async def add_to_queue_and_create(callback: CallbackQuery, state: FSMContext):
    """Добавить пост в очередь публикации и сразу предложить создать новый."""
    try:
        post_id = callback.data.replace("add_to_queue_and_create_", "")
        orchestrator = get_orchestrator(callback.bot)
        
        # Убеждаемся, что добавляем во все платформы, включая ВК
        success = orchestrator.add_post_to_queue(post_id, platforms=["vk", "telegram", "instagram", "max", "avito"])
        
        if success:
            await callback.answer("✅ Пост добавлен в очередь публикации")
            from app.bot.handlers.post_creation import PostCreation

            await state.update_data(avito_screen_level=1, avito_body_level=1)
            service = get_settings_service()
            vk_market_enabled = service.is_vk_market_enabled()
            avito_enabled = service.is_platform_enabled("avito")
            status_hint = get_platform_status_hint_text()
            await safe_edit_message(
                callback.message,
                "✅ Пост добавлен в очередь публикации!\n\n"
                + format_post_creation_text_prompt(
                    status_hint, 0, 0, avito_enabled=avito_enabled
                ),
                reply_markup=get_create_post_entry_keyboard(
                    vk_market_enabled,
                    avito_enabled=avito_enabled,
                    avito_screen_level=1,
                    avito_body_level=1,
                ),
                parse_mode="HTML",
            )
            await state.set_state(PostCreation.waiting_for_text)
        else:
            await callback.answer("❌ Ошибка при добавлении поста в очередь", show_alert=True)
    except Exception as e:
        logger.error(f"Error adding to queue and creating: {str(e)}")
        await callback.answer("❌ Ошибка", show_alert=True)

@router.callback_query(F.data.startswith("add_to_queue_") & ~F.data.startswith("add_to_queue_and_create_"))
async def add_to_queue(callback: CallbackQuery):
    """Добавить пост в очередь публикации."""
    try:
        post_id = callback.data.replace("add_to_queue_", "")
        orchestrator = get_orchestrator(callback.bot)
        
        # Убеждаемся, что добавляем во все платформы, включая ВК
        success = orchestrator.add_post_to_queue(post_id, platforms=["vk", "telegram", "instagram", "max", "avito"])
        
        if success:
            await callback.answer("✅ Пост добавлен в очередь публикации")
            # Обновляем сообщение
            text = callback.message.text
            text += "\n\n✅ Пост добавлен в очередь публикации!"
            text += "\n\n" + get_platform_status_hint_text()
            await safe_edit_message(callback.message, text, reply_markup=callback.message.reply_markup)
        else:
            await callback.answer("❌ Ошибка при добавлении поста в очередь", show_alert=True)
    except Exception as e:
        logger.error(f"Error adding to queue: {str(e)}")
        await callback.answer("❌ Ошибка", show_alert=True)
