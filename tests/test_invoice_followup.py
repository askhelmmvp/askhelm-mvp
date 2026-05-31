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


_IYS_QUOTE_DOC = {
    "doc_type": "quote",
    "supplier_name": "International Yacht Services B.V.",
    "total": 1038.18,
    "currency": "EUR",
    "subtotal": 858.0,
    "tax": 180.18,
    "line_items": [
        {
            "description": "Trac Barnacle Buster Concentrate, biodegradable marine growth remover, 5-gallon pail",
            "unit_rate": 429.00,
            "quantity": 2.0,
            "line_total": 858.00,
        }
    ],
}

_IYS_SINGLE_UNIT_DOC = {
    "doc_type": "quote",
    "supplier_name": "International Yacht Services B.V.",
    "total": 519.09,
    "currency": "EUR",
    "subtotal": 429.0,
    "tax": 90.09,
    "line_items": [
        {
            "description": "Trac Barnacle Buster Concentrate, biodegradable marine growth remover, 5-gallon pail",
            "unit_rate": 429.00,
            "quantity": 1.0,
            "line_total": 429.00,
        }
    ],
}


def _state_with_quote(doc=None):
    return {
        "user_id": "",
        "documents": [doc or _IYS_QUOTE_DOC],
        "pending_invoice": None,
        "last_context": {},
        "sessions": [],
        "active_session_id": None,
    }


class TestDocumentContextUnitPrice(unittest.TestCase):
    """_build_document_context must show unit price × qty for multi-unit items."""

    def test_multi_unit_shows_qty_and_unit_price(self):
        from whatsapp_app import _build_document_context
        ctx = _build_document_context(_state_with_quote())
        self.assertIn("qty 2", ctx)
        self.assertIn("429.00", ctx)
        self.assertIn("858.00", ctx)

    def test_multi_unit_does_not_show_line_total_as_bare_rate(self):
        # Old format was "(858.0 EUR)" — that must not appear
        from whatsapp_app import _build_document_context
        ctx = _build_document_context(_state_with_quote())
        self.assertNotIn("(858", ctx)

    def test_single_unit_shows_rate_without_qty_label(self):
        from whatsapp_app import _build_document_context
        ctx = _build_document_context(_state_with_quote(_IYS_SINGLE_UNIT_DOC))
        self.assertIn("429", ctx)
        self.assertNotIn("qty", ctx.lower())

    def test_item_without_quantity_shows_rate_directly(self):
        doc = {
            **_IYS_QUOTE_DOC,
            "line_items": [{"description": "Shipping", "line_total": 45.00}],
        }
        from whatsapp_app import _build_document_context
        ctx = _build_document_context(_state_with_quote(doc))
        self.assertIn("45", ctx)
        self.assertNotIn("qty", ctx.lower())


class TestUnitPriceNoteInjection(unittest.TestCase):
    """_enrich_query_with_calculations emits UNIT PRICE NOTE for qty × unit_price context."""

    _MULTI_UNIT_QUERY = (
        "Uploaded document: quote from International Yacht Services B.V.\n"
        "Items: Trac Barnacle Buster Concentrate, 5-gallon pail "
        "(qty 2 × 429.00 EUR = 858.00 EUR)\n"
        "Subtotal: 858.00 EUR\nTax: 180.18 EUR\nTotal: 1038.18 EUR\n\n"
        "User question: is this a fair price?"
    )

    def test_unit_price_note_appears(self):
        from services.market_price_service import _enrich_query_with_calculations
        enriched = _enrich_query_with_calculations(self._MULTI_UNIT_QUERY)
        self.assertIn("UNIT PRICE NOTE", enriched)

    def test_unit_price_note_names_correct_unit_price(self):
        from services.market_price_service import _enrich_query_with_calculations
        enriched = _enrich_query_with_calculations(self._MULTI_UNIT_QUERY)
        self.assertIn("429.00 per unit", enriched)

    def test_unit_price_note_warns_against_line_total(self):
        from services.market_price_service import _enrich_query_with_calculations
        enriched = _enrich_query_with_calculations(self._MULTI_UNIT_QUERY)
        self.assertIn("do NOT treat the line total as the per-unit price", enriched)

    def test_single_unit_no_note(self):
        from services.market_price_service import _enrich_query_with_calculations
        query = (
            "Uploaded document: quote\n"
            "Items: Antifouling paint (429.00 EUR)\n"
            "Total: 519.09 EUR\n\nUser question: is this fair?"
        )
        enriched = _enrich_query_with_calculations(query)
        self.assertNotIn("UNIT PRICE NOTE", enriched)


class TestFairPriceMultiUnitIntegration(unittest.TestCase):
    """End-to-end: 'is this a fair price?' with a multi-unit quote uses unit price."""

    _ACCEPTABLE = (
        "DECISION:\nACCEPTABLE PRICE\n\n"
        "WHY:\nEUR 429.00 per 5-gallon pail is within the marine supply range.\n\n"
        "RECOMMENDED ACTIONS:\n• Approve the unit price"
    )

    def test_fair_price_returns_acceptable_price(self):
        with patch("whatsapp_app.check_market_price", return_value=self._ACCEPTABLE):
            from whatsapp_app import _handle_text_message
            result, _ = _handle_text_message(
                "is this a fair price?",
                _state_with_quote(),
                "whatsapp:+44123456789",
            )
        self.assertIn("ACCEPTABLE PRICE", result)

    def test_market_check_receives_unit_price_in_context(self):
        captured = {}

        def capture(query, **kwargs):
            captured["query"] = query
            return self._ACCEPTABLE

        with patch("whatsapp_app.check_market_price", side_effect=capture):
            from whatsapp_app import _handle_text_message
            _handle_text_message(
                "is this a fair price?",
                _state_with_quote(),
                "whatsapp:+44123456789",
            )
        q = captured.get("query", "")
        self.assertIn("429", q)
        self.assertIn("qty 2", q)

    def test_clarification_2_pcs_reassesses_as_unit_price(self):
        state = _state_with_quote()
        state["last_context"] = {
            "type": "market_check",
            "topic": "is this a fair price?",
            "result": "DECISION:\nHIGH PRICE — QUERY\n\nWHY:\nEUR 858 looks high.\n\nRECOMMENDED ACTIONS:\n• Clarify",
        }
        with patch("whatsapp_app.check_market_price", return_value=self._ACCEPTABLE):
            from whatsapp_app import _handle_text_message
            result, _ = _handle_text_message(
                "2 pcs",
                state,
                "whatsapp:+44123456789",
            )
        self.assertIn("ACCEPTABLE PRICE", result)

    def test_clarification_it_is_for_2_pails_reassesses(self):
        state = _state_with_quote()
        state["last_context"] = {
            "type": "market_check",
            "topic": "is this a fair price?",
            "result": "DECISION:\nHIGH PRICE — QUERY\n\nWHY:\nEUR 858 looks high.\n\nRECOMMENDED ACTIONS:\n• Clarify",
        }
        with patch("whatsapp_app.check_market_price", return_value=self._ACCEPTABLE):
            from whatsapp_app import _handle_text_message
            result, _ = _handle_text_message(
                "it is for 2 pails",
                state,
                "whatsapp:+44123456789",
            )
        self.assertIn("ACCEPTABLE PRICE", result)

    def test_matched_quote_invoice_does_not_use_invoice_assessment(self):
        """When comparison_data is active, market_check must not route to invoice_approval_checks."""
        state = _state_with_quote()
        with patch("whatsapp_app.check_market_price", return_value=self._ACCEPTABLE) as mock_market, \
             patch("whatsapp_app.invoice_approval_checks") as mock_inv:
            from whatsapp_app import _handle_text_message
            _handle_text_message(
                "is this a fair price?",
                state,
                "whatsapp:+44123456789",
            )
        mock_market.assert_called_once()
        mock_inv.assert_not_called()

    def test_single_unit_context_no_unit_price_note_in_query(self):
        """Single-unit item: no 'qty' in document context, no UNIT PRICE NOTE injected."""
        captured = {}

        def capture(query, **kwargs):
            captured["query"] = query
            return self._ACCEPTABLE

        with patch("whatsapp_app.check_market_price", side_effect=capture):
            from whatsapp_app import _handle_text_message
            _handle_text_message(
                "is this a fair price?",
                _state_with_quote(_IYS_SINGLE_UNIT_DOC),
                "whatsapp:+44123456789",
            )
        q = captured.get("query", "")
        self.assertNotIn("qty", q.lower())
        self.assertNotIn("UNIT PRICE NOTE", q)


_SANDFIRDEN_INVOICE = {
    "doc_type": "invoice",
    "supplier_name": "Sandfirden Technics b.v.",
    "total": 761.57,
    "currency": "EUR",
    "line_items": [
        {"description": "4 × 246458 GASKET", "line_total": 187.20},
        {"description": "1 × PVKAAN FREIGHT COSTS WITH KAAN", "line_total": 75.00},
        {"description": "60 × 1921956 ANTIFREEZE/CORR. 50/50 20L", "line_total": 367.20},
        {"description": "Total excl. VAT / material", "line_total": 629.40},
        {"description": "21% VAT", "line_total": 132.17},
        {"description": "Total incl. VAT", "line_total": 761.57},
    ],
}


class TestVATReconciliation(unittest.TestCase):
    """_vat_reconciliation correctly identifies reconciled and unreconciled arithmetic."""

    def _reconcile(self, doc):
        from whatsapp_app import _vat_reconciliation
        return _vat_reconciliation(doc)

    def test_sandfirden_reconciles(self):
        excl, vat, ok = self._reconcile(_SANDFIRDEN_INVOICE)
        self.assertTrue(ok, f"629.40 + 132.17 = 761.57 should reconcile; got excl={excl}, vat={vat}")

    def test_sandfirden_excl_vat_correct(self):
        excl, _, _ = self._reconcile(_SANDFIRDEN_INVOICE)
        self.assertAlmostEqual(excl, 629.40, places=1)

    def test_sandfirden_vat_amount_correct(self):
        _, vat, _ = self._reconcile(_SANDFIRDEN_INVOICE)
        self.assertAlmostEqual(vat, 132.17, places=1)

    def test_genuine_discrepancy_not_reconciled(self):
        doc = {
            "total": 500.00,
            "currency": "EUR",
            "line_items": [
                {"description": "Part A", "line_total": 200.00},
                {"description": "Part B", "line_total": 200.00},
                {"description": "21% VAT", "line_total": 42.00},
                # 200 + 200 + 42 = 442 ≠ 500
            ],
        }
        _, _, ok = self._reconcile(doc)
        self.assertFalse(ok, "Genuine discrepancy must not reconcile")

    def test_no_vat_line_returns_none(self):
        doc = {
            "total": 434.00,
            "currency": "EUR",
            "line_items": [
                {"description": "Shore power — 1,240 kWh @ EUR 0.35", "line_total": 434.00},
            ],
        }
        excl, vat, ok = self._reconcile(doc)
        self.assertIsNone(excl)
        self.assertFalse(ok)


class TestSandfirdenInvoiceOnlyContext(unittest.TestCase):
    """_handle_invoice_clarification injects reconciliation note for Sandfirden invoice."""

    def test_reconciliation_note_in_context(self):
        captured = {}

        def capture(ctx, msg):
            captured["ctx"] = ctx
            return "DECISION:\nNO QUOTE — CHECK PRICE\n\nWHY:\nReconciled.\n\nACTIONS:\n• Confirm receipt"

        with patch("whatsapp_app.invoice_approval_checks", side_effect=capture):
            from whatsapp_app import _handle_invoice_clarification
            state = {
                "user_id": "",
                "documents": [_SANDFIRDEN_INVOICE],
                "pending_invoice": None,
            }
            _handle_invoice_clarification("no quote", state)
        ctx = captured.get("ctx", "")
        self.assertIn("Arithmetic reconciles", ctx)
        self.assertNotIn("discrepancy", ctx.lower())

    def test_freight_check_in_response(self):
        def make_response(ctx, msg):
            return (
                "DECISION:\nNO QUOTE — CHECK PRICE, FREIGHT AND VAT\n\n"
                "WHY:\nParts/consumables invoice. Arithmetic reconciles: "
                "629.40 EUR excl. VAT + 132.17 EUR VAT = 761.57 EUR total.\n\n"
                "ACTIONS:\n"
                "• Confirm gasket and antifreeze quantities received\n"
                "• Confirm 75.00 EUR freight charge via Kaan was agreed\n"
                "• Check unit pricing against order\n"
                "• Approve only once freight and receipt confirmed"
            )

        with patch("whatsapp_app.invoice_approval_checks", side_effect=make_response):
            from whatsapp_app import _handle_invoice_clarification
            state = {
                "user_id": "",
                "documents": [_SANDFIRDEN_INVOICE],
                "pending_invoice": None,
            }
            result = _handle_invoice_clarification("no quote", state)
        self.assertIn("freight", result.lower())
        self.assertNotIn("discrepancy", result.lower())

    def test_genuine_discrepancy_flagged_in_context(self):
        captured = {}

        def capture(ctx, msg):
            captured["ctx"] = ctx
            return "DECISION:\nINVESTIGATE\n\nWHY:\nDiscrepancy.\n\nACTIONS:\n• Check"

        discrepancy_doc = {
            "doc_type": "invoice",
            "supplier_name": "Test Supplier",
            "total": 500.00,
            "currency": "EUR",
            "line_items": [
                {"description": "Part A", "line_total": 200.00},
                {"description": "Part B", "line_total": 200.00},
                {"description": "21% VAT", "line_total": 42.00},
            ],
        }
        with patch("whatsapp_app.invoice_approval_checks", side_effect=capture):
            from whatsapp_app import _handle_invoice_clarification
            state = {
                "user_id": "",
                "documents": [discrepancy_doc],
                "pending_invoice": None,
            }
            _handle_invoice_clarification("no quote", state)
        ctx = captured.get("ctx", "")
        self.assertIn("Arithmetic discrepancy", ctx)

    def test_utility_invoice_no_reconciliation_note(self):
        """Shore power invoice with no VAT line does not get a reconciliation note."""
        captured = {}

        def capture(ctx, msg):
            captured["ctx"] = ctx
            return "DECISION:\nVALIDATE\n\nWHY:\nMetered.\n\nACTIONS:\n• Check kWh"

        utility_doc = {
            "doc_type": "invoice",
            "supplier_name": "Marina Barceloneta",
            "total": 434.00,
            "currency": "EUR",
            "line_items": [
                {"description": "Shore power — 1,240 kWh @ EUR 0.35", "line_total": 434.00},
            ],
        }
        with patch("whatsapp_app.invoice_approval_checks", side_effect=capture):
            from whatsapp_app import _handle_invoice_clarification
            state = {
                "user_id": "",
                "documents": [utility_doc],
                "pending_invoice": None,
            }
            _handle_invoice_clarification("no quote", state)
        ctx = captured.get("ctx", "")
        self.assertNotIn("Arithmetic reconciles", ctx)
        self.assertIn("utility", ctx.lower())


# ---------------------------------------------------------------------------
# ASK-39: quote follow-up stock check
# ---------------------------------------------------------------------------

import os
import tempfile


def _make_state(docs=None, user_id=""):
    return {"user_id": user_id, "documents": docs or []}


def _quote_doc(line_items, supplier="Test Supplier", doc_type="quote"):
    return {"doc_type": doc_type, "supplier_name": supplier, "line_items": line_items}


class TestQuoteStockCheckIntent(unittest.TestCase):
    """ASK-39: quote follow-up phrases must route to quote_stock_check."""

    def _cls(self, q):
        from domain.intent import classify_text
        return classify_text(q)

    def test_do_we_already_have_these_onboard(self):
        self.assertEqual(self._cls("do we already have these onboard?"), "quote_stock_check")

    def test_do_we_have_these_in_stock(self):
        self.assertEqual(self._cls("do we have these in stock?"), "quote_stock_check")

    def test_are_these_already_onboard(self):
        self.assertEqual(self._cls("are these already onboard?"), "quote_stock_check")

    def test_check_these_against_stock(self):
        self.assertEqual(self._cls("check these against stock"), "quote_stock_check")

    def test_check_this_against_stock(self):
        self.assertEqual(self._cls("check this against stock"), "quote_stock_check")

    def test_do_we_need_to_order_these(self):
        self.assertEqual(self._cls("do we need to order these?"), "quote_stock_check")

    def test_are_these_items_onboard(self):
        self.assertEqual(self._cls("are these items onboard?"), "quote_stock_check")

    def test_specific_part_stock_query_unaffected(self):
        self.assertEqual(self._cls("how many AIK111571 on board?"), "stock_query")

    def test_specific_part_procurement_unaffected(self):
        self.assertEqual(self._cls("do we need to order more AIK111571?"), "procurement_query")

    def test_spares_query_unaffected(self):
        self.assertEqual(self._cls("show HEM spares"), "spares_query")


class TestQuoteStockCheckNoDocument(unittest.TestCase):
    """ASK-39 test 1: no recent document → NO RECENT DOCUMENT."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.tmpdir
        import importlib, storage_paths, domain.inventory_store as inv_store
        importlib.reload(storage_paths)
        importlib.reload(inv_store)

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        import shutil, importlib, storage_paths, domain.inventory_store as inv_store
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        importlib.reload(storage_paths)
        importlib.reload(inv_store)

    def _check(self, state):
        from whatsapp_app import _handle_quote_stock_check
        r, _ = _handle_quote_stock_check(state)
        return r

    def test_empty_state_gives_no_recent_document(self):
        self.assertIn("NO RECENT DOCUMENT", self._check(_make_state()))

    def test_no_document_not_understood(self):
        self.assertNotIn("DOCUMENT NOT UNDERSTOOD", self._check(_make_state()))

    def test_helpful_action_included(self):
        self.assertIn("Upload a quote", self._check(_make_state()))

    def test_inventory_doc_not_used(self):
        state = _make_state(docs=[{
            "doc_type": "inventory",
            "line_items": [{"description": "Impeller", "part_number": "AIK111571"}],
        }])
        self.assertIn("NO RECENT DOCUMENT", self._check(state))


class TestQuoteStockCheckWithKnownItem(unittest.TestCase):
    """ASK-39 test 2: quote containing a stocked part → STOCK CHECK COMPLETE."""

    _ITEMS = [{
        "description": "Impeller",
        "part_number": "AIK111571",
        "quantity_onboard": 8.0,
        "storage_location": "TD / Tech 2 / Fresh Water System Box 1",
        "make": "Jabsco",
        "confidence": 0.9,
    }]

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.tmpdir
        import importlib, storage_paths, domain.inventory_store as inv_store
        importlib.reload(storage_paths)
        importlib.reload(inv_store)
        inv_store.merge_stock("", self._ITEMS, "test.csv")

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        import shutil, importlib, storage_paths, domain.inventory_store as inv_store
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        importlib.reload(storage_paths)
        importlib.reload(inv_store)

    def _state_with_quote(self):
        return _make_state(docs=[_quote_doc([
            {"description": "Impeller", "part_number": "AIK111571", "quantity": 2},
        ])])

    def _check(self, state):
        from whatsapp_app import _handle_quote_stock_check
        r, _ = _handle_quote_stock_check(state)
        return r

    def test_decision_stock_check_complete(self):
        self.assertIn("STOCK CHECK COMPLETE", self._check(self._state_with_quote()))

    def test_stock_matches_section(self):
        self.assertIn("STOCK MATCHES:", self._check(self._state_with_quote()))

    def test_aik111571_in_matches(self):
        self.assertIn("AIK111571", self._check(self._state_with_quote()))

    def test_quantity_shown(self):
        self.assertIn("Qty 8", self._check(self._state_with_quote()))

    def test_location_shown(self):
        self.assertIn("TD / Tech 2 / Fresh Water System Box 1", self._check(self._state_with_quote()))

    def test_no_document_not_understood(self):
        self.assertNotIn("DOCUMENT NOT UNDERSTOOD", self._check(self._state_with_quote()))

    def test_invoice_doc_type_also_works(self):
        state = _make_state(docs=[_quote_doc(
            [{"description": "Impeller", "part_number": "AIK111571"}], doc_type="invoice"
        )])
        self.assertIn("STOCK CHECK COMPLETE", self._check(state))


class TestQuoteStockCheckMixedItems(unittest.TestCase):
    """ASK-39 test 3: mixed quote — some matched, some not found."""

    _ITEMS = [{
        "description": "Impeller",
        "part_number": "AIK111571",
        "quantity_onboard": 8.0,
        "storage_location": "TD / Tech 2 / Fresh Water System Box 1",
        "make": "Jabsco",
        "confidence": 0.9,
    }]

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.tmpdir
        import importlib, storage_paths, domain.inventory_store as inv_store
        importlib.reload(storage_paths)
        importlib.reload(inv_store)
        inv_store.merge_stock("", self._ITEMS, "test.csv")

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        import shutil, importlib, storage_paths, domain.inventory_store as inv_store
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        importlib.reload(storage_paths)
        importlib.reload(inv_store)

    def _state(self):
        return _make_state(docs=[_quote_doc([
            {"description": "Impeller", "part_number": "AIK111571", "quantity": 2},
            {"description": "Seal kit", "part_number": "UNKNOWN999", "quantity": 1},
        ])])

    def _check(self):
        from whatsapp_app import _handle_quote_stock_check
        r, _ = _handle_quote_stock_check(self._state())
        return r

    def test_matched_item_in_stock_matches(self):
        r = self._check()
        self.assertIn("STOCK MATCHES:", r)
        self.assertIn("AIK111571", r)

    def test_unknown_item_in_not_found(self):
        r = self._check()
        self.assertIn("NOT FOUND:", r)
        self.assertIn("UNKNOWN999", r)

    def test_no_hallucinated_qty_for_unmatched(self):
        r = self._check()
        lines = r.splitlines()
        unknown_line = next((l for l in lines if "UNKNOWN999" in l), "")
        self.assertNotIn("Qty", unknown_line)

    def test_actions_present(self):
        self.assertIn("ACTIONS:", self._check())


class TestQuoteStockCheckDispatchRegression(unittest.TestCase):
    """ASK-39 test 4+5: direct stock and spares queries still work."""

    _ITEMS = [{
        "description": "Impeller",
        "part_number": "AIK111571",
        "quantity_onboard": 8.0,
        "storage_location": "TD / Tech 2 / Fresh Water System Box 1",
        "make": "Jabsco",
        "confidence": 0.9,
    }]

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.tmpdir
        import importlib, storage_paths, domain.inventory_store as inv_store
        importlib.reload(storage_paths)
        importlib.reload(inv_store)
        inv_store.merge_stock("", self._ITEMS, "test.csv")

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        import shutil, importlib, storage_paths, domain.inventory_store as inv_store
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        importlib.reload(storage_paths)
        importlib.reload(inv_store)

    def _dispatch(self, query, state=None):
        from whatsapp_app import _handle_text_message
        r, _ = _handle_text_message(query, state or _make_state())
        return r

    def test_no_document_returns_no_recent_document(self):
        r = self._dispatch("do we already have these onboard?")
        self.assertIn("NO RECENT DOCUMENT", r)
        self.assertNotIn("DOCUMENT NOT UNDERSTOOD", r)

    def test_with_quote_returns_stock_check_complete(self):
        state = _make_state(docs=[_quote_doc([
            {"description": "Impeller", "part_number": "AIK111571"},
        ])])
        r = self._dispatch("do we already have these onboard?", state)
        self.assertIn("STOCK CHECK COMPLETE", r)

    def test_direct_stock_query_regression(self):
        r = self._dispatch("how many AIK111571 on board?")
        self.assertIn("8 ONBOARD", r)
        self.assertNotIn("DOCUMENT NOT UNDERSTOOD", r)


if __name__ == "__main__":
    unittest.main()
