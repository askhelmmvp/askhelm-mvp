"""
Session management tests.

Run with:  python -m pytest tests/test_sessions.py -v
"""
import sys
import os
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from domain.session_manager import (
    make_document_record,
    create_quote_session,
    attach_invoice_to_session,
    create_pending_session,
    reset_user_sessions,
    store_comparison_result,
    find_best_matching_session,
    get_active_session,
    gather_quote_docs_for_comparison,
    create_quote_vs_quote_session,
    score_invoice_against_session,
    AUTO_MATCH_THRESHOLD,
    AMBIGUOUS_THRESHOLD,
    MAX_QUOTES_PER_SESSION,
)
from domain.intent import classify_text


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _empty_state(user_id="test_user"):
    return {"user_id": user_id, "active_session_id": None, "sessions": [], "documents": []}


def _make_quote(supplier="Supplier A", total=1000.0, currency="EUR", doc_number="Q-001", items=None):
    if items is None:
        items = [{"description": "Item Alpha", "quantity": 1, "unit": None, "unit_rate": total, "line_total": total}]
    return make_document_record({
        "doc_type": "quote",
        "supplier_name": supplier,
        "document_number": doc_number,
        "document_date": "2024-01-15",
        "currency": currency,
        "total": total,
        "subtotal": total,
        "tax": 0,
        "line_items": items,
        "exclusions": [],
        "assumptions": [],
    }, f"data/quote_{supplier}.pdf")


def _make_invoice(supplier="Supplier A", total=1000.0, currency="EUR", doc_number="INV-001", items=None):
    if items is None:
        items = [{"description": "Item Alpha", "quantity": 1, "unit": None, "unit_rate": total, "line_total": total}]
    return make_document_record({
        "doc_type": "invoice",
        "supplier_name": supplier,
        "document_number": doc_number,
        "document_date": "2024-02-10",
        "currency": currency,
        "total": total,
        "subtotal": total,
        "tax": 0,
        "line_items": items,
        "exclusions": [],
        "assumptions": [],
    }, f"data/invoice_{supplier}.pdf")


# ---------------------------------------------------------------------------
# Test 1: Quote B always starts a new session
# ---------------------------------------------------------------------------

class TestQuoteAlwaysNewSession(unittest.TestCase):

    def test_quote_b_starts_new_session_not_auto_compared(self):
        state = _empty_state()

        quote_a = _make_quote("Supplier A", 1000.0)
        state, session_a = create_quote_session(quote_a, state)

        quote_b = _make_quote("Supplier B", 1200.0)
        state, session_b = create_quote_session(quote_b, state)

        self.assertNotEqual(session_a["session_id"], session_b["session_id"])
        self.assertEqual(state["active_session_id"], session_b["session_id"])
        self.assertIsNone(session_b["last_comparison"], "No auto-compare should happen")
        self.assertEqual(len(state["sessions"]), 2)
        self.assertEqual(len(state["documents"]), 2)

    def test_active_session_points_to_latest_quote(self):
        state = _empty_state()
        for supplier in ["A", "B", "C"]:
            q = _make_quote(f"Supplier {supplier}", 1000.0)
            state, last_session = create_quote_session(q, state)

        self.assertEqual(state["active_session_id"], last_session["session_id"])
        active = get_active_session(state)
        self.assertEqual(len(active["document_ids"]), 1)


# ---------------------------------------------------------------------------
# Test 2: Matching invoice auto-matches to quote
# ---------------------------------------------------------------------------

class TestInvoiceMatchesQuote(unittest.TestCase):

    def test_matching_invoice_scores_above_threshold(self):
        state = _empty_state()

        quote = _make_quote("Supplier A", 1000.0, "EUR", "Q-001")
        state, session = create_quote_session(quote, state)

        # Same supplier, same total, same line item, close date → high score
        invoice = _make_invoice("Supplier A", 1000.0, "EUR", "INV-001")
        session_id, score, reasons = find_best_matching_session(invoice, state)

        self.assertIsNotNone(session_id)
        self.assertEqual(session_id, session["session_id"])
        self.assertGreaterEqual(score, AUTO_MATCH_THRESHOLD,
                                f"Expected score >= {AUTO_MATCH_THRESHOLD}, got {score}: {reasons}")

    def test_invoice_attached_to_correct_session(self):
        state = _empty_state()

        quote = _make_quote("Supplier A", 1000.0)
        state, session = create_quote_session(quote, state)

        invoice = _make_invoice("Supplier A", 1000.0)
        state, updated_session = attach_invoice_to_session(invoice, session, state)

        self.assertEqual(updated_session["session_type"], "quote_vs_invoice")
        self.assertEqual(len(updated_session["document_ids"]), 2)
        self.assertEqual(state["active_session_id"], updated_session["session_id"])


# ---------------------------------------------------------------------------
# Test 3: Unrelated invoice does not auto-match
# ---------------------------------------------------------------------------

class TestUnrelatedInvoiceNoMatch(unittest.TestCase):

    def test_different_supplier_and_total_scores_below_threshold(self):
        state = _empty_state()

        quote = _make_quote("Supplier Alpha", 1000.0, "EUR", "Q-001")
        state, _ = create_quote_session(quote, state)

        # Completely unrelated: different supplier, total, no shared items
        invoice = _make_invoice("Completely Different Co", 9999.0, "EUR", "INV-999", items=[])
        session_id, score, reasons = find_best_matching_session(invoice, state)

        self.assertLess(score, AUTO_MATCH_THRESHOLD,
                        f"Expected score < {AUTO_MATCH_THRESHOLD}, got {score}: {reasons}")

    def test_no_open_sessions_returns_none(self):
        state = _empty_state()
        invoice = _make_invoice("Any Supplier", 1000.0)
        session_id, score, _ = find_best_matching_session(invoice, state)

        self.assertIsNone(session_id)
        self.assertEqual(score, 0)

    def test_already_matched_session_not_re_used(self):
        """A session with 2 docs (quote+invoice) should not be matched again."""
        state = _empty_state()

        quote = _make_quote("Supplier A", 1000.0)
        state, session = create_quote_session(quote, state)
        invoice_1 = _make_invoice("Supplier A", 1000.0)
        state, session = attach_invoice_to_session(invoice_1, session, state)

        # Session now has 2 docs — should not appear in open_sessions
        invoice_2 = _make_invoice("Supplier A", 1000.0)
        session_id, score, _ = find_best_matching_session(invoice_2, state)
        self.assertIsNone(session_id)


# ---------------------------------------------------------------------------
# Test 4: Follow-up uses only the active session
# ---------------------------------------------------------------------------

class TestFollowUpUsesActiveSession(unittest.TestCase):

    def test_second_quote_clears_active_comparison(self):
        state = _empty_state()

        # Session A gets a comparison result
        quote_a = _make_quote("Supplier A", 1000.0)
        state, session_a = create_quote_session(quote_a, state)
        dummy_comparison = {
            "total_a": 1000, "total_b": 900, "delta": -100,
            "delta_percent": -10, "added_items": [], "missing_items": [],
        }
        state = store_comparison_result(
            session_a, state, quote_a, _make_quote("Other", 900), dummy_comparison
        )

        # Uploading quote B creates a new session with no comparison
        quote_b = _make_quote("Supplier B", 1200.0)
        state, session_b = create_quote_session(quote_b, state)

        active = get_active_session(state)
        self.assertEqual(active["session_id"], session_b["session_id"])
        self.assertIsNone(active["last_comparison"],
                          "Active session for quote B should have no comparison yet")

    def test_follow_up_data_comes_from_active_session(self):
        state = _empty_state()

        quote_a = _make_quote("Supplier A", 1000.0)
        state, session_a = create_quote_session(quote_a, state)
        comparison_a = {"total_a": 1000, "total_b": 900, "delta": -100, "delta_percent": -10, "added_items": [], "missing_items": []}
        state = store_comparison_result(session_a, state, quote_a, _make_quote("X"), comparison_a)

        quote_b = _make_quote("Supplier B", 2000.0)
        state, session_b = create_quote_session(quote_b, state)
        comparison_b = {"total_a": 2000, "total_b": 2500, "delta": 500, "delta_percent": 25, "added_items": [], "missing_items": []}
        state = store_comparison_result(session_b, state, quote_b, _make_invoice("Supplier B", 2500.0), comparison_b)

        active = get_active_session(state)
        cd = active["last_comparison"]["comparison"]
        self.assertEqual(cd["total_a"], 2000, "Follow-up must use session B's data, not session A's")


# ---------------------------------------------------------------------------
# Test 5: Explicit 'compare quotes' activates quote_vs_quote
# ---------------------------------------------------------------------------

class TestExplicitQuoteComparison(unittest.TestCase):

    def test_two_quotes_gathered_for_comparison(self):
        state = _empty_state()

        state, _ = create_quote_session(_make_quote("Supplier A", 1000.0), state)
        state, _ = create_quote_session(_make_quote("Supplier B", 1200.0), state)

        quotes = gather_quote_docs_for_comparison(state)
        self.assertEqual(len(quotes), 2)
        suppliers = {q["supplier_name"] for q in quotes}
        self.assertIn("Supplier A", suppliers)
        self.assertIn("Supplier B", suppliers)

    def test_quote_vs_quote_session_created(self):
        state = _empty_state()

        state, _ = create_quote_session(_make_quote("Supplier A", 1000.0), state)
        state, _ = create_quote_session(_make_quote("Supplier B", 1200.0), state)

        quotes = gather_quote_docs_for_comparison(state)
        state, session = create_quote_vs_quote_session(quotes, state)

        self.assertEqual(session["session_type"], "quote_vs_quote")
        self.assertEqual(len(session["document_ids"]), 2)
        self.assertEqual(state["active_session_id"], session["session_id"])


# ---------------------------------------------------------------------------
# Test 6: 'compare 3 quotes' works with three quotes
# ---------------------------------------------------------------------------

class TestThreeQuoteComparison(unittest.TestCase):

    def test_three_quotes_gathered(self):
        state = _empty_state()
        for supplier, total in [("A", 1000), ("B", 1200), ("C", 900)]:
            state, _ = create_quote_session(_make_quote(f"Supplier {supplier}", total), state)

        quotes = gather_quote_docs_for_comparison(state, max_quotes=3)
        self.assertEqual(len(quotes), 3)

    def test_three_quote_session_created(self):
        state = _empty_state()
        for supplier, total in [("A", 1000), ("B", 1200), ("C", 900)]:
            state, _ = create_quote_session(_make_quote(f"Supplier {supplier}", total), state)

        quotes = gather_quote_docs_for_comparison(state, max_quotes=3)
        state, session = create_quote_vs_quote_session(quotes, state)

        self.assertEqual(session["session_type"], "quote_vs_quote")
        self.assertEqual(len(session["document_ids"]), 3)

    def test_gather_respects_max_quotes(self):
        state = _empty_state()
        for i in range(5):
            state, _ = create_quote_session(_make_quote(f"Supplier {i}", 1000 + i * 100), state)

        quotes = gather_quote_docs_for_comparison(state, max_quotes=3)
        self.assertLessEqual(len(quotes), 3)


# ---------------------------------------------------------------------------
# Test 7: 4th quote into a full session starts a new session
# ---------------------------------------------------------------------------

class TestFourthQuoteHandling(unittest.TestCase):

    def test_fourth_quote_creates_new_session(self):
        state = _empty_state()

        # Create a 3-quote comparison session
        three_quotes = [_make_quote(f"Supplier {c}", 1000 + i * 100) for i, c in enumerate("ABC")]
        for q in three_quotes:
            state, _ = create_quote_session(q, state)
        all_quotes = gather_quote_docs_for_comparison(state, max_quotes=MAX_QUOTES_PER_SESSION)
        state, q_session = create_quote_vs_quote_session(all_quotes, state)

        self.assertEqual(len(q_session["document_ids"]), MAX_QUOTES_PER_SESSION)

        # 4th quote: the whatsapp_app checks if active session is full and creates a new one
        quote_d = _make_quote("Supplier D", 1500.0)
        state, new_session = create_quote_session(quote_d, state)

        self.assertNotEqual(new_session["session_id"], q_session["session_id"])
        self.assertEqual(state["active_session_id"], new_session["session_id"])
        self.assertEqual(len(new_session["document_ids"]), 1)

    def test_full_session_unchanged_after_new_quote(self):
        state = _empty_state()

        three_quotes = [_make_quote(f"Supplier {c}", 1000) for c in "ABC"]
        for q in three_quotes:
            state, _ = create_quote_session(q, state)
        all_quotes = gather_quote_docs_for_comparison(state, max_quotes=MAX_QUOTES_PER_SESSION)
        state, q_session = create_quote_vs_quote_session(all_quotes, state)
        original_session_id = q_session["session_id"]

        quote_d = _make_quote("Supplier D", 1500.0)
        state, _ = create_quote_session(quote_d, state)

        preserved = next(s for s in state["sessions"] if s["session_id"] == original_session_id)
        self.assertEqual(len(preserved["document_ids"]), MAX_QUOTES_PER_SESSION,
                         "Original 3-quote session must not be modified")


# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------

class TestIntentClassification(unittest.TestCase):

    def test_new_session_intents(self):
        for phrase in ["new comparison", "new quote", "start new comparison",
                       "separate job", "reset", "fresh start"]:
            self.assertEqual(classify_text(phrase), "new_session", f"Failed: '{phrase}'")

    def test_quote_compare_intents(self):
        for phrase in ["compare these quotes", "compare quotes", "which quote is better",
                       "which supplier is better", "compare all quotes"]:
            self.assertEqual(classify_text(phrase), "quote_compare", f"Failed: '{phrase}'")

    def test_follow_up_intents(self):
        self.assertEqual(classify_text("why is it higher"), "why_higher")
        self.assertEqual(classify_text("show added items"), "show_added")
        self.assertEqual(classify_text("show missing items"), "show_missing")
        self.assertEqual(classify_text("what should i do"), "what_to_do")

    def test_greeting(self):
        for phrase in ["hi", "hello", "start"]:
            self.assertEqual(classify_text(phrase), "greeting", f"Failed: '{phrase}'")

    def test_unknown(self):
        self.assertEqual(classify_text("send me the schedule"), "unknown")


# ---------------------------------------------------------------------------
# Matching scoring
# ---------------------------------------------------------------------------

class TestMatchingScoring(unittest.TestCase):

    def _session_for_quote(self, quote):
        state = _empty_state()
        state, session = create_quote_session(quote, state)
        return state, session

    def test_exact_supplier_and_total_match(self):
        quote = _make_quote("Marine Parts Ltd", 5000.0, "EUR", "Q-2024-001")
        state, session = self._session_for_quote(quote)

        invoice = _make_invoice("Marine Parts Ltd", 5000.0, "EUR", "INV-2024-001")
        score, reasons = score_invoice_against_session(invoice, session, state)

        self.assertGreaterEqual(score, AUTO_MATCH_THRESHOLD, f"Score {score}: {reasons}")

    def test_partial_supplier_match_boosts_score(self):
        quote = _make_quote("Marine Parts International Ltd", 1000.0)
        state, session = self._session_for_quote(quote)

        invoice = _make_invoice("Marine Parts", 1000.0, items=[])
        score, reasons = score_invoice_against_session(invoice, session, state)
        # partial match (20) + totals exact (20) = 40 minimum
        self.assertGreaterEqual(score, 30)

    def test_reference_linkage_adds_points(self):
        quote = _make_quote("Supplier A", 1000.0, doc_number="Q-100")
        state, session = self._session_for_quote(quote)

        # Invoice doc number contains quote number
        invoice = _make_invoice("Supplier A", 1000.0, doc_number="INV-Q-100", items=[])
        score, reasons = score_invoice_against_session(invoice, session, state)
        # exact supplier (30) + reference (25) + totals exact (20) = 75
        self.assertGreaterEqual(score, AUTO_MATCH_THRESHOLD)

    def test_completely_unrelated_scores_low(self):
        quote = _make_quote("Supplier Alpha", 1000.0)
        state, session = self._session_for_quote(quote)

        invoice = _make_invoice("Unrelated Co", 99999.0, items=[])
        score, reasons = score_invoice_against_session(invoice, session, state)
        self.assertLess(score, AMBIGUOUS_THRESHOLD, f"Score {score}: {reasons}")


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

class TestSessionReset(unittest.TestCase):

    def test_reset_closes_all_sessions(self):
        state = _empty_state()
        for supplier in ["A", "B"]:
            state, _ = create_quote_session(_make_quote(f"Supplier {supplier}"), state)

        state = reset_user_sessions(state)

        self.assertIsNone(state["active_session_id"])
        for s in state["sessions"]:
            self.assertEqual(s["status"], "closed")

    def test_reset_preserves_document_history(self):
        state = _empty_state()
        state, _ = create_quote_session(_make_quote("Supplier A"), state)
        state = reset_user_sessions(state)

        self.assertEqual(len(state["documents"]), 1, "Documents are preserved after reset")


# ---------------------------------------------------------------------------
# Extraction view command
# ---------------------------------------------------------------------------

class TestExtractionView(unittest.TestCase):

    def _state_with_doc(self, supplier="Marine Parts Ltd", total=4500.0, currency="EUR",
                        doc_type="quote", items=None):
        if items is None:
            items = [
                {"description": "Fire pump overhaul", "quantity": 1, "unit_rate": 2000.0, "line_total": 2000.0},
                {"description": "Gasket kit", "quantity": 2, "unit_rate": 150.0, "line_total": 300.0},
                {"description": "Labour", "quantity": 8, "unit_rate": 275.0, "line_total": 2200.0},
            ]
        doc = make_document_record(
            {
                "doc_type": doc_type,
                "supplier_name": supplier,
                "document_number": "Q-2024-042",
                "document_date": "2024-03-10",
                "currency": currency,
                "total": total,
                "subtotal": total,
                "tax": 0,
                "line_items": items,
                "exclusions": [],
                "assumptions": [],
            },
            "data/test_quote.pdf",
        )
        state = {"user_id": "test_user", "active_session_id": None, "sessions": [], "documents": [doc]}
        return state, doc

    def test_show_extraction_intent_classified(self):
        self.assertEqual(classify_text("show extraction"), "show_extraction")
        self.assertEqual(classify_text("show extracted data"), "show_extraction")
        self.assertEqual(classify_text("what did you extract"), "show_extraction")

    def test_show_extraction_returns_structured_output(self):
        from whatsapp_app import _handle_text_message
        state, doc = self._state_with_doc()
        answer, _ = _handle_text_message("show extraction", state)

        self.assertIn("EXTRACTION VIEW", answer)
        self.assertIn("Marine Parts Ltd", answer)
        self.assertIn("4500.0", answer)
        self.assertIn("EUR", answer)
        self.assertIn("Fire pump overhaul", answer)

    def test_show_extracted_data_alias(self):
        from whatsapp_app import _handle_text_message
        state, _ = self._state_with_doc()
        answer, _ = _handle_text_message("show extracted data", state)
        self.assertIn("EXTRACTION VIEW", answer)

    def test_what_did_you_extract_alias(self):
        from whatsapp_app import _handle_text_message
        state, _ = self._state_with_doc()
        answer, _ = _handle_text_message("what did you extract", state)
        self.assertIn("EXTRACTION VIEW", answer)

    def test_no_document_returns_helpful_message(self):
        from whatsapp_app import _handle_text_message
        state = {"user_id": "test_user", "active_session_id": None, "sessions": [], "documents": []}
        answer, _ = _handle_text_message("show extraction", state)
        self.assertIn("No document available", answer)

    def test_shows_last_uploaded_document(self):
        from whatsapp_app import _handle_text_message
        state = {"user_id": "test_user", "active_session_id": None, "sessions": [], "documents": []}
        doc_a = make_document_record(
            {"doc_type": "quote", "supplier_name": "First Supplier", "currency": "EUR",
             "total": 1000.0, "subtotal": 1000.0, "tax": 0,
             "line_items": [], "exclusions": [], "assumptions": [],
             "document_number": None, "document_date": None},
            "data/first.pdf",
        )
        doc_b = make_document_record(
            {"doc_type": "invoice", "supplier_name": "Last Supplier", "currency": "GBP",
             "total": 2500.0, "subtotal": 2500.0, "tax": 0,
             "line_items": [], "exclusions": [], "assumptions": [],
             "document_number": None, "document_date": None},
            "data/last.pdf",
        )
        state["documents"] = [doc_a, doc_b]
        answer, _ = _handle_text_message("show extraction", state)

        self.assertIn("Last Supplier", answer)
        self.assertNotIn("First Supplier", answer)

    def test_line_items_capped_at_five(self):
        from whatsapp_app import _handle_text_message
        items = [
            {"description": f"Item {i}", "quantity": 1, "unit_rate": 100.0, "line_total": 100.0}
            for i in range(8)
        ]
        state, _ = self._state_with_doc(items=items)
        answer, _ = _handle_text_message("show extraction", state)

        self.assertIn("Item 0", answer)
        self.assertIn("Item 4", answer)
        self.assertNotIn("Item 5", answer)
        self.assertIn("3 more", answer)

    def test_extraction_does_not_break_compliance_routing(self):
        self.assertNotEqual(classify_text("show extraction"), "compliance_question")
        self.assertNotEqual(classify_text("show extracted data"), "compliance_question")

    def test_extraction_does_not_break_comparison_routing(self):
        self.assertNotEqual(classify_text("show extraction"), "quote_compare")
        self.assertNotEqual(classify_text("show extraction"), "new_session")


# ---------------------------------------------------------------------------
# Image extraction
# ---------------------------------------------------------------------------

_QUOTE_EXTRACTION = {
    "doc_type": "quote",
    "supplier_name": "Pacific Marine Supplies",
    "document_number": "Q-2024-099",
    "document_date": "2024-04-01",
    "currency": "USD",
    "subtotal": 8200.0,
    "tax": 820.0,
    "total": 9020.0,
    "exclusions": [],
    "assumptions": [],
    "line_items": [
        {"description": "Anchor chain 10mm x 50m", "quantity": 1, "unit": None, "unit_rate": 4500.0, "line_total": 4500.0},
        {"description": "Windlass service kit", "quantity": 1, "unit": None, "unit_rate": 3700.0, "line_total": 3700.0},
    ],
}

_INVOICE_EXTRACTION = {
    "doc_type": "invoice",
    "supplier_name": "Pacific Marine Supplies",
    "document_number": "INV-2024-099",
    "document_date": "2024-04-15",
    "currency": "USD",
    "subtotal": 8200.0,
    "tax": 820.0,
    "total": 9020.0,
    "exclusions": [],
    "assumptions": [],
    "line_items": [
        {"description": "Anchor chain 10mm x 50m", "quantity": 1, "unit": None, "unit_rate": 4500.0, "line_total": 4500.0},
        {"description": "Windlass service kit", "quantity": 1, "unit": None, "unit_rate": 3700.0, "line_total": 3700.0},
    ],
}

_UNKNOWN_EXTRACTION = {
    "doc_type": None,
    "supplier_name": "Unknown Co",
    "document_number": None,
    "document_date": None,
    "currency": "EUR",
    "subtotal": None,
    "tax": None,
    "total": 500.0,
    "exclusions": [],
    "assumptions": [],
    "line_items": [
        {"description": "Misc parts", "quantity": 1, "unit": None, "unit_rate": 500.0, "line_total": 500.0},
    ],
}


class TestImageExtraction(unittest.TestCase):

    def _empty_state(self):
        return {"user_id": "test_user", "active_session_id": None, "sessions": [], "documents": []}

    @patch("whatsapp_app.extract_commercial_document_from_images")
    def test_jpeg_quote_returns_image_processed(self, mock_vision):
        mock_vision.return_value = _QUOTE_EXTRACTION
        from whatsapp_app import _handle_image_upload
        answer, state = _handle_image_upload("data/test_doc.jpg", self._empty_state())

        self.assertIn("IMAGE PROCESSED", answer)
        mock_vision.assert_called_once_with(["data/test_doc.jpg"])

    @patch("whatsapp_app.extract_commercial_document_from_images")
    def test_png_quote_returns_image_processed(self, mock_vision):
        mock_vision.return_value = _QUOTE_EXTRACTION
        from whatsapp_app import _handle_image_upload
        answer, state = _handle_image_upload("data/test_doc.png", self._empty_state())

        self.assertIn("IMAGE PROCESSED", answer)

    @patch("whatsapp_app.extract_commercial_document_from_images")
    def test_image_invoice_returns_image_processed(self, mock_vision):
        mock_vision.return_value = _INVOICE_EXTRACTION
        from whatsapp_app import _handle_image_upload
        answer, state = _handle_image_upload("data/test_doc.jpg", self._empty_state())

        self.assertIn("IMAGE PROCESSED", answer)
        self.assertEqual(len(state["documents"]), 1)

    @patch("whatsapp_app.extract_commercial_document_from_images")
    def test_image_stored_in_state_documents(self, mock_vision):
        mock_vision.return_value = _QUOTE_EXTRACTION
        from whatsapp_app import _handle_image_upload
        _, state = _handle_image_upload("data/test_doc.jpg", self._empty_state())

        self.assertEqual(len(state["documents"]), 1)
        doc = state["documents"][0]
        self.assertEqual(doc["supplier_name"], "Pacific Marine Supplies")
        self.assertEqual(doc["total"], 9020.0)
        self.assertEqual(doc["currency"], "USD")
        self.assertEqual(len(doc["line_items"]), 2)

    @patch("whatsapp_app.extract_commercial_document_from_images")
    def test_image_quote_then_show_extraction(self, mock_vision):
        mock_vision.return_value = _QUOTE_EXTRACTION
        from whatsapp_app import _handle_image_upload, _handle_text_message

        _, state = _handle_image_upload("data/test_doc.jpg", self._empty_state())
        answer, _ = _handle_text_message("show extraction", state)

        self.assertIn("EXTRACTION VIEW", answer)
        self.assertIn("Pacific Marine Supplies", answer)
        self.assertIn("9020.0", answer)
        self.assertIn("Anchor chain", answer)

    @patch("whatsapp_app.extract_commercial_document_from_images")
    def test_unknown_doc_type_returns_image_processed(self, mock_vision):
        mock_vision.return_value = _UNKNOWN_EXTRACTION
        from whatsapp_app import _handle_image_upload
        answer, _ = _handle_image_upload("data/test_doc.jpg", self._empty_state())

        self.assertIn("IMAGE PROCESSED", answer)
        self.assertIn("show extraction", answer)

    @patch("whatsapp_app.extract_commercial_document_from_images")
    def test_image_quote_then_image_invoice_both_stored(self, mock_vision):
        """Both image uploads are stored; IMAGE PROCESSED is returned each time (no auto-compare)."""
        from whatsapp_app import _handle_image_upload

        mock_vision.return_value = _QUOTE_EXTRACTION
        answer_q, state = _handle_image_upload("data/quote.jpg", self._empty_state())

        mock_vision.return_value = _INVOICE_EXTRACTION
        answer_i, state = _handle_image_upload("data/invoice.jpg", state)

        self.assertIn("IMAGE PROCESSED", answer_q)
        self.assertIn("IMAGE PROCESSED", answer_i)
        self.assertEqual(len(state["documents"]), 2)

    @patch("whatsapp_app.extract_commercial_document_from_images")
    def test_extraction_failure_returns_image_received(self, mock_vision):
        """API / parse errors return IMAGE RECEIVED, not a crash or FILE ERROR."""
        mock_vision.side_effect = RuntimeError("vision API timeout")
        from whatsapp_app import _handle_image_upload
        answer, state = _handle_image_upload("data/test_doc.jpg", self._empty_state())

        self.assertIn("IMAGE RECEIVED", answer)
        self.assertIn("clearer image", answer)
        self.assertEqual(len(state["documents"]), 0)

    def test_image_content_type_set_covers_jpg_alias(self):
        from whatsapp_app import _IMAGE_CONTENT_TYPES
        self.assertIn("image/jpeg", _IMAGE_CONTENT_TYPES)
        self.assertIn("image/jpg", _IMAGE_CONTENT_TYPES)
        self.assertIn("image/png", _IMAGE_CONTENT_TYPES)

    def test_unsupported_media_type_not_in_image_set(self):
        from whatsapp_app import _IMAGE_CONTENT_TYPES
        self.assertNotIn("image/gif", _IMAGE_CONTENT_TYPES)
        self.assertNotIn("image/webp", _IMAGE_CONTENT_TYPES)
        self.assertNotIn("application/pdf", _IMAGE_CONTENT_TYPES)

    # --- Twilio reply body always present ---

    @patch("whatsapp_app.extract_commercial_document_from_images")
    def test_commercial_image_reply_body_is_non_empty(self, mock_vision):
        """A quote/invoice image must produce a non-empty reply body for Twilio to deliver."""
        mock_vision.return_value = _QUOTE_EXTRACTION
        from whatsapp_app import _handle_image_upload
        answer, _ = _handle_image_upload("data/quote.jpg", self._empty_state())
        self.assertTrue(answer.strip(), "Reply body must not be empty for commercial image")
        self.assertIn("IMAGE PROCESSED", answer)
        self.assertIn("Pacific Marine Supplies", answer)

    @patch("whatsapp_app.extract_commercial_document_from_images")
    def test_unknown_image_reply_body_is_non_empty(self, mock_vision):
        """An unclassified image must produce a non-empty reply body; no session created."""
        mock_vision.return_value = _UNKNOWN_EXTRACTION
        from whatsapp_app import _handle_image_upload
        answer, state = _handle_image_upload("data/unknown.jpg", self._empty_state())
        self.assertTrue(answer.strip(), "Reply body must not be empty for unknown image")
        self.assertIn("IMAGE PROCESSED", answer)
        self.assertIn("does not look like a standard quote or invoice", answer)
        self.assertEqual(len(state["documents"]), 0, "Unknown doc should not be stored in session")

    @patch("whatsapp_app.extract_commercial_document_from_images")
    def test_failed_image_reply_body_is_non_empty(self, mock_vision):
        """Extraction failure must produce a non-empty reply body — never a silent HTTP 200."""
        mock_vision.side_effect = Exception("vision timeout")
        from whatsapp_app import _handle_image_upload
        answer, _ = _handle_image_upload("data/bad.jpg", self._empty_state())
        self.assertTrue(answer.strip(), "Reply body must not be empty for failed extraction")
        self.assertIn("IMAGE RECEIVED", answer)

    @patch("whatsapp_app.extract_commercial_document_from_images")
    def test_pdf_flow_unaffected_by_image_handler(self, mock_vision):
        """PDF handler must not call the vision service."""
        from whatsapp_app import _handle_pdf_upload
        with patch("whatsapp_app.extract_commercial_document_with_claude") as mock_text_llm, \
             patch("whatsapp_app.extract_pdf_text") as mock_text:
            mock_text.return_value = "some text content"
            mock_text_llm.return_value = _QUOTE_EXTRACTION
            _handle_pdf_upload("data/test.pdf", self._empty_state())

        mock_vision.assert_not_called()


# ---------------------------------------------------------------------------
# Immediate image reply via background thread
# ---------------------------------------------------------------------------

class TestImageImmediateReply(unittest.TestCase):
    """Image uploads must return IMAGE RECEIVED immediately; extraction runs in background."""

    def _post_image(self, mock_download, mock_bg, mock_load, content_type="image/jpeg"):
        mock_download.return_value = "/tmp/upload_test.jpg"
        mock_load.return_value = _empty_state()
        from whatsapp_app import app
        with app.test_client() as client:
            return client.post("/whatsapp", data={
                "From": "whatsapp:+447700900000",
                "NumMedia": "1",
                "MediaUrl0": "https://api.twilio.com/media/xxx",
                "MediaContentType0": content_type,
                "Body": "",
            })

    @patch("whatsapp_app.save_user_state")
    @patch("whatsapp_app.load_user_state")
    @patch("whatsapp_app._process_image_background")
    @patch("whatsapp_app.download_file")
    def test_jpeg_upload_returns_immediate_image_received(self, mock_dl, mock_bg, mock_load, mock_save):
        response = self._post_image(mock_dl, mock_bg, mock_load, "image/jpeg")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"IMAGE RECEIVED", response.data)
        self.assertIn(b"Processing your image now", response.data)

    @patch("whatsapp_app.save_user_state")
    @patch("whatsapp_app.load_user_state")
    @patch("whatsapp_app._process_image_background")
    @patch("whatsapp_app.download_file")
    def test_jpeg_upload_starts_background_thread(self, mock_dl, mock_bg, mock_load, mock_save):
        self._post_image(mock_dl, mock_bg, mock_load, "image/jpeg")
        mock_bg.assert_called_once()
        args = mock_bg.call_args[0]
        self.assertEqual(args[0], "/tmp/upload_test.jpg")   # file_path
        self.assertIsInstance(args[2], str)                 # user_id is a non-empty hash
        self.assertTrue(args[2])

    @patch("whatsapp_app.save_user_state")
    @patch("whatsapp_app.load_user_state")
    @patch("whatsapp_app._process_image_background")
    @patch("whatsapp_app.download_file")
    def test_jpeg_upload_does_not_save_state_in_main_thread(self, mock_dl, mock_bg, mock_load, mock_save):
        """State persistence must be left to the background thread."""
        self._post_image(mock_dl, mock_bg, mock_load, "image/jpeg")
        mock_save.assert_not_called()

    @patch("whatsapp_app.save_user_state")
    @patch("whatsapp_app.load_user_state")
    @patch("whatsapp_app._process_image_background")
    @patch("whatsapp_app.download_file")
    def test_png_upload_returns_immediate_image_received(self, mock_dl, mock_bg, mock_load, mock_save):
        response = self._post_image(mock_dl, mock_bg, mock_load, "image/png")
        self.assertIn(b"IMAGE RECEIVED", response.data)

    @patch("whatsapp_app.save_user_state")
    @patch("whatsapp_app.load_user_state")
    @patch("whatsapp_app._process_image_background")
    @patch("whatsapp_app.download_file")
    def test_reply_content_type_is_xml(self, mock_dl, mock_bg, mock_load, mock_save):
        response = self._post_image(mock_dl, mock_bg, mock_load)
        self.assertIn("text/xml", response.content_type)

    @patch("whatsapp_app.save_user_state")
    @patch("whatsapp_app.load_user_state")
    @patch("whatsapp_app.extract_commercial_document_with_claude")
    @patch("whatsapp_app.extract_pdf_text")
    @patch("whatsapp_app.download_file")
    def test_pdf_flow_still_replies_synchronously(self, mock_dl, mock_pdf_text, mock_pdf_llm, mock_load, mock_save):
        """PDF uploads must NOT use the background thread — they reply inline."""
        mock_dl.return_value = "/tmp/upload_test.pdf"
        mock_pdf_text.return_value = "some invoice text"
        mock_pdf_llm.return_value = _QUOTE_EXTRACTION
        mock_load.return_value = _empty_state()
        from whatsapp_app import app
        with app.test_client() as client:
            response = client.post("/whatsapp", data={
                "From": "whatsapp:+447700900000",
                "NumMedia": "1",
                "MediaUrl0": "https://api.twilio.com/media/doc.pdf",
                "MediaContentType0": "application/pdf",
                "Body": "",
            })
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"IMAGE RECEIVED", response.data)
        mock_save.assert_called_once()  # state saved synchronously for PDFs


# ---------------------------------------------------------------------------
# Operational note detection and summarisation
# ---------------------------------------------------------------------------

_OPERATIONAL_EXTRACTION = {
    "doc_type": None,
    "supplier_name": None,
    "document_number": None,
    "document_date": None,
    "currency": None,
    "subtotal": None,
    "tax": None,
    "total": None,
    "exclusions": [],
    "assumptions": [],
    "line_items": [],
}

_OPERATIONAL_SUMMARY = (
    "DECISION:\n"
    "Operational actions and risks identified\n"
    "\n"
    "KEY POINTS:\n"
    "• Lifeboat drill overdue — last recorded 3 months ago\n"
    "• Fire pump 2 showing low pressure on weekly test\n"
    "• Chief Mate requests replacement impeller\n"
    "\n"
    "RISKS:\n"
    "• Overdue drill is an open non-conformity under the ISM Code\n"
    "• Degraded fire pump pressure may indicate seal or impeller failure\n"
    "\n"
    "ACTIONS:\n"
    "• Schedule lifeboat drill before next port call\n"
    "• Inspect fire pump 2 impeller and shaft seal\n"
    "• Raise NC in SMS for overdue drill\n"
    "• Log both items in planned maintenance system"
)


class TestOperationalNotes(unittest.TestCase):

    def _empty_state(self):
        return {"user_id": "test_user", "active_session_id": None, "sessions": [], "documents": []}

    # --- detection logic ---

    def test_no_fields_is_operational(self):
        from whatsapp_app import _is_operational_note
        self.assertTrue(_is_operational_note({
            "supplier_name": None, "total": None, "subtotal": None, "line_items": [],
        }))

    def test_supplier_only_is_operational(self):
        # Supplier name alone (no pricing, no doc_type) → operational; person name in notes won't block classification
        from whatsapp_app import _is_operational_note
        self.assertTrue(_is_operational_note({
            "supplier_name": "Capt. Anderson", "total": None, "subtotal": None, "line_items": [],
        }))

    def test_explicit_quote_doc_type_is_commercial(self):
        from whatsapp_app import _is_operational_note
        self.assertFalse(_is_operational_note({
            "doc_type": "quote", "supplier_name": None, "total": None, "subtotal": None, "line_items": [],
        }))

    def test_explicit_invoice_doc_type_is_commercial(self):
        from whatsapp_app import _is_operational_note
        self.assertFalse(_is_operational_note({
            "doc_type": "invoice", "supplier_name": "Neptune Supplies", "total": None, "subtotal": None, "line_items": [],
        }))

    def test_total_present_is_commercial(self):
        from whatsapp_app import _is_operational_note
        self.assertFalse(_is_operational_note({
            "supplier_name": None, "total": 1200.0, "subtotal": None, "line_items": [],
        }))

    def test_subtotal_present_is_commercial(self):
        from whatsapp_app import _is_operational_note
        self.assertFalse(_is_operational_note({
            "supplier_name": None, "total": None, "subtotal": 800.0, "line_items": [],
        }))

    def test_priced_line_items_is_commercial(self):
        from whatsapp_app import _is_operational_note
        self.assertFalse(_is_operational_note({
            "supplier_name": None, "total": None, "subtotal": None,
            "line_items": [{"description": "Part A", "unit_rate": 400.0, "line_total": None}],
        }))

    def test_unpriced_line_items_is_operational(self):
        from whatsapp_app import _is_operational_note
        # Bullets extracted as items but with no pricing
        self.assertTrue(_is_operational_note({
            "supplier_name": None, "total": None, "subtotal": None,
            "line_items": [
                {"description": "Check fire pump", "unit_rate": None, "line_total": None},
                {"description": "Overdue drill", "unit_rate": None, "line_total": None},
            ],
        }))

    # --- routing ---

    @patch("whatsapp_app.extract_commercial_document_from_images")
    @patch("whatsapp_app.summarise_operational_note_from_image")
    def test_operational_note_returns_structured_summary(self, mock_summarise, mock_extract):
        mock_extract.return_value = _OPERATIONAL_EXTRACTION
        mock_summarise.return_value = _OPERATIONAL_SUMMARY

        from whatsapp_app import _handle_image_upload
        answer, _ = _handle_image_upload("data/notes.jpg", self._empty_state())

        self.assertIn("DECISION:", answer)
        self.assertIn("Operational actions", answer)
        self.assertIn("KEY POINTS:", answer)
        self.assertIn("RISKS:", answer)
        self.assertIn("ACTIONS:", answer)
        mock_summarise.assert_called_once_with("data/notes.jpg")

    @patch("whatsapp_app.extract_commercial_document_from_images")
    @patch("whatsapp_app.summarise_operational_note_from_image")
    def test_operational_note_not_stored_in_session(self, mock_summarise, mock_extract):
        mock_extract.return_value = _OPERATIONAL_EXTRACTION
        mock_summarise.return_value = _OPERATIONAL_SUMMARY

        from whatsapp_app import _handle_image_upload
        _, updated_state = _handle_image_upload("data/notes.jpg", self._empty_state())

        self.assertEqual(len(updated_state["documents"]), 0)
        self.assertIsNone(updated_state["active_session_id"])

    @patch("whatsapp_app.extract_commercial_document_from_images")
    @patch("whatsapp_app.summarise_operational_note_from_image")
    def test_commercial_image_not_treated_as_operational(self, mock_summarise, mock_extract):
        mock_extract.return_value = _QUOTE_EXTRACTION

        from whatsapp_app import _handle_image_upload
        answer, _ = _handle_image_upload("data/quote.jpg", self._empty_state())

        self.assertIn("IMAGE PROCESSED", answer)
        mock_summarise.assert_not_called()

    @patch("whatsapp_app.extract_commercial_document_from_images")
    @patch("whatsapp_app.summarise_operational_note_from_image")
    def test_operational_note_does_not_break_comparison(self, mock_summarise, mock_extract):
        """Uploading an operational note must not affect any active comparison session."""
        mock_extract.return_value = _OPERATIONAL_EXTRACTION
        mock_summarise.return_value = _OPERATIONAL_SUMMARY

        state = self._empty_state()
        doc_a = make_document_record(
            {**_QUOTE_EXTRACTION, "supplier_name": "Supplier A", "total": 5000.0,
             "document_number": None, "document_date": None},
            "data/quote_a.jpg",
        )
        from domain.session_manager import create_quote_session
        state, session = create_quote_session(doc_a, state)
        session_id_before = state["active_session_id"]

        from whatsapp_app import _handle_image_upload
        _, updated_state = _handle_image_upload("data/notes.jpg", state)

        self.assertEqual(updated_state["active_session_id"], session_id_before)
        self.assertEqual(len(updated_state["documents"]), 1)


class TestOperationalNoteClassification(unittest.TestCase):
    """Regression tests for the handwritten-note-with-person-name bug."""

    def test_handwritten_note_with_person_name_is_operational(self):
        # Capt. Anderson extracted as supplier_name but no pricing → must be operational
        from whatsapp_app import _is_operational_note
        self.assertTrue(_is_operational_note({
            "doc_type": None,
            "supplier_name": "Capt. Anderson",
            "total": None,
            "subtotal": None,
            "line_items": [
                {"description": "Schedule lifeboat drill", "unit_rate": None, "line_total": None},
                {"description": "Check bilge alarms", "unit_rate": None, "line_total": None},
            ],
        }))

    @patch("whatsapp_app.extract_commercial_document_from_images")
    @patch("whatsapp_app.summarise_operational_note_from_image")
    def test_handwritten_note_no_commercial_session_created(self, mock_summarise, mock_extract):
        mock_extract.return_value = {
            "doc_type": None,
            "supplier_name": "Capt. Anderson",
            "total": None, "subtotal": None,
            "line_items": [],
            "exclusions": [], "assumptions": [],
            "document_number": None, "document_date": None,
            "currency": None, "tax": None,
        }
        mock_summarise.return_value = _OPERATIONAL_SUMMARY

        from whatsapp_app import _handle_image_upload
        _, state = _handle_image_upload("data/handwritten.jpg", _empty_state())

        self.assertEqual(len(state["documents"]), 0)
        self.assertIsNone(state["active_session_id"])

    @patch("whatsapp_app.extract_commercial_document_from_images")
    @patch("whatsapp_app.summarise_operational_note_from_image")
    def test_handwritten_note_returns_summary(self, mock_summarise, mock_extract):
        mock_extract.return_value = {
            "doc_type": None,
            "supplier_name": "Chief Eng. Reyes",
            "total": None, "subtotal": None,
            "line_items": [],
            "exclusions": [], "assumptions": [],
            "document_number": None, "document_date": None,
            "currency": None, "tax": None,
        }
        mock_summarise.return_value = _OPERATIONAL_SUMMARY

        from whatsapp_app import _handle_image_upload
        answer, _ = _handle_image_upload("data/handwritten.jpg", _empty_state())

        self.assertIn("DECISION:", answer)
        self.assertIn("KEY POINTS:", answer)
        mock_summarise.assert_called_once()

    def test_quote_without_totals_but_explicit_doc_type_is_commercial(self):
        from whatsapp_app import _is_operational_note
        self.assertFalse(_is_operational_note({
            "doc_type": "quote",
            "supplier_name": None,
            "total": None, "subtotal": None,
            "line_items": [
                {"description": "Service A", "unit_rate": None, "line_total": None},
            ],
        }))

    def test_unknown_doc_type_no_pricing_is_operational(self):
        from whatsapp_app import _is_operational_note
        self.assertTrue(_is_operational_note({
            "doc_type": "unknown",
            "supplier_name": "Meeting Notes",
            "total": None, "subtotal": None,
            "line_items": [],
        }))


if __name__ == "__main__":
    unittest.main()
