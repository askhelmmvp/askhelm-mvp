"""Tests for ASK-10: commercial approval intent (APPROVE / HOLD / QUERY / NO ACTIVE COMPARISON)."""
import unittest
from unittest.mock import patch

from domain.compare import compare_documents


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _q(supplier="Acme Marine Ltd", total=1000.0, items=None):
    return {
        "doc_type": "quote",
        "supplier_name": supplier,
        "currency": "EUR",
        "total": total,
        "line_items": items or [
            {"description": "Pump seal kit", "quantity": 1, "unit_rate": 500.0, "line_total": 500.0},
            {"description": "Labour — engine service", "quantity": 5, "unit_rate": 100.0, "line_total": 500.0},
        ],
    }


def _inv(supplier="Acme Marine Ltd", total=1000.0, items=None):
    return {
        "doc_type": "invoice",
        "supplier_name": supplier,
        "currency": "EUR",
        "total": total,
        "line_items": items or [
            {"description": "Pump seal kit", "quantity": 1, "unit_rate": 500.0, "line_total": 500.0},
            {"description": "Labour — engine service", "quantity": 5, "unit_rate": 100.0, "line_total": 500.0},
        ],
    }


def _comparison_data(doc_a, doc_b):
    return {"doc_a": doc_a, "doc_b": doc_b, "comparison": compare_documents(doc_a, doc_b)}


def _state_with_comparison(doc_a, doc_b):
    cd = _comparison_data(doc_a, doc_b)
    session = {
        "session_id": "sess-1",
        "session_type": "active",
        "status": "active",
        "document_ids": [],
        "last_comparison": cd,
    }
    return {
        "sessions": [session],
        "documents": [],
        "active_session_id": "sess-1",
        "pending_invoice": None,
        "last_context": {},
    }


def _state_empty():
    return {
        "sessions": [],
        "documents": [],
        "active_session_id": None,
        "pending_invoice": None,
        "last_context": {},
    }


# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------

class TestApprovalIntentClassification(unittest.TestCase):
    """classify_text must return 'approval' for payment/approval trigger phrases."""

    def _intent(self, text):
        from domain.intent import classify_text
        return classify_text(text)

    def test_ok_to_pay(self):
        self.assertEqual(self._intent("ok to pay?"), "approval")

    def test_can_i_approve_this(self):
        self.assertEqual(self._intent("can I approve this?"), "approval")

    def test_should_i_approve(self):
        self.assertEqual(self._intent("should I approve?"), "approval")

    def test_go_ahead_exact(self):
        self.assertEqual(self._intent("go ahead"), "approval")

    def test_go_ahead_question(self):
        self.assertEqual(self._intent("go ahead?"), "approval")

    def test_approve_exact(self):
        self.assertEqual(self._intent("approve"), "approval")

    def test_approved_exact(self):
        self.assertEqual(self._intent("approved"), "approval")

    def test_which_should_i_approve(self):
        self.assertEqual(self._intent("which should I approve?"), "approval")

    def test_which_quote_should_i_approve(self):
        self.assertEqual(self._intent("which quote should I approve?"), "approval")

    def test_can_we_pay_it(self):
        self.assertEqual(self._intent("can we pay it?"), "approval")

    def test_is_this_safe_to_approve(self):
        self.assertEqual(self._intent("is this safe to approve?"), "approval")

    def test_pay_this_exact(self):
        self.assertEqual(self._intent("pay this"), "approval")

    def test_ok_for_payment(self):
        self.assertEqual(self._intent("ok for payment?"), "approval")

    def test_can_this_be_approved(self):
        self.assertEqual(self._intent("can this be approved?"), "approval")

    def test_compliance_approved_not_stolen(self):
        """MARPOL question with 'approved' must stay in compliance, not approval."""
        result = self._intent("is this MARPOL approved for our vessel?")
        self.assertNotEqual(result, "approval")
        self.assertEqual(result, "compliance_question")

    def test_fair_price_not_approval(self):
        """'is this a fair price?' must route to market_check, not approval."""
        self.assertEqual(self._intent("is this a fair price?"), "market_check")

    def test_go_ahead_with_this_not_approval(self):
        """'go ahead with this' has more context — routes to commercial_followup."""
        self.assertEqual(self._intent("go ahead with this"), "commercial_followup")

    def test_is_this_reasonable_not_approval(self):
        """'is this reasonable' is a market check question, not an approval trigger."""
        self.assertEqual(self._intent("is this reasonable?"), "market_check")


# ---------------------------------------------------------------------------
# Approval handler: quote vs invoice
# ---------------------------------------------------------------------------

class TestApprovalCleanMatch(unittest.TestCase):
    """Clean invoice match → APPROVE, Low risk."""

    def _response(self):
        from whatsapp_app import _handle_approval
        quote = _q(total=1000.00)
        invoice = _inv(total=999.99)  # rounding only
        state = _state_with_comparison(quote, invoice)
        cd = state["sessions"][0]["last_comparison"]
        return _handle_approval(state, cd)

    def test_decision_is_approve(self):
        self.assertIn("APPROVE", self._response())

    def test_risk_is_low(self):
        r = self._response()
        self.assertIn("RISK:", r)
        self.assertIn("Low", r)

    def test_contains_actions(self):
        self.assertIn("ACTIONS:", self._response())

    def test_not_hold_or_query(self):
        r = self._response()
        self.assertNotIn("HOLD", r)
        self.assertNotIn("NO ACTIVE COMPARISON", r)


class TestApprovalFreightAdded(unittest.TestCase):
    """Invoice adds freight only → QUERY, Medium risk."""

    def _response(self):
        from whatsapp_app import _handle_approval
        quote = _q(total=1000.0)
        invoice = _inv(
            total=1150.0,
            items=[
                {"description": "Pump seal kit", "quantity": 1, "unit_rate": 500.0, "line_total": 500.0},
                {"description": "Labour — engine service", "quantity": 5, "unit_rate": 100.0, "line_total": 500.0},
                {"description": "Freight", "quantity": 1, "unit_rate": 150.0, "line_total": 150.0},
            ],
        )
        state = _state_with_comparison(quote, invoice)
        cd = state["sessions"][0]["last_comparison"]
        return _handle_approval(state, cd)

    def test_decision_is_query(self):
        self.assertIn("QUERY", self._response())

    def test_risk_is_medium(self):
        r = self._response()
        self.assertIn("RISK:", r)
        self.assertIn("Medium", r)

    def test_action_mentions_freight_confirmation(self):
        r = self._response()
        self.assertTrue(
            "freight" in r.lower() or "supplier" in r.lower(),
            f"Expected freight or supplier in: {r}",
        )

    def test_not_approve_or_hold(self):
        r = self._response()
        self.assertNotIn("APPROVE", r)
        self.assertNotIn("HOLD", r)


class TestApprovalMaterialUplift(unittest.TestCase):
    """Invoice materially higher than quote (unexplained) → HOLD, High risk."""

    def _response(self):
        from whatsapp_app import _handle_approval
        quote = _q(total=1000.0)
        invoice = _inv(
            total=1250.0,
            items=[
                {"description": "Pump seal kit", "quantity": 1, "unit_rate": 625.0, "line_total": 625.0},
                {"description": "Labour — engine service", "quantity": 5, "unit_rate": 125.0, "line_total": 625.0},
            ],
        )
        state = _state_with_comparison(quote, invoice)
        cd = state["sessions"][0]["last_comparison"]
        return _handle_approval(state, cd)

    def test_decision_is_hold(self):
        self.assertIn("HOLD", self._response())

    def test_risk_is_high(self):
        r = self._response()
        self.assertIn("RISK:", r)
        self.assertIn("High", r)

    def test_action_says_do_not_approve(self):
        r = self._response().lower()
        self.assertTrue("do not approve" in r or "do not pay" in r)

    def test_not_approve_or_query(self):
        r = self._response()
        self.assertNotIn("APPROVE", r)
        self.assertIn("HOLD", r)
        # The decision line must be HOLD, not QUERY
        lines = r.splitlines()
        for i, l in enumerate(lines):
            if l.strip() == "DECISION:":
                self.assertIn("HOLD", lines[i + 1])
                self.assertNotIn("QUERY", lines[i + 1])
                break


class TestApprovalQuantityMismatch(unittest.TestCase):
    """Invoice quantity differs from quote → HOLD, High risk."""

    def _response(self):
        from whatsapp_app import _handle_approval
        quote = _q(total=1000.0, items=[
            {"description": "Pump seal kit", "quantity": 1, "unit_rate": 500.0, "line_total": 500.0},
            {"description": "Labour — engine service", "quantity": 5, "unit_rate": 100.0, "line_total": 500.0},
        ])
        invoice = _inv(total=1000.0, items=[
            {"description": "Pump seal kit", "quantity": 2, "unit_rate": 500.0, "line_total": 1000.0},
        ])
        state = _state_with_comparison(quote, invoice)
        cd = state["sessions"][0]["last_comparison"]
        return _handle_approval(state, cd)

    def test_decision_is_hold(self):
        self.assertIn("HOLD", self._response())

    def test_risk_is_high(self):
        self.assertIn("High", self._response())


class TestApprovalAdditionalCostItem(unittest.TestCase):
    """Invoice adds a non-ancillary priced item → QUERY, Medium risk."""

    def _response(self):
        from whatsapp_app import _handle_approval
        quote = _q(total=1000.0)
        invoice = _inv(
            total=1150.0,
            items=[
                {"description": "Pump seal kit", "quantity": 1, "unit_rate": 500.0, "line_total": 500.0},
                {"description": "Labour — engine service", "quantity": 5, "unit_rate": 100.0, "line_total": 500.0},
                {"description": "Administration fee", "quantity": 1, "unit_rate": 150.0, "line_total": 150.0},
            ],
        )
        state = _state_with_comparison(quote, invoice)
        cd = state["sessions"][0]["last_comparison"]
        return _handle_approval(state, cd)

    def test_decision_is_query(self):
        self.assertIn("QUERY", self._response())

    def test_risk_is_medium(self):
        self.assertIn("Medium", self._response())


class TestApprovalMissingQuotedItem(unittest.TestCase):
    """Invoice missing a quoted item → HOLD, High risk."""

    def _response(self):
        from whatsapp_app import _handle_approval
        quote = _q(total=1000.0, items=[
            {"description": "Pump seal kit", "quantity": 1, "unit_rate": 500.0, "line_total": 500.0},
            {"description": "Labour — engine service", "quantity": 5, "unit_rate": 100.0, "line_total": 500.0},
        ])
        invoice = _inv(total=500.0, items=[
            {"description": "Pump seal kit", "quantity": 1, "unit_rate": 500.0, "line_total": 500.0},
        ])
        state = _state_with_comparison(quote, invoice)
        cd = state["sessions"][0]["last_comparison"]
        return _handle_approval(state, cd)

    def test_decision_is_hold(self):
        self.assertIn("HOLD", self._response())

    def test_risk_is_high(self):
        self.assertIn("High", self._response())


# ---------------------------------------------------------------------------
# Approval handler: quote vs quote
# ---------------------------------------------------------------------------

class TestApprovalQuoteVsQuoteNoDiff(unittest.TestCase):
    """Quote/quote with no scope diff and clear cheaper supplier → APPROVE [supplier]."""

    def _setup(self):
        quote_a = _q(supplier="Acme Marine Ltd", total=1000.0, items=[
            {"description": "Pump service", "quantity": 1, "unit_rate": 1000.0, "line_total": 1000.0},
        ])
        quote_b = {
            "doc_type": "quote",
            "supplier_name": "Beta Marine GmbH",
            "currency": "EUR",
            "total": 850.0,
            "line_items": [
                {"description": "Pump service", "quantity": 1, "unit_rate": 850.0, "line_total": 850.0},
            ],
        }
        return quote_a, quote_b

    def _response(self):
        from whatsapp_app import _handle_approval
        qa, qb = self._setup()
        state = _state_with_comparison(qa, qb)
        cd = state["sessions"][0]["last_comparison"]
        return _handle_approval(state, cd)

    def test_decision_contains_approve(self):
        r = self._response()
        self.assertTrue(r.startswith("DECISION:\nAPPROVE"), f"Expected APPROVE decision, got: {r[:80]}")

    def test_cheaper_supplier_named(self):
        r = self._response()
        self.assertIn("Beta Marine", r)

    def test_risk_is_low(self):
        self.assertIn("Low", self._response())


class TestApprovalQuoteVsQuoteWithScopeDiff(unittest.TestCase):
    """Quote/quote with scope differences → QUERY, Medium risk."""

    def _response(self):
        from whatsapp_app import _handle_approval
        quote_a = _q(supplier="Acme Marine Ltd", total=1000.0, items=[
            {"description": "Pump seal kit", "quantity": 1, "unit_rate": 500.0, "line_total": 500.0},
            {"description": "Labour", "quantity": 5, "unit_rate": 100.0, "line_total": 500.0},
        ])
        quote_b = {
            "doc_type": "quote",
            "supplier_name": "Beta Marine GmbH",
            "currency": "EUR",
            "total": 800.0,
            "line_items": [
                {"description": "Pump seal kit", "quantity": 1, "unit_rate": 800.0, "line_total": 800.0},
                # Labour is missing from quote_b
            ],
        }
        state = _state_with_comparison(quote_a, quote_b)
        cd = state["sessions"][0]["last_comparison"]
        return _handle_approval(state, cd)

    def test_decision_is_query(self):
        self.assertIn("QUERY", self._response())

    def test_risk_is_medium(self):
        self.assertIn("Medium", self._response())

    def test_mentions_scope(self):
        r = self._response().lower()
        self.assertIn("scope", r)


# ---------------------------------------------------------------------------
# No active comparison
# ---------------------------------------------------------------------------

class TestApprovalNoActiveComparison(unittest.TestCase):
    """No comparison in state → NO ACTIVE COMPARISON, Medium risk."""

    def test_no_comparison_decision(self):
        from whatsapp_app import _handle_approval
        r = _handle_approval(_state_empty(), None)
        self.assertIn("NO ACTIVE COMPARISON", r)

    def test_no_comparison_risk_medium(self):
        from whatsapp_app import _handle_approval
        r = _handle_approval(_state_empty(), None)
        self.assertIn("Medium", r)

    def test_no_comparison_actions_include_upload_quote(self):
        from whatsapp_app import _handle_approval
        r = _handle_approval(_state_empty(), None)
        self.assertIn("quote", r.lower())


class TestApprovalPendingInvoiceNoComparison(unittest.TestCase):
    """Pending invoice (no quote matched) → QUERY, Medium risk (not NO ACTIVE COMPARISON)."""

    def test_pending_invoice_gives_query_not_no_comparison(self):
        from whatsapp_app import _handle_approval
        state = _state_empty()
        state["pending_invoice"] = {
            "doc_record": _inv(total=5000.0)
        }
        r = _handle_approval(state, None)
        self.assertIn("QUERY", r)
        self.assertNotIn("NO ACTIVE COMPARISON", r)

    def test_pending_invoice_risk_is_medium(self):
        from whatsapp_app import _handle_approval
        state = _state_empty()
        state["pending_invoice"] = {"doc_record": _inv(total=5000.0)}
        r = _handle_approval(state, None)
        self.assertIn("Medium", r)


# ---------------------------------------------------------------------------
# End-to-end routing through _handle_text_message
# ---------------------------------------------------------------------------

class TestApprovalRouting(unittest.TestCase):
    """_handle_text_message must route approval triggers to _handle_approval."""

    def _run(self, message, state):
        from whatsapp_app import _handle_text_message
        result, _ = _handle_text_message(message, state, "whatsapp:+44123456789")
        return result

    def test_ok_to_pay_clean_match_gives_approve(self):
        quote = _q(total=1000.0)
        invoice = _inv(total=999.99)
        state = _state_with_comparison(quote, invoice)
        r = self._run("ok to pay?", state)
        self.assertIn("APPROVE", r)
        self.assertNotIn("HOLD", r)

    def test_go_ahead_no_comparison_gives_no_active_comparison(self):
        r = self._run("go ahead?", _state_empty())
        self.assertIn("NO ACTIVE COMPARISON", r)

    def test_approve_with_uplift_gives_hold(self):
        quote = _q(total=1000.0)
        invoice = _inv(
            total=1250.0,
            items=[
                {"description": "Pump seal kit", "quantity": 1, "unit_rate": 625.0, "line_total": 625.0},
                {"description": "Labour — engine service", "quantity": 5, "unit_rate": 125.0, "line_total": 625.0},
            ],
        )
        state = _state_with_comparison(quote, invoice)
        r = self._run("can I approve this?", state)
        self.assertIn("HOLD", r)
        self.assertIn("High", r)


# ---------------------------------------------------------------------------
# Regression: compliance, market check, ASK-29, ASK-30 not affected
# ---------------------------------------------------------------------------

class TestApprovalRegressions(unittest.TestCase):

    def test_compliance_question_not_routed_to_approval(self):
        from domain.intent import classify_text
        result = classify_text("Is this MARPOL approved for our vessel?")
        self.assertNotEqual(result, "approval")

    def test_market_check_not_routed_to_approval(self):
        from domain.intent import classify_text
        result = classify_text("is this a fair price?")
        self.assertNotEqual(result, "approval")

    def test_is_this_reasonable_not_approval(self):
        from domain.intent import classify_text
        result = classify_text("is this reasonable?")
        self.assertNotEqual(result, "approval")

    def test_no_quote_still_routes_to_invoice_clarification(self):
        from domain.intent import classify_text
        self.assertEqual(classify_text("there is no quote"), "invoice_clarification")

    def test_is_this_fair_with_pending_invoice_still_routes_to_invoice_assessment(self):
        """ASK-30: 'is this fair?' with pending invoice → invoice clarification (not approval)."""
        with patch("whatsapp_app.invoice_approval_checks",
                   return_value="DECISION:\nVALIDATE AGAINST AGREEMENT\n\nWHY:\nOK\n\nACTIONS:\n• Check"):
            from whatsapp_app import _handle_text_message
            state = _state_empty()
            state["pending_invoice"] = {"doc_record": _inv(total=5000.0)}
            result, _ = _handle_text_message("is this a fair cost?", state, "whatsapp:+44123456789")
        self.assertIn("VALIDATE AGAINST AGREEMENT", result)
        self.assertNotIn("NO ACTIVE COMPARISON", result)

    def test_ok_to_approve_comparison_result_still_works_via_build_comparison(self):
        """ASK-29: clean line-item match still produces OK TO APPROVE in comparison response."""
        from whatsapp_app import build_comparison_response
        quote = _q(total=1000.0)
        invoice = _inv(total=999.99)
        comparison = compare_documents(quote, invoice)
        response = build_comparison_response(quote, invoice, comparison)
        self.assertIn("OK TO APPROVE", response)


# ---------------------------------------------------------------------------
# Approval clarification intent classification
# ---------------------------------------------------------------------------

class TestApprovalClarificationIntent(unittest.TestCase):
    """classify_text must return 'approval_clarification' for clarification phrases."""

    def _intent(self, text):
        from domain.intent import classify_text
        return classify_text(text)

    def test_freight_accepted_positive(self):
        self.assertEqual(self._intent("freight accepted"), "approval_clarification")

    def test_freight_agreed_positive(self):
        self.assertEqual(self._intent("freight agreed"), "approval_clarification")

    def test_freight_confirmed_positive(self):
        self.assertEqual(self._intent("freight confirmed"), "approval_clarification")

    def test_ok_with_freight_positive(self):
        self.assertEqual(self._intent("ok with freight"), "approval_clarification")

    def test_accepted_exact_positive(self):
        self.assertEqual(self._intent("accepted"), "approval_clarification")

    def test_agreed_exact_positive(self):
        self.assertEqual(self._intent("agreed"), "approval_clarification")

    def test_freight_not_accepted_negative(self):
        self.assertEqual(self._intent("freight not accepted"), "approval_clarification")

    def test_not_accepted_negative(self):
        self.assertEqual(self._intent("not accepted"), "approval_clarification")

    def test_not_agreed_negative(self):
        self.assertEqual(self._intent("not agreed"), "approval_clarification")

    def test_reject_freight_negative(self):
        self.assertEqual(self._intent("reject freight"), "approval_clarification")

    def test_remove_freight_negative(self):
        self.assertEqual(self._intent("remove freight"), "approval_clarification")

    def test_compliance_question_not_stolen(self):
        """'approved by MARPOL' must NOT route to approval_clarification."""
        result = self._intent("is this approved by MARPOL?")
        self.assertNotEqual(result, "approval_clarification")

    def test_can_i_approve_still_routes_to_approval(self):
        """The original approval trigger must still work (not intercepted by clarification)."""
        self.assertEqual(self._intent("can I approve this?"), "approval")


# ---------------------------------------------------------------------------
# Approval clarification handler
# ---------------------------------------------------------------------------

class TestApprovalClarificationHandler(unittest.TestCase):
    """_handle_approval_clarification must resolve open freight queries."""

    def _state_with_freight_query(self):
        """State as if 'can I approve this?' returned QUERY for added freight."""
        return {
            "user_id": "",
            "sessions": [],
            "documents": [],
            "active_session_id": None,
            "pending_invoice": None,
            "last_context": {"type": "approval_query", "open_issue": "freight charge"},
        }

    def test_freight_accepted_returns_approve(self):
        from whatsapp_app import _handle_approval_clarification
        r = _handle_approval_clarification("freight accepted", self._state_with_freight_query())
        self.assertIn("APPROVE", r)
        self.assertNotIn("HOLD", r)

    def test_freight_agreed_returns_approve(self):
        from whatsapp_app import _handle_approval_clarification
        r = _handle_approval_clarification("freight agreed", self._state_with_freight_query())
        self.assertIn("APPROVE", r)

    def test_accepted_exact_returns_approve(self):
        from whatsapp_app import _handle_approval_clarification
        r = _handle_approval_clarification("accepted", self._state_with_freight_query())
        self.assertIn("APPROVE", r)

    def test_agreed_exact_returns_approve(self):
        from whatsapp_app import _handle_approval_clarification
        r = _handle_approval_clarification("agreed", self._state_with_freight_query())
        self.assertIn("APPROVE", r)

    def test_approve_risk_is_low(self):
        from whatsapp_app import _handle_approval_clarification
        r = _handle_approval_clarification("freight accepted", self._state_with_freight_query())
        self.assertIn("Low", r)

    def test_freight_not_accepted_returns_hold(self):
        from whatsapp_app import _handle_approval_clarification
        r = _handle_approval_clarification("freight not accepted", self._state_with_freight_query())
        self.assertIn("HOLD", r)
        self.assertNotIn("APPROVE", r)

    def test_not_accepted_returns_hold(self):
        from whatsapp_app import _handle_approval_clarification
        r = _handle_approval_clarification("not accepted", self._state_with_freight_query())
        self.assertIn("HOLD", r)

    def test_not_agreed_returns_hold(self):
        from whatsapp_app import _handle_approval_clarification
        r = _handle_approval_clarification("not agreed", self._state_with_freight_query())
        self.assertIn("HOLD", r)

    def test_hold_risk_is_medium(self):
        from whatsapp_app import _handle_approval_clarification
        r = _handle_approval_clarification("not accepted", self._state_with_freight_query())
        self.assertIn("Medium", r)

    def test_no_approval_query_context_returns_no_active_query(self):
        from whatsapp_app import _handle_approval_clarification
        state = {
            "user_id": "",
            "last_context": {"type": "market_check"},
        }
        r = _handle_approval_clarification("accepted", state)
        self.assertIn("NO ACTIVE APPROVAL QUERY", r)
        self.assertNotIn("APPROVE", r)

    def test_no_context_at_all_returns_no_active_query(self):
        from whatsapp_app import _handle_approval_clarification
        state = {"user_id": "", "last_context": {}}
        r = _handle_approval_clarification("agreed", state)
        self.assertIn("NO ACTIVE APPROVAL QUERY", r)

    def test_positive_clears_context(self):
        from whatsapp_app import _handle_approval_clarification
        state = self._state_with_freight_query()
        _handle_approval_clarification("freight accepted", state)
        self.assertIsNone(state.get("last_context"))

    def test_negative_clears_context(self):
        from whatsapp_app import _handle_approval_clarification
        state = self._state_with_freight_query()
        _handle_approval_clarification("not accepted", state)
        self.assertIsNone(state.get("last_context"))


# ---------------------------------------------------------------------------
# End-to-end: QUERY → clarification → APPROVE/HOLD
# ---------------------------------------------------------------------------

class TestApprovalClarificationEndToEnd(unittest.TestCase):
    """Full flow: freight comparison → 'can I approve?' → QUERY → clarification → APPROVE/HOLD."""

    def _freight_state(self):
        quote = _q(total=1000.0)
        invoice = _inv(
            total=1150.0,
            items=[
                {"description": "Pump seal kit", "quantity": 1, "unit_rate": 500.0, "line_total": 500.0},
                {"description": "Labour — engine service", "quantity": 5, "unit_rate": 100.0, "line_total": 500.0},
                {"description": "Freight", "quantity": 1, "unit_rate": 150.0, "line_total": 150.0},
            ],
        )
        return _state_with_comparison(quote, invoice)

    def _run(self, message, state):
        from whatsapp_app import _handle_text_message
        result, new_state = _handle_text_message(message, state, "whatsapp:+44123456789")
        return result, new_state

    def test_step1_can_i_approve_returns_query(self):
        state = self._freight_state()
        r, _ = self._run("can I approve this?", state)
        self.assertIn("QUERY", r)

    def test_step1_sets_approval_query_context(self):
        state = self._freight_state()
        _, new_state = self._run("can I approve this?", state)
        self.assertEqual(new_state.get("last_context", {}).get("type"), "approval_query")

    def test_step2_freight_accepted_returns_approve(self):
        state = self._freight_state()
        _, state = self._run("can I approve this?", state)
        r, _ = self._run("freight accepted", state)
        self.assertIn("APPROVE", r)
        self.assertIn("Low", r)
        self.assertNotIn("HOLD", r)

    def test_step2_freight_agreed_returns_approve(self):
        state = self._freight_state()
        _, state = self._run("can I approve this?", state)
        r, _ = self._run("freight agreed", state)
        self.assertIn("APPROVE", r)

    def test_step2_freight_not_accepted_returns_hold(self):
        state = self._freight_state()
        _, state = self._run("can I approve this?", state)
        r, _ = self._run("freight not accepted", state)
        self.assertIn("HOLD", r)
        self.assertIn("Medium", r)

    def test_step2_not_accepted_returns_hold(self):
        state = self._freight_state()
        _, state = self._run("can I approve this?", state)
        r, _ = self._run("not accepted", state)
        self.assertIn("HOLD", r)

    def test_accepted_with_no_context_not_document_not_understood(self):
        """'accepted' alone without approval context must not fall through to DOCUMENT NOT UNDERSTOOD."""
        r, _ = self._run("accepted", _state_empty())
        self.assertNotIn("DOCUMENT NOT UNDERSTOOD", r)
        self.assertIn("NO ACTIVE APPROVAL QUERY", r)

    def test_approval_query_first_still_returns_query(self):
        """Regression: 'can I approve this?' after freight invoice still returns QUERY first."""
        state = self._freight_state()
        r, _ = self._run("can I approve this?", state)
        self.assertIn("QUERY", r)
        self.assertNotIn("APPROVE", r.split("QUERY")[0])


if __name__ == "__main__":
    unittest.main()
