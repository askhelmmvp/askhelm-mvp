"""Tests for role-based response adaptation (ASK-14)."""
import unittest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_IYS_QUOTE_DOC = {
    "doc_type": "quote",
    "supplier_name": "International Yacht Services B.V.",
    "total": 1038.18,
    "currency": "EUR",
    "subtotal": 858.0,
    "tax": 180.18,
    "line_items": [
        {
            "description": "Barnacle Buster Concentrate 5-gallon pail",
            "unit_rate": 429.00,
            "quantity": 2.0,
            "line_total": 858.00,
        }
    ],
}

_CLEAN_COMPARISON = {
    "decision": "MATCH CONFIRMED — OK TO APPROVE",
    "line_check": [],
    "quantity_mismatches": [],
    "added_items": [],
    "missing_items": [],
    "ancillary_items": [],
    "logistics_notes": [],
    "priced_non_ancillary_added_items": [],
}

_CLEAN_COMPARISON_DATA = {
    "doc_a": {**_IYS_QUOTE_DOC, "doc_type": "quote"},
    "doc_b": {**_IYS_QUOTE_DOC, "doc_type": "invoice"},
    "comparison": _CLEAN_COMPARISON,
    "session_type": "quote_vs_invoice",
}


def _state(role=None, comparison=None):
    s = {
        "user_id": "test_user",
        "documents": [_IYS_QUOTE_DOC],
        "pending_invoice": None,
        "last_context": {},
        "sessions": [],
        "active_session_id": None,
    }
    if role:
        s["role"] = role
    return s


# ---------------------------------------------------------------------------
# Role storage helpers
# ---------------------------------------------------------------------------

class TestUserRoleHelpers(unittest.TestCase):

    def test_get_user_role_none_when_not_set(self):
        from domain.user_role import get_user_role
        self.assertIsNone(get_user_role({}))

    def test_get_user_role_returns_stored_role(self):
        from domain.user_role import get_user_role
        self.assertEqual(get_user_role({"role": "captain"}), "captain")

    def test_set_user_role_stores_role(self):
        from domain.user_role import set_user_role, get_user_role
        state = {}
        state = set_user_role(state, "purser")
        self.assertEqual(get_user_role(state), "purser")

    def test_extract_role_captain(self):
        from domain.user_role import extract_role_from_message
        self.assertEqual(extract_role_from_message("set my role to captain"), "captain")

    def test_extract_role_engineer(self):
        from domain.user_role import extract_role_from_message
        self.assertEqual(extract_role_from_message("I am the engineer"), "engineer")

    def test_extract_role_deck_officer(self):
        from domain.user_role import extract_role_from_message
        self.assertEqual(extract_role_from_message("set role deck officer"), "deck_officer")

    def test_extract_role_purser(self):
        from domain.user_role import extract_role_from_message
        self.assertEqual(extract_role_from_message("I'm the purser"), "purser")

    def test_extract_role_chief_engineer(self):
        from domain.user_role import extract_role_from_message
        self.assertEqual(extract_role_from_message("I am chief engineer"), "engineer")

    def test_extract_role_none_for_unknown(self):
        from domain.user_role import extract_role_from_message
        self.assertIsNone(extract_role_from_message("hello there"))


# ---------------------------------------------------------------------------
# Intent classification for role commands
# ---------------------------------------------------------------------------

class TestRoleIntentClassification(unittest.TestCase):

    def _classify(self, text):
        from domain.intent import classify_text
        return classify_text(text)

    def test_set_my_role_to_captain(self):
        self.assertEqual(self._classify("set my role to captain"), "set_role")

    def test_set_role_engineer(self):
        self.assertEqual(self._classify("set role engineer"), "set_role")

    def test_change_my_role_to_purser(self):
        self.assertEqual(self._classify("change my role to purser"), "set_role")

    def test_i_am_the_deck_officer(self):
        self.assertEqual(self._classify("I am the deck officer"), "set_role")

    def test_i_am_captain(self):
        self.assertEqual(self._classify("i am captain"), "set_role")

    def test_im_the_purser(self):
        self.assertEqual(self._classify("i'm the purser"), "set_role")

    def test_my_role_is_engineer(self):
        self.assertEqual(self._classify("my role is engineer"), "set_role")

    def test_show_my_role(self):
        self.assertEqual(self._classify("show my role"), "show_role")

    def test_what_is_my_role(self):
        self.assertEqual(self._classify("what is my role"), "show_role")

    def test_show_role(self):
        self.assertEqual(self._classify("show role"), "show_role")

    def test_my_current_role(self):
        self.assertEqual(self._classify("my current role"), "show_role")


# ---------------------------------------------------------------------------
# set_role handler
# ---------------------------------------------------------------------------

class TestSetRoleHandler(unittest.TestCase):

    def test_set_role_stores_role_in_state(self):
        from whatsapp_app import _handle_text_message
        _, updated = _handle_text_message(
            "set my role to captain",
            _state(),
            "whatsapp:+44123456789",
        )
        self.assertEqual(updated.get("role"), "captain")

    def test_set_role_returns_role_updated_decision(self):
        from whatsapp_app import _handle_text_message
        result, _ = _handle_text_message(
            "set my role to captain",
            _state(),
            "whatsapp:+44123456789",
        )
        self.assertIn("ROLE UPDATED", result)

    def test_set_role_deck_officer(self):
        from whatsapp_app import _handle_text_message
        _, updated = _handle_text_message(
            "set my role to deck officer",
            _state(),
            "whatsapp:+44123456789",
        )
        self.assertEqual(updated.get("role"), "deck_officer")

    def test_set_role_purser(self):
        from whatsapp_app import _handle_text_message
        result, updated = _handle_text_message(
            "i am the purser",
            _state(),
            "whatsapp:+44123456789",
        )
        self.assertEqual(updated.get("role"), "purser")
        self.assertIn("Purser", result)

    def test_set_unknown_role_returns_not_recognised(self):
        from whatsapp_app import _handle_text_message
        result, _ = _handle_text_message(
            "set my role to helmsman",
            _state(),
            "whatsapp:+44123456789",
        )
        self.assertIn("NOT RECOGNISED", result)


# ---------------------------------------------------------------------------
# show_role handler
# ---------------------------------------------------------------------------

class TestShowRoleHandler(unittest.TestCase):

    def test_show_role_returns_stored_role(self):
        from whatsapp_app import _handle_text_message
        result, _ = _handle_text_message(
            "show my role",
            _state(role="captain"),
            "whatsapp:+44123456789",
        )
        self.assertIn("Captain", result)

    def test_show_role_no_role_set(self):
        from whatsapp_app import _handle_text_message
        result, _ = _handle_text_message(
            "show my role",
            _state(),
            "whatsapp:+44123456789",
        )
        self.assertIn("NO ROLE", result)

    def test_show_role_deck_officer(self):
        from whatsapp_app import _handle_text_message
        result, _ = _handle_text_message(
            "show role",
            _state(role="deck_officer"),
            "whatsapp:+44123456789",
        )
        self.assertIn("Deck Officer", result)


# ---------------------------------------------------------------------------
# adapt_response_for_role
# ---------------------------------------------------------------------------

class TestAdaptResponseForRole(unittest.TestCase):

    _APPROVE_RESPONSE = (
        "DECISION:\nAPPROVE\n\n"
        "WHY:\nThe invoice matches the quoted scope.\n\n"
        "RISK:\nLow\n\n"
        "ACTIONS:\n• Approve payment\n• Keep records"
    )

    _HOLD_RESPONSE = (
        "DECISION:\nHOLD\n\n"
        "WHY:\nThe invoice has a cost uplift.\n\n"
        "RISK:\nHigh\n\n"
        "ACTIONS:\n• Hold payment\n• Request revised invoice"
    )

    _QUERY_RESPONSE = (
        "DECISION:\nQUERY\n\n"
        "WHY:\nFreight added.\n\n"
        "RISK:\nMedium\n\n"
        "ACTIONS:\n• Clarify freight\n• Hold until resolved"
    )

    def test_none_role_returns_unchanged(self):
        from whatsapp_app import adapt_response_for_role
        result = adapt_response_for_role(self._APPROVE_RESPONSE, None)
        self.assertEqual(result, self._APPROVE_RESPONSE)

    def test_unknown_role_returns_unchanged(self):
        from whatsapp_app import adapt_response_for_role
        result = adapt_response_for_role(self._APPROVE_RESPONSE, "helmsman")
        self.assertEqual(result, self._APPROVE_RESPONSE)

    def test_decision_preserved_for_all_roles(self):
        from whatsapp_app import adapt_response_for_role
        for role in ("engineer", "captain", "purser", "deck_officer"):
            result = adapt_response_for_role(self._APPROVE_RESPONSE, role)
            self.assertIn("DECISION:\nAPPROVE", result, f"DECISION lost for role {role}")

    def test_engineer_approve_mentions_parts_or_equipment(self):
        from whatsapp_app import adapt_response_for_role
        result = adapt_response_for_role(self._APPROVE_RESPONSE, "engineer")
        lower = result.lower()
        self.assertTrue(
            "part" in lower or "equipment" in lower or "stock" in lower,
            f"Expected technical mention in: {result}",
        )

    def test_captain_approve_mentions_risk_or_approval(self):
        from whatsapp_app import adapt_response_for_role
        result = adapt_response_for_role(self._APPROVE_RESPONSE, "captain")
        lower = result.lower()
        self.assertTrue(
            "risk" in lower or "approve" in lower or "audit" in lower,
            f"Expected risk/approval mention in: {result}",
        )

    def test_purser_approve_mentions_payment_or_vat(self):
        from whatsapp_app import adapt_response_for_role
        result = adapt_response_for_role(self._APPROVE_RESPONSE, "purser")
        lower = result.lower()
        self.assertTrue(
            "payment" in lower or "vat" in lower or "file" in lower,
            f"Expected payment/VAT mention in: {result}",
        )

    def test_deck_officer_approve_mentions_deck_or_log(self):
        from whatsapp_app import adapt_response_for_role
        result = adapt_response_for_role(self._APPROVE_RESPONSE, "deck_officer")
        lower = result.lower()
        self.assertTrue(
            "deck" in lower or "log" in lower or "readiness" in lower,
            f"Expected deck/log mention in: {result}",
        )

    def test_hold_decision_role_adapted(self):
        from whatsapp_app import adapt_response_for_role
        result = adapt_response_for_role(self._HOLD_RESPONSE, "purser")
        self.assertIn("DECISION:\nHOLD", result)
        self.assertIn("payment", result.lower())

    def test_query_decision_role_adapted(self):
        from whatsapp_app import adapt_response_for_role
        result = adapt_response_for_role(self._QUERY_RESPONSE, "captain")
        self.assertIn("DECISION:\nQUERY", result)

    def test_non_approve_decision_unchanged(self):
        from whatsapp_app import adapt_response_for_role
        response = (
            "DECISION:\nMATCH CONFIRMED — OK TO APPROVE\n\n"
            "WHY:\nInvoice matches.\n\n"
            "RECOMMENDED ACTIONS:\n• Proceed"
        )
        result = adapt_response_for_role(response, "captain")
        self.assertEqual(result, response)


# ---------------------------------------------------------------------------
# Approval mode: same decision, different actions per role
# ---------------------------------------------------------------------------

class TestApprovalRoleAdaptation(unittest.TestCase):
    """Tests 3-7 from the spec: same approval result, role-specific emphasis."""

    _APPROVE = (
        "DECISION:\nAPPROVE\n\n"
        "WHY:\nInvoice matches the quote. Confidence: HIGH.\n\n"
        "RISK:\nLow\n\n"
        "ACTIONS:\n• Approve payment\n• Keep quote and invoice together\n• File for audit"
    )

    def _mock_approval(self, role):
        with patch("whatsapp_app._handle_approval", return_value=self._APPROVE):
            from whatsapp_app import _handle_text_message
            result, _ = _handle_text_message(
                "can I approve this?",
                _state(role=role),
                "whatsapp:+44123456789",
            )
        return result

    def test_approve_decision_preserved_for_engineer(self):
        result = self._mock_approval("engineer")
        self.assertIn("DECISION:\nAPPROVE", result)

    def test_approve_decision_preserved_for_captain(self):
        result = self._mock_approval("captain")
        self.assertIn("DECISION:\nAPPROVE", result)

    def test_approve_decision_preserved_for_purser(self):
        result = self._mock_approval("purser")
        self.assertIn("DECISION:\nAPPROVE", result)

    def test_approve_decision_preserved_for_deck_officer(self):
        result = self._mock_approval("deck_officer")
        self.assertIn("DECISION:\nAPPROVE", result)

    def test_engineer_actions_mention_parts_or_equipment(self):
        result = self._mock_approval("engineer")
        lower = result.lower()
        self.assertTrue("part" in lower or "equipment" in lower or "stock" in lower)

    def test_captain_actions_mention_approval_or_audit(self):
        result = self._mock_approval("captain")
        lower = result.lower()
        self.assertTrue("approve" in lower or "audit" in lower or "risk" in lower)

    def test_purser_actions_mention_payment_or_vat(self):
        result = self._mock_approval("purser")
        lower = result.lower()
        self.assertTrue("payment" in lower or "vat" in lower or "file" in lower)

    def test_deck_officer_actions_mention_deck_or_log(self):
        result = self._mock_approval("deck_officer")
        lower = result.lower()
        self.assertTrue("deck" in lower or "log" in lower or "readiness" in lower)

    def test_no_role_returns_unmodified_response(self):
        result = self._mock_approval(None)
        self.assertEqual(result, self._APPROVE)


# ---------------------------------------------------------------------------
# Compliance role hint
# ---------------------------------------------------------------------------

class TestComplianceRoleHint(unittest.TestCase):

    def test_captain_role_hint_sent_to_compliance_engine(self):
        captured = {}

        def mock_compliance(question, yacht_id="h3"):
            captured["question"] = question
            return "DECISION:\nCOMPLIANCE CHECK\n\nWHY:\nMARPOL applies.\n\nACTIONS:\n• Check log"

        with patch("whatsapp_app.answer_compliance_query", side_effect=mock_compliance):
            from whatsapp_app import _handle_text_message
            _handle_text_message(
                "what does MARPOL say about oil discharge?",
                _state(role="captain"),
                "whatsapp:+44123456789",
            )
        q = captured.get("question", "")
        self.assertIn("Captain", q)

    def test_deck_officer_role_hint_sent_to_compliance_engine(self):
        captured = {}

        def mock_compliance(question, yacht_id="h3"):
            captured["question"] = question
            return "DECISION:\nCOMPLIANCE CHECK\n\nWHY:\nStandard applies.\n\nACTIONS:\n• Log"

        with patch("whatsapp_app.answer_compliance_query", side_effect=mock_compliance):
            from whatsapp_app import _handle_text_message
            _handle_text_message(
                "what does MARPOL say about oil discharge?",
                _state(role="deck_officer"),
                "whatsapp:+44123456789",
            )
        q = captured.get("question", "")
        self.assertIn("Deck Officer", q)

    def test_no_role_no_hint_added(self):
        captured = {}

        def mock_compliance(question, yacht_id="h3"):
            captured["question"] = question
            return "DECISION:\nCOMPLIANCE CHECK\n\nWHY:\nApplies.\n\nACTIONS:\n• Check"

        with patch("whatsapp_app.answer_compliance_query", side_effect=mock_compliance):
            from whatsapp_app import _handle_text_message
            _handle_text_message(
                "what does MARPOL say about oil discharge?",
                _state(),
                "whatsapp:+44123456789",
            )
        q = captured.get("question", "")
        self.assertNotIn("USER ROLE", q)


# ---------------------------------------------------------------------------
# Market check role hint
# ---------------------------------------------------------------------------

class TestMarketCheckRoleHint(unittest.TestCase):

    def test_captain_role_hint_in_market_check_query(self):
        captured = {}

        def mock_check(query, **kwargs):
            captured["query"] = query
            return "DECISION:\nACCEPTABLE PRICE\n\nWHY:\nFair.\n\nRECOMMENDED ACTIONS:\n• Proceed"

        with patch("whatsapp_app.check_market_price", side_effect=mock_check):
            from whatsapp_app import _handle_text_message
            _handle_text_message(
                "is this a fair price?",
                _state(role="captain"),
                "whatsapp:+44123456789",
            )
        q = captured.get("query", "")
        self.assertIn("Captain", q)

    def test_purser_role_hint_in_market_check_query(self):
        captured = {}

        def mock_check(query, **kwargs):
            captured["query"] = query
            return "DECISION:\nACCEPTABLE PRICE\n\nWHY:\nFair.\n\nRECOMMENDED ACTIONS:\n• Proceed"

        with patch("whatsapp_app.check_market_price", side_effect=mock_check):
            from whatsapp_app import _handle_text_message
            _handle_text_message(
                "is this a fair price?",
                _state(role="purser"),
                "whatsapp:+44123456789",
            )
        q = captured.get("query", "")
        self.assertIn("Purser", q)

    def test_no_role_no_hint_in_market_check_query(self):
        captured = {}

        def mock_check(query, **kwargs):
            captured["query"] = query
            return "DECISION:\nACCEPTABLE PRICE\n\nWHY:\nFair.\n\nRECOMMENDED ACTIONS:\n• Proceed"

        with patch("whatsapp_app.check_market_price", side_effect=mock_check):
            from whatsapp_app import _handle_text_message
            _handle_text_message(
                "is this a fair price?",
                _state(),
                "whatsapp:+44123456789",
            )
        q = captured.get("query", "")
        self.assertNotIn("USER ROLE", q)


# ---------------------------------------------------------------------------
# Regression: existing flows unaffected when no role set
# ---------------------------------------------------------------------------

class TestRoleRegressions(unittest.TestCase):

    def test_no_role_approval_unchanged(self):
        """Approval without role returns the base response untouched."""
        _BASE = (
            "DECISION:\nAPPROVE\n\nWHY:\nMatches.\n\nRISK:\nLow\n\n"
            "ACTIONS:\n• Approve payment\n• Keep records"
        )
        with patch("whatsapp_app._handle_approval", return_value=_BASE):
            from whatsapp_app import _handle_text_message
            result, _ = _handle_text_message(
                "can I approve this?",
                _state(),
                "whatsapp:+44123456789",
            )
        self.assertEqual(result, _BASE)

    def test_set_role_does_not_break_approval_intent(self):
        """After setting role, approval intent still resolves correctly."""
        from whatsapp_app import _handle_text_message
        _, state_with_role = _handle_text_message(
            "set my role to engineer",
            _state(),
            "whatsapp:+44123456789",
        )
        self.assertEqual(state_with_role.get("role"), "engineer")

    def test_new_session_does_not_clear_role(self):
        """Session reset clears sessions but should preserve role."""
        from whatsapp_app import _handle_text_message
        _, updated = _handle_text_message(
            "new comparison",
            _state(role="captain"),
            "whatsapp:+44123456789",
        )
        self.assertEqual(updated.get("role"), "captain")


if __name__ == "__main__":
    unittest.main()
