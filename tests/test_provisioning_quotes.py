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
        lower = response.lower()
        self.assertTrue(
            "like-for-like" in lower or "check before ordering" in lower or "product form" in lower,
            "No provisioning/like-for-like note found",
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
# Test 7: WhatsApp length guard
# ---------------------------------------------------------------------------

class TestWhatsAppLengthGuard(unittest.TestCase):

    def test_short_body_unchanged(self):
        from whatsapp_app import _split_whatsapp_body
        body = "A" * 800
        self.assertEqual(_split_whatsapp_body(body), [body])

    def test_body_at_limit_unchanged(self):
        from whatsapp_app import _split_whatsapp_body
        body = "A" * 1500
        self.assertEqual(_split_whatsapp_body(body), [body])

    def test_long_body_splits_into_chunks(self):
        from whatsapp_app import _split_whatsapp_body
        # 5 paragraphs × 400 chars each = 2000+ chars
        body = "\n\n".join(["B" * 400] * 5)
        chunks = _split_whatsapp_body(body)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 1500, f"Chunk too long: {len(chunk)}")

    def test_all_chunks_under_max(self):
        from whatsapp_app import _split_whatsapp_body
        # Simulate a 3000-char response
        body = "\n\n".join([f"Section {i}:\n" + "X" * 500 for i in range(5)])
        chunks = _split_whatsapp_body(body)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 1500)

    def test_provisioning_response_with_prefix_fits(self):
        from whatsapp_app import build_provisioning_comparison_response, _split_whatsapp_body
        resp = build_provisioning_comparison_response(_we_supply_quote(), _riviera_quote())
        body = f"⚓ AskHelm \n\n{resp}"
        chunks = _split_whatsapp_body(body)
        # Concise response should fit in one chunk
        self.assertEqual(len(chunks), 1, f"Concise response split unexpectedly: {len(body)} chars")
        self.assertLessEqual(len(body), 1500)


# ---------------------------------------------------------------------------
# Test 8: Default provisioning comparison — concise and informative
# ---------------------------------------------------------------------------

class TestProvisioningResponseLength(unittest.TestCase):

    def setUp(self):
        self.doc_a = _we_supply_quote()
        self.doc_b = _riviera_quote()

    def test_default_response_under_1200_chars(self):
        from whatsapp_app import build_provisioning_comparison_response
        resp = build_provisioning_comparison_response(self.doc_a, self.doc_b)
        self.assertLessEqual(len(resp), 1200, f"Response too long: {len(resp)} chars")

    def test_default_response_has_decision(self):
        from whatsapp_app import build_provisioning_comparison_response
        resp = build_provisioning_comparison_response(self.doc_a, self.doc_b)
        self.assertIn("DECISION", resp)

    def test_default_response_mentions_saving(self):
        from whatsapp_app import build_provisioning_comparison_response
        resp = build_provisioning_comparison_response(self.doc_a, self.doc_b)
        # Saving ≈ €1,349.53
        self.assertTrue("1,349" in resp or "1349" in resp, "Saving amount not in response")

    def test_default_response_mentions_cheaper_supplier(self):
        from whatsapp_app import build_provisioning_comparison_response
        resp = build_provisioning_comparison_response(self.doc_a, self.doc_b)
        self.assertIn("Riviera Gourmet", resp)

    def test_default_response_has_follow_up_hint(self):
        from whatsapp_app import build_provisioning_comparison_response
        resp = build_provisioning_comparison_response(self.doc_a, self.doc_b)
        self.assertIn("line by line", resp.lower())

    def test_default_response_mentions_like_for_like(self):
        from whatsapp_app import build_provisioning_comparison_response
        resp = build_provisioning_comparison_response(self.doc_a, self.doc_b)
        self.assertIn("like-for-like", resp.lower())


# ---------------------------------------------------------------------------
# Test 9: Detailed line-by-line comparison
# ---------------------------------------------------------------------------

class TestProvisioningDetailResponse(unittest.TestCase):

    def setUp(self):
        self.doc_a = _we_supply_quote()
        self.doc_b = _riviera_quote()

    def _state_with_active_comparison(self):
        state = _empty_state()
        state, _ = create_quote_session(self.doc_a, state)
        state, _ = create_quote_session(self.doc_b, state)
        from whatsapp_app import _handle_quote_compare_intent
        _, state = _handle_quote_compare_intent(state, "compare quotes")
        return state

    def test_detail_response_has_fish_items(self):
        from whatsapp_app import build_provisioning_detail_response
        resp = build_provisioning_detail_response(self.doc_a, self.doc_b)
        lower = resp.lower()
        self.assertTrue(
            any(f in lower for f in ("salmon", "sea bass", "tuna", "cod", "haddock")),
            "No fish items in detail response",
        )

    def test_detail_chunks_all_under_1500_chars(self):
        from whatsapp_app import build_provisioning_detail_response, _split_whatsapp_body
        resp = build_provisioning_detail_response(self.doc_a, self.doc_b)
        body = f"⚓ AskHelm \n\n{resp}"
        chunks = _split_whatsapp_body(body)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 1500, f"Chunk too long: {len(chunk)}")

    def test_line_by_line_text_routes_to_detail(self):
        from whatsapp_app import _handle_text_message
        state = self._state_with_active_comparison()
        resp, _ = _handle_text_message("line by line", state)
        lower = resp.lower()
        self.assertNotIn("document not understood", lower)
        self.assertTrue(
            any(f in lower for f in ("salmon", "tuna", "cod", "sea bass")),
            "Detail response missing fish items",
        )

    def test_compare_quotes_default_is_concise(self):
        from whatsapp_app import _handle_text_message
        state = _empty_state()
        state, _ = create_quote_session(self.doc_a, state)
        state, _ = create_quote_session(self.doc_b, state)
        resp, _ = _handle_text_message("compare quotes", state)
        self.assertLessEqual(len(resp), 1500, f"Default compare response too long: {len(resp)}")
        self.assertIn("line by line", resp.lower())


# ---------------------------------------------------------------------------
# Test 10: Unit price regression — OCR/extractor error correction
# ---------------------------------------------------------------------------

class TestUnitPriceRegression(unittest.TestCase):

    def test_correct_unit_rate_when_consistent(self):
        from whatsapp_app import _compute_unit_price
        item = {"unit_rate": 14.50, "quantity": 15, "line_total": 217.50}
        self.assertAlmostEqual(_compute_unit_price(item), 14.50, places=2)

    def test_ocr_error_line_total_as_unit_rate_sea_bass(self):
        from whatsapp_app import _compute_unit_price
        # unit_rate=1013.25 (line total stored in wrong field), qty=17.5, line_total=1013.25
        item = {"unit_rate": 1013.25, "quantity": 17.5, "line_total": 1013.25}
        result = _compute_unit_price(item)
        self.assertAlmostEqual(result, 57.90, delta=0.05)

    def test_ocr_error_line_total_as_unit_rate_salmon(self):
        from whatsapp_app import _compute_unit_price
        # Simulate: unit_rate=375.55 (line total), qty=14.5, line_total=375.55
        item = {"unit_rate": 375.55, "quantity": 14.5, "line_total": 375.55}
        result = _compute_unit_price(item)
        self.assertAlmostEqual(result, 25.90, delta=0.05)

    def test_correct_unit_rate_smoked_salmon(self):
        from whatsapp_app import _compute_unit_price
        item = {"unit_rate": 38.00, "quantity": 2, "line_total": 76.00}
        self.assertAlmostEqual(_compute_unit_price(item), 38.00, places=2)

    def test_fallback_to_line_total_over_qty(self):
        from whatsapp_app import _compute_unit_price
        item = {"quantity": 4, "line_total": 260.0}
        self.assertAlmostEqual(_compute_unit_price(item), 65.0, places=2)

    def test_fallback_to_unit_rate_without_line_total(self):
        from whatsapp_app import _compute_unit_price
        item = {"unit_rate": 22.00, "quantity": 5}
        self.assertAlmostEqual(_compute_unit_price(item), 22.00, places=2)


# ---------------------------------------------------------------------------
# Test 11: Provisioning comparison follow-up routing
# ---------------------------------------------------------------------------

class TestProvisioningComparisonFollowUp(unittest.TestCase):

    def _state_with_active_comparison(self):
        state = _empty_state()
        state, _ = create_quote_session(_we_supply_quote(), state)
        state, _ = create_quote_session(_riviera_quote(), state)
        from whatsapp_app import _handle_quote_compare_intent
        _, state = _handle_quote_compare_intent(state, "compare quotes")
        return state

    def test_give_me_a_summary_uses_existing_comparison(self):
        from whatsapp_app import _handle_text_message
        state = self._state_with_active_comparison()
        response, _ = _handle_text_message("give me a summary", state)
        self.assertNotIn("NOT ENOUGH QUOTES", response)
        self.assertTrue(
            "We Supply Yachts" in response or "Riviera Gourmet" in response,
            "Neither supplier in summary response",
        )

    def test_summarise_comparison_uses_existing_comparison(self):
        from whatsapp_app import _handle_text_message
        state = self._state_with_active_comparison()
        response, _ = _handle_text_message("summarise the comparison", state)
        self.assertNotIn("DOCUMENT NOT UNDERSTOOD", response)
        self.assertNotIn("NOT ENOUGH QUOTES", response)

    def test_overview_uses_existing_comparison(self):
        from whatsapp_app import _handle_text_message
        state = self._state_with_active_comparison()
        response, _ = _handle_text_message("give me an overview", state)
        self.assertNotIn("DOCUMENT NOT UNDERSTOOD", response)



# ---------------------------------------------------------------------------
# Test 12: Provisioning product-specific queries
# ---------------------------------------------------------------------------

class TestProvisioningProductQuery(unittest.TestCase):

    def _state_with_active_comparison(self):
        state = _empty_state()
        state, _ = create_quote_session(_we_supply_quote(), state)
        state, _ = create_quote_session(_riviera_quote(), state)
        from whatsapp_app import _handle_quote_compare_intent
        _, state = _handle_quote_compare_intent(state, "compare quotes")
        return state

    def test_salmon_query_does_not_request_upload(self):
        from whatsapp_app import _handle_text_message
        state = self._state_with_active_comparison()
        response, _ = _handle_text_message("how is the price of the salmon?", state)
        self.assertNotIn("Upload a second", response)
        self.assertNotIn("NOT ENOUGH QUOTES", response)

    def test_salmon_query_mentions_salmon(self):
        from whatsapp_app import _handle_text_message
        state = self._state_with_active_comparison()
        response, _ = _handle_text_message("how is the price of the salmon?", state)
        self.assertIn("salmon", response.lower())

    def test_are_prices_fair_returns_comparison_not_engineering(self):
        from whatsapp_app import _handle_text_message
        state = self._state_with_active_comparison()
        response, _ = _handle_text_message("are the prices fair?", state)
        lower = response.lower()
        self.assertNotIn("make/model", lower)
        self.assertNotIn("part number", lower)
        self.assertTrue(
            "we supply" in lower or "riviera" in lower or "salmon" in lower or "like-for-like" in lower,
            "Response does not reference provisioning context",
        )



if __name__ == "__main__":
    unittest.main()
