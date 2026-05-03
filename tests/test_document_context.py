"""
Tests for document-context market_check routing.
Covers: intent classification fixes, document context building,
vague-reference detection, and the full upload → price-query flow.
"""
import unittest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_market_response(text="DECISION:\nBroad estimate only\n\nWHY:\nTest.\n\nACTIONS:\n• Detail"):
    msg = MagicMock()
    msg.content = [MagicMock(text=text)]
    return msg


def _state_with_quote(
    supplier="Acme Marine Ltd",
    total=1500.0,
    currency="EUR",
    items=None,
):
    doc = {
        "document_id": "doc-q1",
        "doc_type": "quote",
        "supplier_name": supplier,
        "document_number": "QT-001",
        "document_date": "2024-01-10",
        "currency": currency,
        "total": total,
        "subtotal": None,
        "tax": None,
        "line_items": items or [
            {"description": "Windlass service", "quantity": 1, "unit_rate": 800.0, "line_total": 800.0},
            {"description": "Anchor chain inspection", "quantity": 1, "unit_rate": 400.0, "line_total": 400.0},
            {"description": "Labour (4 hrs)", "quantity": 4, "unit_rate": 75.0, "line_total": 300.0},
        ],
        "exclusions": [],
        "assumptions": [],
        "fingerprint": "fp1",
        "status": "in_session",
        "uploaded_at": "2024-01-10T00:00:00+00:00",
        "session_id": "sess-1",
    }
    session = {
        "session_id": "sess-1",
        "session_type": "pending",
        "status": "active",
        "document_ids": ["doc-q1"],
        "anchor_doc_id": "doc-q1",
        "created_at": "2024-01-10T00:00:00+00:00",
        "updated_at": "2024-01-10T00:00:00+00:00",
        "last_comparison": None,
    }
    return {
        "sessions": [session],
        "documents": [doc],
        "active_session_id": "sess-1",
    }


def _empty_state():
    return {"sessions": [], "documents": [], "active_session_id": None}


# ---------------------------------------------------------------------------
# Intent classification: new phrases
# ---------------------------------------------------------------------------

class TestNewMarketCheckPhrases(unittest.TestCase):

    def _cls(self, text):
        from domain.intent import classify_text
        return classify_text(text)

    def test_rough_price_is_market_check(self):
        self.assertEqual(self._cls("rough price for this"), "market_check")

    def test_rough_price_question_is_market_check(self):
        self.assertEqual(self._cls("rough price for this?"), "market_check")

    def test_is_this_expensive_is_market_check(self):
        self.assertEqual(self._cls("is this expensive"), "market_check")

    def test_is_that_expensive_is_market_check(self):
        self.assertEqual(self._cls("is that expensive"), "market_check")

    def test_are_these_expensive_is_market_check(self):
        self.assertEqual(self._cls("are these expensive"), "market_check")

    def test_how_much_is_this_is_market_check(self):
        self.assertEqual(self._cls("how much is this"), "market_check")

    def test_how_much_are_these_is_market_check(self):
        self.assertEqual(self._cls("how much are these"), "market_check")

    def test_give_me_a_rough_price_for_this_is_followup(self):
        # Contains "give me a rough" substring → market_check_followup
        self.assertEqual(self._cls("give me a rough price for this"), "market_check_followup")

    def test_give_me_a_rough_price_for_this_question_is_followup(self):
        self.assertEqual(self._cls("give me a rough price for this?"), "market_check_followup")

    def test_how_much_should_this_be_is_market_check(self):
        self.assertEqual(self._cls("how much should this be"), "market_check")

    def test_what_should_this_cost_is_market_check(self):
        self.assertEqual(self._cls("what should this cost"), "market_check")

    def test_is_this_expensive_not_compliance(self):
        # Must NOT route to compliance
        result = self._cls("is this expensive")
        self.assertNotEqual(result, "compliance_question")

    def test_compliance_expensive_phrases_still_work(self):
        # Phrases with compliance substrings should still route correctly
        self.assertEqual(self._cls("is this compliant"), "compliance_question")
        self.assertEqual(self._cls("is marpol required here"), "compliance_question")


# ---------------------------------------------------------------------------
# _has_vague_document_reference
# ---------------------------------------------------------------------------

class TestHasVagueDocumentReference(unittest.TestCase):

    def _check(self, query):
        from whatsapp_app import _has_vague_document_reference
        return _has_vague_document_reference(query)

    def test_this_detected(self):
        self.assertTrue(self._check("rough price for this"))

    def test_this_with_question_mark(self):
        self.assertTrue(self._check("is this expensive?"))

    def test_these_detected(self):
        self.assertTrue(self._check("are these reasonable?"))

    def test_it_detected(self):
        self.assertTrue(self._check("is it overpriced"))

    def test_them_detected(self):
        self.assertTrue(self._check("how much for them"))

    def test_no_vague_ref_specific_query(self):
        self.assertFalse(self._check("how much for a yanmar impeller"))

    def test_no_vague_ref_part_number(self):
        self.assertFalse(self._check("yanmar 196350-04061 price"))


# ---------------------------------------------------------------------------
# _build_document_context
# ---------------------------------------------------------------------------

class TestBuildDocumentContext(unittest.TestCase):

    def _ctx(self, state):
        from whatsapp_app import _build_document_context
        return _build_document_context(state)

    def test_returns_empty_when_no_documents(self):
        self.assertEqual(self._ctx(_empty_state()), "")

    def test_includes_supplier(self):
        result = self._ctx(_state_with_quote(supplier="Acme Marine Ltd"))
        self.assertIn("Acme Marine Ltd", result)

    def test_includes_doc_type(self):
        result = self._ctx(_state_with_quote())
        self.assertIn("quote", result.lower())

    def test_includes_total(self):
        result = self._ctx(_state_with_quote(total=1500.0, currency="EUR"))
        self.assertIn("1500", result)
        self.assertIn("EUR", result)

    def test_includes_line_items(self):
        result = self._ctx(_state_with_quote())
        self.assertIn("Windlass service", result)
        self.assertIn("Anchor chain inspection", result)

    def test_includes_item_prices(self):
        result = self._ctx(_state_with_quote())
        self.assertIn("800", result)  # windlass service line_total

    def test_caps_at_six_items(self):
        state = _state_with_quote(items=[
            {"description": f"Item {i}", "line_total": float(i * 100)}
            for i in range(1, 10)  # 9 items
        ])
        result = self._ctx(state)
        self.assertIn("Item 6", result)
        self.assertNotIn("Item 7", result)

    def test_handles_missing_supplier(self):
        state = _state_with_quote(supplier="")
        result = self._ctx(state)
        self.assertIn("quote", result.lower())  # still includes doc type


# ---------------------------------------------------------------------------
# _enrich_with_doc_context
# ---------------------------------------------------------------------------

class TestEnrichWithDocContext(unittest.TestCase):

    def _enrich(self, query, state):
        from whatsapp_app import _enrich_with_doc_context
        return _enrich_with_doc_context(query, state)

    def test_enriches_vague_query_with_document(self):
        result = self._enrich("is this expensive?", _state_with_quote())
        self.assertIn("Acme Marine Ltd", result)
        self.assertIn("is this expensive", result)

    def test_no_enrichment_for_specific_query(self):
        query = "how much for a yanmar impeller"
        result = self._enrich(query, _state_with_quote())
        self.assertEqual(result, query)

    def test_no_enrichment_when_no_document(self):
        query = "is this expensive?"
        result = self._enrich(query, _empty_state())
        self.assertEqual(result, query)

    def test_enriched_query_contains_user_question_label(self):
        result = self._enrich("rough price for this?", _state_with_quote())
        self.assertIn("User question:", result)


# ---------------------------------------------------------------------------
# Routing: market_check_followup with document context (no market_check history)
# ---------------------------------------------------------------------------

class TestDocumentContextRouting(unittest.TestCase):

    @patch("whatsapp_app.check_market_price")
    def test_followup_after_upload_routes_to_market_check(self, mock_check):
        """
        Upload quote → ask 'give me a rough price for this?' (no prior market_check context)
        → check_market_price called with document context, not TEXT RECEIVED.
        """
        mock_check.return_value = "DECISION:\nBroad estimate only\n\nWHY:\nTest.\n\nACTIONS:\n• Detail"
        from whatsapp_app import _handle_text_message
        answer, state = _handle_text_message(
            "give me a rough price for this?",
            _state_with_quote(),
        )
        self.assertTrue(mock_check.called, "check_market_price must be called")
        self.assertNotIn("TEXT RECEIVED", answer)

    @patch("whatsapp_app.check_market_price")
    def test_followup_query_passed_to_market_check_contains_doc_context(self, mock_check):
        """The query sent to check_market_price includes supplier and items from the document."""
        mock_check.return_value = "DECISION:\nBroad estimate only\n\nWHY:\nTest.\n\nACTIONS:\n• Detail"
        from whatsapp_app import _handle_text_message
        _handle_text_message("give me a rough price for this?", _state_with_quote())
        call_query = mock_check.call_args[0][0]
        self.assertIn("Acme Marine Ltd", call_query)
        self.assertIn("Windlass service", call_query)

    @patch("whatsapp_app.check_market_price")
    def test_followup_without_document_returns_text_received(self, mock_check):
        """'give me a rough price for this?' with no document → TEXT RECEIVED."""
        from whatsapp_app import _handle_text_message
        answer, _ = _handle_text_message("give me a rough price for this?", _empty_state())
        self.assertFalse(mock_check.called)
        self.assertIn("DOCUMENT NOT UNDERSTOOD", answer)

    @patch("whatsapp_app.check_market_price")
    def test_followup_sets_market_check_last_context(self, mock_check):
        mock_check.return_value = "DECISION:\nBroad estimate only\n\nWHY:\nTest.\n\nACTIONS:\n• Detail"
        from whatsapp_app import _handle_text_message
        _, state = _handle_text_message(
            "give me a rough price for this?",
            _state_with_quote(),
        )
        self.assertEqual(state.get("last_context", {}).get("type"), "market_check")

    @patch("whatsapp_app.check_market_price")
    def test_followup_passes_allow_broad_estimate_true(self, mock_check):
        """Document-context followup should pass allow_broad_estimate=True."""
        mock_check.return_value = "DECISION:\nBroad estimate only\n\nWHY:\nTest.\n\nACTIONS:\n• Detail"
        from whatsapp_app import _handle_text_message
        _handle_text_message("give me a rough price for this?", _state_with_quote())
        call_kwargs = mock_check.call_args[1]
        self.assertTrue(
            call_kwargs.get("allow_broad_estimate"),
            "allow_broad_estimate must be True for document-context followup",
        )


# ---------------------------------------------------------------------------
# Routing: market_check with vague reference enriched from document
# ---------------------------------------------------------------------------

class TestMarketCheckVagueReferenceEnrichment(unittest.TestCase):

    @patch("whatsapp_app.check_market_price")
    def test_is_this_expensive_after_upload_uses_doc_context(self, mock_check):
        """'is this expensive?' after uploading a quote uses document context."""
        mock_check.return_value = "DECISION:\nHigh\n\nWHY:\nTest.\n\nACTIONS:\n• Detail"
        from whatsapp_app import _handle_text_message
        _handle_text_message("is this expensive?", _state_with_quote())
        call_query = mock_check.call_args[0][0]
        self.assertIn("Acme Marine Ltd", call_query)
        self.assertIn("Windlass service", call_query)
        self.assertIn("1500", call_query)

    @patch("whatsapp_app.check_market_price")
    def test_what_should_this_cost_after_upload_uses_doc_context(self, mock_check):
        mock_check.return_value = "DECISION:\nReasonable\n\nWHY:\nTest.\n\nACTIONS:\n• Detail"
        from whatsapp_app import _handle_text_message
        _handle_text_message("what should this cost?", _state_with_quote())
        call_query = mock_check.call_args[0][0]
        self.assertIn("Acme Marine Ltd", call_query)

    @patch("whatsapp_app.check_market_price")
    def test_specific_query_no_enrichment(self, mock_check):
        """'how much for a yanmar impeller' with doc in state → query enriched with doc context."""
        mock_check.return_value = "DECISION:\nBroad estimate only\n\nWHY:\nTest.\n\nACTIONS:\n• Detail"
        from whatsapp_app import _handle_text_message
        _handle_text_message("how much for a yanmar impeller", _state_with_quote())
        call_query = mock_check.call_args[0][0]
        self.assertIn("Acme Marine Ltd", call_query)

    @patch("whatsapp_app.check_market_price")
    def test_vague_query_without_document_no_enrichment(self, mock_check):
        """'is this expensive?' without a document → query sent to Claude unchanged."""
        mock_check.return_value = "DECISION:\nUnclear\n\nWHY:\nTest.\n\nACTIONS:\n• Detail"
        from whatsapp_app import _handle_text_message
        _handle_text_message("is this expensive?", _empty_state())
        call_query = mock_check.call_args[0][0]
        self.assertEqual(call_query, "is this expensive?")

    @patch("whatsapp_app.check_market_price")
    def test_rough_price_is_market_check_uses_doc_context(self, mock_check):
        """'rough price for this' (no 'give me a') → market_check intent → doc context injected."""
        mock_check.return_value = "DECISION:\nBroad estimate only\n\nWHY:\nTest.\n\nACTIONS:\n• Detail"
        from whatsapp_app import _handle_text_message
        _handle_text_message("rough price for this", _state_with_quote())
        self.assertTrue(mock_check.called)
        call_query = mock_check.call_args[0][0]
        self.assertIn("Acme Marine Ltd", call_query)


# ---------------------------------------------------------------------------
# Last_context preserved after document-context market check
# ---------------------------------------------------------------------------

class TestDocumentContextLastContext(unittest.TestCase):

    @patch("whatsapp_app.check_market_price")
    def test_subsequent_followup_uses_market_check_context(self, mock_check):
        """
        After a document-context market check, a follow-up like 'is that high'
        should route to market_check using the previous topic, not TEXT RECEIVED.
        """
        mock_check.return_value = "DECISION:\nBroad estimate only\n\nWHY:\nTest.\n\nACTIONS:\n• Detail"
        from whatsapp_app import _handle_text_message

        # First: document context market check
        _, state = _handle_text_message(
            "give me a rough price for this?",
            _state_with_quote(),
        )
        self.assertEqual(state["last_context"]["type"], "market_check")

        # Second: follow-up should use the market_check context
        answer, _ = _handle_text_message("is that high", state)
        self.assertEqual(mock_check.call_count, 2)
        self.assertNotIn("TEXT RECEIVED", answer)


if __name__ == "__main__":
    unittest.main()
