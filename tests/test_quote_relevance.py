"""Tests for quote relevance filtering (Task 4)."""
import unittest
from unittest.mock import patch, MagicMock

from domain.compare import filter_quotes_by_relevance, _quote_keywords, _overlap_coefficient
from domain.session_manager import (
    make_document_record,
    create_quote_session,
    create_quote_vs_quote_session,
    gather_quote_docs_for_comparison,
)


# ---------------------------------------------------------------------------
# Fixtures — real-world supplier data from the bug report
# ---------------------------------------------------------------------------

_CALPEDA_ITEMS_HYDRO = [
    {"description": "Calpeda MXPM 206 E pump", "quantity": 1, "line_total": 3200.0},
    {"description": "Calpeda pump impeller 3 inch", "quantity": 2, "line_total": 480.0},
    {"description": "Calpeda mechanical seal MXPM", "quantity": 2, "line_total": 260.0},
    {"description": "Calpeda pressure switch", "quantity": 1, "line_total": 95.0},
]

_CALPEDA_ITEMS_IYS = [
    {"description": "Calpeda MXPM 206 pump unit", "quantity": 1, "line_total": 3050.0},
    {"description": "Calpeda MXPM impeller", "quantity": 2, "line_total": 460.0},
    {"description": "Mechanical seal kit Calpeda", "quantity": 2, "line_total": 240.0},
    {"description": "Delivery and freight", "quantity": 1, "line_total": 120.0},
]

_MIELE_ITEMS_KINGDOM = [
    {"description": "Miele WDB020 detergent dispenser", "quantity": 2, "line_total": 180.0},
    {"description": "Miele door hinge washer 6kg", "quantity": 4, "line_total": 96.0},
    {"description": "Miele drum bearing kit", "quantity": 1, "line_total": 145.0},
    {"description": "Miele carbon brush set WDB", "quantity": 2, "line_total": 64.0},
    {"description": "Miele inlet valve 220V", "quantity": 2, "line_total": 118.0},
    {"description": "Shipping and handling", "quantity": 1, "line_total": 85.0},
]


def _empty_state(user_id="test_user"):
    return {"user_id": user_id, "active_session_id": None, "sessions": [], "documents": []}


def _make_quote(supplier, total, currency="EUR", items=None):
    if items is None:
        items = [{"description": "Item", "quantity": 1, "line_total": total}]
    return make_document_record({
        "doc_type": "quote",
        "supplier_name": supplier,
        "document_number": "Q-001",
        "document_date": "2024-01-15",
        "currency": currency,
        "total": total,
        "subtotal": total,
        "tax": 0,
        "line_items": items,
        "exclusions": [],
        "assumptions": [],
    }, f"data/quote_{supplier}.pdf")


def _hydro_quote():
    return _make_quote("Hydro Electrique Marine", 4035.0, items=_CALPEDA_ITEMS_HYDRO)


def _iys_quote():
    return _make_quote("International Yacht Services", 3870.0, items=_CALPEDA_ITEMS_IYS)


def _kingdom_quote():
    return _make_quote("Kingdom Ocean Management Sarl", 688.0, items=_MIELE_ITEMS_KINGDOM)


# ---------------------------------------------------------------------------
# Unit tests for filter_quotes_by_relevance
# ---------------------------------------------------------------------------

class TestFilterQuotesByRelevance(unittest.TestCase):

    def test_two_quotes_always_pass_through(self):
        selected, excluded = filter_quotes_by_relevance([_hydro_quote(), _iys_quote()])
        self.assertEqual(len(selected), 2)
        self.assertEqual(len(excluded), 0)

    def test_kingdom_excluded_when_calpeda_quotes_present(self):
        quotes = [_hydro_quote(), _iys_quote(), _kingdom_quote()]
        selected, excluded = filter_quotes_by_relevance(quotes)
        self.assertEqual(len(selected), 2)
        self.assertEqual(len(excluded), 1)
        selected_suppliers = {q["supplier_name"] for q in selected}
        self.assertIn("Hydro Electrique Marine", selected_suppliers)
        self.assertIn("International Yacht Services", selected_suppliers)
        excluded_suppliers = {q["supplier_name"] for q in excluded}
        self.assertIn("Kingdom Ocean Management Sarl", excluded_suppliers)

    def test_two_miele_quotes_both_selected(self):
        """Regression: two Miele quotes must still compare normally."""
        miele_a = _make_quote(
            "Kingdom Ocean Management Sarl", 688.0, items=_MIELE_ITEMS_KINGDOM
        )
        miele_b = _make_quote(
            "Marine Parts Direct", 720.0,
            items=[
                {"description": "Miele WDB020 detergent dispenser", "quantity": 2, "line_total": 190.0},
                {"description": "Miele drum bearing kit", "quantity": 1, "line_total": 155.0},
                {"description": "Miele inlet valve 220V", "quantity": 2, "line_total": 125.0},
                {"description": "Courier delivery", "quantity": 1, "line_total": 50.0},
                {"description": "Miele carbon brush set WDB", "quantity": 2, "line_total": 70.0},
                {"description": "Miele door hinge washer 6kg", "quantity": 4, "line_total": 100.0},
            ],
        )
        selected, excluded = filter_quotes_by_relevance([miele_a, miele_b])
        self.assertEqual(len(selected), 2)
        self.assertEqual(len(excluded), 0)

    def test_overlap_coefficient_zero_when_empty(self):
        self.assertEqual(_overlap_coefficient(frozenset(), frozenset({"foo"})), 0.0)
        self.assertEqual(_overlap_coefficient(frozenset({"foo"}), frozenset()), 0.0)

    def test_overlap_coefficient_identical_sets(self):
        s = frozenset({"calpeda", "pump", "impeller", "seal"})
        self.assertEqual(_overlap_coefficient(s, s), 1.0)

    def test_quote_keywords_extracts_distinctive_words(self):
        kw = _quote_keywords(_hydro_quote())
        self.assertIn("calpeda", kw)
        self.assertIn("pump", kw)
        # Generic words should be excluded
        self.assertNotIn("of", kw)
        self.assertNotIn("and", kw)


# ---------------------------------------------------------------------------
# Integration: session-closing fix prevents stale quote accumulation
# ---------------------------------------------------------------------------

class TestStaleSessionExclusion(unittest.TestCase):

    def test_create_quote_vs_quote_closes_all_active_sessions(self):
        state = _empty_state()
        state, _ = create_quote_session(_kingdom_quote(), state)
        state, _ = create_quote_session(_hydro_quote(), state)

        # Both sessions are still active before comparison is triggered
        active_before = [s for s in state["sessions"] if s["status"] == "active"]
        self.assertEqual(len(active_before), 2)

        quotes = gather_quote_docs_for_comparison(state, max_quotes=3)
        # Filter to just the Calpeda pair
        calpeda_quotes = [q for q in quotes if "Hydro" in (q.get("supplier_name") or "")]
        # add IYS
        state, _ = create_quote_session(_iys_quote(), state)
        quotes = gather_quote_docs_for_comparison(state, max_quotes=3)

        state, session = create_quote_vs_quote_session(quotes[:2], state)

        # All sessions except the new quote_vs_quote session must be closed
        active_after = [
            s for s in state["sessions"]
            if s["status"] == "active" and s["session_id"] != session["session_id"]
        ]
        self.assertEqual(len(active_after), 0, "Stale active sessions remain after create_quote_vs_quote_session")

    def test_gather_finds_no_quotes_after_session_closed(self):
        """After quote_vs_quote session is created, gathering finds no additional quotes."""
        state = _empty_state()
        state, _ = create_quote_session(_kingdom_quote(), state)
        state, _ = create_quote_session(_hydro_quote(), state)
        quotes = gather_quote_docs_for_comparison(state, max_quotes=3)
        state, _ = create_quote_vs_quote_session(quotes, state)

        # Now upload fresh quotes and confirm Kingdom session doesn't bleed in
        state, _ = create_quote_session(_iys_quote(), state)
        fresh_quotes = gather_quote_docs_for_comparison(state, max_quotes=3)
        suppliers = {q["supplier_name"] for q in fresh_quotes}
        self.assertNotIn("Kingdom Ocean Management Sarl", suppliers)


# ---------------------------------------------------------------------------
# End-to-end: _handle_quote_compare_intent with exclusion notice
# ---------------------------------------------------------------------------

class TestHandleQuoteCompareIntentExclusionNotice(unittest.TestCase):

    def _build_state_with_three_quotes(self):
        state = _empty_state()
        state, _ = create_quote_session(_kingdom_quote(), state)
        state, _ = create_quote_session(_hydro_quote(), state)
        state, _ = create_quote_session(_iys_quote(), state)
        return state

    def test_exclusion_notice_included_in_response(self):
        state = self._build_state_with_three_quotes()
        from whatsapp_app import _handle_quote_compare_intent
        response, _ = _handle_quote_compare_intent(state)
        self.assertIn("NOTE:", response)
        self.assertIn("Kingdom Ocean Management Sarl", response)
        self.assertIn("excluded", response.lower())

    def test_calpeda_suppliers_are_compared(self):
        state = self._build_state_with_three_quotes()
        from whatsapp_app import _handle_quote_compare_intent
        response, _ = _handle_quote_compare_intent(state)
        self.assertTrue(
            "Hydro Electrique Marine" in response or "International Yacht Services" in response,
            "Neither Calpeda supplier appears in comparison response",
        )

    def test_bundled_vs_itemised_flag(self):
        """Bundled quote triggers NOTE in response."""
        bundled = _make_quote(
            "Bundled Supplier", 3500.0,
            items=[{"description": "Calpeda pump system complete", "quantity": 1, "line_total": 3500.0}],
        )
        itemised = _hydro_quote()
        state = _empty_state()
        state, _ = create_quote_session(bundled, state)
        state, _ = create_quote_session(itemised, state)
        from whatsapp_app import _handle_quote_compare_intent
        response, _ = _handle_quote_compare_intent(state)
        self.assertIn("NOTE:", response)
        self.assertIn("bundled", response.lower())

    def test_two_related_quotes_no_exclusion_notice(self):
        """When all quotes are related, no exclusion notice is added."""
        state = _empty_state()
        state, _ = create_quote_session(_hydro_quote(), state)
        state, _ = create_quote_session(_iys_quote(), state)
        from whatsapp_app import _handle_quote_compare_intent
        response, _ = _handle_quote_compare_intent(state)
        self.assertNotIn("excluded", response.lower())


if __name__ == "__main__":
    unittest.main()
