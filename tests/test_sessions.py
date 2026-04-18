"""
Session management tests.

Run with:  python -m pytest tests/test_sessions.py -v
"""
import sys
import os
import unittest

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
        self.assertEqual(classify_text("what is the weather"), "unknown")


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


if __name__ == "__main__":
    unittest.main()
