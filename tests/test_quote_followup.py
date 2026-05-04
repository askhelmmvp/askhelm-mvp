"""Tests for quote comparison follow-up routing and line-item comparison logic."""
import unittest
from unittest.mock import patch, MagicMock

# ---- Fixtures ---------------------------------------------------------------

_IYS_ITEMS = [
    {"description": "10678100 Door Lock Miele", "quantity": 2, "unit_rate": 22.50, "line_total": 45.00},
    {"description": "10195860 Drum Seal Miele", "quantity": 2, "unit_rate": 14.00, "line_total": 28.00},
    {"description": "12082971 Miele Electro Magnetic Lock for PW811", "quantity": 1, "unit_rate": 85.00, "line_total": 85.00},
    {"description": "11531441 Inlet Valve Miele", "quantity": 1, "unit_rate": 55.00, "line_total": 55.00},
    {"description": "5640883 Fluff Filter BG Miele", "quantity": 2, "unit_rate": 18.00, "line_total": 36.00},
]

_KINGDOM_ITEMS = [
    {"description": "10678100 Door Lock Miele", "quantity": 2, "unit_rate": 19.00, "line_total": 38.00},
    {"description": "10195860 Drum Seal Miele", "quantity": 2, "unit_rate": 11.00, "line_total": 22.00},
    {"description": "9790308 Miele Lock for PW811", "quantity": 1, "unit_rate": 62.00, "line_total": 62.00},
    {"description": "11553570 Inlet Valve Miele", "quantity": 1, "unit_rate": 48.00, "line_total": 48.00},
    {"description": "5640883 Fluff Filter BG Miele", "quantity": 2, "unit_rate": 15.00, "line_total": 30.00},
    {"description": "Inbound Shipping", "quantity": 1, "unit_rate": 45.00, "line_total": 45.00},
]


def _iys_doc(**kw):
    return {
        "document_id": "iys-001",
        "doc_type": "quote",
        "supplier_name": "International Yacht Services B.V.",
        "document_number": "78987",
        "currency": "EUR",
        "total": 4904.80,
        "subtotal": 4053.55,
        "tax": 851.25,
        "line_items": _IYS_ITEMS,
        **kw,
    }


def _kingdom_doc(**kw):
    return {
        "document_id": "kingdom-001",
        "doc_type": "quote",
        "supplier_name": "Kingdom Ocean Management Sàrl",
        "document_number": "CH-2026-00381",
        "currency": "EUR",
        "total": 4006.68,
        "subtotal": None,
        "tax": None,
        "line_items": _KINGDOM_ITEMS,
        **kw,
    }


def _comparison_data():
    from domain.compare import compare_documents
    doc_a = _iys_doc()
    doc_b = _kingdom_doc()
    comp = compare_documents(doc_a, doc_b)
    return {"doc_a": doc_a, "doc_b": doc_b, "comparison": comp}


def _active_session_state(last_comparison):
    """Minimal state dict with an active session that has last_comparison set."""
    return {
        "user_id": "",
        "sessions": [
            {
                "session_id": "sess-1",
                "session_type": "quote_vs_quote",
                "status": "active",
                "document_ids": ["iys-001", "kingdom-001"],
                "anchor_doc_id": "iys-001",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "last_comparison": last_comparison,
            }
        ],
        "documents": [_iys_doc(session_id="sess-1"), _kingdom_doc(session_id="sess-1")],
        "active_session_id": "sess-1",
        "last_context": {},
        "pending_invoice": None,
    }


# ---- Test 1: existing comparison still works --------------------------------

class TestQuoteCompareStillWorks(unittest.TestCase):
    """Regression: 'compare quotes' must still produce comparison output."""

    def test_compare_quotes_intent(self):
        from domain.intent import classify_text
        self.assertEqual(classify_text("compare quotes"), "quote_compare")

    def test_compare_quotes_returns_comparison(self):
        comp_data = _comparison_data()
        state = _active_session_state(comp_data)
        with patch("whatsapp_app.gather_quote_docs_for_comparison", return_value=[_iys_doc(), _kingdom_doc()]):
            from whatsapp_app import _handle_quote_compare_intent
            result, _ = _handle_quote_compare_intent(state)
        # Response contains either HIGHER or LOWER depending on which quote is anchor
        self.assertTrue(
            "HIGHER" in result.upper() or "LOWER" in result.upper(),
            f"Expected HIGHER or LOWER in comparison result, got: {result[:200]}"
        )


# ---- Test 2: "are the parts and quantities the same?" -----------------------

class TestPartsQuantitiesIntent(unittest.TestCase):
    """'are the parts and quantities the same?' must route to quote_compare_followup."""

    def test_parts_same_intent(self):
        from domain.intent import classify_text
        self.assertEqual(classify_text("are the parts the same?"), "quote_compare_followup")

    def test_quantities_same_intent(self):
        from domain.intent import classify_text
        self.assertEqual(classify_text("are the quantities the same?"), "quote_compare_followup")

    def test_parts_and_quantities_same_intent(self):
        from domain.intent import classify_text
        self.assertEqual(classify_text("are the parts and quantities the same?"), "quote_compare_followup")

    def test_response_has_part_check_section(self):
        comp_data = _comparison_data()
        from whatsapp_app import build_line_item_comparison_response
        result = build_line_item_comparison_response(comp_data)
        self.assertIn("DECISION:", result)
        self.assertIn("PART CHECK:", result)
        self.assertIn("ACTIONS:", result)

    def test_response_not_compliance_fallback(self):
        comp_data = _comparison_data()
        state = _active_session_state(comp_data)
        from whatsapp_app import _handle_text_message
        result, _ = _handle_text_message("are the parts and quantities the same?", state)
        self.assertNotIn("Not explicitly covered in the loaded documents", result)
        self.assertIn("PART", result)


# ---- Test 3: "which quote should I go for?" ---------------------------------

class TestWhichQuoteIntent(unittest.TestCase):
    """'which quote should I go for?' must give a procurement recommendation."""

    def test_which_quote_should_i_go_for_intent(self):
        from domain.intent import classify_text
        self.assertEqual(classify_text("which quote should I go for?"), "quote_compare_followup")

    def test_response_has_decision_and_actions(self):
        comp_data = _comparison_data()
        from whatsapp_app import build_procurement_recommendation
        result = build_procurement_recommendation(comp_data)
        self.assertIn("DECISION:", result)
        self.assertIn("ACTIONS:", result)

    def test_response_not_generic_market_price(self):
        comp_data = _comparison_data()
        state = _active_session_state(comp_data)
        from whatsapp_app import _handle_text_message
        result, _ = _handle_text_message("which quote should I go for?", state)
        self.assertNotIn("PRICE RANGE ESTIMATE", result)
        self.assertNotIn("Not explicitly covered in the loaded documents", result)
        self.assertIn("DECISION:", result)

    def test_recommendation_names_cheaper_supplier(self):
        comp_data = _comparison_data()
        from whatsapp_app import build_procurement_recommendation
        result = build_procurement_recommendation(comp_data)
        # Kingdom (4006.68) is cheaper than IYS (4904.80)
        self.assertIn("Kingdom", result)

    def test_who_should_i_order_from_routes_correctly(self):
        from domain.intent import classify_text
        self.assertEqual(classify_text("who should I order from?"), "quote_compare_followup")


# ---- Test 4: IYS vs Kingdom — part number difference detection --------------

class TestLineItemComparisonLogic(unittest.TestCase):
    """build_line_item_comparison must identify same and different-PN lines."""

    def setUp(self):
        from domain.compare import build_line_item_comparison
        self.result = build_line_item_comparison(_iys_doc(), _kingdom_doc())

    def test_has_pn_differences_is_true(self):
        self.assertTrue(self.result["has_pn_differences"])

    def test_same_pn_items_matched_without_pn_differs(self):
        door_lock = next(
            (m for m in self.result["matched"]
             if m["pn_a"] == "10678100"),
            None,
        )
        self.assertIsNotNone(door_lock, "10678100 Door Lock should be matched")
        self.assertFalse(door_lock["pn_differs"])

    def test_different_pn_items_flagged(self):
        pw811_lock = next(
            (m for m in self.result["matched"]
             if m["pn_a"] == "12082971" or m["pn_b"] == "9790308"),
            None,
        )
        self.assertIsNotNone(pw811_lock, "PW811 lock with different PNs should be matched")
        self.assertTrue(pw811_lock["pn_differs"])

    def test_inlet_valve_pn_difference_detected(self):
        inlet = next(
            (m for m in self.result["matched"]
             if m["pn_a"] == "11531441" or m["pn_b"] == "11553570"),
            None,
        )
        self.assertIsNotNone(inlet, "Inlet valve with different PNs should be matched")
        self.assertTrue(inlet["pn_differs"])

    def test_shipping_in_only_in_b(self):
        only_b_descs = [(i.get("description") or "").lower() for i in self.result["only_in_b"]]
        self.assertTrue(any("shipping" in d for d in only_b_descs), "Inbound Shipping should be only in Kingdom")

    def test_line_item_response_flags_pn_differences(self):
        from domain.compare import compare_documents
        from whatsapp_app import build_line_item_comparison_response
        comp = compare_documents(_iys_doc(), _kingdom_doc())
        cd = {"doc_a": _iys_doc(), "doc_b": _kingdom_doc(), "comparison": comp}
        result = build_line_item_comparison_response(cd)
        self.assertIn("check equivalence", result)


# ---- Test 5: "sample the expensive items against market price" --------------

class TestMarketSampling(unittest.TestCase):
    """Market price sampling must call check_market_price on top-value items."""

    def test_sample_expensive_items_intent(self):
        from domain.intent import classify_text
        self.assertEqual(classify_text("sample the expensive items against market price"), "quote_compare_followup")

    def test_sampling_calls_check_market_price(self):
        comp_data = _comparison_data()
        mock_response = "DECISION:\nACCEPTABLE PRICE\n\nWHY:\nMarket assessment.\n\nRECOMMENDED ACTIONS:\n• Proceed"
        with patch("whatsapp_app.check_market_price", return_value=mock_response) as mock_fn:
            from whatsapp_app import _handle_quote_compare_followup
            _handle_quote_compare_followup(
                "sample the expensive items against market price",
                comp_data,
                {"user_id": ""},
            )
        mock_fn.assert_called_once()
        call_arg = mock_fn.call_args[0][0]
        # The query should mention some item descriptions from the quotes
        self.assertIn("International Yacht Services", call_arg)


# ---- Test 6: no active comparison session -----------------------------------

class TestNoActiveComparison(unittest.TestCase):
    """Follow-up without active comparison must give helpful guidance, not compliance."""

    def test_parts_same_without_comparison_is_not_compliance(self):
        empty_state = {
            "user_id": "",
            "sessions": [],
            "documents": [],
            "active_session_id": None,
            "last_context": {},
            "pending_invoice": None,
        }
        from whatsapp_app import _handle_text_message
        result, _ = _handle_text_message("are the parts and quantities the same?", empty_state)
        self.assertNotIn("Not explicitly covered in the loaded documents", result)

    def test_no_comparison_response_prompts_upload(self):
        from whatsapp_app import _handle_quote_compare_followup
        result = _handle_quote_compare_followup("are the parts the same?", None, {"user_id": ""})
        self.assertIn("NO ACTIVE COMPARISON", result)


# ---- Test 7: compliance regression ------------------------------------------

class TestComplianceRegression(unittest.TestCase):
    """Compliance questions must still go to compliance when no commercial context."""

    def test_marpol_still_compliance(self):
        from domain.intent import classify_text
        self.assertEqual(classify_text("does marpol annex vi apply to us?"), "compliance_question")

    def test_fire_pump_overdue_still_compliance(self):
        from domain.intent import classify_text
        result = classify_text("our fire pump test is overdue, is that a problem?")
        self.assertEqual(result, "compliance_question")


if __name__ == "__main__":
    unittest.main()
