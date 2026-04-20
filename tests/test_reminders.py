"""
Tests for the WhatsApp reminder feature.
Covers: command detection, relative/absolute datetime parsing,
reminder storage, due reminder selection, and delivery.
"""
import json
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo

import tempfile
import pathlib


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc(days_offset=0, hours_offset=0):
    return datetime.now(timezone.utc) + timedelta(days=days_offset, hours=hours_offset)


# ---------------------------------------------------------------------------
# Command detection
# ---------------------------------------------------------------------------

class TestReminderCommandDetection(unittest.TestCase):

    def _is(self, msg):
        from services.reminder_service import is_reminder_command
        return is_reminder_command(msg)

    def test_exclamation_remindme(self):
        self.assertTrue(self._is("!remindme tomorrow to call the yard"))

    def test_exclamation_remind_me(self):
        self.assertTrue(self._is("!remind me in 2 hours to check the engine"))

    def test_remind_me_to(self):
        self.assertTrue(self._is("remind me to call the yard tomorrow"))

    def test_remind_me_space(self):
        self.assertTrue(self._is("remind me tomorrow 9am oil check"))

    def test_remindme_no_exclamation(self):
        self.assertTrue(self._is("remindme in 30 minutes to restart the generator"))

    def test_set_a_reminder_to(self):
        self.assertTrue(self._is("set a reminder to check the bilge pump"))

    def test_set_a_reminder_for(self):
        self.assertTrue(self._is("set a reminder for tomorrow at 9am"))

    def test_set_reminder_to(self):
        self.assertTrue(self._is("set reminder to call the marina"))

    def test_case_insensitive(self):
        self.assertTrue(self._is("REMIND ME tomorrow to buy flares"))

    def test_not_a_reminder_random_text(self):
        self.assertFalse(self._is("how much for a yanmar impeller"))

    def test_not_a_reminder_greeting(self):
        self.assertFalse(self._is("hi"))

    def test_not_a_reminder_compliance(self):
        self.assertFalse(self._is("is marpol required here"))


# ---------------------------------------------------------------------------
# Intent classification routes reminders
# ---------------------------------------------------------------------------

class TestReminderIntent(unittest.TestCase):

    def _cls(self, text):
        from domain.intent import classify_text
        return classify_text(text)

    def test_exclamation_remindme_is_reminder(self):
        self.assertEqual(self._cls("!remindme tomorrow 9am check engine"), "reminder")

    def test_remind_me_to_is_reminder(self):
        self.assertEqual(self._cls("remind me to call the yard tomorrow"), "reminder")

    def test_set_a_reminder_is_reminder(self):
        self.assertEqual(self._cls("set a reminder to check the bilge"), "reminder")

    def test_not_reminder_for_market_check(self):
        self.assertNotEqual(self._cls("how much for a bilge pump"), "reminder")


# ---------------------------------------------------------------------------
# Relative time parsing
# ---------------------------------------------------------------------------

class TestRelativeTimeParsing(unittest.TestCase):

    def _parse(self, s):
        from services.reminder_service import parse_datetime_and_text
        return parse_datetime_and_text(s)

    def test_in_2_hours(self):
        before = datetime.now(timezone.utc)
        due, text = self._parse("in 2 hours check the engine")
        after = datetime.now(timezone.utc)
        self.assertIsNotNone(due)
        self.assertAlmostEqual((due - before).total_seconds(), 7200, delta=5)
        self.assertEqual(text, "check the engine")

    def test_in_30_minutes(self):
        before = datetime.now(timezone.utc)
        due, text = self._parse("in 30 minutes restart the generator")
        self.assertIsNotNone(due)
        self.assertAlmostEqual((due - before).total_seconds(), 1800, delta=5)
        self.assertEqual(text, "restart the generator")

    def test_in_1_hour(self):
        due, text = self._parse("in 1 hour oil change")
        self.assertIsNotNone(due)
        self.assertEqual(text, "oil change")

    def test_in_45_mins(self):
        due, text = self._parse("in 45 mins call marina")
        self.assertIsNotNone(due)
        self.assertEqual(text, "call marina")

    def test_tomorrow_with_time(self):
        due, text = self._parse("tomorrow 0800 check the anchor chain")
        self.assertIsNotNone(due)
        local = due.astimezone(ZoneInfo("Europe/London"))
        self.assertEqual(local.hour, 8)
        self.assertEqual(local.minute, 0)
        self.assertEqual(text, "check the anchor chain")

    def test_tomorrow_8am(self):
        due, text = self._parse("tomorrow 8am call the yard")
        self.assertIsNotNone(due)
        local = due.astimezone(ZoneInfo("Europe/London"))
        self.assertEqual(local.hour, 8)
        self.assertEqual(text, "call the yard")

    def test_tomorrow_no_time_defaults_to_9am(self):
        due, text = self._parse("tomorrow buy flares")
        self.assertIsNotNone(due)
        local = due.astimezone(ZoneInfo("Europe/London"))
        self.assertEqual(local.hour, 9)
        self.assertEqual(text, "buy flares")

    def test_next_monday(self):
        due, text = self._parse("next Monday 0900 service the generator")
        self.assertIsNotNone(due)
        local = due.astimezone(ZoneInfo("Europe/London"))
        self.assertEqual(local.weekday(), 0)  # Monday
        self.assertEqual(local.hour, 9)
        self.assertEqual(text, "service the generator")

    def test_next_week_defaults_to_9am(self):
        due, text = self._parse("next week review the quote")
        self.assertIsNotNone(due)
        local = due.astimezone(ZoneInfo("Europe/London"))
        self.assertEqual(local.hour, 9)
        self.assertEqual(text, "review the quote")

    def test_next_month(self):
        due, text = self._parse("next month renew insurance")
        self.assertIsNotNone(due)
        self.assertEqual(text, "renew insurance")

    def test_unrecognised_returns_none(self):
        due, text = self._parse("whenever possible check the bilge")
        self.assertIsNone(due)


# ---------------------------------------------------------------------------
# Exact date/time parsing
# ---------------------------------------------------------------------------

class TestExactDateTimeParsing(unittest.TestCase):

    def _parse(self, s):
        from services.reminder_service import parse_datetime_and_text
        return parse_datetime_and_text(s)

    def test_dd_month_with_time(self):
        due, text = self._parse("25 April 14:30 sign the contract")
        self.assertIsNotNone(due)
        local = due.astimezone(ZoneInfo("Europe/London"))
        self.assertEqual(local.day, 25)
        self.assertEqual(local.month, 4)
        self.assertEqual(local.hour, 14)
        self.assertEqual(local.minute, 30)
        self.assertEqual(text, "sign the contract")

    def test_dd_month_abbreviated(self):
        due, text = self._parse("25 Apr 2026 14:30 sign the contract")
        self.assertIsNotNone(due)
        local = due.astimezone(ZoneInfo("Europe/London"))
        self.assertEqual(local.day, 25)
        self.assertEqual(local.month, 4)
        self.assertEqual(local.year, 2026)
        self.assertEqual(text, "sign the contract")

    def test_dd_month_no_time_defaults_to_9am(self):
        due, text = self._parse("3 June call the marina")
        self.assertIsNotNone(due)
        local = due.astimezone(ZoneInfo("Europe/London"))
        self.assertEqual(local.day, 3)
        self.assertEqual(local.month, 6)
        self.assertEqual(local.hour, 9)

    def test_dd_month_hhmm_no_colon(self):
        due, text = self._parse("10 March 0830 check engine room")
        self.assertIsNotNone(due)
        local = due.astimezone(ZoneInfo("Europe/London"))
        self.assertEqual(local.hour, 8)
        self.assertEqual(local.minute, 30)
        self.assertEqual(text, "check engine room")

    def test_next_friday_time(self):
        due, text = self._parse("next Friday 2pm submit MCA form")
        self.assertIsNotNone(due)
        local = due.astimezone(ZoneInfo("Europe/London"))
        self.assertEqual(local.weekday(), 4)  # Friday
        self.assertEqual(local.hour, 14)
        self.assertEqual(text, "submit MCA form")


# ---------------------------------------------------------------------------
# Reminder storage
# ---------------------------------------------------------------------------

class TestReminderStorage(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._path = pathlib.Path(self._tmp.name) / "reminders" / "reminders.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _patch(self):
        import services.reminder_service as svc
        return patch.object(svc, "REMINDERS_FILE", self._path)

    def test_save_and_load(self):
        import services.reminder_service as svc
        with self._patch():
            due = _utc(hours_offset=2)
            svc.create_reminder(phone="+447700000000", due_at=due, text="Check bilge pump")
            loaded = svc.load_reminders()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["text"], "Check bilge pump")
        self.assertEqual(loaded[0]["status"], "pending")

    def test_multiple_reminders_accumulate(self):
        import services.reminder_service as svc
        with self._patch():
            svc.create_reminder(phone="+447700000000", due_at=_utc(hours_offset=1), text="First")
            svc.create_reminder(phone="+447700000000", due_at=_utc(hours_offset=2), text="Second")
            loaded = svc.load_reminders()
        self.assertEqual(len(loaded), 2)

    def test_reminder_has_required_fields(self):
        import services.reminder_service as svc
        with self._patch():
            due = _utc(hours_offset=1)
            r = svc.create_reminder(phone="+447700000001", due_at=due, text="Engine check")
        for field in ("reminder_id", "phone", "text", "due_at", "timezone", "created_at", "status"):
            self.assertIn(field, r)
        self.assertEqual(r["phone"], "+447700000001")


# ---------------------------------------------------------------------------
# Due reminder selection
# ---------------------------------------------------------------------------

class TestDueReminderSelection(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._path = pathlib.Path(self._tmp.name) / "reminders" / "reminders.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _write_reminders(self, reminders):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(reminders))

    def _patch(self):
        import services.reminder_service as svc
        return patch.object(svc, "REMINDERS_FILE", self._path)

    def test_overdue_reminder_is_processed(self):
        import services.reminder_service as svc
        overdue = _utc(hours_offset=-1).isoformat()
        self._write_reminders([{
            "reminder_id": "r1", "phone": "+44770", "text": "Test",
            "due_at": overdue, "timezone": "Europe/London",
            "created_at": overdue, "status": "pending",
        }])
        with self._patch():
            with patch.object(svc, "_send_reminder_message") as mock_send:
                n = svc.process_due_reminders()
        self.assertEqual(n, 1)
        mock_send.assert_called_once()

    def test_future_reminder_is_not_processed(self):
        import services.reminder_service as svc
        future = _utc(hours_offset=2).isoformat()
        self._write_reminders([{
            "reminder_id": "r2", "phone": "+44770", "text": "Later",
            "due_at": future, "timezone": "Europe/London",
            "created_at": future, "status": "pending",
        }])
        with self._patch():
            with patch.object(svc, "_send_reminder_message") as mock_send:
                n = svc.process_due_reminders()
        self.assertEqual(n, 0)
        mock_send.assert_not_called()

    def test_sent_reminder_not_reprocessed(self):
        import services.reminder_service as svc
        overdue = _utc(hours_offset=-1).isoformat()
        self._write_reminders([{
            "reminder_id": "r3", "phone": "+44770", "text": "Already sent",
            "due_at": overdue, "timezone": "Europe/London",
            "created_at": overdue, "status": "sent",
        }])
        with self._patch():
            with patch.object(svc, "_send_reminder_message") as mock_send:
                n = svc.process_due_reminders()
        self.assertEqual(n, 0)
        mock_send.assert_not_called()

    def test_status_updated_to_sent_on_success(self):
        import services.reminder_service as svc
        overdue = _utc(hours_offset=-1).isoformat()
        self._write_reminders([{
            "reminder_id": "r4", "phone": "+44770", "text": "Test",
            "due_at": overdue, "timezone": "Europe/London",
            "created_at": overdue, "status": "pending",
        }])
        with self._patch():
            with patch.object(svc, "_send_reminder_message"):
                svc.process_due_reminders()
            loaded = svc.load_reminders()
        self.assertEqual(loaded[0]["status"], "sent")

    def test_status_updated_to_failed_on_error(self):
        import services.reminder_service as svc
        overdue = _utc(hours_offset=-1).isoformat()
        self._write_reminders([{
            "reminder_id": "r5", "phone": "+44770", "text": "Test",
            "due_at": overdue, "timezone": "Europe/London",
            "created_at": overdue, "status": "pending",
        }])
        with self._patch():
            with patch.object(svc, "_send_reminder_message", side_effect=Exception("twilio error")):
                svc.process_due_reminders()
            loaded = svc.load_reminders()
        self.assertEqual(loaded[0]["status"], "failed")


# ---------------------------------------------------------------------------
# Reminder delivery (Twilio integration)
# ---------------------------------------------------------------------------

class TestReminderDelivery(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._path = pathlib.Path(self._tmp.name) / "reminders" / "reminders.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _write_overdue(self, text="Bilge pump check", phone="+447700000000"):
        overdue = _utc(hours_offset=-1).isoformat()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps([{
            "reminder_id": "rd1", "phone": phone, "text": text,
            "due_at": overdue, "timezone": "Europe/London",
            "created_at": overdue, "status": "pending",
        }]))

    def _patch(self):
        import services.reminder_service as svc
        return patch.object(svc, "REMINDERS_FILE", self._path)

    def test_twilio_client_called_with_correct_to(self):
        import services.reminder_service as svc
        self._write_overdue(phone="+447711223344")
        mock_client = MagicMock()
        with self._patch():
            with patch.object(svc, "_TWILIO_ACCOUNT_SID", "ACtest"):
                with patch.object(svc, "_TWILIO_AUTH_TOKEN", "authtest"):
                    with patch.object(svc, "_TWILIO_FROM_NUMBER", "whatsapp:+14155238886"):
                        with patch("twilio.rest.Client", return_value=mock_client):
                            svc.process_due_reminders()
        mock_client.messages.create.assert_called_once()
        call_kwargs = mock_client.messages.create.call_args[1]
        self.assertEqual(call_kwargs["to"], "+447711223344")

    def test_reminder_body_contains_text(self):
        import services.reminder_service as svc
        self._write_overdue(text="Check the anchor chain")
        mock_client = MagicMock()
        with self._patch():
            with patch.object(svc, "_TWILIO_ACCOUNT_SID", "ACtest"):
                with patch.object(svc, "_TWILIO_AUTH_TOKEN", "authtest"):
                    with patch.object(svc, "_TWILIO_FROM_NUMBER", "whatsapp:+14155238886"):
                        with patch("twilio.rest.Client", return_value=mock_client):
                            svc.process_due_reminders()
        call_kwargs = mock_client.messages.create.call_args[1]
        self.assertIn("Check the anchor chain", call_kwargs["body"])

    def test_no_twilio_creds_skips_send(self):
        import services.reminder_service as svc
        self._write_overdue()
        with self._patch():
            with patch.object(svc, "_TWILIO_ACCOUNT_SID", None):
                with patch.object(svc, "_TWILIO_AUTH_TOKEN", None):
                    with patch.object(svc, "_TWILIO_FROM_NUMBER", None):
                        n = svc.process_due_reminders()
        # attempted=1 but Twilio skipped — status should be 'sent' (send is best-effort)
        self.assertEqual(n, 1)


# ---------------------------------------------------------------------------
# Format due datetime
# ---------------------------------------------------------------------------

class TestFormatDueDatetime(unittest.TestCase):

    def test_formats_correctly(self):
        from services.reminder_service import format_due_datetime
        # 2026-04-27 is in BST (UTC+1); 13:30 UTC → 14:30 local
        dt = datetime(2026, 4, 27, 13, 30, tzinfo=timezone.utc)
        result = format_due_datetime(dt, "Europe/London")
        self.assertIn("Monday", result)
        self.assertIn("27", result)
        self.assertIn("April", result)
        self.assertIn("2026", result)
        self.assertIn("14:30", result)


# ---------------------------------------------------------------------------
# End-to-end: _handle_text_message routes reminder command
# ---------------------------------------------------------------------------

class TestReminderHandlerRouting(unittest.TestCase):

    def test_reminder_command_returns_reminder_set(self):
        from whatsapp_app import _handle_text_message
        import services.reminder_service as svc
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "reminders" / "reminders.json"
            with patch.object(svc, "REMINDERS_FILE", path):
                answer, _ = _handle_text_message(
                    "remind me tomorrow 9am check the engine",
                    {"sessions": [], "documents": [], "active_session_id": None},
                    phone="+447700000000",
                )
        self.assertIn("REMINDER SET", answer)
        self.assertIn("check the engine", answer)

    def test_reminder_bad_datetime_returns_not_set(self):
        from whatsapp_app import _handle_text_message
        answer, _ = _handle_text_message(
            "remind me whenever possible to check bilge",
            {"sessions": [], "documents": [], "active_session_id": None},
            phone="+447700000000",
        )
        self.assertIn("REMINDER NOT SET", answer)

    def test_reminder_no_text_returns_not_set(self):
        from whatsapp_app import _handle_text_message
        import services.reminder_service as svc
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "reminders" / "reminders.json"
            with patch.object(svc, "REMINDERS_FILE", path):
                answer, _ = _handle_text_message(
                    "remind me tomorrow",
                    {"sessions": [], "documents": [], "active_session_id": None},
                    phone="+447700000000",
                )
        self.assertIn("REMINDER NOT SET", answer)


if __name__ == "__main__":
    unittest.main()
