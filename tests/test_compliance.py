"""Tests for compliance engine and intent routing."""
import os
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

    # --- natural language patterns ---
    def test_fire_pump_overdue_natural_language(self):
        self.assertEqual(self._cls("my monthly fire pump test is overdue, is that ok?"), COMPLIANCE)

    def test_overdue_fire_pump_question(self):
        self.assertEqual(self._cls("is an overdue fire pump test ok?"), COMPLIANCE)

    def test_is_this_ok_pattern(self):
        self.assertEqual(self._cls("is this ok?"), COMPLIANCE)

    def test_is_this_a_problem(self):
        self.assertEqual(self._cls("is this a problem?"), COMPLIANCE)

    def test_can_we_operate(self):
        self.assertEqual(self._cls("can we operate like this?"), COMPLIANCE)

    def test_what_happens_if(self):
        self.assertEqual(self._cls("what happens if we skip the test?"), COMPLIANCE)

    def test_commercial_guard_blocks_fallback(self):
        self.assertNotEqual(self._cls("is this price ok?"), COMPLIANCE)

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
        _chunks = [{"source_reference": "MARPOL Annex VI Reg 13", "content": "Tier III NOx limits apply in NECAs.", "score": 0.5}]
        mock_retriever.search.return_value = _chunks
        mock_retriever.search_with_yacht.return_value = _chunks
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
        _chunks = [{"source_reference": "MARPOL Annex VI Reg 14", "content": "Sulphur content limit 0.1% m/m in SECA.", "score": 0.5}]
        mock_retriever.search.return_value = _chunks
        mock_retriever.search_with_yacht.return_value = _chunks
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
        mock_retriever.search_with_yacht.return_value = []
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
        self.assertIn("Not explicitly covered", result)


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
        _chunks = [{"source_reference": "ISM Code Chapter 9", "content": "Non-conformities must be documented and corrected.", "score": 0.5}]
        mock_retriever.search.return_value = _chunks
        mock_retriever.search_with_yacht.return_value = _chunks
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
        _chunks = [{"source_reference": "ISM Code Ch 9", "content": "Non-conformities...", "score": 0.5}]
        mock_retriever.search.return_value = _chunks
        mock_retriever.search_with_yacht.return_value = _chunks
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
        _sys = call_kwargs["system"]
        system_prompt = _sys[0]["text"] if isinstance(_sys, list) else _sys

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
        _chunks = [{"source_reference": "ISM Code Ch 10", "content": "Safety equipment must be tested periodically.", "score": 0.5}]
        mock_retriever.search.return_value = _chunks
        mock_retriever.search_with_yacht.return_value = _chunks
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
        _sys = call_kwargs["system"]
        system_prompt = _sys[0]["text"] if isinstance(_sys, list) else _sys

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
        mock_retriever.search_with_yacht.return_value = []
        mock_retriever_getter.return_value = mock_retriever

        from domain.compliance_engine import answer_compliance_query
        result = answer_compliance_query("what does marpol say about ballast water?")
        self.assertEqual(result, NOT_COVERED_FALLBACK)


class TestComplianceStoragePaths(unittest.TestCase):
    def test_compliance_profile_path(self):
        from storage_paths import get_compliance_profile_path
        path = get_compliance_profile_path("h3")
        self.assertIn("h3", str(path))
        self.assertTrue(str(path).endswith("compliance_profile.json"))

    def test_yacht_compliance_dir(self):
        from storage_paths import get_yacht_compliance_dir
        path = get_yacht_compliance_dir("h3")
        self.assertIn("compliance", str(path))
        self.assertIn("h3", str(path))

    def test_yacht_compliance_chunks_path(self):
        from storage_paths import get_yacht_compliance_chunks_path
        path = get_yacht_compliance_chunks_path("h3")
        self.assertTrue(str(path).endswith("compliance_chunks.jsonl"))

    def test_yacht_compliance_index_path(self):
        from storage_paths import get_yacht_compliance_index_path
        path = get_yacht_compliance_index_path("h3")
        self.assertTrue(str(path).endswith("compliance_index.pkl"))

    def test_storage_dir_env_alias(self):
        import os
        import importlib
        import storage_paths as sp
        original = os.environ.get("DATA_DIR")
        os.environ.pop("DATA_DIR", None)
        os.environ["STORAGE_DIR"] = "/tmp/testaskhelm"
        try:
            importlib.reload(sp)
            self.assertEqual(str(sp.get_data_dir()), "/tmp/testaskhelm")
        finally:
            os.environ.pop("STORAGE_DIR", None)
            if original:
                os.environ["DATA_DIR"] = original
            importlib.reload(sp)


class TestComplianceProfileService(unittest.TestCase):
    def setUp(self):
        import tempfile
        import os
        self.tmpdir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.tmpdir
        import importlib
        import storage_paths
        importlib.reload(storage_paths)

    def tearDown(self):
        import os
        import shutil
        import importlib
        os.environ.pop("DATA_DIR", None)
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        import storage_paths
        importlib.reload(storage_paths)

    def test_load_creates_default_profile(self):
        import importlib
        import services.compliance_profile as cp
        importlib.reload(cp)
        profile = cp.load_profile("h3")
        self.assertEqual(profile["yacht_id"], "h3")
        self.assertEqual(profile["selected_regulations"], [])
        self.assertEqual(profile["vessel_documents"], [])

    def test_enable_regulation(self):
        import importlib
        import services.compliance_profile as cp
        importlib.reload(cp)
        added = cp.enable_regulation("h3", "ISM Code 2018")
        self.assertTrue(added)
        self.assertIn("ISM Code 2018", cp.get_selected_regulations("h3"))

    def test_enable_regulation_idempotent(self):
        import importlib
        import services.compliance_profile as cp
        importlib.reload(cp)
        cp.enable_regulation("h3", "MARPOL Annex VI")
        added_again = cp.enable_regulation("h3", "MARPOL Annex VI")
        self.assertFalse(added_again)
        self.assertEqual(cp.get_selected_regulations("h3").count("MARPOL Annex VI"), 1)

    def test_disable_regulation(self):
        import importlib
        import services.compliance_profile as cp
        importlib.reload(cp)
        cp.enable_regulation("h3", "MARPOL Annex VI")
        removed = cp.disable_regulation("h3", "MARPOL Annex VI")
        self.assertTrue(removed)
        self.assertNotIn("MARPOL Annex VI", cp.get_selected_regulations("h3"))

    def test_disable_missing_regulation(self):
        import importlib
        import services.compliance_profile as cp
        importlib.reload(cp)
        removed = cp.disable_regulation("h3", "Non-existent Regulation")
        self.assertFalse(removed)

    def test_add_vessel_document(self):
        import importlib
        import services.compliance_profile as cp
        importlib.reload(cp)
        cp.add_vessel_document("h3", {"name": "H3 SMS", "type": "sms"})
        docs = cp.list_vessel_documents("h3")
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["name"], "H3 SMS")

    def test_add_vessel_document_replaces_same_name(self):
        import importlib
        import services.compliance_profile as cp
        importlib.reload(cp)
        cp.add_vessel_document("h3", {"name": "H3 SMS", "type": "sms", "path": "old"})
        cp.add_vessel_document("h3", {"name": "H3 SMS", "type": "sms", "path": "new"})
        docs = cp.list_vessel_documents("h3")
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["path"], "new")


class TestComplianceCommandIntents(unittest.TestCase):
    def _cls(self, text):
        from domain.intent import classify_text
        return classify_text(text)

    def test_show_compliance_profile(self):
        self.assertEqual(self._cls("show compliance profile"), "show_compliance_profile")

    def test_compliance_profile_short(self):
        self.assertEqual(self._cls("compliance profile"), "show_compliance_profile")

    def test_show_selected_regulations(self):
        self.assertEqual(self._cls("show selected regulations"), "show_selected_regulations")

    def test_show_vessel_procedures(self):
        self.assertEqual(self._cls("show vessel procedures"), "show_vessel_procedures")

    def test_show_global_regulations(self):
        self.assertEqual(self._cls("show global regulations"), "show_compliance_sources")

    def test_show_loaded_regulations(self):
        self.assertEqual(self._cls("show loaded regulations"), "show_compliance_sources")

    def test_enable_regulation_intent(self):
        self.assertEqual(self._cls("enable MARPOL Annex VI for H3"), "enable_regulation")

    def test_disable_regulation_intent(self):
        self.assertEqual(self._cls("disable ISM Code for H3"), "disable_regulation")

    def test_enable_does_not_go_to_compliance(self):
        self.assertNotEqual(self._cls("enable MARPOL Annex VI for H3"), "compliance_question")

    def test_disable_does_not_go_to_compliance(self):
        self.assertNotEqual(self._cls("disable ISM Code for H3"), "compliance_question")

    def test_nox_trigger_is_compliance_not_equipment(self):
        self.assertEqual(self._cls("what are the NOx limits for our engine"), "compliance_question")


class TestComplianceDocClassifier(unittest.TestCase):
    def test_sms_by_filename(self):
        from services.compliance_ingest import classify_compliance_doc
        self.assertEqual(classify_compliance_doc("some text here", "H3 SMS.pdf"), "yacht_sms")

    def test_safety_management_by_filename(self):
        from services.compliance_ingest import classify_compliance_doc
        self.assertEqual(classify_compliance_doc("", "H3 Safety Management Manual.pdf"), "yacht_sms")

    def test_garbage_plan_by_filename(self):
        from services.compliance_ingest import classify_compliance_doc
        self.assertEqual(
            classify_compliance_doc("", "H3 Garbage Management Plan.pdf"), "yacht_procedure"
        )

    def test_fuel_changeover_by_filename(self):
        from services.compliance_ingest import classify_compliance_doc
        self.assertEqual(
            classify_compliance_doc("", "fuel changeover procedure.pdf"), "yacht_procedure"
        )

    def test_sms_by_text_keywords(self):
        from services.compliance_ingest import classify_compliance_doc
        text = (
            "This safety management system defines master's responsibility "
            "and designated person duties for this vessel."
        )
        self.assertEqual(classify_compliance_doc(text, "unknown.pdf"), "yacht_sms")

    def test_procedure_by_text(self):
        from services.compliance_ingest import classify_compliance_doc
        text = "This garbage management plan describes waste handling procedures onboard."
        self.assertEqual(classify_compliance_doc(text, "unknown.pdf"), "yacht_procedure")

    def test_invoice_returns_none(self):
        from services.compliance_ingest import classify_compliance_doc
        self.assertIsNone(classify_compliance_doc("Invoice for spare parts #12345", "invoice.pdf"))

    def test_empty_text_no_match(self):
        from services.compliance_ingest import classify_compliance_doc
        self.assertIsNone(classify_compliance_doc("", "random_file.pdf"))


class TestRetrievalWithYacht(unittest.TestCase):
    @patch("services.askhelm_retriever.AskHelmComplianceRetriever.search")
    def test_selected_regulations_filter(self, mock_search):
        mock_search.return_value = [
            {"id": "r1", "source": "ISM Code 2018", "score": 0.5, "content": "ISM content"},
            {"id": "r2", "source": "MARPOL Annex VI", "score": 0.4, "content": "MARPOL content"},
            {"id": "r3", "source": "LYC Code", "score": 0.3, "content": "LYC content"},
        ]
        retriever = MagicMock(spec=["search", "search_with_yacht", "_search_yacht_index", "metadata"])
        retriever.search = mock_search
        retriever._search_yacht_index.return_value = []

        from services.askhelm_retriever import AskHelmComplianceRetriever
        results = AskHelmComplianceRetriever.search_with_yacht(
            retriever, "test query", yacht_id="h3",
            selected_regulations=["ISM Code 2018", "MARPOL Annex VI"],
            top_k=5, min_score=0.05,
        )
        sources = [r["source"] for r in results]
        self.assertIn("ISM Code 2018", sources)
        self.assertIn("MARPOL Annex VI", sources)
        self.assertNotIn("LYC Code", sources)

    @patch("services.askhelm_retriever.AskHelmComplianceRetriever.search")
    def test_no_selected_regulations_returns_all(self, mock_search):
        mock_search.return_value = [
            {"id": "r1", "source": "ISM Code 2018", "score": 0.5, "content": "ISM"},
            {"id": "r2", "source": "LYC Code", "score": 0.3, "content": "LYC"},
        ]
        retriever = MagicMock(spec=["search", "search_with_yacht", "_search_yacht_index", "metadata"])
        retriever.search = mock_search
        retriever._search_yacht_index.return_value = []

        from services.askhelm_retriever import AskHelmComplianceRetriever
        results = AskHelmComplianceRetriever.search_with_yacht(
            retriever, "test query", yacht_id="h3",
            selected_regulations=None, top_k=5, min_score=0.05,
        )
        sources = [r["source"] for r in results]
        self.assertIn("ISM Code 2018", sources)
        self.assertIn("LYC Code", sources)

    @patch("services.askhelm_retriever.AskHelmComplianceRetriever.search")
    def test_yacht_chunks_prepended(self, mock_search):
        mock_search.return_value = [
            {"id": "global1", "source": "ISM Code 2018", "score": 0.4, "content": "ISM"},
        ]
        yacht_chunk = {"id": "yacht1", "source": "H3 SMS", "score": 0.6, "content": "SMS"}
        retriever = MagicMock(spec=["search", "search_with_yacht", "_search_yacht_index", "metadata"])
        retriever.search = mock_search
        retriever._search_yacht_index.return_value = [yacht_chunk]

        from services.askhelm_retriever import AskHelmComplianceRetriever
        results = AskHelmComplianceRetriever.search_with_yacht(
            retriever, "test query", yacht_id="h3",
            selected_regulations=None, top_k=5, min_score=0.05,
        )
        self.assertEqual(results[0]["id"], "yacht1")
        self.assertEqual(results[1]["id"], "global1")


class TestComplianceAnswerLength(unittest.TestCase):
    """Compliance answers must be safe for WhatsApp delivery (≤ 1175 chars body)."""

    _LONG_ANSWER = (
        "DECISION:\nMAINTENANCE REQUIREMENTS FOUND\n\n"
        "WHY:\n"
        + ("LYC 23A.15 requires vessel equipment to be checked and tested daily. " * 20)
        + "\n\nSOURCE:\nLarge Yacht Code LYC — Part A, 23A.15\n\n"
        "ACTIONS:\n• Keep daily checks recorded\n• Maintain inspection programme\n• Review SMS every 3 years"
    )

    def test_cap_short_answer_unchanged(self):
        from domain.compliance_engine import _cap_compliance_answer
        short = "DECISION:\nOK\n\nWHY:\nBrief.\n\nSOURCE:\nDoc\n\nACTIONS:\n• Act"
        self.assertEqual(_cap_compliance_answer(short), short)

    def test_cap_long_answer_truncates_to_limit(self):
        from domain.compliance_engine import _cap_compliance_answer
        result = _cap_compliance_answer(self._LONG_ANSWER)
        self.assertLessEqual(len(result), 1175)

    def test_cap_truncates_on_line_boundary(self):
        from domain.compliance_engine import _cap_compliance_answer
        result = _cap_compliance_answer(self._LONG_ANSWER)
        self.assertFalse(result.endswith(" "), "trailing space after truncation")

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_question")
    def test_compliance_query_caps_long_llm_response(self, mock_llm, mock_retriever_getter):
        mock_retriever = MagicMock()
        mock_retriever.search_with_yacht.return_value = [
            {"source_reference": "LYC 23A.15", "content": "Daily checks required.", "score": 0.5}
        ]
        mock_retriever_getter.return_value = mock_retriever
        mock_llm.return_value = self._LONG_ANSWER

        from domain.compliance_engine import answer_compliance_query
        result = answer_compliance_query("what does the Large Yacht Code say about maintenance?")
        self.assertLessEqual(len(result), 1175)

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_question")
    def test_compliance_answer_has_required_sections(self, mock_llm, mock_retriever_getter):
        mock_retriever = MagicMock()
        mock_retriever.search_with_yacht.return_value = [
            {"source_reference": "LYC 23A.15", "content": "Daily checks required.", "score": 0.5}
        ]
        mock_retriever_getter.return_value = mock_retriever
        mock_llm.return_value = (
            "DECISION:\nMAINTENANCE REQUIREMENTS FOUND\n\n"
            "WHY:\nLYC 23A.15 requires daily checks when in use.\n\n"
            "SOURCE:\nLarge Yacht Code LYC — Part A, 23A.15\n\n"
            "ACTIONS:\n• Keep daily checks recorded\n• Review SMS every 3 years"
        )

        from domain.compliance_engine import answer_compliance_query
        result = answer_compliance_query("what does the Large Yacht Code say about maintenance?")
        self.assertIn("DECISION:", result)
        self.assertIn("WHY:", result)
        self.assertIn("SOURCE:", result)
        self.assertIn("ACTIONS:", result)
        self.assertLessEqual(len(result), 1175)

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_followup_question")
    def test_compliance_followup_caps_long_response(self, mock_followup, mock_retriever_getter):
        mock_retriever = MagicMock()
        mock_retriever.search_with_yacht.return_value = [
            {"source_reference": "LYC 23A.15", "content": "Daily checks required.", "score": 0.5}
        ]
        mock_retriever_getter.return_value = mock_retriever
        mock_followup.return_value = self._LONG_ANSWER

        from domain.compliance_engine import answer_compliance_followup
        result = answer_compliance_followup("maintenance requirements")
        self.assertLessEqual(len(result), 1175)

    def test_prompt_max_tokens_is_400(self):
        """LLM token budget for compliance answers must not exceed 400."""
        import inspect
        import services.anthropic_service as svc
        src = inspect.getsource(svc.answer_compliance_question)
        self.assertIn("max_tokens=400", src)


class TestDocumentClassification(unittest.TestCase):
    """MLC FAQ and similar regulatory guidance should classify as regulatory_guidance."""

    def _classify(self, text: str, filename: str):
        from services.compliance_ingest import classify_compliance_doc
        return classify_compliance_doc(text, filename)

    # --- filename-based detection ---

    def test_mlc_filename_classifies_as_regulatory_guidance(self):
        self.assertEqual(self._classify("", "MLC FAQs.pdf"), "regulatory_guidance")

    def test_mlc_lowercase_filename(self):
        self.assertEqual(self._classify("", "mlc_guidance.pdf"), "regulatory_guidance")

    def test_maritime_labour_filename(self):
        self.assertEqual(self._classify("", "maritime labour convention.pdf"), "regulatory_guidance")

    def test_flag_state_filename(self):
        self.assertEqual(self._classify("", "flag state guidance.pdf"), "regulatory_guidance")

    # --- text-based detection ---

    def test_ilo_text_classifies_as_regulatory_guidance(self):
        text = "International Labour Organization Maritime Labour Convention, 2006 FAQ"
        self.assertEqual(self._classify(text, "guidance.pdf"), "regulatory_guidance")

    def test_flag_state_responsibilities_text(self):
        text = "flag state responsibilities under the convention"
        self.assertEqual(self._classify(text, "doc.pdf"), "regulatory_guidance")

    def test_port_state_control_text(self):
        text = "port state control inspection requirements"
        self.assertEqual(self._classify(text, "doc.pdf"), "regulatory_guidance")

    def test_seafarer_rights_text(self):
        text = "seafarer rights and entitlements under MLC 2006"
        self.assertEqual(self._classify(text, "doc.pdf"), "regulatory_guidance")

    def test_mlc_2006_text(self):
        text = "mlc, 2006 applies to all ships of 500 GT or more"
        self.assertEqual(self._classify(text, "doc.pdf"), "regulatory_guidance")

    # --- should NOT classify as regulatory_guidance ---

    def test_equipment_manual_text_not_regulatory(self):
        text = "owner's manual installation guide troubleshooting chapter appendix"
        result = self._classify(text, "watermaker_manual.pdf")
        self.assertNotEqual(result, "regulatory_guidance")

    def test_sms_text_not_regulatory(self):
        text = "safety management system designated person company safety policy master's responsibility"
        result = self._classify(text, "sms.pdf")
        self.assertEqual(result, "yacht_sms")

    # --- intent routing for reclassification ---

    def test_reclassify_add_to_regulations_routing(self):
        self.assertEqual(classify_text("add this to regulations / compliance instead"), "reclassify_as_compliance")

    def test_reclassify_move_to_compliance(self):
        self.assertEqual(classify_text("move this to compliance"), "reclassify_as_compliance")

    def test_reclassify_this_is_not_a_manual(self):
        self.assertEqual(classify_text("this is not a manual"), "reclassify_as_compliance")

    def test_reclassify_save_as_compliance(self):
        self.assertEqual(classify_text("save this as compliance"), "reclassify_as_compliance")

    def test_reclassify_does_not_route_to_compliance_qa(self):
        result = classify_text("add this to compliance")
        self.assertNotEqual(result, "compliance_question")

    # --- regression: compliance Q&A unaffected ---

    def test_compliance_qa_still_works(self):
        self.assertEqual(classify_text("does MARPOL apply to our vessel?"), "compliance_question")

    def test_equipment_manual_intent_not_reclassify(self):
        self.assertNotEqual(classify_text("show manuals"), "reclassify_as_compliance")


class TestDocumentReclassification(unittest.TestCase):
    """After a manual is imported, the user can reclassify it as compliance."""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.tmpdir
        import importlib, storage_paths, domain.manual_store as ms
        importlib.reload(storage_paths)
        importlib.reload(ms)
        # Create a dummy PDF stand-in so file_path exists
        self.pdf_path = os.path.join(self.tmpdir, "MLC_FAQs.pdf")
        with open(self.pdf_path, "wb") as f:
            f.write(b"%PDF-1.4 dummy")

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        import shutil, importlib, storage_paths, domain.manual_store as ms
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        importlib.reload(storage_paths)
        importlib.reload(ms)

    def _save_manual_entry(self, user_id=""):
        import importlib
        import domain.manual_store as ms
        importlib.reload(ms)
        ms.save_manual(user_id, {
            "manufacturer": "ILO",
            "product_name": "MLC FAQs",
            "system": "OWS",
        }, [], self.pdf_path)

    @patch("whatsapp_app.ingest_compliance_pdf")
    @patch("whatsapp_app._reset_compliance_retriever")
    def test_reclassify_removes_manual_entry(self, mock_reset, mock_ingest):
        import importlib, domain.manual_store as ms
        importlib.reload(ms)
        mock_ingest.return_value = 5
        self._save_manual_entry()

        from whatsapp_app import _handle_reclassify_as_compliance
        state = {
            "user_id": "",
            "last_context": {
                "type": "manual_imported",
                "file_path": self.pdf_path,
                "source_name": "MLC FAQs",
            },
        }
        result, new_state = _handle_reclassify_as_compliance(state)

        self.assertIn("DOCUMENT MOVED TO COMPLIANCE LIBRARY", result)
        importlib.reload(ms)
        remaining = ms.get_all_manuals("")
        self.assertEqual(remaining, [])

    @patch("whatsapp_app.ingest_compliance_pdf")
    @patch("whatsapp_app._reset_compliance_retriever")
    def test_reclassify_calls_ingest(self, mock_reset, mock_ingest):
        mock_ingest.return_value = 5

        from whatsapp_app import _handle_reclassify_as_compliance
        state = {
            "user_id": "",
            "last_context": {
                "type": "manual_imported",
                "file_path": self.pdf_path,
                "source_name": "MLC FAQs",
            },
        }
        _handle_reclassify_as_compliance(state)
        mock_ingest.assert_called_once_with(self.pdf_path, "MLC FAQs")

    @patch("whatsapp_app.ingest_compliance_pdf")
    @patch("whatsapp_app._reset_compliance_retriever")
    def test_reclassify_updates_last_context(self, mock_reset, mock_ingest):
        mock_ingest.return_value = 5

        from whatsapp_app import _handle_reclassify_as_compliance
        state = {
            "user_id": "",
            "last_context": {
                "type": "manual_imported",
                "file_path": self.pdf_path,
                "source_name": "MLC FAQs",
            },
        }
        _, new_state = _handle_reclassify_as_compliance(state)
        self.assertEqual(new_state["last_context"]["type"], "compliance_doc_imported")

    def test_reclassify_without_manual_context_returns_error(self):
        from whatsapp_app import _handle_reclassify_as_compliance
        state = {"user_id": "", "last_context": {"type": "market_check"}}
        result, _ = _handle_reclassify_as_compliance(state)
        self.assertIn("RECLASSIFICATION NOT POSSIBLE", result)

    # --- regression: equipment manual still imports correctly ---

    def test_equipment_manual_not_classified_as_regulatory_guidance(self):
        from services.compliance_ingest import classify_compliance_doc
        manual_text = (
            "Owner's Manual for Newport 400 Watermaker. "
            "Table of contents. Safety instructions. Troubleshooting. "
            "Installation procedure. Warranty information."
        )
        result = classify_compliance_doc(manual_text, "newport400_manual.pdf")
        self.assertNotEqual(result, "regulatory_guidance")


class TestManualResetRouting(unittest.TestCase):
    """reset manuals must not trigger comparison reset."""

    def test_reset_manuals_routes_to_reset_manuals(self):
        self.assertEqual(classify_text("reset manuals"), "reset_manuals")

    def test_clear_manuals_routes_to_reset_manuals(self):
        self.assertEqual(classify_text("clear manuals"), "reset_manuals")

    def test_delete_manuals_routes_to_reset_manuals(self):
        self.assertEqual(classify_text("delete manuals"), "reset_manuals")

    def test_new_comparison_still_routes_to_new_session(self):
        self.assertEqual(classify_text("new comparison"), "new_session")

    def test_reset_equipment_still_routes_to_reset_equipment(self):
        self.assertEqual(classify_text("reset equipment"), "reset_equipment")

    def test_reset_manuals_not_new_session(self):
        self.assertNotEqual(classify_text("reset manuals"), "new_session")

    def test_remove_manual_routes_to_reclassify(self):
        self.assertEqual(classify_text("remove this manual"), "reclassify_as_compliance")

    def test_compliance_qa_unaffected(self):
        self.assertEqual(classify_text("does MARPOL apply?"), "compliance_question")


class TestManualComplianceIndicator(unittest.TestCase):
    """is_compliance_record detects ILO/MLC records in the manual store."""

    def _make_entry(self, manufacturer="", product_name="", document_type=""):
        return {
            "manufacturer": manufacturer,
            "product_name": product_name,
            "document_type": document_type,
        }

    def test_ilo_manufacturer_is_compliance(self):
        from domain.manual_store import is_compliance_record
        m = self._make_entry(manufacturer="International Labour Organization")
        self.assertTrue(is_compliance_record(m))

    def test_mlc_product_name_is_compliance(self):
        from domain.manual_store import is_compliance_record
        m = self._make_entry(product_name="Maritime Labour Convention, 2006 (MLC, 2006)")
        self.assertTrue(is_compliance_record(m))

    def test_flag_state_product_name_is_compliance(self):
        from domain.manual_store import is_compliance_record
        m = self._make_entry(product_name="Flag State Responsibilities Guide")
        self.assertTrue(is_compliance_record(m))

    def test_equipment_manual_not_compliance(self):
        from domain.manual_store import is_compliance_record
        m = self._make_entry(manufacturer="Spectra", product_name="Newport 400", document_type="Owner's Manual")
        self.assertFalse(is_compliance_record(m))

    def test_mtu_manual_not_compliance(self):
        from domain.manual_store import is_compliance_record
        m = self._make_entry(manufacturer="MTU", product_name="16V 2000 M86", document_type="Service Manual")
        self.assertFalse(is_compliance_record(m))


class TestManualReset(unittest.TestCase):
    """clear_all_manuals removes all entries and _handle_reset_manuals responds correctly."""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.tmpdir
        import importlib, storage_paths, domain.manual_store as ms
        importlib.reload(storage_paths)
        importlib.reload(ms)

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        import shutil, importlib, storage_paths, domain.manual_store as ms
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        importlib.reload(storage_paths)
        importlib.reload(ms)

    def _save(self, manufacturer="MTU", product="Engine Manual"):
        import importlib, domain.manual_store as ms
        importlib.reload(ms)
        ms.save_manual("", {"manufacturer": manufacturer, "product_name": product}, [], "dummy.pdf")

    def test_clear_all_manuals_removes_entries(self):
        import importlib, domain.manual_store as ms
        importlib.reload(ms)
        self._save()
        self._save(manufacturer="Spectra", product="Watermaker Manual")
        self.assertEqual(len(ms.get_all_manuals("")), 2)
        ms.clear_all_manuals("")
        self.assertEqual(ms.get_all_manuals(""), [])

    def test_clear_all_returns_count(self):
        import importlib, domain.manual_store as ms
        importlib.reload(ms)
        self._save()
        self._save()
        count = ms.clear_all_manuals("")
        self.assertEqual(count, 2)

    def test_handle_reset_manuals_response(self):
        import importlib, domain.manual_store as ms
        importlib.reload(ms)
        self._save()
        from whatsapp_app import _handle_reset_manuals
        result, _ = _handle_reset_manuals({"user_id": ""})
        self.assertIn("MANUAL LIBRARY CLEARED", result)

    def test_handle_reset_manuals_empty_library(self):
        import importlib, domain.manual_store as ms
        importlib.reload(ms)
        from whatsapp_app import _handle_reset_manuals
        result, _ = _handle_reset_manuals({"user_id": ""})
        self.assertIn("ALREADY EMPTY", result)

    def test_handle_reset_manuals_clears_store(self):
        import importlib, domain.manual_store as ms
        importlib.reload(ms)
        self._save()
        from whatsapp_app import _handle_reset_manuals
        _handle_reset_manuals({"user_id": ""})
        importlib.reload(ms)
        self.assertEqual(ms.get_all_manuals(""), [])


class TestReclassifyExistingManual(unittest.TestCase):
    """Reclassification works for existing persisted manuals (Path 2)."""

    _MLC_ENTRY = {
        "manufacturer": "International Labour Organization",
        "product_name": "Maritime Labour Convention, 2006 (MLC, 2006)",
        "document_type": "Frequently Asked Questions (FAQ)",
        "system": "OWS",
    }
    _EQUIPMENT_ENTRY = {
        "manufacturer": "Spectra",
        "product_name": "Newport 400",
        "document_type": "Owner's Manual",
        "system": "Watermaker",
    }

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.tmpdir
        self.mlc_pdf = os.path.join(self.tmpdir, "mlc_faqs.pdf")
        self.equip_pdf = os.path.join(self.tmpdir, "newport400.pdf")
        for p in (self.mlc_pdf, self.equip_pdf):
            with open(p, "wb") as f:
                f.write(b"%PDF-1.4 dummy")
        import importlib, storage_paths, domain.manual_store as ms
        importlib.reload(storage_paths)
        importlib.reload(ms)

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        import shutil, importlib, storage_paths, domain.manual_store as ms
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        importlib.reload(storage_paths)
        importlib.reload(ms)

    def _save_manual(self, entry, pdf_path=None):
        import importlib, domain.manual_store as ms
        importlib.reload(ms)
        ms.save_manual("", entry, [], pdf_path or self.mlc_pdf)

    def _reclassify(self, state=None):
        from whatsapp_app import _handle_reclassify_as_compliance
        if state is None:
            state = {"user_id": "", "last_context": {"type": "market_check"}}
        return _handle_reclassify_as_compliance(state)

    @patch("whatsapp_app.ingest_compliance_pdf")
    @patch("whatsapp_app._reset_compliance_retriever")
    def test_path2_removes_compliance_record(self, mock_reset, mock_ingest):
        mock_ingest.return_value = 3
        import importlib, domain.manual_store as ms
        importlib.reload(ms)
        self._save_manual(self._MLC_ENTRY)
        result, _ = self._reclassify()
        self.assertIn("DOCUMENT MOVED TO COMPLIANCE LIBRARY", result)
        importlib.reload(ms)
        self.assertEqual(ms.get_all_manuals(""), [])

    @patch("whatsapp_app.ingest_compliance_pdf")
    @patch("whatsapp_app._reset_compliance_retriever")
    def test_path2_calls_ingest(self, mock_reset, mock_ingest):
        mock_ingest.return_value = 3
        import importlib, domain.manual_store as ms
        importlib.reload(ms)
        self._save_manual(self._MLC_ENTRY)
        self._reclassify()
        mock_ingest.assert_called_once()

    @patch("whatsapp_app.ingest_compliance_pdf")
    @patch("whatsapp_app._reset_compliance_retriever")
    def test_path2_preserves_equipment_manuals(self, mock_reset, mock_ingest):
        """Real equipment manual is not reclassified."""
        mock_ingest.return_value = 0
        import importlib, domain.manual_store as ms
        importlib.reload(ms)
        self._save_manual(self._MLC_ENTRY, self.mlc_pdf)
        self._save_manual(self._EQUIPMENT_ENTRY, self.equip_pdf)
        self._reclassify()
        importlib.reload(ms)
        remaining = ms.get_all_manuals("")
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["manufacturer"], "Spectra")

    def test_path2_no_compliance_records_returns_error(self):
        import importlib, domain.manual_store as ms
        importlib.reload(ms)
        self._save_manual(self._EQUIPMENT_ENTRY)
        result, _ = self._reclassify()
        self.assertIn("RECLASSIFICATION NOT POSSIBLE", result)

    def test_no_manuals_at_all_returns_error(self):
        result, _ = self._reclassify()
        self.assertIn("RECLASSIFICATION NOT POSSIBLE", result)


if __name__ == "__main__":
    unittest.main()
