"""Отложенная публикация: парсинг МСК, приоритет, UI-кнопки."""
from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from app.bot.keyboards.main_keyboard import get_main_keyboard, get_post_actions_keyboard
from app.bot.utils.post_success_ui import build_post_ready_keyboard
from app.scheduler.queue_manager import SCHEDULED_PRIORITY
from app.utils.time_msk import (
    combine_msk_naive_utc,
    format_schedule_confirm_msk,
    format_schedule_msk,
    parse_msk_date,
    parse_msk_time,
    parse_schedule_flexible,
    parse_schedule_msk_to_naive_utc,
    parse_schedule_user_input,
)


class ParseMskScheduleTest(unittest.TestCase):
    def test_parse_date_dd_mm(self):
        now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(parse_msk_date("13.10", now=now), date(2026, 10, 13))
        self.assertEqual(parse_msk_date("13/10/26", now=now), date(2026, 10, 13))
        self.assertEqual(parse_msk_date("13.10.2026", now=now), date(2026, 10, 13))
        self.assertEqual(parse_msk_date("1310", now=now), date(2026, 10, 13))
        self.assertEqual(parse_msk_date("171026", now=now), date(2026, 10, 17))
        self.assertEqual(parse_msk_date("17102026", now=now), date(2026, 10, 17))

    def test_parse_date_invalid(self):
        with self.assertRaises(ValueError):
            parse_msk_date("32.13")
        with self.assertRaises(ValueError):
            parse_msk_date("abc")

    def test_parse_time(self):
        self.assertEqual(parse_msk_time("18:30").hour, 18)
        self.assertEqual(parse_msk_time("9:05").minute, 5)
        self.assertEqual(parse_msk_time("1610").hour, 16)
        self.assertEqual(parse_msk_time("1610").minute, 10)
        self.assertEqual(parse_msk_time("930").hour, 9)
        with self.assertRaises(ValueError):
            parse_msk_time("25:00")
        with self.assertRaises(ValueError):
            parse_msk_time("18")

    def test_parse_schedule_user_input_variants(self):
        now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
        d, t = parse_schedule_user_input("171026", now=now)
        self.assertEqual(d, date(2026, 10, 17))
        self.assertIsNone(t)

        d, t = parse_schedule_user_input("1610 171026", now=now)
        self.assertEqual(d, date(2026, 10, 17))
        self.assertEqual((t.hour, t.minute), (16, 10))

        d, t = parse_schedule_user_input("1610 17102026", now=now)
        self.assertEqual(d, date(2026, 10, 17))
        self.assertEqual((t.hour, t.minute), (16, 10))

        d, t = parse_schedule_user_input("16:10 17.10.2026", now=now)
        self.assertEqual(d, date(2026, 10, 17))
        self.assertEqual((t.hour, t.minute), (16, 10))

        d, t = parse_schedule_user_input("1610171026", now=now)
        self.assertEqual(d, date(2026, 10, 17))
        self.assertEqual((t.hour, t.minute), (16, 10))

    def test_combine_and_validate_future(self):
        # 12:00 МСК = 09:00 UTC
        naive = combine_msk_naive_utc(date(2026, 10, 13), 12, 0)
        self.assertEqual(naive, datetime(2026, 10, 13, 9, 0))

        now = datetime(2026, 10, 13, 8, 0, tzinfo=timezone.utc)
        ok = parse_schedule_msk_to_naive_utc(date(2026, 10, 13), 12, 0, now=now)
        self.assertEqual(ok, datetime(2026, 10, 13, 9, 0))

        with self.assertRaises(ValueError):
            parse_schedule_msk_to_naive_utc(
                date(2026, 10, 13), 12, 0, now=datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc)
            )

    def test_format_schedule_msk(self):
        # 09:00 UTC → 12:00 МСК
        label = format_schedule_msk(datetime(2026, 10, 13, 9, 0))
        self.assertIn("13.10", label)
        self.assertIn("12:00", label)
        self.assertIn("МСК", label)

    def test_format_schedule_confirm_msk(self):
        # 13:10 UTC → 16:10 МСК 17 октября
        label = format_schedule_confirm_msk(datetime(2026, 10, 17, 13, 10))
        self.assertEqual(label, "16:10 17 октября 2026 (МСК)")

    def test_parse_schedule_flexible_time_only_and_prefer(self):
        now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
        d, t = parse_schedule_flexible("940", now=now)
        self.assertIsNone(d)
        self.assertEqual((t.hour, t.minute), (9, 40))

        d, t = parse_schedule_flexible("1005", prefer_time=True, now=now)
        self.assertIsNone(d)
        self.assertEqual((t.hour, t.minute), (10, 5))

        d, t = parse_schedule_flexible("1005", prefer_time=False, now=now)
        self.assertEqual(d, date(2026, 5, 10))
        self.assertIsNone(t)


class SchedulePriorityConstantTest(unittest.TestCase):
    def test_priority_between_normal_and_publish_now(self):
        self.assertEqual(SCHEDULED_PRIORITY, 100)
        self.assertGreater(SCHEDULED_PRIORITY, 0)
        self.assertLess(SCHEDULED_PRIORITY, 999)


class ScheduleUiButtonsTest(unittest.TestCase):
    def test_post_ready_has_schedule_button(self):
        kb = build_post_ready_keyboard("abc-123")
        texts = [b.text for row in kb.inline_keyboard for b in row]
        cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
        self.assertIn("📅 Запланировать", texts)
        self.assertIn("schedule_post_abc-123", cbs)
        self.assertIn("📋 В очередь", texts)

    def test_draft_actions_row(self):
        kb = get_post_actions_keyboard()
        first = [b.text for b in kb.inline_keyboard[0]]
        self.assertEqual(first, ["📤 Отправить везде", "📅 Запланировать"])
        self.assertEqual(kb.inline_keyboard[0][1].callback_data, "schedule_selected_post")

    def test_main_menu_scheduled_dynamic(self):
        kb0 = get_main_keyboard(scheduled_count=0)
        labels0 = [b.text for row in kb0.inline_keyboard for b in row]
        self.assertFalse(any("Запланированные" in t for t in labels0))

        kb1 = get_main_keyboard(scheduled_count=3, drafts_count=1)
        labels1 = [b.text for row in kb1.inline_keyboard for b in row]
        self.assertIn("📅 Запланированные (3)", labels1)
        # Между черновиками и очередью
        flat = labels1
        draft_i = next(i for i, t in enumerate(flat) if "Черновики" in t)
        sched_i = next(i for i, t in enumerate(flat) if "Запланированные" in t)
        queue_i = next(i for i, t in enumerate(flat) if "В очереди" in t)
        self.assertLess(draft_i, sched_i)
        self.assertLess(sched_i, queue_i)


class QueueStatsSplitLogicTest(unittest.TestCase):
    """Проверяем SQL-фильтры через контракт методов (моки сессии не нужны)."""

    def test_due_filter_excludes_future(self):
        from app.scheduler import queue_manager as qm

        self.assertIn("scheduled_at <= :now", qm._DUE_SCHEDULE_SQL)
        self.assertIn("scheduled_at > :now", qm._FUTURE_USER_SCHEDULE_SQL)
        self.assertIn("priority >= :sched_priority", qm._FUTURE_USER_SCHEDULE_SQL)

    def test_orchestrator_exposes_schedule_api(self):
        from pathlib import Path

        src = Path(__file__).resolve().parents[1] / "app/scheduler/orchestrator.py"
        text = src.read_text(encoding="utf-8")
        self.assertIn("def schedule_post", text)
        self.assertIn("SCHEDULED_PRIORITY", text)
        self.assertIn("def reschedule_post", text)
        self.assertIn("def cancel_scheduled_post", text)
        self.assertIn("def promote_scheduled_now", text)
        self.assertIn("def get_scheduled_count", text)
        self.assertIn("def list_scheduled_posts", text)

    def test_queue_manager_has_schedule_helpers(self):
        from app.scheduler.queue_manager import QueueManager

        for name in (
            "count_scheduled_posts",
            "list_scheduled_posts",
            "update_post_schedule",
            "promote_scheduled_now",
            "cancel_scheduled_post",
        ):
            self.assertTrue(callable(getattr(QueueManager, name)))


def test_schedule_keyboard_callbacks_fit_telegram_limit():
    from app.bot.keyboards.schedule_keyboard import (
        get_schedule_confirm_keyboard,
        get_schedule_day_keyboard,
        get_schedule_time_keyboard,
        get_scheduled_post_actions_keyboard,
    )

    post_id = "a" * 36  # uuid-like
    for kb in (
        get_schedule_day_keyboard(post_id),
        get_schedule_time_keyboard(post_id, reschedule=True),
        get_schedule_confirm_keyboard(post_id),
        get_scheduled_post_actions_keyboard(post_id),
    ):
        for row in kb.inline_keyboard:
            for btn in row:
                assert btn.callback_data is None or len(btn.callback_data.encode()) <= 64


if __name__ == "__main__":
    unittest.main()
