"""Сухие прогоны паузы очереди: UI-статус, воркеры, оркестратор, Авито."""
import asyncio
import inspect
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.bot.handlers.scheduler import (
    _parse_queue_publish_now_callback,
    _strip_prefix,
)
from app.bot.keyboards.scheduler_keyboard import (
    get_avito_platform_keyboard,
    get_platform_queue_keyboard,
    get_queue_menu_keyboard,
)
from app.scheduler.avito_archive_worker import AvitoArchiveWorker
from app.scheduler.orchestrator import PublicationOrchestrator
from app.scheduler.platform_worker import PlatformWorker
from app.scheduler.queue_manager import QueueManager
from app.scheduler.queue_ui import format_queue_pause_status


def _callback_data_set(markup) -> set[str]:
    return {
        btn.callback_data
        for row in markup.inline_keyboard
        for btn in row
        if getattr(btn, "callback_data", None)
    }


class QueuePauseStatusTextTest(unittest.TestCase):
    def test_running_global_and_platform_texts_differ(self):
        running = format_queue_pause_status(global_pause=False, platform_pauses={})
        global_on = format_queue_pause_status(global_pause=True, platform_pauses={})
        vk_only = format_queue_pause_status(
            global_pause=False, platform_pauses={"vk": True, "telegram": False}
        )
        self.assertIn("идут", running)
        self.assertIn("Глобальная пауза", global_on)
        self.assertIn("ВК", vk_only)
        self.assertNotEqual(running, global_on)
        self.assertNotEqual(running, vk_only)
        self.assertNotEqual(global_on, vk_only)

    def test_platform_screen_mentions_global_pause(self):
        text = format_queue_pause_status(
            global_pause=True,
            platform_pauses={"vk": True},
            platform="vk",
        )
        self.assertIn("Глобальная пауза", text)
        self.assertIn("Возобновить все", text)


class QueuePauseKeyboardTest(unittest.TestCase):
    def test_global_toggle_is_single_button(self):
        idle = _callback_data_set(get_queue_menu_keyboard({}, global_pause=False))
        paused = _callback_data_set(get_queue_menu_keyboard({}, global_pause=True))
        self.assertIn("queue_pause_global", idle)
        self.assertNotIn("queue_resume_global", idle)
        self.assertIn("queue_resume_global", paused)
        self.assertNotIn("queue_pause_global", paused)

    def test_platform_toggle_is_single_button(self):
        idle = _callback_data_set(get_platform_queue_keyboard("vk", []))
        paused = _callback_data_set(
            get_platform_queue_keyboard("vk", [], platform_paused=True)
        )
        self.assertIn("queue_pause_platform_vk", idle)
        self.assertNotIn("queue_resume_platform_vk", idle)
        self.assertIn("queue_resume_platform_vk", paused)
        self.assertNotIn("queue_pause_platform_vk", paused)

    def test_avito_hides_upload_when_paused(self):
        running = _callback_data_set(
            get_avito_platform_keyboard(can_upload=True, has_work=True)
        )
        paused = _callback_data_set(
            get_avito_platform_keyboard(
                can_upload=True, has_work=True, platform_paused=True
            )
        )
        global_paused = _callback_data_set(
            get_avito_platform_keyboard(
                can_upload=True, has_work=True, global_pause=True
            )
        )
        self.assertIn("queue_avito_upload_feed", running)
        self.assertNotIn("queue_avito_upload_feed", paused)
        self.assertNotIn("queue_avito_upload_feed", global_paused)
        self.assertIn("queue_pause_platform_avito", running)
        self.assertIn("queue_resume_platform_avito", paused)


class QueueCallbackPrefixTest(unittest.TestCase):
    def test_strip_prefix_does_not_confuse_pause_with_platform(self):
        data = "queue_pause_platform_vk"
        self.assertEqual(_strip_prefix(data, "queue_pause_platform_"), "vk")
        self.assertEqual(_strip_prefix(data, "queue_platform_"), "")
        self.assertEqual(_strip_prefix("queue_pause_post_12", "queue_pause_post_"), "12")
        self.assertEqual(_strip_prefix("queue_pause_post_12", "queue_post_"), "")

    def test_publish_now_parses_platform(self):
        plat, post_id = _parse_queue_publish_now_callback(
            "queue_publish_now_telegram_abc-uuid"
        )
        self.assertEqual(plat, "telegram")
        self.assertEqual(post_id, "abc-uuid")


class PlatformWorkerPauseTest(unittest.TestCase):
    def test_wait_aborts_when_platform_paused(self):
        worker = PlatformWorker("vk", MagicMock())
        worker.is_paused = True
        worker.last_published_at = datetime.now(timezone.utc)
        with patch.object(worker, "_platform_interval_seconds", return_value=60):
            ok = asyncio.run(worker.wait_for_interval())
        self.assertFalse(ok)

    def test_wait_aborts_on_first_item_if_global_pause(self):
        orch = MagicMock()
        orch.global_pause = True
        worker = PlatformWorker("telegram", MagicMock(), orchestrator=orch)
        worker.last_published_at = None
        ok = asyncio.run(worker.wait_for_interval())
        self.assertFalse(ok)

    def test_global_pause_without_worker_flag(self):
        orch = MagicMock()
        orch.global_pause = True
        worker = PlatformWorker("max", MagicMock(), orchestrator=orch)
        self.assertFalse(worker.is_paused)
        self.assertTrue(worker._is_effectively_paused())

    def test_mark_as_publishing_requires_pending(self):
        src = inspect.getsource(QueueManager.mark_as_publishing)
        self.assertIn("status = 'pending'", src)


class OrchestratorPauseIsolationTest(unittest.TestCase):
    def _make_orchestrator(self, svc=None):
        if svc is None:
            svc = MagicMock()
            svc.is_global_publication_pause.return_value = False
            svc.get_platform_publication_pauses.return_value = {}
        for target in (
            "app.scheduler.orchestrator.QueueManager",
            "app.scheduler.orchestrator.AvitoArchiveWorker",
        ):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        gs = patch(
            "app.scheduler.orchestrator.get_settings_service",
            return_value=svc,
        )
        gs.start()
        self.addCleanup(gs.stop)
        orch = PublicationOrchestrator()
        orch.start()
        return orch, svc

    def test_global_pause_does_not_set_worker_is_paused(self):
        orch, svc = self._make_orchestrator()
        self.assertFalse(orch.workers["vk"].is_paused)
        orch.pause_global()
        self.assertTrue(orch.global_pause)
        self.assertFalse(orch.workers["vk"].is_paused)
        self.assertTrue(orch.workers["vk"]._is_effectively_paused())
        svc.set_global_publication_pause.assert_called_with(True)

    def test_resume_global_keeps_individual_platform_pause(self):
        orch, svc = self._make_orchestrator()
        orch.pause_platform("vk")
        self.assertTrue(orch.workers["vk"].is_paused)
        orch.pause_global()
        orch.resume_global()
        self.assertFalse(orch.global_pause)
        self.assertTrue(orch.workers["vk"].is_paused)
        self.assertTrue(orch.is_platform_paused("vk"))
        svc.set_platform_publication_pause.assert_any_call("vk", True)

    def test_restores_platform_pauses_on_start(self):
        svc = MagicMock()
        svc.is_global_publication_pause.return_value = True
        svc.get_platform_publication_pauses.return_value = {
            "vk": True,
            "telegram": False,
            "instagram": False,
            "max": False,
            "avito": False,
        }
        orch, _ = self._make_orchestrator(svc)
        self.assertTrue(orch.global_pause)
        self.assertTrue(orch.workers["vk"].is_paused)
        self.assertFalse(orch.workers["telegram"].is_paused)

    def test_publish_now_resumes_paused_before_enqueue(self):
        orch, _ = self._make_orchestrator()
        orch.queue_manager.resume_paused_for_post_platform.return_value = 1
        orch.queue_manager.add_post_to_queue.return_value = [MagicMock()]
        ok = orch.publish_now("post-1", platforms=["vk"])
        self.assertTrue(ok)
        orch.queue_manager.resume_paused_for_post_platform.assert_called_once_with(
            "post-1", "vk"
        )


class AvitoPauseGateTest(unittest.TestCase):
    def test_archive_worker_respects_global_pause(self):
        orch = MagicMock()
        orch.global_pause = True
        orch.workers = {}
        worker = AvitoArchiveWorker(MagicMock(), orchestrator=orch)
        self.assertTrue(worker._is_paused())

    def test_archive_worker_respects_avito_platform_pause(self):
        orch = MagicMock()
        orch.global_pause = False
        orch.workers = {"avito": MagicMock(is_paused=True)}
        worker = AvitoArchiveWorker(MagicMock(), orchestrator=orch)
        self.assertTrue(worker._is_paused())

    def test_feed_upload_refuses_when_paused(self):
        from app.integrations.avito.avito_feed_dispatcher import execute_feed_upload

        svc = MagicMock()
        svc.is_publishing_paused.return_value = True
        with patch(
            "app.integrations.avito.avito_feed_dispatcher.get_settings_service",
            return_value=svc,
        ):
            ok, msg = asyncio.run(
                execute_feed_upload(MagicMock(), MagicMock(), manual=True)
            )
        self.assertFalse(ok)
        self.assertIn("паузе", msg.lower())
        svc.is_publishing_paused.assert_called_with("avito")


if __name__ == "__main__":
    unittest.main()
