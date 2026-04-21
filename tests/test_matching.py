"""
Tests for quote-to-invoice matching improvements and freight delta detection.
All logic under test is pure Python — no mocks needed.
"""
import unittest

from domain.session_manager import (
    AUTO_MATCH_THRESHOLD,
    AMBIGUOUS_THRESHOLD,
    find_best_matching_session,
    score_invoice_against_session,
    _should_force_compare,
    _supplier_score,
)
from domain.compare import compare_documents, _is_freight_item


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _quote_doc(
    doc_id="doc-q1",
    supplier="Acme Marine Ltd",
    total=1000.0,
    subtotal=None,
    items=None,
    doc_number="QT-001",
    doc_date="2024-01-10",
    currency="EUR",
    session_id="sess-1",
):
    return {
        "document_id": doc_id,
        "doc_type": "quote",
        "supplier_name": supplier,
        "document_number": doc_number,
        "document_date": doc_date,
        "currency": currency,
        "total": total,
        "subtotal": subtotal,
        "tax": None,
        "line_items": items or [
            {"description": "Impeller replacement", "quantity": 1, "unit_rate": 400.0, "line_total": 400.0},
            {"description": "Labour - engine service", "quantity": 4, "unit_rate": 100.0, "line_total": 400.0},
            {"description": "Oil filter set", "quantity": 1, "unit_rate": 200.0, "line_total": 200.0},
        ],
        "exclusions": [],
        "assumptions": [],
        "fingerprint": "fp-quote",
        "status": "in_session",
        "uploaded_at": "2024-01-10T00:00:00+00:00",
        "session_id": session_id,
    }


def _invoice_doc(
    doc_id="doc-i1",
    supplier="Acme Marine Ltd",
    total=1150.0,
    subtotal=1000.0,
    items=None,
    doc_number="INV-001",
    doc_date="2024-01-20",
    currency="EUR",
    session_id=None,
):
    return {
        "document_id": doc_id,
        "doc_type": "invoice",
        "supplier_name": supplier,
        "document_number": doc_number,
        "document_date": doc_date,
        "currency": currency,
        "total": total,
        "subtotal": subtotal,
        "tax": None,
        "line_items": items if items is not None else [
            {"description": "Impeller replacement", "quantity": 1, "unit_rate": 400.0, "line_total": 400.0},
            {"description": "Labour - engine service", "quantity": 4, "unit_rate": 100.0, "line_total": 400.0},
            {"description": "Oil filter set", "quantity": 1, "unit_rate": 200.0, "line_total": 200.0},
            {"description": "Freight", "quantity": 1, "unit_rate": 150.0, "line_total": 150.0},
        ],
        "exclusions": [],
        "assumptions": [],
        "fingerprint": "fp-invoice",
        "status": "new",
        "uploaded_at": "2024-01-20T00:00:00+00:00",
        "session_id": session_id,
    }


def _state_with_quote(quote=None):
    q = quote or _quote_doc()
    session = {
        "session_id": "sess-1",
        "session_type": "pending",
        "status": "active",
        "document_ids": [q["document_id"]],
        "anchor_doc_id": q["document_id"],
        "created_at": "2024-01-10T00:00:00+00:00",
        "updated_at": "2024-01-10T00:00:00+00:00",
        "last_comparison": None,
    }
    return {
        "sessions": [session],
        "documents": [q],
        "active_session_id": "sess-1",
    }


# ---------------------------------------------------------------------------
# Freight keyword detection
# ---------------------------------------------------------------------------

class TestFreightKeywords(unittest.TestCase):

    def test_freight_detected(self):
        self.assertTrue(_is_freight_item("Freight"))

    def test_freight_case_insensitive(self):
        self.assertTrue(_is_freight_item("FREIGHT CHARGE"))

    def test_delivery_detected(self):
        self.assertTrue(_is_freight_item("Delivery to port"))

    def test_packing_detected(self):
        self.assertTrue(_is_freight_item("Packing and crating"))

    def test_transport_detected(self):
        self.assertTrue(_is_freight_item("Transport to vessel"))

    def test_shipping_detected(self):
        self.assertTrue(_is_freight_item("Shipping and handling"))

    def test_courier_detected(self):
        self.assertTrue(_is_freight_item("Courier service"))

    def test_carriage_detected(self):
        self.assertTrue(_is_freight_item("Carriage charge"))

    def test_regular_part_not_freight(self):
        self.assertFalse(_is_freight_item("Impeller replacement"))

    def test_labour_not_freight(self):
        self.assertFalse(_is_freight_item("Labour - engine service"))

    def test_oil_filter_not_freight(self):
        self.assertFalse(_is_freight_item("Oil filter set"))

    def test_empty_desc_not_freight(self):
        self.assertFalse(_is_freight_item(""))


# ---------------------------------------------------------------------------
# compare_documents: freight_items field
# ---------------------------------------------------------------------------

class TestCompareDocumentsFreight(unittest.TestCase):

    def test_freight_item_in_added_items_is_flagged(self):
        quote = _quote_doc()
        invoice = _invoice_doc()
        result = compare_documents(quote, invoice)
        self.assertEqual(len(result["freight_items"]), 1)
        self.assertEqual(result["freight_items"][0]["description"], "Freight")

    def test_non_freight_addition_not_in_freight_items(self):
        quote = _quote_doc()
        invoice = _invoice_doc(items=[
            {"description": "Impeller replacement", "quantity": 1, "unit_rate": 400.0, "line_total": 400.0},
            {"description": "Labour - engine service", "quantity": 4, "unit_rate": 100.0, "line_total": 400.0},
            {"description": "Oil filter set", "quantity": 1, "unit_rate": 200.0, "line_total": 200.0},
            {"description": "Spare gasket", "quantity": 1, "unit_rate": 50.0, "line_total": 50.0},
        ], total=1050.0, subtotal=1000.0)
        result = compare_documents(quote, invoice)
        self.assertEqual(len(result["freight_items"]), 0)
        self.assertEqual(len(result["added_items"]), 1)
        self.assertEqual(result["added_items"][0]["description"], "Spare gasket")

    def test_delivery_keyword_flagged(self):
        quote = _quote_doc()
        invoice = _invoice_doc(items=[
            {"description": "Impeller replacement", "unit_rate": 400.0, "line_total": 400.0},
            {"description": "Labour - engine service", "unit_rate": 400.0, "line_total": 400.0},
            {"description": "Oil filter set", "unit_rate": 200.0, "line_total": 200.0},
            {"description": "Delivery to vessel", "unit_rate": 80.0, "line_total": 80.0},
        ], total=1080.0, subtotal=1000.0)
        result = compare_documents(quote, invoice)
        self.assertEqual(len(result["freight_items"]), 1)

    def test_no_freight_when_items_identical(self):
        quote = _quote_doc()
        invoice = _invoice_doc(items=quote["line_items"][:], total=1000.0, subtotal=1000.0)
        result = compare_documents(quote, invoice)
        self.assertEqual(len(result["freight_items"]), 0)

    def test_sandfirden_part_number_prefix_does_not_cause_mismatch(self):
        """
        Exact live bug: quote extraction includes part number in description,
        invoice extraction omits it (or vice versa).  The items are the same —
        only FREIGHT COSTS should appear as added; nothing should be missing.
        """
        quote = {
            "total": 670.82,
            "line_items": [
                {"description": "1921956 ANTIFREEZE/CORR. 50/50 20L", "line_total": 367.20},
                {"description": "246458 GASKET", "line_total": 187.20},
            ],
        }
        invoice = {
            "total": 761.57,
            "line_items": [
                {"description": "ANTIFREEZE/CORR. 50/50 20L", "line_total": 367.20},
                {"description": "GASKET", "line_total": 187.20},
                {"description": "FREIGHT COSTS WITH KAAN", "line_total": 75.00},
            ],
        }
        result = compare_documents(quote, invoice)
        self.assertEqual(result["missing_items"], [], f"Unexpected missing: {result['missing_items']}")
        self.assertEqual(len(result["added_items"]), 1)
        self.assertEqual(result["added_items"][0]["description"], "FREIGHT COSTS WITH KAAN")
        self.assertEqual(len(result["ancillary_items"]), 1)
        self.assertTrue(result["all_added_are_ancillary"])

    def test_description_variation_does_not_cause_mismatch(self):
        """Minor OCR formatting difference ('20L' vs '20 L') must not split items."""
        quote = {
            "total": 670.82,
            "line_items": [
                {"description": "ANTIFREEZE/CORR. 50/50 20L", "line_total": 367.20},
                {"description": "GASKET", "line_total": 187.20},
            ],
        }
        invoice = {
            "total": 761.57,
            "line_items": [
                {"description": "Antifreeze Corr 50/50 20 L", "line_total": 367.20},
                {"description": "Gasket", "line_total": 187.20},
                {"description": "Freight delivery", "line_total": 75.00},
            ],
        }
        result = compare_documents(quote, invoice)
        self.assertEqual(result["missing_items"], [], f"Unexpected missing: {result['missing_items']}")
        self.assertEqual(len(result["added_items"]), 1)

    def test_delta_calculated_on_full_total_not_subtotal(self):
        quote = _quote_doc(total=1000.0)
        invoice = _invoice_doc(total=1150.0, subtotal=1000.0)
        result = compare_documents(quote, invoice)
        self.assertAlmostEqual(result["delta"], 150.0)
        self.assertAlmostEqual(result["delta_percent"], 15.0)


# ---------------------------------------------------------------------------
# _should_force_compare
# ---------------------------------------------------------------------------

class TestShouldForceCompare(unittest.TestCase):

    def _session(self, state):
        return state["sessions"][0]

    def test_same_supplier_majority_items_forces_compare(self):
        state = _state_with_quote()
        invoice = _invoice_doc()  # 3 matching + 1 freight = 100% quote-relative overlap
        self.assertTrue(_should_force_compare(invoice, self._session(state), state))

    def test_different_supplier_no_force(self):
        state = _state_with_quote()
        invoice = _invoice_doc(supplier="Totally Different Co")
        self.assertFalse(_should_force_compare(invoice, self._session(state), state))

    def test_partial_supplier_name_forces_compare(self):
        """'Acme Marine' is a substring of 'Acme Marine Ltd' → still matches."""
        state = _state_with_quote()
        invoice = _invoice_doc(supplier="Acme Marine")
        self.assertTrue(_should_force_compare(invoice, self._session(state), state))

    def test_below_50pct_item_overlap_no_force(self):
        state = _state_with_quote()
        invoice = _invoice_doc(items=[
            {"description": "Impeller replacement"},      # 1 match (of 3 quote items)
            {"description": "Completely different A"},
            {"description": "Completely different B"},
            {"description": "Freight"},
        ])
        # quote-relative overlap = 1/3 = 33% < 50%
        self.assertFalse(_should_force_compare(invoice, self._session(state), state))

    def test_exactly_50pct_overlap_forces_compare(self):
        quote = _quote_doc(items=[
            {"description": "Item A"},
            {"description": "Item B"},
        ])
        state = _state_with_quote(quote)
        invoice = _invoice_doc(items=[
            {"description": "Item A"},
            {"description": "Item Z"},
        ])
        # overlap = 1 / 2 = 50% → qualifies
        self.assertTrue(_should_force_compare(invoice, state["sessions"][0], state))

    def test_no_line_items_no_force(self):
        state = _state_with_quote()
        invoice = _invoice_doc(items=[])
        self.assertFalse(_should_force_compare(invoice, self._session(state), state))


# ---------------------------------------------------------------------------
# Improved total scoring: subtotal used when it matches better
# ---------------------------------------------------------------------------

class TestSubtotalScoring(unittest.TestCase):

    def _single_session(self, state):
        return state["sessions"][0]

    def test_subtotal_used_when_closer_to_quote_total(self):
        """Invoice has freight on top; subtotal matches quote total → ≥20 pts for totals."""
        quote = _quote_doc(total=1000.0)
        state = _state_with_quote(quote)
        invoice = _invoice_doc(total=1150.0, subtotal=1000.0)  # 15% total diff, 0% subtotal diff
        score, reasons = score_invoice_against_session(invoice, self._single_session(state), state)
        total_reason = next((r for r in reasons if "totals" in r.lower()), "")
        self.assertIn("identical", total_reason, f"Expected subtotal match. Reasons: {reasons}")

    def test_large_total_divergence_without_subtotal_scores_lower(self):
        """No subtotal available; 30% divergence → 0 pts for totals."""
        quote = _quote_doc(total=1000.0)
        state = _state_with_quote(quote)
        invoice = _invoice_doc(total=1300.0, subtotal=None)
        score, reasons = score_invoice_against_session(invoice, self._single_session(state), state)
        total_reason = next((r for r in reasons if "totals" in r.lower() or "diverge" in r.lower()), "")
        self.assertIn("diverge", total_reason, f"Expected diverge. Reasons: {reasons}")

    def test_subtotal_worse_than_total_uses_total(self):
        """When subtotal is further from quote total, total comparison is used."""
        quote = _quote_doc(total=1000.0)
        state = _state_with_quote(quote)
        # invoice total (1010) is closer to quote (1000) than subtotal (800)
        invoice = _invoice_doc(total=1010.0, subtotal=800.0)
        score, reasons = score_invoice_against_session(invoice, self._single_session(state), state)
        total_reason = next((r for r in reasons if "totals" in r.lower()), "")
        self.assertIn("1010", total_reason, f"Expected total (1010) used. Reasons: {reasons}")


# ---------------------------------------------------------------------------
# find_best_matching_session: confidence ≥ threshold for obvious match
# ---------------------------------------------------------------------------

class TestFindBestMatchingSession(unittest.TestCase):

    def test_same_supplier_same_items_plus_freight_auto_matches(self):
        """Classic case: same supplier, same items, freight added → score ≥ AUTO_MATCH_THRESHOLD."""
        state = _state_with_quote()
        invoice = _invoice_doc()  # subtotal=1000 matches quote total=1000
        session_id, score, reasons = find_best_matching_session(invoice, state)
        self.assertIsNotNone(session_id)
        self.assertGreaterEqual(
            score, AUTO_MATCH_THRESHOLD,
            f"Expected ≥{AUTO_MATCH_THRESHOLD}, got {score}. Reasons: {reasons}",
        )

    def test_force_compare_reason_appended_when_triggered(self):
        """When force-compare kicks in, the reason is recorded.

        Scenario: same supplier, only 2 of 3 quoted items present in invoice
        (67% quote-relative overlap), no dates, totals 30% apart.
        Natural score = 30(supplier) + 0(ref) + 0(totals) + 20(items@67%) + 0(dates) = 50 < 60.
        Force-compare applies: supplier matches + 2/3 = 67% >= 50%.
        """
        quote = _quote_doc(total=1000.0, subtotal=None, doc_date="", doc_number="QT-001")
        state = _state_with_quote(quote)
        invoice = _invoice_doc(
            items=[
                {"description": "Impeller replacement", "unit_rate": 400.0, "line_total": 400.0},
                {"description": "Labour - engine service", "unit_rate": 400.0, "line_total": 400.0},
                # Oil filter set deliberately absent — quote item not carried over
                {"description": "Different part entirely", "unit_rate": 300.0, "line_total": 300.0},
                {"description": "Freight", "unit_rate": 300.0, "line_total": 300.0},
            ],
            total=1400.0, subtotal=None, doc_date="", doc_number="INV-999",
        )
        session_id, score, reasons = find_best_matching_session(invoice, state)
        self.assertIsNotNone(session_id)
        self.assertGreaterEqual(score, AUTO_MATCH_THRESHOLD)
        self.assertTrue(any("force" in r.lower() for r in reasons), f"Reasons: {reasons}")

    def test_different_supplier_no_auto_match(self):
        state = _state_with_quote()
        invoice = _invoice_doc(supplier="Completely Different Co")
        _, score, _ = find_best_matching_session(invoice, state)
        self.assertLess(score, AUTO_MATCH_THRESHOLD)

    def test_no_sessions_returns_none(self):
        state = {"sessions": [], "documents": [], "active_session_id": None}
        invoice = _invoice_doc()
        session_id, score, _ = find_best_matching_session(invoice, state)
        self.assertIsNone(session_id)
        self.assertEqual(score, 0)


# ---------------------------------------------------------------------------
# build_comparison_response: freight-specific output
# ---------------------------------------------------------------------------

class TestBuildComparisonResponseFreight(unittest.TestCase):

    def test_freight_addition_generates_freight_decision(self):
        from whatsapp_app import build_comparison_response
        quote = _quote_doc()
        invoice = _invoice_doc()
        comparison = compare_documents(quote, invoice)
        response = build_comparison_response(quote, invoice, comparison)
        self.assertIn("MATCH CONFIRMED", response)
        self.assertIn("COST INCREASE", response)
        self.assertIn("Acme Marine Ltd", response)

    def test_freight_why_includes_amount(self):
        from whatsapp_app import build_comparison_response
        quote = _quote_doc()
        invoice = _invoice_doc()  # freight line_total=150
        comparison = compare_documents(quote, invoice)
        response = build_comparison_response(quote, invoice, comparison)
        self.assertIn("150", response)
        self.assertIn("not shown on the original quote", response)

    def test_freight_actions_include_confirm_wording(self):
        from whatsapp_app import build_comparison_response
        quote = _quote_doc()
        invoice = _invoice_doc()
        comparison = compare_documents(quote, invoice)
        response = build_comparison_response(quote, invoice, comparison)
        self.assertIn("Confirm freight was agreed", response)

    def test_non_freight_addition_uses_standard_response(self):
        """Invoice adds a spare part (not ancillary) → standard cost-increase response."""
        from whatsapp_app import build_comparison_response
        quote = _quote_doc()
        invoice = _invoice_doc(items=[
            {"description": "Impeller replacement", "unit_rate": 400.0, "line_total": 400.0},
            {"description": "Labour - engine service", "unit_rate": 400.0, "line_total": 400.0},
            {"description": "Oil filter set", "unit_rate": 200.0, "line_total": 200.0},
            {"description": "Spare gasket kit", "unit_rate": 150.0, "line_total": 150.0},
        ], total=1150.0, subtotal=1000.0)
        comparison = compare_documents(quote, invoice)
        response = build_comparison_response(quote, invoice, comparison)
        # Not the ancillary-uplift path (freight-specific actions absent)
        self.assertNotIn("Confirm if freight was agreed", response)
        # But still a clear decision
        self.assertIn("DECISION", response)
        self.assertIn("MATCH CONFIRMED", response)

    def test_delivery_item_also_triggers_freight_response(self):
        from whatsapp_app import build_comparison_response
        quote = _quote_doc()
        invoice = _invoice_doc(items=[
            {"description": "Impeller replacement", "unit_rate": 400.0, "line_total": 400.0},
            {"description": "Labour - engine service", "unit_rate": 400.0, "line_total": 400.0},
            {"description": "Oil filter set", "unit_rate": 200.0, "line_total": 200.0},
            {"description": "Delivery to vessel", "unit_rate": 80.0, "line_total": 80.0},
        ], total=1080.0, subtotal=1000.0)
        comparison = compare_documents(quote, invoice)
        response = build_comparison_response(quote, invoice, comparison)
        self.assertIn("MATCH CONFIRMED", response)
        self.assertIn("COST INCREASE", response)
        # Delivery categorised as "delivery" and amount shown
        self.assertIn("delivery", response)
        self.assertIn("80", response)


# ---------------------------------------------------------------------------
# Full integration: quote → invoice with freight → comparison happens
# ---------------------------------------------------------------------------

class TestFullFreightIntegration(unittest.TestCase):

    def test_invoice_upload_produces_comparison_not_unmatched(self):
        """
        End-to-end: state has a quote; invoice arrives with same supplier,
        same items, plus freight. _handle_invoice_upload must perform a
        comparison rather than reporting NO MATCHING QUOTE.
        """
        from whatsapp_app import _handle_invoice_upload
        quote = _quote_doc()
        state = _state_with_quote(quote)
        invoice = _invoice_doc()

        answer, updated_state = _handle_invoice_upload(
            invoice,
            invoice["supplier_name"],
            invoice["total"],
            invoice["currency"],
            len(invoice["line_items"]),
            state,
        )
        self.assertNotIn("NO MATCHING QUOTE", answer, f"Got: {answer[:200]}")
        self.assertNotIn("MATCH UNCERTAIN", answer, f"Got: {answer[:200]}")
        self.assertIn("DECISION:", answer)

    def test_invoice_upload_with_freight_flags_freight_in_response(self):
        from whatsapp_app import _handle_invoice_upload
        quote = _quote_doc()
        state = _state_with_quote(quote)
        invoice = _invoice_doc()

        answer, _ = _handle_invoice_upload(
            invoice,
            invoice["supplier_name"],
            invoice["total"],
            invoice["currency"],
            len(invoice["line_items"]),
            state,
        )
        self.assertIn("MATCH CONFIRMED", answer, f"Got: {answer[:300]}")
        self.assertIn("COST INCREASE", answer, f"Got: {answer[:300]}")

    def test_invoice_upload_different_supplier_no_comparison_performed(self):
        """Invoice from a different supplier → no automatic comparison (uncertain or no match)."""
        from whatsapp_app import _handle_invoice_upload
        quote = _quote_doc()
        state = _state_with_quote(quote)
        # Supplier mismatch + completely different items → very low score, no force-compare
        invoice = _invoice_doc(
            supplier="Completely Different Co",
            items=[
                {"description": "Unrelated service A", "unit_rate": 500.0, "line_total": 500.0},
                {"description": "Unrelated service B", "unit_rate": 650.0, "line_total": 650.0},
            ],
        )

        answer, _ = _handle_invoice_upload(
            invoice,
            invoice["supplier_name"],
            invoice["total"],
            invoice["currency"],
            len(invoice["line_items"]),
            state,
        )
        # Must not produce a freight comparison response
        self.assertNotIn("FREIGHT ADDED", answer)
        # Must indicate no automatic comparison happened
        self.assertTrue(
            "NO MATCHING QUOTE" in answer or "MATCH UNCERTAIN" in answer,
            f"Expected unmatched response, got: {answer[:200]}",
        )


class TestSupplierScore(unittest.TestCase):
    """_supplier_score must handle legal-suffix variants like 'b.v.' vs 'BV'."""

    def test_exact_match_scores_30(self):
        pts, _ = _supplier_score("Acme Marine Ltd", "Acme Marine Ltd")
        self.assertEqual(pts, 30)

    def test_bv_vs_b_dot_v_dot_scores_30(self):
        # The real Sandfirden bug: quote has "b.v.", invoice has "BV".
        # After normalization both lose the dots/case, and sig_words
        # {"sandfirden", "technics"} have Jaccard 1.0 → 30pts.
        pts, reason = _supplier_score("Sandfirden Technics b.v.", "Sandfirden Technics BV")
        self.assertEqual(pts, 30, f"Expected 30 pts, got {pts}. Reason: {reason}")

    def test_partial_word_overlap_scores_at_least_20(self):
        pts, _ = _supplier_score("ACME Marine Services Ltd", "Acme Marine")
        self.assertGreaterEqual(pts, 20)

    def test_completely_different_scores_0(self):
        pts, _ = _supplier_score("Sandfirden Technics", "Rotterdam Shipyard")
        self.assertEqual(pts, 0)

    def test_bv_variant_lifts_session_score_to_threshold(self):
        """End-to-end: 'b.v.' vs 'BV' supplier should now auto-match with same items."""
        quote = _quote_doc(supplier="Sandfirden Technics b.v.")
        state = _state_with_quote(quote)
        invoice = _invoice_doc(supplier="Sandfirden Technics BV")
        session_id, score, reasons = find_best_matching_session(invoice, state)
        self.assertIsNotNone(session_id)
        self.assertGreaterEqual(
            score, AUTO_MATCH_THRESHOLD,
            f"Expected ≥{AUTO_MATCH_THRESHOLD}, got {score}. Reasons: {reasons}",
        )


if __name__ == "__main__":
    unittest.main()
