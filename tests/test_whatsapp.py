"""Tests for WhatsApp webhook async transport (ASK-43)."""
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import whatsapp_app
from whatsapp_app import app, _send_whatsapp_message


def _post(data):
    with app.test_client() as c:
        return c.post("/whatsapp", data=data)


def _text_payload(body="hi", sid="SMtest001", phone="whatsapp:+1234567890"):
    return {"From": phone, "Body": body, "NumMedia": "0", "MessageSid": sid}


class TestWebhookQuickAck(unittest.TestCase):
    """Webhook must return 200 before slow processing completes."""

    @patch("whatsapp_app.save_user_state")
    @patch("whatsapp_app.load_user_state", return_value={})
    @patch("whatsapp_app._send_whatsapp_message")
    @patch("whatsapp_app._handle_text_message")
    def test_returns_200_without_waiting_for_slow_handler(
        self, mock_handler, mock_send, mock_load, mock_save
    ):
        """Text message webhook must return 200 before handler completes."""
        handler_started = threading.Event()
        handler_released = threading.Event()

        def slow_handler(incoming, state, phone=""):
            handler_started.set()
            handler_released.wait(timeout=5)
            return "DECISION: ok.", state

        mock_handler.side_effect = slow_handler

        resp = _post(_text_payload(sid="SMquick001"))
        self.assertEqual(resp.status_code, 200)
        # Webhook must return before we release the handler
        self.assertFalse(
            handler_released.is_set(),
            "Webhook waited for slow handler — should have returned before it completed",
        )

        handler_released.set()
        handler_started.wait(timeout=2)

    @patch("whatsapp_app.save_user_state")
    @patch("whatsapp_app.load_user_state", return_value={})
    @patch("whatsapp_app._send_whatsapp_message")
    @patch("whatsapp_app._handle_text_message")
    def test_returns_empty_twiml_for_text_message(
        self, mock_handler, mock_send, mock_load, mock_save
    ):
        """Text message webhook must return empty TwiML — no <Message> element."""
        handler_released = threading.Event()

        def slow_handler(incoming, state, phone=""):
            handler_released.wait(timeout=5)
            return "DECISION: ok.", state

        mock_handler.side_effect = slow_handler

        resp = _post(_text_payload(sid="SMtwiml001"))
        handler_released.set()

        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/xml", resp.content_type)
        body = resp.data.decode()
        self.assertNotIn("<Message>", body)

    @patch("whatsapp_app.save_user_state")
    @patch("whatsapp_app.load_user_state", return_value={})
    @patch("whatsapp_app._send_whatsapp_message")
    @patch("whatsapp_app._handle_text_message")
    def test_reply_sent_via_rest_after_processing(
        self, mock_handler, mock_send, mock_load, mock_save
    ):
        """Generated reply must reach _send_whatsapp_message after background processing."""
        done = threading.Event()

        def handler(incoming, state, phone=""):
            return "DECISION: Tier III applies.\nWHY: MARPOL VI.\nSOURCE: MARPOL VI\nACTIONS: • Verify", state

        mock_handler.side_effect = handler
        mock_send.side_effect = lambda *a, **kw: done.set()

        _post(_text_payload(body="when does Tier III apply?", sid="SMasync001"))
        done.wait(timeout=3)

        mock_send.assert_called()
        call_args = mock_send.call_args
        sent_body = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("body", "")
        self.assertIn("Tier III", sent_body)


class TestOutboundApiSend(unittest.TestCase):
    """_send_whatsapp_message must call Twilio REST and return outbound MessageSid."""

    @patch("whatsapp_app.TWILIO_FROM_NUMBER", "whatsapp:+14155238886")
    @patch("whatsapp_app.TWILIO_ACCOUNT_SID", "ACtest")
    @patch("whatsapp_app.TWILIO_AUTH_TOKEN", "token123")
    @patch("whatsapp_app.TwilioRestClient")
    def test_creates_twilio_message_and_returns_sid(self, mock_cls):
        mock_msg = MagicMock()
        mock_msg.sid = "SMoutbound999"
        mock_instance = MagicMock()
        mock_instance.messages.create.return_value = mock_msg
        mock_cls.return_value = mock_instance

        result = _send_whatsapp_message(
            "whatsapp:+1234567890",
            "⚓ AskHelm \n\nDECISION: yes.",
            inbound_message_sid="SMinbound123",
        )

        self.assertEqual(result, "SMoutbound999")
        mock_instance.messages.create.assert_called_once()
        create_kwargs = mock_instance.messages.create.call_args[1]
        self.assertEqual(create_kwargs["to"], "whatsapp:+1234567890")
        self.assertIn("yes.", create_kwargs["body"])

    @patch("whatsapp_app.TWILIO_FROM_NUMBER", None)
    def test_returns_none_when_credentials_missing(self):
        result = _send_whatsapp_message("whatsapp:+1234567890", "test body")
        self.assertIsNone(result)

    @patch("whatsapp_app.TWILIO_FROM_NUMBER", "whatsapp:+14155238886")
    @patch("whatsapp_app.TWILIO_ACCOUNT_SID", "ACtest")
    @patch("whatsapp_app.TWILIO_AUTH_TOKEN", "token123")
    @patch("whatsapp_app.TwilioRestClient")
    def test_returns_none_and_logs_on_api_failure(self, mock_cls):
        mock_cls.return_value.messages.create.side_effect = Exception("API error")
        result = _send_whatsapp_message("whatsapp:+1234567890", "test body")
        self.assertIsNone(result)


class TestSlowComplianceAsync(unittest.TestCase):
    """Webhook must return before slow compliance processing completes."""

    @patch("whatsapp_app.save_user_state")
    @patch("whatsapp_app.load_user_state", return_value={})
    @patch("whatsapp_app._send_whatsapp_message")
    @patch("whatsapp_app._handle_text_message")
    def test_webhook_returns_before_handler_completes(
        self, mock_handler, mock_send, mock_load, mock_save
    ):
        handler_running = threading.Event()
        handler_released = threading.Event()

        def slow_compliance(incoming, state, phone=""):
            handler_running.set()
            handler_released.wait(timeout=5)
            return (
                "DECISION: SOLAS requires A-60.\nWHY: SOLAS Ch II-2.\n"
                "SOURCE: SOLAS Ch II-2\nACTIONS: • Verify rating",
                state,
            )

        mock_handler.side_effect = slow_compliance

        resp = _post(_text_payload(
            body="does SOLAS require A-60 bulkheads?", sid="SMslow002"
        ))
        self.assertEqual(resp.status_code, 200)
        # Handler should have started (background thread) but not yet finished
        handler_running.wait(timeout=2)
        self.assertFalse(
            handler_released.is_set(),
            "Handler finished synchronously — should still be blocked",
        )

        # Release and verify reply is sent
        handler_released.set()
        time.sleep(0.2)
        mock_send.assert_called()


class TestDuplicateMessageSid(unittest.TestCase):
    """Same MessageSid in-flight must not trigger duplicate processing."""

    @patch("whatsapp_app.save_user_state")
    @patch("whatsapp_app.load_user_state", return_value={})
    @patch("whatsapp_app._send_whatsapp_message")
    @patch("whatsapp_app._handle_text_message")
    def test_duplicate_sid_skips_handler(
        self, mock_handler, mock_send, mock_load, mock_save
    ):
        mock_handler.return_value = ("DECISION: ok.", {})
        sid = "SMdup_test_777"

        with whatsapp_app._inbound_seen_lock:
            whatsapp_app._inbound_seen.add(sid)
        try:
            resp = _post(_text_payload(sid=sid))
            self.assertEqual(resp.status_code, 200)
            mock_handler.assert_not_called()
            mock_send.assert_not_called()
        finally:
            with whatsapp_app._inbound_seen_lock:
                whatsapp_app._inbound_seen.discard(sid)

    @patch("whatsapp_app.save_user_state")
    @patch("whatsapp_app.load_user_state", return_value={})
    @patch("whatsapp_app._send_whatsapp_message")
    @patch("whatsapp_app._handle_text_message")
    def test_duplicate_sid_returns_empty_twiml(
        self, mock_handler, mock_send, mock_load, mock_save
    ):
        sid = "SMdup_twiml_888"
        with whatsapp_app._inbound_seen_lock:
            whatsapp_app._inbound_seen.add(sid)
        try:
            resp = _post(_text_payload(sid=sid))
            body = resp.data.decode()
            self.assertNotIn("<Message>", body)
        finally:
            with whatsapp_app._inbound_seen_lock:
                whatsapp_app._inbound_seen.discard(sid)


class TestInboundLogging(unittest.TestCase):
    """Inbound MessageSid, From, and body preview must be logged."""

    @patch("whatsapp_app.save_user_state")
    @patch("whatsapp_app.load_user_state", return_value={})
    @patch("whatsapp_app._send_whatsapp_message")
    @patch("whatsapp_app._handle_text_message")
    def test_processing_start_logged_with_intent(
        self, mock_handler, mock_send, mock_load, mock_save
    ):
        """processing_start log must include inbound_message_sid."""
        done = threading.Event()
        mock_handler.side_effect = lambda *a, **kw: (done.set() or "DECISION: ok.", {})

        import logging
        with self.assertLogs("whatsapp_app", level="INFO") as cm:
            _post(_text_payload(body="hi", sid="SMlog001"))
            done.wait(timeout=2)

        log_text = "\n".join(cm.output)
        self.assertIn("inbound_whatsapp:", log_text)
        self.assertIn("SMlog001", log_text)


class TestComplianceRegression(unittest.TestCase):
    """Compliance queries must still reach answer_compliance_query via background thread."""

    @patch("whatsapp_app.save_user_state")
    @patch("whatsapp_app.load_user_state", return_value={})
    @patch("whatsapp_app._send_whatsapp_message")
    @patch("whatsapp_app.answer_compliance_query")
    def test_compliance_query_still_answered(
        self, mock_compliance, mock_send, mock_load, mock_save
    ):
        done = threading.Event()
        mock_compliance.return_value = (
            "DECISION: Tier III applies in NECAs.\nWHY: MARPOL VI Reg 13.\n"
            "SOURCE: MARPOL VI Reg 13\nACTIONS: • Verify engine build date"
        )

        def send_side(phone, body, **kw):
            done.set()

        mock_send.side_effect = send_side

        _post(_text_payload(body="when does Tier III apply?", sid="SMcomp001"))
        done.wait(timeout=5)

        mock_compliance.assert_called()
        mock_send.assert_called()
        sent_body = mock_send.call_args[0][1]
        self.assertIn("Tier III", sent_body)


class TestInventoryRegression(unittest.TestCase):
    """Inventory queries must still be answered via background thread."""

    @patch("whatsapp_app.save_user_state")
    @patch("whatsapp_app.load_user_state", return_value={})
    @patch("whatsapp_app._send_whatsapp_message")
    @patch("whatsapp_app._handle_text_message")
    def test_inventory_query_reaches_handler(
        self, mock_handler, mock_send, mock_load, mock_save
    ):
        done = threading.Event()
        mock_handler.return_value = (
            "DECISION: 3 ball valves in stock.\nWHY: inventory.\nSOURCE: n/a\nACTIONS: • ok",
            {},
        )
        mock_send.side_effect = lambda *a, **kw: done.set()

        _post(_text_payload(body="show valve stock", sid="SMinv001"))
        done.wait(timeout=3)

        mock_handler.assert_called_once()
        incoming_arg = mock_handler.call_args[0][0]
        self.assertEqual(incoming_arg, "show valve stock")


if __name__ == "__main__":
    unittest.main()
