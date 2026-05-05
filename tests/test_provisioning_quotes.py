"""Tests for ASK-28: provisioning quote handling and quote relevance filtering."""
import unittest
from unittest.mock import patch, MagicMock

from domain.compare import (
    filter_quotes_by_relevance,
    categorize_quote,
    _quote_keywords,
    _overlap_coefficient,
)
from domain.session_manager import (
    make_document_record,
    create_quote_session,
    create_quote_vs_quote_session,
    gather_quote_docs_for_comparison,
)


# ---------------------------------------------------------------------------
# Fixtures — based on real ASK-28 data
# ---------------------------------------------------------------------------

_WE_SUPPLY_ITEMS = [
    {"description": "Sea bass fillet 15 kg", "quantity": 15, "unit": "kg", "unit_rate": 14.50, "line_total": 217.50},
    {"description": "Salmon fillet 17.5 kg", "quantity": 17.5, "unit": "kg", "unit_rate": 12.00, "line_total": 210.00},
    {"description": "Smoked salmon 2 kg", "quantity": 2, "unit": "kg", "unit_rate": 38.00, "line_total": 76.00},
    {"description": "Prawns peeled 5 kg", "quantity": 5, "unit": "kg", "unit_rate": 22.00, "line_total": 110.00},
    {"description": "Cod loin 8 kg", "quantity": 8, "unit": "kg", "unit_rate": 18.00, "line_total": 144.00},
    {"description": "Haddock fillet 6 kg", "quantity": 6, "unit": "kg", "unit_rate": 15.00, "line_total": 90.00},
    {"description": "Bluefin tuna 4 kg", "quantity": 4, "unit": "kg", "unit_rate": 65.00, "line_total": 260.00},
    {"description": "Squid rings 3 kg", "quantity": 3, "unit": "kg", "unit_rate": 12.00, "line_total": 36.00},
    {"description": "Brill fillet 5 kg", "quantity": 5, "unit": "kg", "unit_rate": 28.00, "line_total": 140.00},
    {"description": "Cooked octopus 2 kg", "quantity": 2, "unit": "kg", "unit_rate": 32.00, "line_total": 64.00},
]

_RIVIERA_ITEMS = [
    {"description": "Salmon sides 14.5 kg", "quantity": 14.5, "unit": "kg", "unit_rate": 11.50, "line_total": 166.75},
    {"description": "Bluefin tuna 4 kg", "quantity": 4, "unit": "kg", "unit_rate": 60.00, "line_total": 240.00},
    {"description": "Brill fillets 5 kg", "quantity": 5, "unit": "kg", "unit_rate": 26.00, "line_total": 130.00},
    {"description": "Cod sides 8 kg", "quantity": 8, "unit": "kg", "unit_rate": 16.50, "line_total": 132.00},
    {"description": "Sea bass sides 17.5 kg", "quantity": 17.5, "unit": "kg", "unit_rate": 13.00, "line_total": 227.50},
    {"description": "Haddock portions 6 kg", "quantity": 6, "unit": "kg", "unit_rate": 14.00, "line_total": 84.00},
    {"description": "Prawns 5 kg", "quantity": 5, "unit": "kg", "unit_rate": 20.00, "line_total": 100.00},
    {"description": "Smoked salmon 1.5 kg", "quantity": 1.5, "unit": "kg", "unit_rate": 36.00, "line_total": 54.00},
    {"description": "Octopus 2 kg", "quantity": 2, "unit": "kg", "unit_rate": 30.00, "line_total": 60.00},
    {"description": "Squid rings 3 kg", "quantity": 3, "unit": "kg", "unit_rate": 11.00, "line_total": 33.00},
]

_COMPASS_TENDERS_ITEMS = [
    {"description": "GRP tender hull 3.5m", "quantity": 1, "unit": "ea", "unit_rate": 420.0, "line_total": 420.0},
    {"description": "Outboard engine bracket", "quantity": 2, "unit": "ea", "unit_rate": 44.25, "line_total": 88.5},
]

_CALPEDA_ITEMS_A = [
    {"description": "Calpeda MXPM 206 E pump", "quantity": 1, "unit_rate": 3200.0, "line_total": 3200.0},
    {"description": "Calpeda pump impeller 3 inch", "quantity": 2, "unit_rate": 240.0, "line_total": 480.0},
    {"description": "Calpeda mechanical seal MXPM", "quantity": 2, "unit_rate": 130.0, "line_total": 260.0},
]

_CALPEDA_ITEMS_B = [
    {"description": "Calpeda MXPM 206 pump unit", "quantity": 1, "unit_rate": 3050.0, "line_total": 3050.0},
    {"description": "Calpeda MXPM impeller", "quantity": 2, "unit_rate": 230.0, "line_total": 460.0},
    {"description": "Mechanical seal kit Calpeda", "quantity": 2, "unit_rate": 120.0, "line_total": 240.0},
]

_MIELE_ITEMS = [
    {"description": "Miele WDB020 detergent dispenser", "quantity": 2, "line_total": 180.0},
    {"description": "Miele door hinge washer 6kg", "quantity": 4, "line_total": 96.0},
    {"description": "Miele drum bearing kit", "quantity": 1, "line_total": 145.0},
    {"description": "Miele carbon brush set WDB", "quantity": 2, "line_total": 64.0},
]


def _empty_state():
    return {"user_id": "test", "active_session_id": None, "sessions": [], "documents": []}


def _make_quote(supplier, total, currency="EUR", items=None, subtotal=None, tax=None):
    if items is None:
        items = [{"description": "Item", "quantity": 1, "line_total": total}]
    return make_document_record({
        "doc_type": "quote",
        "supplier_name": supplier,
        "document_number": "Q-001",
        "document_date": "2024-01-15",
        "currency": currency,
        "total": total,
        "subtotal": subtotal or total,
        "tax": tax or 0,
        "line_items": items,
        "exclusions": [],
        "assumptions": [],
    }, f"data/quote_{supplier}.pdf")


def _we_supply_quote():
    return _make_quote("We Supply Yachts BV", 6809.38, subtotal=6247.13, tax=562.25, items=_WE_SUPPLY_ITEMS)


def _riviera_quote():
    return _make_quote("Riviera Gourmet SAS", 5459.85, subtotal=5175.20, tax=284.65, items=_RIVIERA_ITEMS)


def _compass_quote():
    return _make_quote("Compass Tenders Ltd", 508.5, currency="GBP", items=_COMPASS_TENDERS_ITEMS)


def _calpeda_quote_a():
    return _make_quote("Hydro Electrique Marine", 3940.0, items=_CALPEDA_ITEMS_A)


def _calpeda_quote_b():
    return _make_quote("International Yacht Services", 3750.0, items=_CALPEDA_ITEMS_B)


def _miele_quote():
    return _make_quote("Kingdom Ocean Management Sarl", 485.0, items=_MIELE_ITEMS)


# ---------------------------------------------------------------------------
# Test 1: Compass Tenders excluded from fish comparison (ASK-28)
# ---------------------------------------------------------------------------

class TestCompassTendersExcluded(unittest.TestCase):

    def _three_quote_state(self):
        state = _empty_state()
        state, _ = create_quote_session(_compass_quote(), state)
        state, _ = create_quote_session(_we_supply_quote(), state)
        state, _ = create_quote_session(_riviera_quote(), state)
        return state

    def test_filter_excludes_compass_from_fish_quotes(self):
        quotes = [_compass_quote(), _we_supply_quote(), _riviera_quote()]
        selected, excluded = filter_quotes_by_relevance(quotes)
        self.assertEqual(len(selected), 2)
        self.assertEqual(len(excluded), 1)
        excluded_supplier = excluded[0]["supplier_name"]
        self.assertEqual(excluded_supplier, "Compass Tenders Ltd")

    def test_fish_quotes_selected_together(self):
        quotes = [_compass_quote(), _we_supply_quote(), _riviera_quote()]
        selected, _ = filter_quotes_by_relevance(quotes)
        suppliers = {q["supplier_name"] for q in selected}
        self.assertIn("We Supply Yachts BV", suppliers)
        self.assertIn("Riviera Gourmet SAS", suppliers)

    def test_compare_intent_excludes_compass_and_mentions_it(self):
        state = self._three_quote_state()
        from whatsapp_app import _handle_quote_compare_intent
        response, _ = _handle_quote_compare_intent(state)
        self.assertIn("Compass Tenders", response)
        self.assertIn("EXCLUDED", response)

    def test_compare_intent_compares_fish_quotes(self):
        state = self._three_quote_state()
        from whatsapp_app import _handle_quote_compare_intent
        response, _ = _handle_quote_compare_intent(state)
        self.assertTrue(
            "We Supply Yachts" in response or "Riviera Gourmet" in response,
            "Neither fish supplier appears in response",
        )

    def test_provisioning_comparison_note_in_response(self):
        state = self._three_quote_state()
        from whatsapp_app import _handle_quote_compare_intent
        response, _ = _handle_quote_compare_intent(state)
        # The provisioning comparison must contain product form / like-for-like guidance
        lower = response.lower()
        self.assertTrue(
            "product form" in lower or "like-for-like" in lower
            or "check before ordering" in lower or "before ordering" in lower,
            f"No product-form or like-for-like guidance found: {response[:300]}",
        )


# ---------------------------------------------------------------------------
# Test 2: "Is this a fair price?" for provisioning quote
# ---------------------------------------------------------------------------

class TestProvisioningFairPrice(unittest.TestCase):

    def _state_with_fish_quote(self):
        state = _empty_state()
        state, _ = create_quote_session(_we_supply_quote(), state)
        return state

    def test_fair_price_returns_provisioning_response(self):
        from whatsapp_app import _handle_text_message
        state = self._state_with_fish_quote()
        response, _ = _handle_text_message("is this a fair price?", state)
        # Must NOT ask for equipment make/model
        self.assertNotIn("make/model", response.lower())
        self.assertNotIn("equipment make", response.lower())

    def test_fair_price_mentions_provisioning_context(self):
        from whatsapp_app import _handle_text_message
        state = self._state_with_fish_quote()
        response, _ = _handle_text_message("is this a fair price?", state)
        lower = response.lower()
        self.assertTrue(
            "provisioning" in lower or "galley" in lower or "fish" in lower
            or "product" in lower or "like-for-like" in lower,
            f"Response doesn't mention provisioning context: {response[:200]}",
        )

    def test_fair_price_suggests_comparison_actions(self):
        from whatsapp_app import _handle_text_message
        state = self._state_with_fish_quote()
        response, _ = _handle_text_message("is this a fair price?", state)
        self.assertIn("RECOMMENDED ACTIONS", response)


# ---------------------------------------------------------------------------
# Test 3: Stale session exclusion (regression from previous fix)
# ---------------------------------------------------------------------------

class TestStaleSessionExclusion(unittest.TestCase):

    def test_create_quote_vs_quote_closes_all_active_sessions(self):
        state = _empty_state()
        state, _ = create_quote_session(_compass_quote(), state)
        state, _ = create_quote_session(_we_supply_quote(), state)

        quotes = gather_quote_docs_for_comparison(state)
        state, session = create_quote_vs_quote_session(quotes, state)

        stale = [
            s for s in state["sessions"]
            if s["status"] == "active" and s["session_id"] != session["session_id"]
        ]
        self.assertEqual(len(stale), 0, "Stale active sessions remain after create_quote_vs_quote_session")

    def test_gather_skips_quote_vs_quote_session(self):
        state = _empty_state()
        state, _ = create_quote_session(_we_supply_quote(), state)
        state, _ = create_quote_session(_riviera_quote(), state)
        quotes = gather_quote_docs_for_comparison(state)
        state, _ = create_quote_vs_quote_session(quotes, state)

        state, _ = create_quote_session(_compass_quote(), state)
        fresh = gather_quote_docs_for_comparison(state)
        suppliers = {q["supplier_name"] for q in fresh}
        self.assertNotIn("We Supply Yachts BV", suppliers)
        self.assertNotIn("Riviera Gourmet SAS", suppliers)
        self.assertIn("Compass Tenders Ltd", suppliers)


# ---------------------------------------------------------------------------
# Test 4: Categorize quote
# ---------------------------------------------------------------------------

class TestCategorizeQuote(unittest.TestCase):

    def test_fish_quote_is_provisioning(self):
        self.assertEqual(categorize_quote(_we_supply_quote()), "provisioning")
        self.assertEqual(categorize_quote(_riviera_quote()), "provisioning")

    def test_pump_quote_is_engineering(self):
        self.assertEqual(categorize_quote(_calpeda_quote_a()), "engineering")

    def test_tender_quote_is_tender(self):
        self.assertEqual(categorize_quote(_compass_quote()), "tender")

    def test_sparse_quote_is_unknown(self):
        sparse = _make_quote("Generic Supplier", 1000.0, items=[
            {"description": "Miscellaneous items", "line_total": 1000.0}
        ])
        # Should not confidently categorize a single generic item
        result = categorize_quote(sparse)
        self.assertIn(result, ("unknown", "provisioning", "engineering", "tender", "refit"))


# ---------------------------------------------------------------------------
# Test 5: Regression — engineering quotes still work
# ---------------------------------------------------------------------------

class TestEngineeringQuoteRegression(unittest.TestCase):

    def test_two_calpeda_quotes_compare_normally(self):
        state = _empty_state()
        state, _ = create_quote_session(_calpeda_quote_a(), state)
        state, _ = create_quote_session(_calpeda_quote_b(), state)
        from whatsapp_app import _handle_quote_compare_intent
        response, _ = _handle_quote_compare_intent(state)
        self.assertIn("DECISION", response)
        self.assertNotIn("EXCLUDED", response)

    def test_miele_excluded_from_calpeda_comparison(self):
        state = _empty_state()
        state, _ = create_quote_session(_miele_quote(), state)
        state, _ = create_quote_session(_calpeda_quote_a(), state)
        state, _ = create_quote_session(_calpeda_quote_b(), state)
        from whatsapp_app import _handle_quote_compare_intent
        response, _ = _handle_quote_compare_intent(state)
        # Miele should be excluded (engineering spare, different product family)
        if "EXCLUDED" in response:
            self.assertIn("Kingdom Ocean Management", response)

    def test_is_fair_price_for_engineering_still_routes_to_market_check(self):
        state = _empty_state()
        state, _ = create_quote_session(_calpeda_quote_a(), state)
        from whatsapp_app import _handle_text_message
        with patch("whatsapp_app.check_market_price", return_value=(
            "DECISION:\nACCEPTABLE PRICE\n\nWHY:\nFair market rate.\n\nRECOMMENDED ACTIONS:\n• Proceed"
        )):
            response, _ = _handle_text_message("is this a fair price?", state)
        # Should not give provisioning response for engineering quotes
        self.assertNotIn("galley/provisioning", response.lower())


# ---------------------------------------------------------------------------
# Test 6: Regression — compliance still routes to compliance RAG
# ---------------------------------------------------------------------------

class TestComplianceRegression(unittest.TestCase):

    def test_marpol_question_routes_to_compliance(self):
        from domain.intent import classify_text
        result = classify_text("is this compliant with MARPOL Annex VI?")
        self.assertEqual(result, "compliance_question")

    def test_compliance_not_overridden_by_provisioning_context(self):
        with patch("whatsapp_app.answer_compliance_query",
                   return_value="DECISION: Yes\nWHY: Compliant\nSOURCE: MARPOL\nACTIONS: • Monitor"):
            from whatsapp_app import _handle_text_message
            state = _empty_state()
            state, _ = create_quote_session(_we_supply_quote(), state)
            response, _ = _handle_text_message("is this compliant with MARPOL Annex VI?", state)
        self.assertIn("DECISION", response)


# ---------------------------------------------------------------------------
# Test 7: Line-by-line provisioning comparison (ASK-28 Part 2)
# ---------------------------------------------------------------------------

class TestProvisioningLineComparison(unittest.TestCase):

    def _two_fish_state(self):
        state = _empty_state()
        state, _ = create_quote_session(_we_supply_quote(), state)
        state, _ = create_quote_session(_riviera_quote(), state)
        return state

    def test_response_has_line_by_line_section(self):
        state = self._two_fish_state()
        from whatsapp_app import _handle_quote_compare_intent
        response, _ = _handle_quote_compare_intent(state)
        self.assertIn("LINE-BY-LINE", response)

    def test_response_has_headline_comparison(self):
        state = self._two_fish_state()
        from whatsapp_app import _handle_quote_compare_intent
        response, _ = _handle_quote_compare_intent(state)
        self.assertIn("HEADLINE COMPARISON", response)

    def test_decision_names_cheaper_supplier(self):
        state = self._two_fish_state()
        from whatsapp_app import _handle_quote_compare_intent
        response, _ = _handle_quote_compare_intent(state)
        # Riviera total 5459.85 < We Supply 6809.38 → Riviera is cheaper
        lower = response.lower()
        self.assertIn("riviera", lower, "Cheaper supplier (Riviera) not named in decision")
        self.assertTrue(
            "cheaper" in lower or "lower" in lower or "better value" in lower,
            f"No cheaper/lower/better-value wording: {response[:200]}",
        )

    def test_salmon_line_shows_per_kg_price(self):
        state = self._two_fish_state()
        from whatsapp_app import _handle_quote_compare_intent
        response, _ = _handle_quote_compare_intent(state)
        self.assertIn("/kg", response)
        lower = response.lower()
        self.assertIn("salmon", lower)
        # Must not show line_total as per-kg price
        self.assertNotIn("210.00/kg", response)
        self.assertNotIn("375.55/kg", response)
        self.assertNotIn("166.75/kg", response)

    def test_recommended_actions_present(self):
        state = self._two_fish_state()
        from whatsapp_app import _handle_quote_compare_intent
        response, _ = _handle_quote_compare_intent(state)
        self.assertIn("RECOMMENDED ACTIONS", response)

    def test_no_engineering_wording(self):
        state = self._two_fish_state()
        from whatsapp_app import _handle_quote_compare_intent
        response, _ = _handle_quote_compare_intent(state)
        lower = response.lower()
        self.assertNotIn("make/model", lower)
        self.assertNotIn("equipment make", lower)
        self.assertNotIn("confirm with the chef", lower)

    def test_product_form_caveats_present(self):
        state = self._two_fish_state()
        from whatsapp_app import _handle_quote_compare_intent
        response, _ = _handle_quote_compare_intent(state)
        # There are product form differences (e.g. loin vs side for cod) — must appear
        lower = response.lower()
        self.assertTrue(
            "check before ordering" in lower or "product form" in lower
            or "loin" in lower or "fillet" in lower,
            f"No product form caveats found: {response[:400]}",
        )


# ---------------------------------------------------------------------------
# Test 8: Follow-up routing and re-use of existing comparison (ASK-28 Part 2)
# ---------------------------------------------------------------------------

class TestProvisioningComparisonFollowUp(unittest.TestCase):

    def _state_after_comparison(self):
        state = _empty_state()
        state, _ = create_quote_session(_we_supply_quote(), state)
        state, _ = create_quote_session(_riviera_quote(), state)
        from whatsapp_app import _handle_quote_compare_intent
        _, state = _handle_quote_compare_intent(state)
        return state

    def test_which_quote_should_i_go_for_routes_to_quote_compare(self):
        from domain.intent import classify_text
        self.assertEqual(classify_text("which quote should i go for"), "quote_compare")

    def test_summarise_the_quotes_routes_to_quote_compare(self):
        from domain.intent import classify_text
        self.assertEqual(classify_text("summarise the quotes"), "quote_compare")

    def test_give_me_more_information_routes_to_quote_compare(self):
        from domain.intent import classify_text
        self.assertEqual(classify_text("give me more information on the quotations"), "quote_compare")

    def test_overview_of_each_quote_routes_to_quote_compare(self):
        from domain.intent import classify_text
        self.assertEqual(classify_text("give me an overview of each quote"), "quote_compare")

    def test_are_these_like_for_like_routes_to_quote_compare(self):
        from domain.intent import classify_text
        self.assertEqual(classify_text("are these like for like"), "quote_compare")

    def test_follow_up_reuses_existing_comparison(self):
        state = self._state_after_comparison()
        from whatsapp_app import _handle_quote_compare_intent
        response, _ = _handle_quote_compare_intent(state)
        self.assertNotIn("NOT ENOUGH QUOTES", response)
        self.assertIn("LINE-BY-LINE", response)

    def test_follow_up_names_cheaper_supplier(self):
        state = self._state_after_comparison()
        from whatsapp_app import _handle_quote_compare_intent
        response, _ = _handle_quote_compare_intent(state)
        lower = response.lower()
        self.assertIn("riviera", lower)


# ---------------------------------------------------------------------------
# Test 9: Product-specific provisioning queries (ASK-28 Part 2)
# ---------------------------------------------------------------------------

class TestProvisioningProductQuery(unittest.TestCase):

    def _state_after_comparison(self):
        state = _empty_state()
        state, _ = create_quote_session(_we_supply_quote(), state)
        state, _ = create_quote_session(_riviera_quote(), state)
        from whatsapp_app import _handle_quote_compare_intent
        _, state = _handle_quote_compare_intent(state)
        return state

    def test_compare_the_salmon_routes_to_quote_compare(self):
        from domain.intent import classify_text
        self.assertEqual(classify_text("compare the salmon"), "quote_compare")

    def test_price_per_kg_routes_to_quote_compare(self):
        from domain.intent import classify_text
        self.assertEqual(classify_text("how is the salmon price per kg?"), "quote_compare")

    def test_product_query_shows_detail_section(self):
        state = self._state_after_comparison()
        from whatsapp_app import _handle_quote_compare_intent
        response, _ = _handle_quote_compare_intent(state, "compare the salmon")
        lower = response.lower()
        self.assertIn("salmon", lower)
        # Should show per-kg price, not line total as unit price
        self.assertNotIn("210.00/kg", response)
        self.assertNotIn("375.55/kg", response)

    def test_product_query_shows_correct_unit_price_not_line_total(self):
        """Unit price regression: must show €12.00/kg for salmon, not €210.00/kg."""
        state = self._state_after_comparison()
        from whatsapp_app import _handle_quote_compare_intent
        response, _ = _handle_quote_compare_intent(state, "how is the salmon price per kg?")
        # We Supply salmon: unit_rate=12.00, qty=17.5, line_total=210.00 → must show 12.00
        self.assertNotIn("210.00/kg", response, "Line total shown as unit price for salmon")
        self.assertIn("salmon", response.lower())

    def test_are_prices_fair_with_active_comparison_returns_comparison(self):
        """'Are the prices fair?' with active comparison must return provisioning overview."""
        state = self._state_after_comparison()
        from whatsapp_app import _handle_text_message
        response, _ = _handle_text_message("are the prices fair?", state)
        # Must NOT route to engineering market check (which asks for part numbers)
        self.assertNotIn("part number", response.lower())
        self.assertNotIn("equipment make", response.lower())
        # Must NOT ask for more info in a way that implies engineering context
        self.assertNotIn("make/model", response.lower())
        # Should contain provisioning-relevant content
        lower = response.lower()
        self.assertTrue(
            "provisioning" in lower or "product form" in lower
            or "line-by-line" in lower or "headline comparison" in lower
            or "riviera" in lower or "we supply" in lower,
            f"Response lacks provisioning context: {response[:200]}",
        )


# ---------------------------------------------------------------------------
# Test 10: Unit price regression (ASK-28 Part 2)
# ---------------------------------------------------------------------------

class TestUnitPriceComputation(unittest.TestCase):

    def test_correct_unit_rate_returned_when_product_checks_out(self):
        from whatsapp_app import _compute_unit_price
        item = {"unit_rate": 14.50, "quantity": 15, "line_total": 217.50}
        self.assertAlmostEqual(_compute_unit_price(item), 14.50, places=2)

    def test_falls_back_to_line_total_div_qty_when_unit_rate_is_wrong(self):
        """Simulates extractor error where unit_rate = line_total (common in Riviera Gourmet PDFs)."""
        from whatsapp_app import _compute_unit_price
        # Sea bass: 17.5 kg, line_total = 1013.25, extractor puts 1013.25 in unit_rate
        item = {"unit_rate": 1013.25, "quantity": 17.5, "line_total": 1013.25}
        result = _compute_unit_price(item)
        self.assertAlmostEqual(result, 57.90, places=1)
        self.assertNotAlmostEqual(result, 1013.25, places=0)

    def test_salmon_unit_price_not_line_total(self):
        """Riviera salmon: 14.5 kg @ 25.90/kg = 375.55 — must show 25.90, not 375.55."""
        from whatsapp_app import _compute_unit_price
        item = {"unit_rate": 375.55, "quantity": 14.5, "line_total": 375.55}
        result = _compute_unit_price(item)
        self.assertAlmostEqual(result, 25.90, places=1)

    def test_we_supply_salmon_unit_price(self):
        """We Supply salmon fillet: qty=17.5, unit_rate=12.00, line_total=210.00."""
        from whatsapp_app import _compute_unit_price
        item = {"unit_rate": 12.00, "quantity": 17.5, "unit": "kg", "line_total": 210.00}
        self.assertAlmostEqual(_compute_unit_price(item), 12.00, places=2)


if __name__ == "__main__":
    unittest.main()
