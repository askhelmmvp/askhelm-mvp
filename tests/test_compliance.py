"""Tests for compliance engine and intent routing."""
import unittest
from unittest.mock import patch, MagicMock

from domain.intent import classify_text

COMPLIANCE = "compliance_question"


class TestComplianceIntent(unittest.TestCase):
    def _cls(self, text):
        return classify_text(text)

    # --- existing keyword triggers ---
    def test_marpol_trigger(self):
        self.assertEqual(self._cls("does marpol apply to my vessel"), COMPLIANCE)

    def test_annex_vi_trigger(self):
        self.assertEqual(self._cls("what does annex vi say about NOx"), COMPLIANCE)

    def test_tier_iii_trigger(self):
        self.assertEqual(self._cls("do we need tier iii engines"), COMPLIANCE)

    def test_seca_trigger(self):
        self.assertEqual(self._cls("are we in a seca zone"), COMPLIANCE)

    def test_sulphur_trigger(self):
        self.assertEqual(self._cls("sulphur limit in Baltic"), COMPLIANCE)

    def test_ism_code_trigger(self):
        self.assertEqual(self._cls("what does ism code require for drills"), COMPLIANCE)

    def test_non_conformity_trigger(self):
        self.assertEqual(self._cls("how do we close a non-conformity"), COMPLIANCE)

    def test_liferaft_trigger(self):
        self.assertEqual(self._cls("when does the liferaft need servicing"), COMPLIANCE)

    def test_yacht_code_trigger(self):
        self.assertEqual(self._cls("yacht code requirements for fire pumps"), COMPLIANCE)

    def test_compliant_trigger(self):
        self.assertEqual(self._cls("is this compliant with flag state rules"), COMPLIANCE)

    # --- new keyword triggers ---
    def test_nox_trigger(self):
        self.assertEqual(self._cls("what are the NOx limits for our engine"), COMPLIANCE)

    def test_eca_trigger(self):
        self.assertEqual(self._cls("does this ECA restriction apply to yachts"), COMPLIANCE)

    def test_allowed_trigger(self):
        self.assertEqual(self._cls("is bilge water discharge allowed here"), COMPLIANCE)

    def test_permitted_trigger(self):
        self.assertEqual(self._cls("is grey water discharge permitted in port"), COMPLIANCE)

    def test_regulation_trigger(self):
        self.assertEqual(self._cls("what regulation covers this"), COMPLIANCE)

    def test_requirement_trigger(self):
        self.assertEqual(self._cls("what is the requirement for ECDIS"), COMPLIANCE)

    def test_compliance_trigger(self):
        self.assertEqual(self._cls("check our compliance status"), COMPLIANCE)

    # --- question-pattern triggers ---
    def test_does_apply_pattern(self):
        self.assertEqual(self._cls("Does Tier III apply in Norwegian Sea?"), COMPLIANCE)

    def test_what_does_say_pattern(self):
        self.assertEqual(self._cls("What does ISM say about non-conformity?"), COMPLIANCE)

    def test_what_is_required_pattern(self):
        self.assertEqual(self._cls("what is required for port entry inspection"), COMPLIANCE)

    def test_is_this_allowed_pattern(self):
        self.assertEqual(self._cls("is this allowed under current rules"), COMPLIANCE)

    def test_are_we_allowed_pattern(self):
        self.assertEqual(self._cls("are we allowed to discharge bilge here"), COMPLIANCE)

    # --- commercial intents still win ---
    def test_new_session_beats_compliance(self):
        self.assertEqual(self._cls("new quote"), "new_session")

    def test_quote_compare_beats_compliance(self):
        self.assertEqual(self._cls("compare quotes"), "quote_compare")

    def test_greeting_not_compliance(self):
        self.assertEqual(self._cls("hi"), "greeting")

    def test_unknown_not_compliance(self):
        self.assertEqual(self._cls("send me the schedule"), "unknown")


class TestComplianceRouting(unittest.TestCase):
    """Verify compliance questions reach the engine, not 'TEXT RECEIVED'."""

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_question")
    def test_tier_iii_question_routed_to_engine(self, mock_llm, mock_retriever_getter):
        mock_retriever = MagicMock()
        mock_retriever.search.return_value = [
            {"source_reference": "MARPOL Annex VI Reg 13", "content": "Tier III NOx limits apply in NECAs."}
        ]
        mock_retriever_getter.return_value = mock_retriever
        mock_llm.return_value = (
            "DECISION: Yes — Tier III applies in designated NECAs including Norwegian waters.\n"
            "WHY: MARPOL Annex VI Regulation 13 requires Tier III NOx standards in NECAs.\n"
            "SOURCE: MARPOL Annex VI Reg 13\n"
            "ACTIONS: Confirm vessel's engine build date and whether NECA boundaries apply to route."
        )

        from domain.compliance_engine import answer_compliance_query
        result = answer_compliance_query("Does Tier III apply in Norwegian Sea?")

        self.assertIn("DECISION:", result)
        self.assertIn("WHY:", result)
        self.assertIn("SOURCE:", result)
        self.assertIn("ACTIONS:", result)
        self.assertNotIn("TEXT RECEIVED", result)

    def test_compliance_intent_not_unknown(self):
        self.assertNotEqual(classify_text("Does Tier III apply in Norwegian Sea?"), "unknown")
        self.assertNotEqual(classify_text("What does ISM say about non-conformity?"), "unknown")
        self.assertNotEqual(classify_text("Is bilge water discharge allowed here?"), "unknown")

    def test_compliance_works_without_uploaded_document(self):
        # classify_text takes only the message string — no document state needed
        result = classify_text("Does Annex VI apply to our route?")
        self.assertEqual(result, COMPLIANCE)


class TestComplianceEngine(unittest.TestCase):
    def _engine(self):
        import domain.compliance_engine as ce
        ce._retriever = None
        return ce

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_question")
    def test_returns_llm_answer_when_chunks_found(self, mock_llm, mock_retriever_getter):
        mock_retriever = MagicMock()
        mock_retriever.search.return_value = [
            {"source_reference": "MARPOL Annex VI Reg 14", "content": "Sulphur content limit 0.1% m/m in SECA."}
        ]
        mock_retriever_getter.return_value = mock_retriever
        mock_llm.return_value = (
            "DECISION: Yes — sulphur limit is 0.1% m/m in SECA.\n"
            "WHY: MARPOL Annex VI Regulation 14 requires 0.1% m/m within ECAs.\n"
            "SOURCE: MARPOL Annex VI Reg 14\n"
            "ACTIONS: Ensure bunkers certified at ≤0.1% m/m before entering SECA."
        )

        from domain.compliance_engine import answer_compliance_query
        result = answer_compliance_query("sulphur limit in SECA")

        self.assertIn("DECISION:", result)
        self.assertIn("WHY:", result)
        self.assertIn("SOURCE:", result)
        self.assertIn("ACTIONS:", result)
        mock_llm.assert_called_once()

    @patch("domain.compliance_engine._get_retriever")
    def test_fallback_when_no_chunks(self, mock_retriever_getter):
        mock_retriever = MagicMock()
        mock_retriever.search.return_value = []
        mock_retriever_getter.return_value = mock_retriever

        from domain.compliance_engine import answer_compliance_query
        result = answer_compliance_query("something obscure and unmatched")

        self.assertIn("DECISION:", result)
        self.assertIn("Not explicitly covered", result)

    @patch("domain.compliance_engine._get_retriever")
    def test_fallback_when_retriever_raises(self, mock_retriever_getter):
        mock_retriever_getter.side_effect = RuntimeError("index corrupt")

        from domain.compliance_engine import answer_compliance_query
        result = answer_compliance_query("marpol tier iii")

        self.assertIn("DECISION:", result)
        self.assertIn("Cannot confirm", result)


class TestAnswerComplianceQuestion(unittest.TestCase):
    def test_empty_chunks_returns_fallback(self):
        from services.anthropic_service import answer_compliance_question
        result = answer_compliance_question("does marpol apply", [])
        self.assertIn("DECISION:", result)
        self.assertIn("Not explicitly covered", result)
        self.assertIn("ACTIONS:", result)

    @patch("services.anthropic_service.client")
    def test_calls_claude_with_chunks(self, mock_client):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=(
            "DECISION: Yes.\nWHY: Rule X.\nSOURCE: Reg 14\nACTIONS: Check bunkers."
        ))]
        mock_client.messages.create.return_value = mock_response

        from services.anthropic_service import answer_compliance_question
        chunks = [{"source_reference": "MARPOL Annex VI Reg 14", "content": "Sulphur 0.1%"}]
        result = answer_compliance_question("sulphur limit", chunks)

        self.assertIn("DECISION:", result)
        mock_client.messages.create.assert_called_once()
        call_kwargs = mock_client.messages.create.call_args[1]
        self.assertEqual(call_kwargs["model"], "claude-sonnet-4-6")


class TestComplianceFollowUp(unittest.TestCase):
    """Follow-up questions after a compliance answer must route to the compliance engine."""

    # --- intent classification ---
    def test_what_now_is_compliance_followup(self):
        self.assertEqual(classify_text("what now"), "compliance_followup")

    def test_next_steps_is_compliance_followup(self):
        self.assertEqual(classify_text("next steps"), "compliance_followup")

    def test_what_does_this_mean_is_compliance_followup(self):
        self.assertEqual(classify_text("what does this mean"), "compliance_followup")

    def test_what_to_do_stays_what_to_do(self):
        # "what should I do" is still classified as what_to_do; routing handles context
        self.assertEqual(classify_text("what should i do"), "what_to_do")

    # --- scenario: compliance Q then "what should I do" ---
    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_followup_question")
    def test_what_should_i_do_routes_to_compliance_when_last_context_is_compliance(
        self, mock_followup, mock_retriever_getter
    ):
        mock_retriever = MagicMock()
        mock_retriever.search.return_value = [
            {"source_reference": "ISM Code Chapter 9", "content": "Non-conformities must be documented and corrected."}
        ]
        mock_retriever_getter.return_value = mock_retriever
        mock_followup.return_value = (
            "DECISION: Raise and close the non-conformity.\n"
            "\n"
            "WHY:\n"
            "Overdue testing is an open non-conformity until documented and corrected.\n"
            "\n"
            "ACTIONS:\n"
            "• Raise a non-conformity report in the SMS\n"
            "• Schedule and complete the overdue fire pump test\n"
            "• Record test results in the planned maintenance log\n"
            "• Close the NC once evidence is filed"
        )

        # State simulates: compliance question was just answered
        state = {
            "sessions": [],
            "documents": [],
            "active_session_id": None,
            "last_context": {
                "type": "compliance",
                "topic": "is overdue fire pump testing a non-conformity?",
            },
        }

        from whatsapp_app import _handle_text_message
        answer, _ = _handle_text_message("what should i do", state)

        self.assertIn("DECISION:", answer)
        self.assertIn("ACTIONS:", answer)
        self.assertNotIn("NO ACTIVE COMPARISON", answer)
        # Must call the action-focused function, NOT the full explanation function
        mock_followup.assert_called_once()

    # --- scenario: "what should I do" with no compliance context falls through to commercial ---
    def test_what_should_i_do_goes_commercial_when_no_compliance_context(self):
        state = {
            "sessions": [],
            "documents": [],
            "active_session_id": None,
        }

        from whatsapp_app import _handle_text_message
        answer, _ = _handle_text_message("what should i do", state)

        # Commercial fallback — no comparison in session
        self.assertIn("NO ACTIVE COMPARISON", answer)

    # --- compliance_followup with no prior context ---
    def test_compliance_followup_no_context_returns_helpful_message(self):
        state = {
            "sessions": [],
            "documents": [],
            "active_session_id": None,
        }

        from whatsapp_app import _handle_text_message
        answer, _ = _handle_text_message("next steps", state)

        self.assertIn("DECISION:", answer)
        self.assertIn("No recent compliance topic", answer)

    # --- compliance question sets last_context ---
    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_question")
    def test_compliance_question_sets_last_context(self, mock_llm, mock_retriever_getter):
        mock_retriever = MagicMock()
        mock_retriever.search.return_value = [
            {"source_reference": "ISM Code Ch 9", "content": "Non-conformities..."}
        ]
        mock_retriever_getter.return_value = mock_retriever
        mock_llm.return_value = "DECISION: Yes.\nWHY: X.\nSOURCE: Y\nACTIONS: Z"

        state = {
            "sessions": [],
            "documents": [],
            "active_session_id": None,
        }

        from whatsapp_app import _handle_text_message
        question = "is overdue fire pump testing a non-conformity?"
        _, updated_state = _handle_text_message(question, state)

        ctx = updated_state.get("last_context", {})
        self.assertEqual(ctx.get("type"), "compliance")
        self.assertEqual(ctx.get("topic"), question)

    # --- new_session clears last_context ---
    def test_new_session_clears_compliance_context(self):
        state = {
            "sessions": [],
            "documents": [],
            "active_session_id": None,
            "last_context": {"type": "compliance", "topic": "some prior question"},
        }

        from whatsapp_app import _handle_text_message
        _, updated_state = _handle_text_message("new comparison", state)

        self.assertNotIn("last_context", updated_state)


class TestComplianceFollowUpBehaviour(unittest.TestCase):
    """Follow-up response must be action-focused and must not repeat the original answer."""

    @patch("services.anthropic_service.client")
    def test_followup_system_prompt_forbids_repetition(self, mock_client):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=(
            "DECISION: Raise and close the non-conformity.\n"
            "\n"
            "WHY:\n"
            "Overdue testing is an open non-conformity until corrected.\n"
            "\n"
            "ACTIONS:\n"
            "• Raise an NC report\n"
            "• Complete the overdue test\n"
            "• Record results\n"
            "• Close the NC"
        ))]
        mock_client.messages.create.return_value = mock_response

        from services.anthropic_service import answer_compliance_followup_question
        chunks = [{"source_reference": "ISM Code Ch 9", "content": "Non-conformities must be corrected."}]
        answer_compliance_followup_question("is overdue fire pump testing a non-conformity?", chunks)

        call_kwargs = mock_client.messages.create.call_args[1]
        system_prompt = call_kwargs["system"]

        # Must instruct not to repeat the decision or definition
        self.assertIn("Do NOT repeat", system_prompt)
        self.assertIn("re-explain", system_prompt)
        # Must not include a SOURCE line
        self.assertNotIn("SOURCE:", system_prompt.split("RULES")[0] if "RULES" in system_prompt else "")
        # Must cap at 4 bullets
        self.assertIn("Maximum 4", system_prompt)
        # Must use shorter max_tokens than the full explanation
        self.assertLessEqual(call_kwargs["max_tokens"], 400)

    @patch("services.anthropic_service.client")
    def test_followup_response_has_no_source_line(self, mock_client):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=(
            "DECISION: Complete the overdue test immediately.\n"
            "\n"
            "WHY:\n"
            "An open non-conformity must be corrected before the next audit.\n"
            "\n"
            "ACTIONS:\n"
            "• Raise NC report in the SMS\n"
            "• Run and record the fire pump test\n"
            "• Close NC with evidence attached"
        ))]
        mock_client.messages.create.return_value = mock_response

        from services.anthropic_service import answer_compliance_followup_question
        chunks = [{"source_reference": "ISM Code Ch 9", "content": "Non-conformities must be corrected."}]
        result = answer_compliance_followup_question(
            "is overdue fire pump testing a non-conformity?", chunks
        )

        # Follow-up format must NOT include SOURCE
        self.assertNotIn("SOURCE:", result)
        # Must have the action format
        self.assertIn("DECISION:", result)
        self.assertIn("WHY:", result)
        self.assertIn("ACTIONS:", result)

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_followup_question")
    def test_full_scenario_fire_pump_nc_then_what_to_do(self, mock_followup, mock_retriever_getter):
        """
        Scenario: user asks about NC, then asks 'what should I do'.
        Second response must be shorter and action-focused.
        """
        TOPIC = "is overdue fire pump testing a non-conformity?"
        FIRST_ANSWER = (
            "DECISION: Yes — overdue fire pump testing is a non-conformity under the ISM Code.\n"
            "WHY: ISM Code Chapter 10 requires periodic testing of safety equipment; "
            "any overdue item constitutes a non-conformity against the SMS.\n"
            "SOURCE: ISM Code Chapter 10, Section 10.3\n"
            "ACTIONS: • Document the overdue test\n• Raise a non-conformity report\n• Schedule the test"
        )
        FOLLOWUP_ANSWER = (
            "DECISION: Close the non-conformity by completing the test.\n"
            "\n"
            "WHY:\n"
            "The NC remains open until the fire pump test is done and recorded.\n"
            "\n"
            "ACTIONS:\n"
            "• Raise NC report in the SMS now\n"
            "• Run and record the fire pump test\n"
            "• Attach evidence to the NC\n"
            "• Close NC before next internal audit"
        )

        mock_retriever = MagicMock()
        mock_retriever.search.return_value = [
            {"source_reference": "ISM Code Ch 10", "content": "Safety equipment must be tested periodically."}
        ]
        mock_retriever_getter.return_value = mock_retriever
        mock_followup.return_value = FOLLOWUP_ANSWER

        state = {
            "sessions": [],
            "documents": [],
            "active_session_id": None,
            "last_context": {"type": "compliance", "topic": TOPIC},
        }

        from whatsapp_app import _handle_text_message
        answer, _ = _handle_text_message("what should i do", state)

        # Follow-up must be action-focused
        self.assertIn("ACTIONS:", answer)
        self.assertNotIn("NO ACTIVE COMPARISON", answer)
        # Follow-up must not repeat the full explanation from the first answer
        self.assertNotIn("SOURCE:", answer)
        # Follow-up must be shorter than or equal to what would be a full repeat
        self.assertLess(len(answer), len(FIRST_ANSWER))
        # The engine must have called the action-focused function, not the full one
        mock_followup.assert_called_once()


class TestComplianceGrounding(unittest.TestCase):
    """Answers must be grounded in loaded documents only — no external knowledge."""

    @patch("services.anthropic_service.client")
    def test_system_prompt_forbids_external_knowledge(self, mock_client):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=(
            "DECISION: Not explicitly covered in the loaded documents.\n"
            "WHY: This question is not answered in the loaded compliance sources.\n"
            "SOURCE: No matching loaded source\n"
            "ACTIONS: • Refer to the relevant regulation or onboard procedure if this needs to be confirmed"
        ))]
        mock_client.messages.create.return_value = mock_response

        from services.anthropic_service import answer_compliance_question
        chunks = [{"source_reference": "MARPOL Annex VI Reg 14", "content": "Sulphur limit 0.1% in SECA."}]
        answer_compliance_question("what does marpol annex vi say about ballast water?", chunks)

        call_kwargs = mock_client.messages.create.call_args[1]
        system_prompt = call_kwargs["system"]

        # System prompt must explicitly forbid external knowledge
        self.assertIn("Do NOT use training knowledge", system_prompt)
        self.assertIn("Do NOT mention any convention", system_prompt)
        self.assertIn("Do NOT supplement", system_prompt)
        # System prompt must embed the exact fallback text the model must copy
        self.assertIn("Not explicitly covered in the loaded documents.", system_prompt)
        self.assertIn("No matching loaded source", system_prompt)

    @patch("services.anthropic_service.client")
    def test_ballast_water_returns_strict_fallback_no_bwm(self, mock_client):
        # Claude is correctly prompted; simulate it returning the strict fallback
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=(
            "DECISION: Not explicitly covered in the loaded documents.\n"
            "WHY: This question is not answered in the loaded compliance sources.\n"
            "SOURCE: No matching loaded source\n"
            "ACTIONS: • Refer to the relevant regulation or onboard procedure if this needs to be confirmed"
        ))]
        mock_client.messages.create.return_value = mock_response

        from services.anthropic_service import answer_compliance_question
        # Chunks are about MARPOL Annex VI sulphur — not ballast water
        chunks = [
            {"source_reference": "MARPOL Annex VI Reg 14", "content": "Sulphur content 0.1% m/m in SECA."},
            {"source_reference": "MARPOL Annex VI Reg 13", "content": "NOx Tier III in NECAs."},
        ]
        result = answer_compliance_question(
            "what does marpol annex vi say about ballast water?", chunks
        )

        # Must not contain external regulatory knowledge
        self.assertNotIn("BWM", result)
        self.assertNotIn("Ballast Water Management Convention", result)
        self.assertNotIn("ballast water convention", result.lower())
        # Must use the strict fallback
        self.assertIn("Not explicitly covered", result)
        self.assertIn("No matching loaded source", result)
        self.assertNotIn("Cannot confirm", result)

    def test_empty_chunks_uses_not_covered_fallback(self):
        from services.anthropic_service import answer_compliance_question, NOT_COVERED_FALLBACK
        result = answer_compliance_question("what does marpol say about ballast water?", [])
        self.assertEqual(result, NOT_COVERED_FALLBACK)

    @patch("domain.compliance_engine._get_retriever")
    def test_engine_no_chunks_uses_not_covered_fallback(self, mock_retriever_getter):
        from services.anthropic_service import NOT_COVERED_FALLBACK
        mock_retriever = MagicMock()
        mock_retriever.search.return_value = []
        mock_retriever_getter.return_value = mock_retriever

        from domain.compliance_engine import answer_compliance_query
        result = answer_compliance_query("what does marpol say about ballast water?")
        self.assertEqual(result, NOT_COVERED_FALLBACK)


if __name__ == "__main__":
    unittest.main()
