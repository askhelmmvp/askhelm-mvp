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
        result = answer_compliance_query("ballast water treatment schedule")

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

    def test_prompt_max_tokens_is_concise(self):
        """LLM token budget for compliance answers — concise WhatsApp format (350 max)."""
        import inspect
        import services.anthropic_service as svc
        src = inspect.getsource(svc.answer_compliance_question)
        self.assertIn("max_tokens=350", src)


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

    # --- "how many" compliance override (ASK-11 follow-up) ---

    def test_fire_door_battery_power_routes_to_compliance(self):
        result = classify_text("How many times do fire doors need to operate on battery power?")
        self.assertEqual(result, "compliance_question")

    def test_fire_door_query_not_stock(self):
        result = classify_text("How many times do fire doors need to operate on battery power?")
        self.assertNotEqual(result, "stock_query")

    def test_how_many_solas_routes_to_compliance(self):
        result = classify_text("how many times does solas require this test?")
        self.assertEqual(result, "compliance_question")

    def test_stock_pn_query_unaffected_by_guard(self):
        result = classify_text("how many 03GCPMS005 do we have on board?")
        self.assertEqual(result, "stock_query")


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


class TestSolasClassification(unittest.TestCase):
    """SOLAS and IMO convention documents must route to compliance, not manuals."""

    def test_solas_filename_classified_as_regulatory_guidance(self):
        from services.compliance_ingest import classify_compliance_doc
        result = classify_compliance_doc("", "SOLAS-Consolidated-Edition-2018.docx.pdf")
        self.assertEqual(result, "regulatory_guidance")

    def test_marpol_filename_classified_as_regulatory_guidance(self):
        from services.compliance_ingest import classify_compliance_doc
        result = classify_compliance_doc("", "MARPOL-2019-Consolidated.pdf")
        self.assertEqual(result, "regulatory_guidance")

    def test_stcw_filename_classified_as_regulatory_guidance(self):
        from services.compliance_ingest import classify_compliance_doc
        result = classify_compliance_doc("", "STCW-Code-2011.pdf")
        self.assertEqual(result, "regulatory_guidance")

    def test_solas_text_phrase_classified_as_regulatory_guidance(self):
        from services.compliance_ingest import classify_compliance_doc
        text = "International Convention for the Safety of Life at Sea, 1974, as amended."
        result = classify_compliance_doc(text, "doc.pdf")
        self.assertEqual(result, "regulatory_guidance")

    def test_contracting_governments_phrase_classified_as_regulatory_guidance(self):
        from services.compliance_ingest import classify_compliance_doc
        text = "Contracting Governments shall ensure that all ships are surveyed."
        result = classify_compliance_doc(text, "doc.pdf")
        self.assertEqual(result, "regulatory_guidance")

    def test_real_manual_text_not_classified_as_regulatory(self):
        from services.compliance_ingest import classify_compliance_doc
        text = (
            "MTU Series 4000 Marine Diesel Engine. "
            "This manual covers installation, operation and service of the engine. "
            "Serial number plate is located on the engine block. "
            "Torque specifications are listed in Appendix A."
        )
        result = classify_compliance_doc(text, "mtu_4000_manual.pdf")
        self.assertIsNone(result)


class TestSolasManualComplianceIndicator(unittest.TestCase):
    """is_compliance_record must detect SOLAS/IMO entries persisted in manual library."""

    def test_solas_product_name_is_compliance(self):
        from domain.manual_store import is_compliance_record
        m = {"product_name": "SOLAS 2018 Consolidated Edition", "document_type": "Regulatory Convention"}
        self.assertTrue(is_compliance_record(m))

    def test_marpol_product_name_is_compliance(self):
        from domain.manual_store import is_compliance_record
        m = {"manufacturer": "IMO", "product_name": "MARPOL Consolidated 2019"}
        self.assertTrue(is_compliance_record(m))

    def test_regulatory_convention_doc_type_is_compliance(self):
        from domain.manual_store import is_compliance_record
        m = {"product_name": "Safety at Sea", "document_type": "Regulatory Convention"}
        self.assertTrue(is_compliance_record(m))

    def test_international_convention_doc_type_is_compliance(self):
        from domain.manual_store import is_compliance_record
        m = {"product_name": "STCW Code 2011", "document_type": "International Convention"}
        self.assertTrue(is_compliance_record(m))

    def test_equipment_manual_not_compliance(self):
        from domain.manual_store import is_compliance_record
        m = {"manufacturer": "Grundfos", "product_name": "CM Pump Series", "document_type": "Technical Manual"}
        self.assertFalse(is_compliance_record(m))


class TestNormaliseComplianceSourceName(unittest.TestCase):
    """normalise_compliance_source_name must map upload IDs to human-readable titles."""

    def _normalise(self, name, content=""):
        from services.compliance_ingest import normalise_compliance_source_name
        return normalise_compliance_source_name(name, content)

    def test_upload_id_with_solas_content_returns_solas_title(self):
        content = (
            "International Convention for the Safety of Life at Sea, 1974, as amended. "
            "Contracting Governments shall ensure compliance."
        )
        result = self._normalise("upload 8880396640424531070", content)
        self.assertEqual(result, "SOLAS Consolidated Edition 2018")

    def test_known_upload_id_with_solas_signal(self):
        result = self._normalise(
            "upload 8880396640424531070",
            "SOLAS Chapter II-1 — Construction",
        )
        self.assertEqual(result, "SOLAS Consolidated Edition 2018")

    def test_upload_id_without_known_content_returned_unchanged(self):
        result = self._normalise("upload 1234567890", "Some unknown regulatory text.")
        self.assertEqual(result, "upload 1234567890")

    def test_non_upload_name_returned_unchanged(self):
        result = self._normalise("ISM Code", "anything")
        self.assertEqual(result, "ISM Code")

    def test_marpol_name_returned_unchanged(self):
        result = self._normalise("MARPOL Annex I", "marpol content")
        self.assertEqual(result, "MARPOL Annex I")

    def test_empty_name_returned_unchanged(self):
        result = self._normalise("", "content")
        self.assertEqual(result, "")

    def test_list_sources_shows_solas_not_upload_id(self):
        """list_sources must normalise upload-ID source names in the returned list."""
        from services.compliance_ingest import list_sources
        from unittest.mock import patch

        fake_chunks = [
            {
                "source": "upload 8880396640424531070",
                "document": "upload 8880396640424531070.pdf",
                "content": (
                    "International Convention for the Safety of Life at Sea, "
                    "contracting governments shall ensure all ships are surveyed."
                ),
                "source_reference": "SOLAS — test",
            },
            {
                "source": "ISM Code",
                "document": "ism_code.pdf",
                "content": "ISM Code Chapter 1",
                "source_reference": "ISM — test",
            },
        ]
        with patch("services.compliance_ingest.load_chunks", return_value=fake_chunks):
            sources = list_sources()

        names = [s["source"] for s in sources]
        self.assertIn("SOLAS Consolidated Edition 2018", names)
        self.assertNotIn("upload 8880396640424531070", names)
        self.assertIn("ISM Code", names)


class TestShowRegulationsResponseBuilder(unittest.TestCase):
    """
    The _handle_show_compliance_sources response builder must normalise
    upload-ID source names at the display point, regardless of what
    list_compliance_sources returns.
    """

    _SOLAS_CONTENT = (
        "International Convention for the Safety of Life at Sea, 1974, as amended. "
        "Contracting Governments shall ensure compliance with the provisions."
    )

    def _call_handler(self, mock_sources):
        from unittest.mock import patch
        from whatsapp_app import _handle_show_compliance_sources
        state = {"user_id": "test_user"}
        with patch("whatsapp_app.list_compliance_sources", return_value=mock_sources):
            response, _ = _handle_show_compliance_sources(state)
        return response

    def test_upload_id_with_solas_content_sample_displays_as_solas(self):
        sources = [
            {
                "source": "upload 8880396640424531070",
                "chunks": 50,
                "content_sample": self._SOLAS_CONTENT,
            }
        ]
        response = self._call_handler(sources)
        self.assertIn("SOLAS Consolidated Edition 2018", response)
        self.assertNotIn("upload 8880396640424531070", response)

    def test_normal_source_names_pass_through_unchanged(self):
        sources = [
            {"source": "ISM Code", "chunks": 10, "content_sample": ""},
            {"source": "MARPOL Annex I", "chunks": 8, "content_sample": ""},
        ]
        response = self._call_handler(sources)
        self.assertIn("ISM Code", response)
        self.assertIn("MARPOL Annex I", response)

    def test_mixed_sources_normalises_only_upload_ids(self):
        sources = [
            {"source": "ISM Code", "chunks": 10, "content_sample": ""},
            {
                "source": "upload 8880396640424531070",
                "chunks": 50,
                "content_sample": self._SOLAS_CONTENT,
            },
            {"source": "MARPOL Annex V", "chunks": 6, "content_sample": ""},
        ]
        response = self._call_handler(sources)
        self.assertIn("SOLAS Consolidated Edition 2018", response)
        self.assertNotIn("upload 8880396640424531070", response)
        self.assertIn("ISM Code", response)
        self.assertIn("MARPOL Annex V", response)

    def test_response_contains_regulations_found_decision(self):
        sources = [
            {"source": "ISM Code", "chunks": 10, "content_sample": ""},
        ]
        response = self._call_handler(sources)
        self.assertIn("REGULATIONS FOUND", response)
        self.assertIn("REGULATIONS:", response)


class TestNamedRegulationDetection(unittest.TestCase):
    """_detect_named_regulation must match specific regulation names."""

    def _detect(self, text):
        from domain.compliance_engine import _detect_named_regulation
        return _detect_named_regulation(text)

    def test_solas_detected(self):
        self.assertEqual(self._detect("what is in SOLAS?"), "SOLAS")

    def test_solas_case_insensitive(self):
        self.assertEqual(self._detect("what does solas say about bulkheads"), "SOLAS")

    def test_marpol_annex_vi_detected(self):
        self.assertEqual(
            self._detect("what are the NOx regulations in MARPOL Annex VI?"),
            "MARPOL Annex VI"
        )

    def test_marpol_annex_i_detected(self):
        self.assertEqual(
            self._detect("what does MARPOL Annex I say about bilge?"),
            "MARPOL Annex I"
        )

    def test_ism_code_detected(self):
        self.assertEqual(self._detect("what is ISM code chapter 10?"), "ISM Code")

    def test_marpol_alone_not_detected(self):
        # bare "marpol" with no annex or topic-inference term must not match
        self.assertIsNone(self._detect("marpol discharge requirements"))

    def test_tier_iii_detected_as_marpol_annex_vi(self):
        self.assertEqual(self._detect("does tier iii apply in norwegian sea"), "MARPOL Annex VI")

    def test_obscure_query_not_detected(self):
        self.assertIsNone(self._detect("something completely obscure"))

    def test_marpol_annex_v_not_confused_with_vi(self):
        # "annex v" without "i" suffix must match Annex V, not Annex VI
        result = self._detect("what does MARPOL Annex V say about garbage?")
        self.assertEqual(result, "MARPOL Annex V")


class TestGeneralGuidanceFallback(unittest.TestCase):
    """When retrieval fails for a named regulation, use general guidance, not NOT_COVERED."""

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_general_guidance")
    @patch("services.compliance_ingest.list_sources")
    def test_solas_triggers_general_guidance_when_no_chunks(
        self, mock_sources, mock_guidance, mock_retriever_getter
    ):
        mock_retriever = MagicMock()
        mock_retriever.search_with_yacht.return_value = []
        mock_retriever_getter.return_value = mock_retriever
        mock_sources.return_value = [
            {"source": "SOLAS Consolidated Edition 2018", "chunks": 50, "content_sample": ""}
        ]
        mock_guidance.return_value = (
            "DECISION:\nGENERAL GUIDANCE — SOLAS — SOURCE LOADED, SECTION NOT FOUND\n\n"
            "WHY:\nSOLAS covers international ship safety requirements.\n\n"
            "GENERAL GUIDANCE:\n• Fire protection in SOLAS Chapter II-2\n\n"
            "SOURCE:\nSOLAS is loaded, but the exact section was not found.\n\n"
            "ACTIONS:\n• Ask about a specific SOLAS chapter"
        )

        from domain.compliance_engine import answer_compliance_query
        result = answer_compliance_query("what is in SOLAS?")

        self.assertIn("DECISION:", result)
        self.assertIn("GENERAL GUIDANCE", result)
        mock_guidance.assert_called_once()

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_general_guidance")
    @patch("services.compliance_ingest.list_sources")
    def test_marpol_annex_vi_triggers_general_guidance(
        self, mock_sources, mock_guidance, mock_retriever_getter
    ):
        mock_retriever = MagicMock()
        mock_retriever.search_with_yacht.return_value = []
        mock_retriever_getter.return_value = mock_retriever
        mock_sources.return_value = [
            {"source": "MARPOL Annex VI", "chunks": 20, "content_sample": ""}
        ]
        mock_guidance.return_value = (
            "DECISION:\nGENERAL GUIDANCE — MARPOL ANNEX VI — SOURCE LOADED, SECTION NOT FOUND\n\n"
            "WHY:\nMARPOL Annex VI controls NOx and SOx air emissions from ships.\n\n"
            "GENERAL GUIDANCE:\n• NOx limits depend on engine installation date\n\n"
            "SOURCE:\nMARPOL Annex VI is loaded, but the exact section was not found.\n\n"
            "ACTIONS:\n• Check MARPOL Annex VI Regulation 13 for NOx"
        )

        from domain.compliance_engine import answer_compliance_query
        result = answer_compliance_query("what are the NOx regulations in MARPOL Annex VI?")

        self.assertIn("DECISION:", result)
        self.assertIn("GENERAL GUIDANCE", result)
        mock_guidance.assert_called_once()

    @patch("domain.compliance_engine._get_retriever")
    def test_obscure_query_still_returns_not_covered(self, mock_retriever_getter):
        """Queries without a known regulation name still use NOT_COVERED_FALLBACK."""
        mock_retriever = MagicMock()
        mock_retriever.search_with_yacht.return_value = []
        mock_retriever_getter.return_value = mock_retriever

        from domain.compliance_engine import answer_compliance_query
        result = answer_compliance_query("something completely obscure and unmatched")

        self.assertIn("Not explicitly covered", result)

    @patch("domain.compliance_engine._get_retriever")
    def test_bare_marpol_returns_not_covered(self, mock_retriever_getter):
        """bare 'marpol' with no annex or topic-inference term must not trigger general guidance."""
        mock_retriever_getter.side_effect = RuntimeError("index corrupt")

        from domain.compliance_engine import answer_compliance_query
        result = answer_compliance_query("marpol discharge requirements")

        self.assertIn("DECISION:", result)
        self.assertIn("Not explicitly covered", result)

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_question")
    def test_strong_retrieval_wins_over_general_guidance(
        self, mock_llm, mock_retriever_getter
    ):
        """When retrieval scores >= 0.15 and LLM returns a good answer, use it — no general guidance."""
        mock_retriever = MagicMock()
        mock_retriever.search_with_yacht.return_value = [
            {"source_reference": "ISM Code Ch 10", "content": "Maintenance required.", "score": 0.5}
        ]
        mock_retriever_getter.return_value = mock_retriever
        mock_llm.return_value = (
            "DECISION: Yes.\nWHY: ISM Code Ch 10 requires maintenance.\n"
            "SOURCE: ISM Code 2018 — Chapter 10\nACTIONS: • Maintain equipment"
        )

        from domain.compliance_engine import answer_compliance_query
        result = answer_compliance_query("what is ISM code chapter 10?")

        self.assertNotIn("GENERAL GUIDANCE", result)
        self.assertIn("DECISION:", result)
        mock_llm.assert_called_once()

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_general_guidance")
    @patch("services.compliance_ingest.list_sources")
    def test_general_guidance_uses_not_loaded_when_source_absent(
        self, mock_sources, mock_guidance, mock_retriever_getter
    ):
        """When SOLAS is named but not loaded, general guidance shows 'not loaded' disclaimer."""
        mock_retriever = MagicMock()
        mock_retriever.search_with_yacht.return_value = []
        mock_retriever_getter.return_value = mock_retriever
        mock_sources.return_value = [
            {"source": "ISM Code 2018", "chunks": 12, "content_sample": ""}
        ]  # SOLAS is NOT in loaded sources
        mock_guidance.return_value = (
            "DECISION:\nGENERAL GUIDANCE — SOLAS — SOURCE NOT LOADED\n\n"
            "WHY:\nSOLAS sets international ship safety standards.\n\n"
            "GENERAL GUIDANCE:\n• SOLAS covers fire, lifesaving, navigation\n\n"
            "SOURCE:\nSOLAS is not currently loaded.\n\n"
            "ACTIONS:\n• Upload SOLAS to get verified answers"
        )

        from domain.compliance_engine import answer_compliance_query
        result = answer_compliance_query("what does SOLAS say about fire dampers?")

        self.assertIn("GENERAL GUIDANCE", result)
        # Verify is_loaded=False was passed
        call_args = mock_guidance.call_args
        self.assertEqual(call_args[0][2], False)  # is_loaded


class TestAnswerComplianceGeneralGuidance(unittest.TestCase):
    """answer_compliance_general_guidance must produce correctly labeled general guidance."""

    @patch("services.anthropic_service.client")
    def test_loaded_source_with_strong_hit_label(self, mock_client):
        """had_strong_hit=True → system prompt says section was searched but not found."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=(
            "DECISION:\nGENERAL GUIDANCE — SOLAS — SOURCE LOADED, SECTION NOT FOUND\n\n"
            "WHY:\nSOLAS covers fire protection requirements.\n\n"
            "GENERAL GUIDANCE:\n• A-60 divisions are fire-rated structural divisions\n\n"
            "SOURCE:\nSOLAS is loaded, but the exact section was not found.\n\n"
            "ACTIONS:\n• Check SOLAS Chapter II-2"
        ))]
        mock_client.messages.create.return_value = mock_response

        from services.anthropic_service import answer_compliance_general_guidance
        result = answer_compliance_general_guidance(
            "what does SOLAS say about A60 bulkheads?", "SOLAS",
            is_loaded=True, had_strong_hit=True,
        )

        self.assertIn("GENERAL GUIDANCE", result)
        call_kwargs = mock_client.messages.create.call_args[1]
        system_text = call_kwargs["system"][0]["text"]
        self.assertIn("could not locate the exact section", system_text)
        self.assertIn("SOURCE LOADED, SECTION NOT FOUND", system_text)

    @patch("services.anthropic_service.client")
    def test_loaded_source_no_strong_hit_label_is_general_guidance(self, mock_client):
        """had_strong_hit=False (default) → no 'SECTION NOT FOUND' label — source is loaded but not searched."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=(
            "DECISION:\nGENERAL GUIDANCE — MARPOL ANNEX VI\n\n"
            "WHY:\nGlobal sulphur cap is 0.5% m/m.\n\n"
            "GENERAL GUIDANCE:\n• Verify bunker certificates match zone requirements\n\n"
            "SOURCE:\nMARPOL Annex VI is loaded. Confirm the specific section before relying on it.\n\n"
            "ACTIONS:\n• Check bunker delivery notes"
        ))]
        mock_client.messages.create.return_value = mock_response

        from services.anthropic_service import answer_compliance_general_guidance
        result = answer_compliance_general_guidance(
            "what fuel sulphur limits apply?", "MARPOL Annex VI",
            is_loaded=True, had_strong_hit=False,
        )

        self.assertIn("GENERAL GUIDANCE", result)
        call_kwargs = mock_client.messages.create.call_args[1]
        system_text = call_kwargs["system"][0]["text"]
        self.assertNotIn("SOURCE LOADED, SECTION NOT FOUND", system_text)
        self.assertIn("MARPOL ANNEX VI", system_text)

    @patch("services.anthropic_service.client")
    def test_not_loaded_label_in_system_prompt(self, mock_client):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=(
            "DECISION:\nGENERAL GUIDANCE — SOLAS — SOURCE NOT LOADED\n\n"
            "WHY:\nSOLAS sets international safety standards.\n\n"
            "GENERAL GUIDANCE:\n• Fire protection rules in SOLAS II-2\n\n"
            "SOURCE:\nSOLAS is not currently loaded.\n\n"
            "ACTIONS:\n• Upload SOLAS PDF"
        ))]
        mock_client.messages.create.return_value = mock_response

        from services.anthropic_service import answer_compliance_general_guidance
        result = answer_compliance_general_guidance(
            "what does SOLAS say about A60 bulkheads?", "SOLAS", is_loaded=False
        )

        self.assertIn("GENERAL GUIDANCE", result)
        call_kwargs = mock_client.messages.create.call_args[1]
        system_text = call_kwargs["system"][0]["text"]
        self.assertIn("not currently loaded", system_text)
        self.assertIn("SOURCE NOT LOADED", system_text)

    @patch("services.anthropic_service.client")
    def test_uses_sonnet_model(self, mock_client):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="DECISION:\nGENERAL GUIDANCE\n\nWHY:\nTest.")]
        mock_client.messages.create.return_value = mock_response

        from services.anthropic_service import answer_compliance_general_guidance
        answer_compliance_general_guidance("test question", "SOLAS", is_loaded=False)

        call_kwargs = mock_client.messages.create.call_args[1]
        self.assertEqual(call_kwargs["model"], "claude-sonnet-4-6")

    @patch("services.anthropic_service.client")
    def test_max_tokens_adequate_for_guidance(self, mock_client):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="DECISION:\nGENERAL GUIDANCE\n\nWHY:\nTest.")]
        mock_client.messages.create.return_value = mock_response

        from services.anthropic_service import answer_compliance_general_guidance
        answer_compliance_general_guidance("test question", "MARPOL Annex VI", is_loaded=True)

        call_kwargs = mock_client.messages.create.call_args[1]
        # General guidance needs more tokens than strict source answer (400)
        self.assertGreaterEqual(call_kwargs["max_tokens"], 400)


class TestTopicInference(unittest.TestCase):
    """_detect_named_regulation must infer regulation from well-known topic terms."""

    def _detect(self, text):
        from domain.compliance_engine import _detect_named_regulation
        return _detect_named_regulation(text)

    def test_tier_iii_infers_marpol_annex_vi(self):
        self.assertEqual(self._detect("when does Tier III apply?"), "MARPOL Annex VI")

    def test_tier_3_infers_marpol_annex_vi(self):
        self.assertEqual(self._detect("do we need Tier 3 engines?"), "MARPOL Annex VI")

    def test_eiapp_infers_marpol_annex_vi(self):
        self.assertEqual(self._detect("what is an EIAPP certificate?"), "MARPOL Annex VI")

    def test_eca_requirements_infer_marpol_annex_vi(self):
        self.assertEqual(self._detect("what are ECA requirements?"), "MARPOL Annex VI")

    def test_fuel_sulphur_infers_marpol_annex_vi(self):
        self.assertEqual(self._detect("what fuel sulphur limits apply?"), "MARPOL Annex VI")

    def test_sulphur_limit_infers_marpol_annex_vi(self):
        self.assertEqual(self._detect("what is the sulphur limit in the Baltic?"), "MARPOL Annex VI")

    def test_emission_control_area_infers_marpol_annex_vi(self):
        self.assertEqual(self._detect("what is an emission control area?"), "MARPOL Annex VI")

    def test_ism_say_infers_ism_code(self):
        self.assertEqual(self._detect("what does ISM say about maintenance?"), "ISM Code")

    def test_ism_maintenance_infers_ism_code(self):
        self.assertEqual(self._detect("ISM maintenance requirements"), "ISM Code")

    def test_yacht_code_infers_large_yacht_code(self):
        self.assertEqual(self._detect("what does the yacht code say about fire pumps?"), "Large Yacht Code")

    def test_lyc_alone_infers_large_yacht_code(self):
        self.assertEqual(self._detect("does LYC require a fire pump test?"), "Large Yacht Code")

    def test_bare_marpol_no_inference(self):
        self.assertIsNone(self._detect("marpol discharge requirements"))

    def test_unrelated_query_no_inference(self):
        self.assertIsNone(self._detect("what time does the captain need to log the position?"))

    def test_bare_nox_infers_marpol_annex_vi(self):
        self.assertEqual(self._detect("what are the NOx regulations?"), "MARPOL Annex VI")

    def test_explicit_marpol_annex_vi_still_detected(self):
        self.assertEqual(self._detect("MARPOL Annex VI NOx limits"), "MARPOL Annex VI")

    def test_ism_code_explicit_still_detected(self):
        self.assertEqual(self._detect("what is ISM code chapter 10?"), "ISM Code")


class TestPlaybookGuard(unittest.TestCase):
    """Regulation source inquiries must skip the operational playbook."""

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_general_guidance")
    @patch("services.compliance_ingest.list_sources")
    def test_yacht_code_fire_pump_skips_playbook(
        self, mock_sources, mock_guidance, mock_retriever_getter
    ):
        """'what does the yacht code say about fire pumps?' must NOT return overdue playbook response."""
        mock_retriever = MagicMock()
        mock_retriever.search_with_yacht.return_value = []
        mock_retriever_getter.return_value = mock_retriever
        mock_sources.return_value = []
        mock_guidance.return_value = (
            "DECISION:\nGENERAL GUIDANCE — LARGE YACHT CODE — SOURCE NOT LOADED\n\n"
            "WHY:\nThe Large Yacht Code requires fire pumps to meet pressure standards.\n\n"
            "GENERAL GUIDANCE:\n• Fire pump capacity is defined in the LYC\n\n"
            "SOURCE:\nLarge Yacht Code is not currently loaded.\n\n"
            "ACTIONS:\n• Upload the Large Yacht Code for verified answers"
        )

        from domain.compliance_engine import answer_compliance_query
        result = answer_compliance_query("what does the yacht code say about fire pumps?")

        self.assertIn("GENERAL GUIDANCE", result)
        self.assertNotIn("ACTION REQUIRED", result)
        self.assertNotIn("DO NOT LEAVE OVERDUE", result)
        mock_guidance.assert_called_once()

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_general_guidance")
    @patch("services.compliance_ingest.list_sources")
    def test_ism_say_maintenance_skips_playbook(
        self, mock_sources, mock_guidance, mock_retriever_getter
    ):
        """'what does ISM say about maintenance?' must route to guidance, not playbook."""
        mock_retriever = MagicMock()
        mock_retriever.search_with_yacht.return_value = []
        mock_retriever_getter.return_value = mock_retriever
        mock_sources.return_value = []
        mock_guidance.return_value = (
            "DECISION:\nGENERAL GUIDANCE — ISM CODE — SOURCE NOT LOADED\n\n"
            "WHY:\nISM Code Chapter 10 covers maintenance and testing.\n\n"
            "GENERAL GUIDANCE:\n• Planned maintenance system required\n\n"
            "SOURCE:\nISM Code is not currently loaded.\n\n"
            "ACTIONS:\n• Upload ISM Code for verified answers"
        )

        from domain.compliance_engine import answer_compliance_query
        result = answer_compliance_query("what does ISM say about maintenance?")

        self.assertIn("GENERAL GUIDANCE", result)
        mock_guidance.assert_called_once()

    def test_source_inquiry_re_matches_what_does_say(self):
        from domain.compliance_engine import _REGULATION_SOURCE_INQUIRY_RE
        self.assertTrue(_REGULATION_SOURCE_INQUIRY_RE.search(
            "what does the yacht code say about fire pumps?"
        ))

    def test_source_inquiry_re_matches_what_does_require(self):
        from domain.compliance_engine import _REGULATION_SOURCE_INQUIRY_RE
        self.assertTrue(_REGULATION_SOURCE_INQUIRY_RE.search(
            "what does ISM require for maintenance?"
        ))

    def test_source_inquiry_re_does_not_match_operational_query(self):
        from domain.compliance_engine import _REGULATION_SOURCE_INQUIRY_RE
        self.assertFalse(_REGULATION_SOURCE_INQUIRY_RE.search(
            "is the fire pump test overdue?"
        ))

    def test_source_inquiry_re_matches_what_does_cover(self):
        from domain.compliance_engine import _REGULATION_SOURCE_INQUIRY_RE
        self.assertTrue(_REGULATION_SOURCE_INQUIRY_RE.search(
            "what does SOLAS cover for fire detection?"
        ))


class TestTopicInferenceEndToEnd(unittest.TestCase):
    """Topic-inferred regulations must reach general guidance when retrieval fails."""

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_general_guidance")
    @patch("services.compliance_ingest.list_sources")
    def test_tier_iii_reaches_general_guidance(
        self, mock_sources, mock_guidance, mock_retriever_getter
    ):
        mock_retriever = MagicMock()
        mock_retriever.search_with_yacht.return_value = []
        mock_retriever_getter.return_value = mock_retriever
        mock_sources.return_value = []
        mock_guidance.return_value = (
            "DECISION:\nGENERAL GUIDANCE — MARPOL ANNEX VI — SOURCE NOT LOADED\n\n"
            "WHY:\nTier III NOx standards apply in designated NECAs.\n\n"
            "GENERAL GUIDANCE:\n• Tier III applies in Norwegian ECA and North American ECA\n\n"
            "SOURCE:\nMARPOL Annex VI is not currently loaded.\n\n"
            "ACTIONS:\n• Upload MARPOL Annex VI for verified answers"
        )

        from domain.compliance_engine import answer_compliance_query
        result = answer_compliance_query("when does Tier III apply?")

        self.assertIn("GENERAL GUIDANCE", result)
        self.assertNotIn("Not explicitly covered", result)
        mock_guidance.assert_called_once()
        self.assertEqual(mock_guidance.call_args[0][1], "MARPOL Annex VI")

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_general_guidance")
    @patch("services.compliance_ingest.list_sources")
    def test_eiapp_reaches_general_guidance(
        self, mock_sources, mock_guidance, mock_retriever_getter
    ):
        mock_retriever = MagicMock()
        mock_retriever.search_with_yacht.return_value = []
        mock_retriever_getter.return_value = mock_retriever
        mock_sources.return_value = []
        mock_guidance.return_value = (
            "DECISION:\nGENERAL GUIDANCE — MARPOL ANNEX VI — SOURCE NOT LOADED\n\n"
            "WHY:\nEIAPP certifies engine air pollution prevention compliance.\n\n"
            "GENERAL GUIDANCE:\n• All diesel engines above 130 kW require an EIAPP certificate\n\n"
            "SOURCE:\nMARPOL Annex VI is not currently loaded.\n\n"
            "ACTIONS:\n• Verify with flag state for exact requirements"
        )

        from domain.compliance_engine import answer_compliance_query
        result = answer_compliance_query("what is an EIAPP certificate?")

        self.assertIn("GENERAL GUIDANCE", result)
        mock_guidance.assert_called_once()

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_general_guidance")
    @patch("services.compliance_ingest.list_sources")
    def test_eca_requirements_reaches_guidance(
        self, mock_sources, mock_guidance, mock_retriever_getter
    ):
        mock_retriever = MagicMock()
        mock_retriever.search_with_yacht.return_value = []
        mock_retriever_getter.return_value = mock_retriever
        mock_sources.return_value = []
        mock_guidance.return_value = (
            "DECISION:\nGENERAL GUIDANCE — MARPOL ANNEX VI — SOURCE NOT LOADED\n\n"
            "WHY:\nECA requirements limit sulphur to 0.1% m/m.\n\n"
            "GENERAL GUIDANCE:\n• Use low-sulphur fuel or scrubber in ECAs\n\n"
            "SOURCE:\nMARPOL Annex VI is not currently loaded.\n\n"
            "ACTIONS:\n• Check current ECA boundaries before entering"
        )

        from domain.compliance_engine import answer_compliance_query
        result = answer_compliance_query("what are ECA requirements?")

        self.assertIn("GENERAL GUIDANCE", result)
        mock_guidance.assert_called_once()

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_general_guidance")
    @patch("services.compliance_ingest.list_sources")
    def test_fuel_sulphur_limits_reaches_guidance(
        self, mock_sources, mock_guidance, mock_retriever_getter
    ):
        mock_retriever = MagicMock()
        mock_retriever.search_with_yacht.return_value = []
        mock_retriever_getter.return_value = mock_retriever
        mock_sources.return_value = []
        mock_guidance.return_value = (
            "DECISION:\nGENERAL GUIDANCE — MARPOL ANNEX VI — SOURCE NOT LOADED\n\n"
            "WHY:\nGlobal sulphur cap is 0.5% m/m; ECA limit is 0.1% m/m.\n\n"
            "GENERAL GUIDANCE:\n• Verify bunker certificates match zone requirements\n\n"
            "SOURCE:\nMARPOL Annex VI is not currently loaded.\n\n"
            "ACTIONS:\n• Upload MARPOL Annex VI for verified answers"
        )

        from domain.compliance_engine import answer_compliance_query
        result = answer_compliance_query("what fuel sulphur limits apply?")

        self.assertIn("GENERAL GUIDANCE", result)
        mock_guidance.assert_called_once()


class TestRoleContextIsolation(unittest.TestCase):
    """Role context must not contaminate document retrieval queries (ASK-42)."""

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_question")
    def test_role_context_does_not_reach_retriever(self, mock_llm, mock_retriever_getter):
        """retriever.search_with_yacht must receive the raw question, not a role-prefixed string."""
        mock_retriever = MagicMock()
        mock_retriever.search_with_yacht.return_value = [
            {"source_reference": "MARPOL Annex VI Reg 13", "content": "NOx Tier III limits.", "score": 0.5}
        ]
        mock_retriever_getter.return_value = mock_retriever
        mock_llm.return_value = (
            "DECISION: Yes.\nWHY: MARPOL VI Reg 13.\nSOURCE: MARPOL VI Reg 13\nACTIONS: • Check engine"
        )

        from domain.compliance_engine import answer_compliance_query
        answer_compliance_query(
            "what are the NOx regulations?",
            yacht_id="h3",
            role_context="USER ROLE: Captain. Emphasise risk, compliance exposure, decision confidence.",
        )

        retriever_query = mock_retriever.search_with_yacht.call_args[0][0]
        self.assertEqual(retriever_query, "what are the NOx regulations?")
        self.assertNotIn("USER ROLE", retriever_query)
        self.assertNotIn("Captain", retriever_query)

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_question")
    def test_role_context_reaches_llm_question(self, mock_llm, mock_retriever_getter):
        """Role context must reach the LLM question but not the retriever query."""
        mock_retriever = MagicMock()
        mock_retriever.search_with_yacht.return_value = [
            {"source_reference": "MARPOL Annex VI Reg 13", "content": "NOx limits.", "score": 0.5}
        ]
        mock_retriever_getter.return_value = mock_retriever
        mock_llm.return_value = (
            "DECISION: Yes.\nWHY: MARPOL VI.\nSOURCE: Reg 13\nACTIONS: • Act"
        )

        role_ctx = "USER ROLE: Captain. Emphasise risk."

        from domain.compliance_engine import answer_compliance_query
        answer_compliance_query(
            "what are the NOx regulations?",
            yacht_id="h3",
            role_context=role_ctx,
        )

        # Retriever: raw question.
        retriever_query = mock_retriever.search_with_yacht.call_args[0][0]
        self.assertNotIn("USER ROLE", retriever_query)

        # LLM: role-enhanced question.
        llm_question = mock_llm.call_args[0][0]
        self.assertIn("USER ROLE", llm_question)
        self.assertIn("what are the NOx regulations?", llm_question)

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_question")
    def test_no_role_context_uses_raw_question_for_both(self, mock_llm, mock_retriever_getter):
        """When no role_context is given, retriever and LLM both receive raw question."""
        mock_retriever = MagicMock()
        mock_retriever.search_with_yacht.return_value = [
            {"source_reference": "MARPOL VI Reg 13", "content": "NOx.", "score": 0.5}
        ]
        mock_retriever_getter.return_value = mock_retriever
        mock_llm.return_value = (
            "DECISION: Yes.\nWHY: Reg 13.\nSOURCE: Reg 13\nACTIONS: • Act"
        )

        from domain.compliance_engine import answer_compliance_query
        answer_compliance_query("what are the NOx regulations?")

        retriever_query = mock_retriever.search_with_yacht.call_args[0][0]
        llm_question = mock_llm.call_args[0][0]
        self.assertEqual(retriever_query, "what are the NOx regulations?")
        self.assertEqual(llm_question, "what are the NOx regulations?")

    @patch("whatsapp_app.answer_compliance_query")
    def test_whatsapp_handler_passes_raw_question_and_role_separately(self, mock_acq):
        """WhatsApp handler must NOT pre-concatenate role into the query string."""
        mock_acq.return_value = (
            "DECISION: Yes.\nWHY: Test.\nSOURCE: Test\nACTIONS: • Act"
        )

        state = {
            "sessions": [], "documents": [], "active_session_id": None, "user_id": "",
            "role": "captain",  # role stored the way domain/user_role.py sets it
        }

        from whatsapp_app import _handle_text_message
        _handle_text_message("what are the NOx regulations?", state)

        mock_acq.assert_called_once()
        call_args = mock_acq.call_args
        called_question = call_args[0][0]
        self.assertEqual(called_question, "what are the NOx regulations?")
        self.assertNotIn("USER ROLE", called_question)
        # role_context kwarg must carry the captain hint
        called_role_ctx = call_args[1].get("role_context", "")
        self.assertIn("Captain", called_role_ctx)


class TestDeterministicRouting(unittest.TestCase):
    """_get_expansion_queries must put topic-specific direct queries first."""

    def _expansions(self, reg_name, question):
        from domain.compliance_engine import _get_expansion_queries
        return _get_expansion_queries(reg_name, question)

    def test_nox_question_gets_nox_direct_query_first(self):
        queries = self._expansions("MARPOL Annex VI", "what are the NOx regulations?")
        self.assertGreater(len(queries), 0)
        self.assertIn("NOx", queries[0])
        self.assertIn("Tier III", queries[0])

    def test_tier_iii_question_gets_nox_direct_query_first(self):
        queries = self._expansions("MARPOL Annex VI", "when does Tier III apply?")
        self.assertIn("NOx", queries[0])
        self.assertIn("NECA", queries[0])

    def test_eiapp_question_gets_nox_direct_query_first(self):
        queries = self._expansions("MARPOL Annex VI", "what is an EIAPP certificate?")
        self.assertIn("EIAPP", queries[0])

    def test_fuel_sulphur_question_gets_sulphur_direct_query_first(self):
        queries = self._expansions("MARPOL Annex VI", "what fuel sulphur limits apply?")
        self.assertGreater(len(queries), 0)
        self.assertIn("sulphur", queries[0].lower())
        self.assertIn("ECA", queries[0])

    def test_sox_question_gets_sulphur_direct_query_first(self):
        queries = self._expansions("MARPOL Annex VI", "what are SOx limits?")
        self.assertIn("SOx", queries[0])

    def test_a60_question_gets_a60_direct_query_first(self):
        queries = self._expansions("SOLAS", "what does SOLAS say about A60 bulkheads?")
        self.assertGreater(len(queries), 0)
        self.assertIn("A-60", queries[0])
        self.assertIn("structural fire protection", queries[0])

    def test_fire_damper_question_gets_fire_damper_query_first(self):
        queries = self._expansions("SOLAS", "what are the regulations on fire dampers in SOLAS?")
        self.assertGreater(len(queries), 0)
        self.assertIn("fire damper", queries[0].lower())

    def test_generic_marpol_vi_question_uses_general_expansions(self):
        queries = self._expansions("MARPOL Annex VI", "what is MARPOL Annex VI about?")
        from domain.compliance_engine import _REGULATION_EXPANSIONS
        # No direct query matches — must use general expansion list
        self.assertIn(queries[0], _REGULATION_EXPANSIONS["MARPOL Annex VI"])

    def test_direct_match_skips_general_expansions(self):
        """When a direct-topic query matches, general expansions must be excluded so broad
        unrelated SOLAS/MARPOL searches are avoided for well-known topics."""
        queries = self._expansions("MARPOL Annex VI", "when does Tier III apply?")
        from domain.compliance_engine import _REGULATION_EXPANSIONS
        # Direct query matches — general list must NOT be appended
        for g in _REGULATION_EXPANSIONS["MARPOL Annex VI"]:
            self.assertNotIn(g, queries, f"General expansion {g!r} should not appear when direct query matches")

    def test_no_duplicate_queries(self):
        """Deduplication must remove any repeated entries."""
        queries = self._expansions("MARPOL Annex VI", "MARPOL Annex VI NOx Tier III question")
        self.assertEqual(len(queries), len(set(queries)))

    def test_wrong_regulation_direct_query_not_included(self):
        """A60 direct query must not appear in MARPOL Annex VI expansions."""
        queries = self._expansions("MARPOL Annex VI", "what does SOLAS say about A60 bulkheads?")
        for q in queries:
            self.assertNotIn("A-60", q)

    def test_american_spelling_sulfur_matches_direct_query(self):
        queries = self._expansions("MARPOL Annex VI", "what are the fuel sulfur limits?")
        self.assertGreater(len(queries), 0)
        self.assertIn("sulphur", queries[0].lower())


class TestExpansionConfidence(unittest.TestCase):
    """Expansion loop collects best TF-IDF hit then makes ONE LLM call."""

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_question")
    def test_all_tfidf_expansions_tried_with_single_llm_call(self, mock_llm, mock_retriever_getter):
        """All expansion TF-IDF queries are run to find the best score; only ONE LLM call is made."""
        mock_retriever = MagicMock()

        retrieval_call_count = [0]

        def search_side_effect(query, **kwargs):
            retrieval_call_count[0] += 1
            if retrieval_call_count[0] == 1:
                return []  # initial retrieval: weak
            # All expansion queries return strong chunks
            return [{"source_reference": "MARPOL VI Reg 13", "content": "NOx limits.", "score": 0.5}]

        mock_retriever.search_with_yacht.side_effect = search_side_effect
        mock_retriever_getter.return_value = mock_retriever
        mock_llm.return_value = (
            "DECISION: NOx Tier III applies in NECAs.\nWHY: MARPOL VI Reg 13.\n"
            "SOURCE: MARPOL VI Reg 13\nACTIONS: • Check engine build date"
        )

        from domain.compliance_engine import answer_compliance_query
        result = answer_compliance_query("when does Tier III apply?")

        self.assertIn("DECISION:", result)
        self.assertNotIn("Not explicitly covered", result)
        # All TF-IDF expansion calls fired (initial + N expansions)
        self.assertGreater(retrieval_call_count[0], 1)
        # Exactly ONE LLM call regardless of how many expansions scored above threshold
        self.assertEqual(mock_llm.call_count, 1)

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_question")
    def test_best_scoring_expansion_used_for_llm_call(self, mock_llm, mock_retriever_getter):
        """When multiple general expansions score above threshold, the LLM receives the highest-scoring chunks.
        Uses a non-direct-routed MARPOL VI question so the full general expansion list is tried."""
        mock_retriever = MagicMock()

        call_count = [0]
        high_score_chunks = [{"source_reference": "MARPOL VI Reg 3", "content": "fuel oil record.", "score": 0.7}]
        low_score_chunks = [{"source_reference": "MARPOL VI Reg 2", "content": "definitions.", "score": 0.3}]

        def search_side_effect(query, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return []  # initial retrieval: weak
            if call_count[0] == 2:
                return low_score_chunks  # first expansion: lower score
            if call_count[0] == 3:
                return high_score_chunks  # second expansion: higher score wins
            return []  # remaining expansions: nothing

        mock_retriever.search_with_yacht.side_effect = search_side_effect
        mock_retriever_getter.return_value = mock_retriever
        mock_llm.return_value = (
            "DECISION: Fuel oil record book required.\nWHY: MARPOL VI Reg 3.\n"
            "SOURCE: MARPOL VI Reg 3\nACTIONS: • Maintain ORB"
        )

        from domain.compliance_engine import answer_compliance_query
        # No direct-query match for "fuel oil record books" → general expansion list (6 queries)
        answer_compliance_query("what does MARPOL Annex VI say about fuel oil record books?")

        # LLM must have been called exactly once with the highest-scoring chunks
        self.assertEqual(mock_llm.call_count, 1)
        called_chunks = mock_llm.call_args[0][1]
        self.assertEqual(called_chunks, high_score_chunks)

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_question")
    def test_role_contaminated_query_no_longer_breaks_retrieval(self, mock_llm, mock_retriever_getter):
        """Simulates the pre-fix bug: role-contaminated query scores poorly but engine recovers."""
        mock_retriever = MagicMock()

        def search_side_effect(query, **kwargs):
            # Raw question scores well; role-contaminated would score poorly
            if "USER ROLE" in query:
                return []  # simulate old broken behaviour
            return [
                {"source_reference": "MARPOL VI Reg 13", "content": "NOx Tier III.", "score": 0.5}
            ]

        mock_retriever.search_with_yacht.side_effect = search_side_effect
        mock_retriever_getter.return_value = mock_retriever
        mock_llm.return_value = (
            "DECISION: Yes.\nWHY: MARPOL VI Reg 13.\nSOURCE: Reg 13\nACTIONS: • Act"
        )

        from domain.compliance_engine import answer_compliance_query
        # Even when role_context is provided, retrieval uses raw question
        result = answer_compliance_query(
            "what are the NOx regulations?",
            role_context="USER ROLE: Captain. Emphasise risk.",
        )

        self.assertIn("DECISION:", result)
        self.assertNotIn("Not explicitly covered", result)


class TestFocusedSolasExpansion(unittest.TestCase):
    """A60 and fire-damper questions must not trigger broad unrelated SOLAS searches."""

    def _get_queries(self, question):
        from domain.compliance_engine import _get_expansion_queries
        return _get_expansion_queries("SOLAS", question)

    def test_a60_query_has_no_lifesaving_search(self):
        queries = self._get_queries("what does SOLAS say about A60 bulkheads?")
        bad_terms = ["lifesaving", "liferaft", "lifeboat", "navigation", "radio"]
        for term in bad_terms:
            for q in queries:
                self.assertNotIn(term, q.lower(),
                    f"A60 expansion should not include {term!r} but got: {q!r}")

    def test_a60_query_has_no_navigation_search(self):
        queries = self._get_queries("does SOLAS require A-60 structural fire protection?")
        for q in queries:
            self.assertNotIn("navigation", q.lower())
            self.assertNotIn("radio", q.lower())

    def test_fire_damper_query_has_no_lifesaving_search(self):
        queries = self._get_queries("what are the regulations on fire dampers in SOLAS?")
        bad_terms = ["lifesaving", "liferaft", "lifeboat", "navigation", "radio"]
        for term in bad_terms:
            for q in queries:
                self.assertNotIn(term, q.lower(),
                    f"Fire damper expansion should not include {term!r} but got: {q!r}")

    def test_a60_query_stays_focused_on_fire_protection(self):
        queries = self._get_queries("what does SOLAS say about A60 bulkheads?")
        self.assertTrue(
            any("A-60" in q or "structural fire protection" in q for q in queries),
            "A60 query must route to structural fire protection — got: " + str(queries),
        )

    def test_fire_damper_query_stays_focused_on_ventilation(self):
        queries = self._get_queries("fire dampers in SOLAS")
        self.assertTrue(
            any("damper" in q.lower() or "ventilation" in q.lower() for q in queries),
            "Fire damper query must route to ventilation/damper topic — got: " + str(queries),
        )

    def test_unrecognised_solas_topic_uses_general_expansions(self):
        """When no direct query matches, fall back to the full general list (includes lifesaving)."""
        queries = self._get_queries("what does SOLAS say about liferafts?")
        self.assertTrue(
            any("lifesaving" in q for q in queries),
            "Liferaft question should use general expansions including lifesaving",
        )


class TestOneAnthropicCallMaximum(unittest.TestCase):
    """Routed compliance questions must use at most one Anthropic call in the expansion phase."""

    def _run_compliance(self, question, retriever_mock, llm_mock):
        from domain.compliance_engine import answer_compliance_query
        return answer_compliance_query(question)

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_question")
    def _assert_one_llm_call(self, question, mock_llm, mock_retriever_getter):
        mock_retriever = MagicMock()
        mock_retriever.search_with_yacht.side_effect = lambda q, **kw: (
            [] if kw.get("_call_number", 0) == 0
            else [{"source_reference": "source", "content": "text", "score": 0.5}]
        )
        call_n = [0]

        def search_side(q, **kw):
            call_n[0] += 1
            if call_n[0] == 1:
                return []
            return [{"source_reference": "src", "content": "content", "score": 0.5}]

        mock_retriever.search_with_yacht.side_effect = search_side
        mock_retriever_getter.return_value = mock_retriever
        mock_llm.return_value = (
            "DECISION: Answer.\nWHY: Reason.\nSOURCE: Source\nACTIONS: • Act"
        )

        from domain.compliance_engine import answer_compliance_query
        answer_compliance_query(question)
        self.assertEqual(mock_llm.call_count, 1,
            f"Expected 1 LLM call for {question!r}, got {mock_llm.call_count}")

    def test_fuel_sulphur_one_llm_call(self):
        self._assert_one_llm_call("what fuel sulphur limits apply?")

    def test_nox_regulations_one_llm_call(self):
        self._assert_one_llm_call("what are the NOx regulations?")

    def test_tier_iii_one_llm_call(self):
        self._assert_one_llm_call("when does Tier III apply?")

    def test_eiapp_one_llm_call(self):
        self._assert_one_llm_call("what is an EIAPP certificate?")

    def test_a60_one_llm_call(self):
        self._assert_one_llm_call("what does SOLAS say about A60 bulkheads?")

    def test_fire_dampers_one_llm_call(self):
        self._assert_one_llm_call("what are the regulations on fire dampers in SOLAS?")


class TestComplianceCache(unittest.TestCase):
    """Repeated identical questions must return cached answers without calling the retriever again."""

    def setUp(self):
        # Clear the cache before each test
        from domain.compliance_engine import _compliance_cache
        _compliance_cache.clear()

    def tearDown(self):
        from domain.compliance_engine import _compliance_cache
        _compliance_cache.clear()

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_question")
    def test_second_identical_call_hits_cache(self, mock_llm, mock_retriever_getter):
        mock_retriever = MagicMock()
        mock_retriever.search_with_yacht.return_value = [
            {"source_reference": "MARPOL VI Reg 13", "content": "NOx.", "score": 0.5}
        ]
        mock_retriever_getter.return_value = mock_retriever
        mock_llm.return_value = (
            "DECISION: Tier III applies in NECAs.\nWHY: MARPOL VI Reg 13.\n"
            "SOURCE: MARPOL VI Reg 13\nACTIONS: • Verify engine date"
        )

        from domain.compliance_engine import answer_compliance_query
        result1 = answer_compliance_query("when does Tier III apply?", yacht_id="test_yacht")
        result2 = answer_compliance_query("when does Tier III apply?", yacht_id="test_yacht")

        self.assertEqual(result1, result2)
        # Retriever should only be called on the first request; second is from cache
        first_call_count = mock_retriever.search_with_yacht.call_count
        self.assertGreater(first_call_count, 0)
        # LLM called once; second result comes from cache
        self.assertEqual(mock_llm.call_count, 1)

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_question")
    def test_different_questions_not_cached_together(self, mock_llm, mock_retriever_getter):
        mock_retriever = MagicMock()
        mock_retriever.search_with_yacht.return_value = [
            {"source_reference": "src", "content": "text", "score": 0.5}
        ]
        mock_retriever_getter.return_value = mock_retriever
        mock_llm.side_effect = [
            "DECISION: A.\nWHY: A.\nSOURCE: A\nACTIONS: • A",
            "DECISION: B.\nWHY: B.\nSOURCE: B\nACTIONS: • B",
        ]

        from domain.compliance_engine import answer_compliance_query
        r1 = answer_compliance_query("when does Tier III apply?", yacht_id="test_yacht")
        r2 = answer_compliance_query("what fuel sulphur limits apply?", yacht_id="test_yacht")

        self.assertNotEqual(r1, r2)
        self.assertEqual(mock_llm.call_count, 2)

    def test_reset_retriever_clears_cache(self):
        from domain import compliance_engine
        compliance_engine._compliance_cache["testkey"] = ("answer", 9999999999)
        compliance_engine.reset_retriever()
        self.assertEqual(len(compliance_engine._compliance_cache), 0)

    def test_make_cache_key_normalises_whitespace(self):
        from domain.compliance_engine import _make_cache_key
        k1 = _make_cache_key("when  does Tier III apply?", "h3", "", [])
        k2 = _make_cache_key("when does  Tier III apply?", "h3", "", [])
        self.assertEqual(k1, k2)

    def test_make_cache_key_differs_for_different_yacht(self):
        from domain.compliance_engine import _make_cache_key
        k1 = _make_cache_key("what are NOx limits?", "h3", "", [])
        k2 = _make_cache_key("what are NOx limits?", "yacht2", "", [])
        self.assertNotEqual(k1, k2)


class TestSolasSourceSelection(unittest.TestCase):
    """SOLAS must be included in retrieval when user explicitly asks about it."""

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_general_guidance")
    @patch("services.compliance_ingest.list_sources")
    @patch("services.compliance_profile.get_selected_regulations")
    def test_solas_added_to_retrieval_when_not_in_profile(
        self, mock_get_selected, mock_sources, mock_guidance, mock_retriever_getter
    ):
        """When yacht profile has no SOLAS but user asks about SOLAS, SOLAS must be added."""
        mock_get_selected.return_value = ["Large Yacht Code LYC", "ISM code"]
        mock_retriever = MagicMock()
        mock_retriever.search_with_yacht.return_value = []
        mock_retriever_getter.return_value = mock_retriever
        mock_sources.return_value = []
        mock_guidance.return_value = "DECISION:\nGENERAL GUIDANCE — SOLAS — SOURCE NOT LOADED\n\nWHY:\nSOLAS."

        from domain.compliance_engine import answer_compliance_query
        answer_compliance_query("does SOLAS require A-60 bulkheads?")

        # All retrieval calls must include SOLAS in selected_regulations
        for call in mock_retriever.search_with_yacht.call_args_list:
            selected = call[1].get("selected_regulations") or []
            self.assertTrue(
                any("SOLAS" in s for s in selected),
                f"SOLAS missing from selected_regulations in call: {call}",
            )

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_general_guidance")
    @patch("services.compliance_ingest.list_sources")
    @patch("services.compliance_profile.get_selected_regulations")
    def test_marpol_added_to_retrieval_when_not_in_profile(
        self, mock_get_selected, mock_sources, mock_guidance, mock_retriever_getter
    ):
        """When yacht profile has no MARPOL but user asks about ECA, MARPOL Annex VI is added."""
        mock_get_selected.return_value = ["ISM code"]
        mock_retriever = MagicMock()
        mock_retriever.search_with_yacht.return_value = []
        mock_retriever_getter.return_value = mock_retriever
        mock_sources.return_value = []
        mock_guidance.return_value = "DECISION:\nGENERAL GUIDANCE — MARPOL ANNEX VI — SOURCE NOT LOADED\n\nWHY:\nECA."

        from domain.compliance_engine import answer_compliance_query
        answer_compliance_query("what are ECA requirements?")

        for call in mock_retriever.search_with_yacht.call_args_list:
            selected = call[1].get("selected_regulations") or []
            self.assertTrue(
                any("MARPOL" in s for s in selected),
                f"MARPOL missing from selected_regulations in call: {call}",
            )

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_question")
    @patch("services.compliance_profile.get_selected_regulations")
    def test_regulation_already_in_profile_not_duplicated(
        self, mock_get_selected, mock_llm, mock_retriever_getter
    ):
        """If the regulation is already in the yacht profile it must not be appended twice."""
        mock_get_selected.return_value = ["ISM code", "SOLAS"]
        mock_retriever = MagicMock()
        mock_retriever.search_with_yacht.return_value = [
            {"source_reference": "SOLAS Ch II-2", "content": "A-60 divisions.", "score": 0.5}
        ]
        mock_retriever_getter.return_value = mock_retriever
        mock_llm.return_value = (
            "DECISION: Yes, A-60 required.\nWHY: SOLAS Ch II-2.\n"
            "SOURCE: SOLAS Ch II-2\nACTIONS: • Verify rating"
        )

        from domain.compliance_engine import answer_compliance_query
        answer_compliance_query("does SOLAS require A-60 bulkheads?")

        first_call = mock_retriever.search_with_yacht.call_args_list[0]
        selected = first_call[1].get("selected_regulations") or []
        self.assertEqual(selected.count("SOLAS"), 1)


class TestConfidenceLabels(unittest.TestCase):
    """General guidance labels must accurately reflect whether strong retrieval hits were found."""

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_question")
    @patch("services.anthropic_service.answer_compliance_general_guidance")
    @patch("services.compliance_ingest.list_sources")
    @patch("services.compliance_profile.get_selected_regulations")
    def test_had_strong_hit_true_when_expansion_score_above_threshold(
        self, mock_get_selected, mock_sources, mock_guidance, mock_llm, mock_retriever_getter
    ):
        """When expansion score ≥ threshold but LLM returns NOT_COVERED, had_strong_hit=True."""
        mock_get_selected.return_value = []
        call_count = [0]

        def search_side_effect(query, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return []  # initial retrieval: empty
            return [{"source_reference": "SOLAS Ch II-2", "content": "A-60.", "score": 0.4}]

        mock_retriever = MagicMock()
        mock_retriever.search_with_yacht.side_effect = search_side_effect
        mock_retriever_getter.return_value = mock_retriever
        mock_sources.return_value = [{"source": "SOLAS"}]
        mock_llm.return_value = (
            "DECISION: Not explicitly covered in the loaded documents.\n"
            "WHY: Not in excerpts.\nSOURCE: No matching loaded source\n"
            "ACTIONS: • Refer to regulation"
        )
        mock_guidance.return_value = "DECISION:\nGENERAL GUIDANCE — SOLAS — SOURCE LOADED, SECTION NOT FOUND"

        from domain.compliance_engine import answer_compliance_query
        answer_compliance_query("does SOLAS require A-60 bulkheads?")

        self.assertTrue(mock_guidance.called)
        _, kwargs = mock_guidance.call_args
        self.assertTrue(kwargs.get("had_strong_hit"), "had_strong_hit must be True when best_exp_score ≥ threshold")

    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_general_guidance")
    @patch("services.compliance_ingest.list_sources")
    @patch("services.compliance_profile.get_selected_regulations")
    def test_had_strong_hit_false_when_no_expansion_scored(
        self, mock_get_selected, mock_sources, mock_guidance, mock_retriever_getter
    ):
        """When no expansion scored above threshold, had_strong_hit=False."""
        mock_get_selected.return_value = []
        mock_retriever = MagicMock()
        mock_retriever.search_with_yacht.return_value = []  # all retrievals empty
        mock_retriever_getter.return_value = mock_retriever
        mock_sources.return_value = [{"source": "MARPOL Annex VI"}]
        mock_guidance.return_value = "DECISION:\nGENERAL GUIDANCE — MARPOL ANNEX VI"

        from domain.compliance_engine import answer_compliance_query
        answer_compliance_query("what fuel sulphur limits apply?")

        self.assertTrue(mock_guidance.called)
        _, kwargs = mock_guidance.call_args
        self.assertFalse(kwargs.get("had_strong_hit"), "had_strong_hit must be False when no expansion scored above threshold")


class TestSulphurLocationSource(unittest.TestCase):
    """ASK-47: deterministic sulphur/ECA location answers must carry a non-empty SOURCE.

    The LLM is instructed to copy SOURCE from the excerpt header but sometimes leaves
    it blank; the engine must backfill it from the retrieved chunk's source_reference."""

    # An answer with an EMPTY SOURCE field, mimicking the live WhatsApp regression.
    EMPTY_SOURCE_ANSWER = (
        "DECISION:\n"
        "Sulphur limits apply globally and in designated Emission Control Areas (ECAs).\n\n"
        "WHY:\n"
        "Since 1 January 2020, a global cap of 0.50% m/m applies worldwide; in ECAs a "
        "stricter 0.10% m/m limit applies.\n\n"
        "SOURCE:\n\n"
        "ACTIONS:\n"
        "• Confirm bunkers meet the applicable limit before entering an ECA"
    )

    def _source_value(self, answer):
        """Return the text of the SOURCE field (between 'SOURCE:' and the next section)."""
        import re
        m = re.search(
            r'(?ims)^SOURCE:[ \t]*(.*?)(?=^(?:ACTIONS|GENERAL GUIDANCE|DECISION|WHY)\b|\Z)',
            answer,
        )
        return m.group(1).strip() if m else ""

    def _run(self, question, source_reference, llm_answer=None):
        with patch("domain.compliance_engine._get_retriever") as mock_getter, \
             patch("services.anthropic_service.answer_compliance_question") as mock_llm, \
             patch("services.compliance_profile.get_selected_regulations", return_value=[]):
            mock_retriever = MagicMock()
            _chunks = [{
                "source_reference": source_reference,
                "content": "Sulphur cap 0.50% m/m global; 0.10% m/m in ECAs.",
                "score": 0.5,
            }]
            mock_retriever.search_with_yacht.return_value = _chunks
            mock_getter.return_value = mock_retriever
            mock_llm.return_value = llm_answer or self.EMPTY_SOURCE_ANSWER
            from domain.compliance_engine import answer_compliance_query
            return answer_compliance_query(question)

    def test_sulphur_location_source_backfilled(self):
        result = self._run(
            "where do sulphur limits apply?",
            "MARPOL Annex VI — Regulation 14, Sulphur Emissions",
        )
        self.assertIn("SOURCE:", result)
        source = self._source_value(result)
        self.assertTrue(source, "SOURCE field must not be empty")
        self.assertIn("MARPOL Annex VI", source)
        self.assertIn("Regulation 14", source)

    def test_seca_source_regression(self):
        result = self._run(
            "where are the SECAs?",
            "MARPOL Annex VI — Regulation 14, Sulphur Emissions",
        )
        source = self._source_value(result)
        self.assertIn("MARPOL Annex VI", source)
        self.assertIn("Regulation 14", source)

    def test_010_limit_source_regression(self):
        result = self._run(
            "where does the 0.10% sulphur limit apply?",
            "MARPOL Annex VI — Regulation 14, Sulphur Emissions",
        )
        source = self._source_value(result)
        self.assertIn("MARPOL Annex VI", source)
        self.assertIn("Regulation 14", source)

    def test_050_limit_source_regression(self):
        result = self._run(
            "where does the 0.50% sulphur limit apply?",
            "MARPOL Annex VI — Regulation 14, Sulphur Emissions",
        )
        source = self._source_value(result)
        self.assertIn("MARPOL Annex VI", source)
        self.assertIn("Regulation 14", source)

    def test_neca_routes_to_regulation_13(self):
        # NECA location questions must reach compliance and carry the NOx (Reg 13) source.
        self.assertEqual(classify_text("where are the NECAs?"), COMPLIANCE)
        result = self._run(
            "where are the NECAs?",
            "MARPOL Annex VI — Regulation 13, NOx Emissions",
        )
        source = self._source_value(result)
        self.assertIn("MARPOL Annex VI", source)
        self.assertIn("Regulation 13", source)

    def test_inventory_location_still_routes_to_stock(self):
        # Inventory lookups must NOT be hijacked by the compliance routing change.
        self.assertEqual(classify_text("where is AIK111571?"), "stock_query")

    def test_populated_source_left_untouched(self):
        populated = self.EMPTY_SOURCE_ANSWER.replace(
            "SOURCE:\n\n", "SOURCE:\nExisting Source Ref\n\n"
        )
        result = self._run(
            "where do sulphur limits apply?",
            "MARPOL Annex VI — Regulation 14, Sulphur Emissions",
            llm_answer=populated,
        )
        self.assertEqual(self._source_value(result), "Existing Source Ref")


class TestBackfillSource(unittest.TestCase):
    """Unit coverage for the deterministic SOURCE backfill helper."""

    CHUNKS = [{"source_reference": "MARPOL Annex VI — Regulation 14, Sulphur Emissions"}]

    def test_empty_source_filled(self):
        from domain.compliance_engine import _backfill_source
        answer = "DECISION:\nX\n\nWHY:\nY\n\nSOURCE:\n\nACTIONS:\n• do thing"
        out = _backfill_source(answer, self.CHUNKS)
        self.assertIn("SOURCE:\nMARPOL Annex VI — Regulation 14, Sulphur Emissions", out)
        self.assertIn("ACTIONS:", out)

    def test_empty_source_at_end_filled(self):
        from domain.compliance_engine import _backfill_source
        out = _backfill_source("DECISION:\nX\n\nWHY:\nY\n\nSOURCE:\n", self.CHUNKS)
        self.assertTrue(out.rstrip().endswith("Sulphur Emissions"))

    def test_populated_source_untouched(self):
        from domain.compliance_engine import _backfill_source
        answer = "DECISION:\nX\n\nWHY:\nY\n\nSOURCE:\nAlready here\n\nACTIONS:\n• do thing"
        self.assertEqual(_backfill_source(answer, self.CHUNKS), answer)

    def test_no_chunks_untouched(self):
        from domain.compliance_engine import _backfill_source
        answer = "DECISION:\nX\n\nSOURCE:\n\nACTIONS:\n• do thing"
        self.assertEqual(_backfill_source(answer, []), answer)

    def test_fallback_answer_untouched(self):
        from domain.compliance_engine import _backfill_source
        answer = (
            "DECISION: Not explicitly covered in the loaded documents.\n"
            "SOURCE: No matching loaded source\nACTIONS: • check"
        )
        self.assertEqual(_backfill_source(answer, self.CHUNKS), answer)


class TestEcaLocationRouting(unittest.TestCase):
    """ASK-46: NECA/SECA/ECA/sulphur location questions must route to MARPOL VI guidance."""

    def _cls(self, text):
        return classify_text(text)

    # --- Intent routing ---
    def test_neca_location_routes_to_compliance(self):
        self.assertEqual(self._cls("where are the NECAs?"), COMPLIANCE)

    def test_nox_eca_location_routes_to_compliance(self):
        self.assertEqual(self._cls("where are the NOx ECAs?"), COMPLIANCE)

    def test_seca_location_routes_to_compliance(self):
        self.assertEqual(self._cls("where are the SECAs?"), COMPLIANCE)

    def test_eca_generic_routes_to_compliance(self):
        self.assertEqual(self._cls("where are the ECAs?"), COMPLIANCE)

    def test_sulphur_limits_routes_to_compliance(self):
        self.assertEqual(self._cls("where do sulphur limits apply?"), COMPLIANCE)

    def test_fuel_sulphur_limits_routes_to_compliance(self):
        self.assertEqual(self._cls("where do fuel sulphur limits apply?"), COMPLIANCE)

    def test_sulphur_010_routes_to_compliance(self):
        self.assertEqual(self._cls("where does the 0.10% sulphur limit apply?"), COMPLIANCE)

    def test_sulphur_050_routes_to_compliance(self):
        self.assertEqual(self._cls("where does the 0.50% sulphur limit apply?"), COMPLIANCE)

    # --- Tier III and inventory regressions ---
    def test_tier_iii_still_routes_to_compliance(self):
        self.assertEqual(self._cls("where does Tier III apply?"), COMPLIANCE)

    def test_ratchet_straps_still_inventory(self):
        self.assertEqual(self._cls("where are the ratchet straps?"), "stock_query")

    def test_part_number_still_inventory(self):
        self.assertEqual(self._cls("where is AIK111571?"), "stock_query")

    def test_show_stock_still_stock_query(self):
        self.assertEqual(self._cls("show valve stock"), "stock_query")

    # --- Compliance engine: NECA must not return NOT_COVERED ---
    @patch("services.compliance_profile.get_selected_regulations", return_value=[])
    @patch("services.compliance_ingest.list_sources", return_value=[])
    @patch("domain.operational_playbook.lookup", return_value=None)
    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_general_guidance")
    def test_neca_engine_calls_general_guidance(
        self, mock_guidance, mock_retriever_getter, _pb, _src, _sel
    ):
        """'where are the NECAs?' must reach general guidance with MARPOL Annex VI."""
        mock_retriever = MagicMock()
        mock_retriever.search_with_yacht.return_value = []
        mock_retriever_getter.return_value = mock_retriever
        mock_guidance.return_value = (
            "DECISION:\nGENERAL GUIDANCE — MARPOL ANNEX VI\n\n"
            "WHY:\nNECAs are NOx Emission Control Areas — MARPOL Annex VI Regulation 13.\n\n"
            "SOURCE:\nMARPOL Annex VI — Regulation 13 / NOx Emission Control Areas"
        )
        from domain.compliance_engine import answer_compliance_query
        answer = answer_compliance_query("where are the NECAs?")
        self.assertNotIn("Not explicitly covered", answer)
        self.assertTrue(
            any(t in answer for t in ["MARPOL Annex VI", "Regulation 13", "NOx Emission Control"]),
            f"Expected MARPOL Annex VI / Regulation 13, got: {answer[:150]}"
        )
        self.assertTrue(mock_guidance.called, "answer_compliance_general_guidance must be called")
        _, kwargs = mock_guidance.call_args
        self.assertEqual(kwargs.get("regulation_name") or mock_guidance.call_args[0][1], "MARPOL Annex VI")

    # --- Compliance engine: SECA must not return NOT_COVERED ---
    @patch("services.compliance_profile.get_selected_regulations", return_value=[])
    @patch("services.compliance_ingest.list_sources", return_value=[])
    @patch("domain.operational_playbook.lookup", return_value=None)
    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_general_guidance")
    def test_seca_engine_calls_general_guidance(
        self, mock_guidance, mock_retriever_getter, _pb, _src, _sel
    ):
        """'where are the SECAs?' must reach general guidance mentioning 0.10%."""
        mock_retriever = MagicMock()
        mock_retriever.search_with_yacht.return_value = []
        mock_retriever_getter.return_value = mock_retriever
        mock_guidance.return_value = (
            "DECISION:\nGENERAL GUIDANCE — MARPOL ANNEX VI\n\n"
            "WHY:\n0.10% sulphur limit applies inside SECAs under MARPOL Annex VI Regulation 14.\n\n"
            "SOURCE:\nMARPOL Annex VI — Regulation 14 / SOx"
        )
        from domain.compliance_engine import answer_compliance_query
        answer = answer_compliance_query("where are the SECAs?")
        self.assertNotIn("Not explicitly covered", answer)
        self.assertTrue(
            any(t in answer for t in ["MARPOL Annex VI", "Regulation 14", "0.10%"]),
            f"Expected MARPOL Annex VI / Regulation 14 / 0.10%, got: {answer[:150]}"
        )

    # --- Compliance engine: sulphur limits must not return NOT_COVERED ---
    @patch("services.compliance_profile.get_selected_regulations", return_value=[])
    @patch("services.compliance_ingest.list_sources", return_value=[])
    @patch("domain.operational_playbook.lookup", return_value=None)
    @patch("domain.compliance_engine._get_retriever")
    @patch("services.anthropic_service.answer_compliance_general_guidance")
    def test_sulphur_limits_engine_calls_general_guidance(
        self, mock_guidance, mock_retriever_getter, _pb, _src, _sel
    ):
        """'where do sulphur limits apply?' must reach general guidance with global cap."""
        mock_retriever = MagicMock()
        mock_retriever.search_with_yacht.return_value = []
        mock_retriever_getter.return_value = mock_retriever
        mock_guidance.return_value = (
            "DECISION:\nGENERAL GUIDANCE — MARPOL ANNEX VI\n\n"
            "WHY:\nGlobal 0.50% sulphur cap; 0.10% inside ECAs — MARPOL Annex VI Regulation 14.\n\n"
            "SOURCE:\nMARPOL Annex VI — Regulation 14"
        )
        from domain.compliance_engine import answer_compliance_query
        answer = answer_compliance_query("where do sulphur limits apply?")
        self.assertNotIn("Not explicitly covered", answer)
        self.assertTrue(
            "0.50%" in answer or "0.10%" in answer,
            f"Expected 0.50% or 0.10% reference, got: {answer[:150]}"
        )


class TestWhereIsComplianceRouting(unittest.TestCase):
    """ASK-45: 'where are/is' + compliance term must route to compliance, not stock_query."""

    def _cls(self, text):
        return classify_text(text)

    # --- compliance routing for "where" questions ---
    def test_where_are_necas_is_compliance(self):
        self.assertEqual(self._cls("where are the NECAs?"), COMPLIANCE)

    def test_where_are_ecas_is_compliance(self):
        self.assertEqual(self._cls("where are the ECAs?"), COMPLIANCE)

    def test_where_are_secas_is_compliance(self):
        self.assertEqual(self._cls("where are the SECAs?"), COMPLIANCE)

    def test_where_does_tier_iii_apply_is_compliance(self):
        self.assertEqual(self._cls("where does Tier III apply?"), COMPLIANCE)

    def test_where_do_sulphur_limits_apply_is_compliance(self):
        self.assertEqual(self._cls("where do sulphur limits apply?"), COMPLIANCE)

    # --- inventory regressions — must NOT route to compliance ---
    def test_where_are_ratchet_straps_is_inventory(self):
        self.assertEqual(self._cls("where are the ratchet straps?"), "stock_query")

    def test_where_is_teak_oil_is_inventory(self):
        self.assertEqual(self._cls("where is the teak oil?"), "stock_query")

    def test_where_is_part_number_is_inventory(self):
        self.assertEqual(self._cls("where is AIK111571?"), "stock_query")


if __name__ == "__main__":
    unittest.main()
