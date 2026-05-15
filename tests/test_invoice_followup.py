"""Tests for invoice follow-up clarification flow."""
import unittest
from unittest.mock import patch, MagicMock


_INVOICE_DOC = {
    "doc_type": "invoice",
    "supplier_name": "Barcelona Refit S.L.",
    "total": 42500.0,
    "currency": "EUR",
    "line_items": [
        {"description": "Shore power connection", "line_total": 2500.0},
        {"description": "Labour — 850 hours @ EUR 45", "line_total": 38250.0},
    ],
}

_PROFORMA_DOC = {
    "doc_type": "proforma",
    "supplier_name": "Marine Systems Ltd",
    "total": 12000.0,
    "currency": "EUR",
    "line_items": [],
}


def _state_with_invoice(doc=None):
    return {
        "user_id": "",
        "documents": [doc or _INVOICE_DOC],
        "pending_invoice": None,
        "last_context": {},
    }


def _state_with_pending(doc=None):
    return {
        "user_id": "",
        "documents": [],
        "pending_invoice": {"doc_record": doc or _INVOICE_DOC},
        "last_context": {},
    }


class TestInvoiceClarificationIntent(unittest.TestCase):
    """classify_text must return invoice_clarification for known phrases."""

    def test_no_quote_phrase_routes_to_invoice_clarification(self):
        from domain.intent import classify_text
        self.assertEqual(classify_text("there is no quote"), "invoice_clarification")

    def test_final_instalment_routes(self):
        from domain.intent import classify_text
        self.assertEqual(classify_text("this is a final instalment invoice"), "invoice_clarification")

    def test_consumption_invoice_routes(self):
        from domain.intent import classify_text
        self.assertEqual(classify_text("it's a consumption invoice for shore power"), "invoice_clarification")

    def test_refit_agreement_routes(self):
        from domain.intent import classify_text
        self.assertEqual(classify_text("payment against the refit agreement"), "invoice_clarification")

    def test_unrelated_message_does_not_route(self):
        from domain.intent import classify_text
        result = classify_text("what is the market price for an oil filter?")
        self.assertNotEqual(result, "invoice_clarification")


class TestHandleInvoiceClarification(unittest.TestCase):
    """_handle_invoice_clarification must build context from stored invoice doc."""

    def _mock_approval(self, *args, **kwargs):
        return (
            "DECISION:\nVALIDATE AGAINST AGREEMENT\n\n"
            "WHY:\nThis is an agreed instalment invoice — no quote comparison required.\n\n"
            "ACTIONS:\n• Verify labour hours match agreed schedule\n• Confirm rate"
        )

    def test_uses_invoice_from_documents(self):
        with patch("whatsapp_app.invoice_approval_checks", side_effect=self._mock_approval) as mock_fn:
            from whatsapp_app import _handle_invoice_clarification
            state = _state_with_invoice()
            result = _handle_invoice_clarification("there is no quote, this is a final instalment", state)
        mock_fn.assert_called_once()
        ctx_arg = mock_fn.call_args[0][0]
        self.assertIn("Barcelona Refit", ctx_arg)
        self.assertIn("42500", ctx_arg)

    def test_uses_pending_invoice_when_present(self):
        with patch("whatsapp_app.invoice_approval_checks", side_effect=self._mock_approval) as mock_fn:
            from whatsapp_app import _handle_invoice_clarification
            state = _state_with_pending()
            _handle_invoice_clarification("it is an instalment, not a quote", state)
        ctx_arg = mock_fn.call_args[0][0]
        self.assertIn("Barcelona Refit", ctx_arg)

    def test_returns_llm_response(self):
        with patch("whatsapp_app.invoice_approval_checks", side_effect=self._mock_approval):
            from whatsapp_app import _handle_invoice_clarification
            result = _handle_invoice_clarification("no quote", _state_with_invoice())
        self.assertIn("VALIDATE AGAINST AGREEMENT", result)

    def test_no_invoice_context_still_calls_llm(self):
        with patch("whatsapp_app.invoice_approval_checks", side_effect=self._mock_approval) as mock_fn:
            from whatsapp_app import _handle_invoice_clarification
            empty_state = {"user_id": "", "documents": [], "pending_invoice": None}
            _handle_invoice_clarification("no quote available", empty_state)
        mock_fn.assert_called_once()


class TestInvoiceClarificationRouting(unittest.TestCase):
    """_handle_text_message must route invoice_clarification intent correctly."""

    def _make_approval_response(self):
        return (
            "DECISION:\nVALIDATE AGAINST AGREEMENT\n\n"
            "WHY:\nInstalment invoice — verify against agreed schedule.\n\n"
            "ACTIONS:\n• Check hours\n• Confirm rate"
        )

    def test_invoice_clarification_intent_with_invoice_doc(self):
        with patch("whatsapp_app.invoice_approval_checks",
                   return_value=self._make_approval_response()):
            from whatsapp_app import _handle_text_message
            state = _state_with_invoice()
            result, _ = _handle_text_message(
                "there is no quote — this is a final instalment invoice",
                state,
                "whatsapp:+44123456789",
            )
        self.assertIn("VALIDATE AGAINST AGREEMENT", result)

    def test_unknown_intent_with_invoice_context_falls_through_to_clarification(self):
        with patch("whatsapp_app.invoice_approval_checks",
                   return_value=self._make_approval_response()):
            from whatsapp_app import _handle_text_message
            state = _state_with_invoice()
            result, _ = _handle_text_message(
                "this invoice covers the agreed shore power connection",
                state,
                "whatsapp:+44123456789",
            )
        self.assertIn("VALIDATE AGAINST AGREEMENT", result)
        self.assertNotIn("DOCUMENT NOT UNDERSTOOD", result)


class TestPromptCaching(unittest.TestCase):
    """All Claude API calls must include cache_control on the system prompt block."""

    def _system_is_cached(self, call_kwargs):
        system = call_kwargs.get("system")
        if isinstance(system, list):
            return any(b.get("cache_control", {}).get("type") == "ephemeral" for b in system)
        return False

    def test_compliance_answer_has_cache_control(self):
        with patch("services.anthropic_service.client") as mock_client:
            mock_client.messages.create.return_value = MagicMock(
                content=[MagicMock(text="DECISION: Yes\nWHY: reason\nSOURCE: reg\nACTIONS: • do it")]
            )
            from services.anthropic_service import answer_compliance_question
            answer_compliance_question("test question", [{"source_reference": "LYC", "content": "text"}])
        kwargs = mock_client.messages.create.call_args[1]
        self.assertTrue(self._system_is_cached(kwargs), "compliance answer must use cache_control")

    def test_market_price_check_has_cache_control(self):
        with patch("services.market_price_service.client") as mock_client:
            mock_client.messages.create.return_value = MagicMock(
                content=[MagicMock(text="DECISION:\nACCEPTABLE PRICE\n\nWHY:\nFair market rate.\n\nRECOMMENDED ACTIONS:\n• Proceed")]
            )
            from services.market_price_service import check_market_price
            check_market_price("oil filter MAN-123 EUR 45")
        kwargs = mock_client.messages.create.call_args[1]
        self.assertTrue(self._system_is_cached(kwargs), "market price check must use cache_control")

    def test_invoice_approval_checks_has_cache_control(self):
        with patch("services.market_price_service.client") as mock_client:
            mock_client.messages.create.return_value = MagicMock(
                content=[MagicMock(text="DECISION:\nVALIDATE AGAINST AGREEMENT\n\nWHY:\nInstalment.\n\nACTIONS:\n• Check")]
            )
            from services.market_price_service import invoice_approval_checks
            invoice_approval_checks("Invoice from Yard", "no quote")
        kwargs = mock_client.messages.create.call_args[1]
        self.assertTrue(self._system_is_cached(kwargs), "invoice approval must use cache_control")


class TestInvoiceSelfAssessmentDetection(unittest.TestCase):
    """_is_invoice_self_assessment identifies queries about the active invoice."""

    def _check(self, text):
        from whatsapp_app import _is_invoice_self_assessment
        return _is_invoice_self_assessment(text)

    def test_is_this_fair(self):
        self.assertTrue(self._check("is this fair?"))

    def test_is_this_a_fair_cost(self):
        self.assertTrue(self._check("Is this a fair cost?"))

    def test_is_this_reasonable(self):
        self.assertTrue(self._check("Is this reasonable for a refit?"))

    def test_assess_this(self):
        self.assertTrue(self._check("assess this invoice"))

    def test_summarise_and_assess(self):
        self.assertTrue(self._check("summarise and assess risk"))

    def test_named_part_not_self_assessment(self):
        self.assertFalse(self._check("what should a Volvo IPS 600 service cost?"))

    def test_market_price_for_item_not_self_assessment(self):
        self.assertFalse(self._check("market price for hydraulic pump seal kit"))


class TestUtilityInvoiceDetection(unittest.TestCase):
    """_is_utility_invoice detects shore power / metered service invoices."""

    def _check(self, doc):
        from whatsapp_app import _is_utility_invoice
        return _is_utility_invoice(doc)

    def _doc(self, *descs):
        return {"line_items": [{"description": d} for d in descs]}

    def test_kwh_line_item_detected(self):
        self.assertTrue(self._check(self._doc("Shore power — 1,240 kWh @ EUR 0.35")))

    def test_shore_power_detected(self):
        self.assertTrue(self._check(self._doc("Shore power connection fee")))

    def test_docking_period_detected(self):
        self.assertTrue(self._check(self._doc("Docking period berth charge")))

    def test_ev_charging_detected(self):
        self.assertTrue(self._check(self._doc("EV charging station usage")))

    def test_regular_engineering_invoice_not_detected(self):
        self.assertFalse(self._check(self._doc("Pump seal kit", "Labour 4 hours")))

    def test_empty_doc_not_detected(self):
        self.assertFalse(self._check({}))


class TestMarketCheckInvoiceIntercept(unittest.TestCase):
    """When pending invoice exists, self-referential market_check routes to invoice assessment."""

    _APPROVAL_RESPONSE = (
        "DECISION:\nVALIDATE AGAINST AGREEMENT\n\n"
        "WHY:\nThis is an agreement-based invoice.\n\n"
        "ACTIONS:\n• Verify rates"
    )

    def _state_with_pending(self):
        return {
            "user_id": "",
            "documents": [],
            "pending_invoice": {"doc_record": _INVOICE_DOC},
            "last_context": {},
        }

    def test_is_this_fair_with_pending_invoice_routes_to_clarification(self):
        with patch("whatsapp_app.invoice_approval_checks",
                   return_value=self._APPROVAL_RESPONSE):
            from whatsapp_app import _handle_text_message
            state = self._state_with_pending()
            result, _ = _handle_text_message(
                "is this a fair cost?",
                state,
                "whatsapp:+44123456789",
            )
        self.assertIn("VALIDATE AGAINST AGREEMENT", result)
        self.assertNotIn("INSUFFICIENT DATA", result)

    def test_assess_this_with_pending_invoice_routes_to_clarification(self):
        with patch("whatsapp_app.invoice_approval_checks",
                   return_value=self._APPROVAL_RESPONSE):
            from whatsapp_app import _handle_text_message
            state = self._state_with_pending()
            result, _ = _handle_text_message(
                "assess this",
                state,
                "whatsapp:+44123456789",
            )
        self.assertIn("VALIDATE AGAINST AGREEMENT", result)

    def test_named_part_market_check_not_intercepted(self):
        with patch("whatsapp_app.check_market_price",
                   return_value="DECISION:\nACCEPTABLE\n\nWHY:\nFair.\n\nRECOMMENDED ACTIONS:\n• Proceed"):
            from whatsapp_app import _handle_text_message
            state = self._state_with_pending()
            result, _ = _handle_text_message(
                "what should a hydraulic pump seal cost?",
                state,
                "whatsapp:+44123456789",
            )
        self.assertNotIn("VALIDATE AGAINST AGREEMENT", result)


class TestUtilityInvoiceContextHint(unittest.TestCase):
    """_handle_invoice_clarification adds utility note when invoice is metered."""

    _UTILITY_INVOICE = {
        "doc_type": "invoice",
        "supplier_name": "Marina Barceloneta",
        "total": 434.00,
        "currency": "EUR",
        "line_items": [
            {"description": "Shore power — 1,240 kWh @ EUR 0.35", "line_total": 434.00},
        ],
    }

    def test_utility_hint_added_to_context(self):
        captured = {}

        def capture_approval(ctx, msg):
            captured["ctx"] = ctx
            return "DECISION:\nVALIDATE\n\nWHY:\nMetered.\n\nACTIONS:\n• Check kWh"

        with patch("whatsapp_app.invoice_approval_checks", side_effect=capture_approval):
            from whatsapp_app import _handle_invoice_clarification
            state = {
                "user_id": "",
                "documents": [self._UTILITY_INVOICE],
                "pending_invoice": None,
            }
            _handle_invoice_clarification("no quote", state)
        self.assertIn("utility", captured["ctx"].lower())

    def test_non_utility_invoice_has_no_hint(self):
        captured = {}

        def capture_approval(ctx, msg):
            captured["ctx"] = ctx
            return "DECISION:\nVALIDATE\n\nWHY:\nInstalment.\n\nACTIONS:\n• Check"

        labour_only_doc = {
            "doc_type": "invoice",
            "supplier_name": "Palma Rigging Co.",
            "total": 3600.0,
            "currency": "EUR",
            "line_items": [
                {"description": "Labour — 80 hours rigging maintenance", "line_total": 3600.0},
            ],
        }
        with patch("whatsapp_app.invoice_approval_checks", side_effect=capture_approval):
            from whatsapp_app import _handle_invoice_clarification
            state = {
                "user_id": "",
                "documents": [labour_only_doc],
                "pending_invoice": None,
            }
            _handle_invoice_clarification("no quote", state)
        self.assertNotIn("utility", captured.get("ctx", "").lower())


class TestInvoiceReceivedActions(unittest.TestCase):
    """_invoice_pending_fallback sends updated action suggestions."""

    def test_fallback_message_includes_no_quote_option(self):
        import threading
        from unittest.mock import patch, MagicMock
        state = {
            "user_id": "u1",
            "pending_invoice": {
                "doc_record": {
                    "fingerprint": "fp1",
                    "supplier_name": "Test Yard",
                    "total": 5000,
                    "currency": "EUR",
                }
            },
        }
        sent_messages = []
        with patch("whatsapp_app.load_user_state", return_value=state), \
             patch("whatsapp_app._send_whatsapp_message",
                   side_effect=lambda ph, msg: sent_messages.append(msg)), \
             patch("whatsapp_app.time.sleep"):
            from whatsapp_app import _invoice_pending_fallback
            _invoice_pending_fallback("u1", "+44123456789", "fp1")
        self.assertTrue(sent_messages, "fallback must send a message")
        body = sent_messages[0]
        self.assertIn("no quote", body.lower())
        self.assertIn("fair cost", body.lower())


if __name__ == "__main__":
    unittest.main()
