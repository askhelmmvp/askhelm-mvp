"""
Tests for services/market_price_service.py confidence-level enforcement.
All tests mock the Anthropic client so no real API calls are made.
"""
import unittest
from unittest.mock import patch, MagicMock

from services.market_price_service import (
    check_market_price,
    _has_part_number,
    _parse_confidence,
    _parse_sections,
    _enforce_insufficient,
    _enforce_similar,
    CONFIDENCE_EXACT,
    CONFIDENCE_SIMILAR,
    CONFIDENCE_INSUFFICIENT,
)


def _mock_response(text: str) -> MagicMock:
    msg = MagicMock()
    msg.content = [MagicMock(text=text)]
    return msg


# ---------------------------------------------------------------------------
# _has_part_number
# ---------------------------------------------------------------------------

class TestHasPartNumber(unittest.TestCase):

    def test_hyphenated_part_number(self):
        self.assertTrue(_has_part_number("yanmar 196350-04061"))

    def test_part_number_with_pn_marker(self):
        self.assertTrue(_has_part_number("p/n 196350-04061"))

    def test_part_number_with_part_number_marker(self):
        self.assertTrue(_has_part_number("part number NJ-1234/56"))

    def test_short_code_not_matched(self):
        self.assertFalse(_has_part_number("AB-12 pump"))

    def test_no_part_number_generic_query(self):
        self.assertFalse(_has_part_number("how much for a windlass service"))

    def test_oem_code_marker(self):
        self.assertTrue(_has_part_number("oem code AB-12345"))

    def test_part_no_marker(self):
        self.assertTrue(_has_part_number("part no 196350-04061"))

    def test_danfoss_pressure_sensor_no_part_number(self):
        self.assertFalse(_has_part_number("how much for a danfoss pressure sensor"))


# ---------------------------------------------------------------------------
# _parse_confidence / _parse_sections
# ---------------------------------------------------------------------------

class TestParseConfidence(unittest.TestCase):

    def test_exact_match_parsed(self):
        raw = "CONFIDENCE:\nexact_match\n\nDECISION:\nReasonable"
        level, _ = _parse_confidence(raw)
        self.assertEqual(level, CONFIDENCE_EXACT)

    def test_similar_item_estimate_parsed(self):
        raw = "CONFIDENCE:\nsimilar_item_estimate\n\nDECISION:\nBroad estimate only"
        level, _ = _parse_confidence(raw)
        self.assertEqual(level, CONFIDENCE_SIMILAR)

    def test_insufficient_confidence_parsed(self):
        raw = "CONFIDENCE:\ninsufficient_confidence\n\nDECISION:\nNo reliable exact price confirmed"
        level, _ = _parse_confidence(raw)
        self.assertEqual(level, CONFIDENCE_INSUFFICIENT)

    def test_missing_confidence_returns_none(self):
        raw = "DECISION:\nReasonable\n\nWHY:\nTest\n\nACTIONS:\n• Do something"
        level, _ = _parse_confidence(raw)
        self.assertIsNone(level)


class TestParseSections(unittest.TestCase):

    def test_all_sections_parsed(self):
        raw = (
            "CONFIDENCE:\nexact_match\n\n"
            "DECISION:\nReasonable\n\n"
            "WHY:\nTypical market rate.\n\n"
            "ACTIONS:\n• Get quotes\n• Check OEM"
        )
        sections = _parse_sections(raw)
        self.assertIn("DECISION", sections)
        self.assertIn("WHY", sections)
        self.assertIn("ACTIONS", sections)
        self.assertEqual(sections["DECISION"], "Reasonable")

    def test_missing_section_not_in_dict(self):
        raw = "DECISION:\nHigh\n\nACTIONS:\n• Do something"
        sections = _parse_sections(raw)
        self.assertNotIn("WHY", sections)


# ---------------------------------------------------------------------------
# Mode A: exact part number + weak confidence
# ---------------------------------------------------------------------------

class TestPartNumberWithWeakMatch(unittest.TestCase):

    @patch("services.market_price_service.client")
    def test_part_number_similar_downgraded_to_mode_a(self, mock_client):
        """Part number + similar_item_estimate → standardised Mode A, no price range leaked."""
        mock_client.messages.create.return_value = _mock_response(
            "CONFIDENCE:\nsimilar_item_estimate\n\n"
            "DECISION:\nBroad estimate only\n\n"
            "WHY:\nYanmar universal joints typically cost €80–€150, but this exact part number is not verified.\n\n"
            "ACTIONS:\n• Contact Yanmar dealer\n• Get 2 quotes"
        )
        result = check_market_price("how much for a yanmar universal joint p/n 196350-04061")
        self.assertIn("No reliable exact price confirmed", result)
        self.assertNotIn("€80", result)
        self.assertNotIn("€150", result)
        self.assertIn("Send the quoted price", result)

    @patch("services.market_price_service.client")
    def test_part_number_insufficient_stays_mode_a(self, mock_client):
        """Claude returns insufficient_confidence → enforce_insufficient uses Claude WHY."""
        mock_client.messages.create.return_value = _mock_response(
            "CONFIDENCE:\ninsufficient_confidence\n\n"
            "DECISION:\nNo reliable exact price confirmed\n\n"
            "WHY:\nThis exact part number cannot be reliably priced without OEM data.\n\n"
            "ACTIONS:\n• Get 2 quotes\n• Check OEM dealer"
        )
        result = check_market_price("yanmar 196350-04061 price")
        self.assertIn("No reliable exact price confirmed", result)
        self.assertIn("Send the quoted price", result)

    @patch("services.market_price_service.client")
    def test_part_number_exact_match_not_downgraded(self, mock_client):
        """exact_match stays even with a part number (strong data case)."""
        mock_client.messages.create.return_value = _mock_response(
            "CONFIDENCE:\nexact_match\n\n"
            "DECISION:\nWithin expected range — typical cost €120–€180\n\n"
            "WHY:\nOEM Yanmar universal joints are well-documented at this price.\n\n"
            "ACTIONS:\n• Verify with local dealer\n• Compare aftermarket"
        )
        result = check_market_price("yanmar universal joint 196350-04061 price")
        self.assertIn("Within expected range", result)
        self.assertIn("€120", result)

    @patch("services.market_price_service.client")
    def test_allow_broad_estimate_skips_downgrade(self, mock_client):
        """allow_broad_estimate=True preserves similar_item_estimate even for part number queries."""
        mock_client.messages.create.return_value = _mock_response(
            "CONFIDENCE:\nsimilar_item_estimate\n\n"
            "DECISION:\nBroad estimate only\n\n"
            "WHY:\nYanmar universal joints of this type typically cost €80–€200 OEM.\n\n"
            "ACTIONS:\n• This is an estimate — verify with a Yanmar dealer"
        )
        result = check_market_price(
            "how much for yanmar p/n 196350-04061\nUser follow-up: ok give me an estimate",
            allow_broad_estimate=True,
        )
        self.assertIn("Broad estimate only", result)
        self.assertIn("€80", result)
        self.assertNotIn("No reliable exact price confirmed", result)


# ---------------------------------------------------------------------------
# Mode B: generic item, no price given
# ---------------------------------------------------------------------------

class TestGenericItemNoPriceGiven(unittest.TestCase):

    @patch("services.market_price_service.client")
    def test_danfoss_pressure_sensor_returns_broad_estimate(self, mock_client):
        """Generic OEM item without part number gets a broad estimate (Mode B)."""
        mock_client.messages.create.return_value = _mock_response(
            "CONFIDENCE:\nsimilar_item_estimate\n\n"
            "DECISION:\nBroad estimate only\n\n"
            "WHY:\nDanfoss marine pressure sensors typically range €150–€600 depending on model and range.\n\n"
            "ACTIONS:\n• What model number or pressure range? That'll narrow it down"
        )
        result = check_market_price("how much for a danfoss pressure sensor")
        self.assertIn("Broad estimate only", result)
        self.assertIn("€150", result)
        self.assertIn("€600", result)
        self.assertNotIn("CONFIDENCE:", result)

    @patch("services.market_price_service.client")
    def test_generic_windlass_service_broad_estimate(self, mock_client):
        mock_client.messages.create.return_value = _mock_response(
            "CONFIDENCE:\nsimilar_item_estimate\n\n"
            "DECISION:\nBroad estimate only\n\n"
            "WHY:\nTypical range is €300–€900 depending on windlass size and make.\n\n"
            "ACTIONS:\n• What make and model is the windlass?"
        )
        result = check_market_price("what should a windlass service cost")
        self.assertIn("Broad estimate only", result)
        self.assertIn("€300", result)

    @patch("services.market_price_service.client")
    def test_common_service_exact_match_allowed(self, mock_client):
        """Commoditised services can return exact_match from Claude."""
        mock_client.messages.create.return_value = _mock_response(
            "CONFIDENCE:\nexact_match\n\n"
            "DECISION:\nReasonable — typical hull cleaning €12–€20 per metre\n\n"
            "WHY:\nHull cleaning is a commoditised service with transparent market rates.\n\n"
            "ACTIONS:\n• Compare 2–3 local yard rates\n• Check what's included"
        )
        result = check_market_price("is €700 reasonable for hull cleaning on a 40m yacht")
        self.assertIn("Reasonable", result)
        self.assertNotIn("CONFIDENCE:", result)


# ---------------------------------------------------------------------------
# Mode C: user gives a specific price
# ---------------------------------------------------------------------------

class TestUserGivesQuotedPrice(unittest.TestCase):

    @patch("services.market_price_service.client")
    def test_pump_overhaul_kit_too_vague_asks_one_question(self, mock_client):
        """€4500 for pump overhaul kit — too vague → Unclear, one short clarifying question."""
        mock_client.messages.create.return_value = _mock_response(
            "CONFIDENCE:\nexact_match\n\n"
            "DECISION:\nUnclear\n\n"
            "WHY:\nPump overhaul kits vary widely — need make and model to assess.\n\n"
            "ACTIONS:\n• What make and model is the pump?"
        )
        result = check_market_price("is €4500 reasonable for a pump overhaul kit")
        self.assertIn("Unclear", result)
        self.assertIn("What make and model", result)
        # Should NOT contain a long list
        bullet_count = result.count("•")
        self.assertLessEqual(bullet_count, 2)

    @patch("services.market_price_service.client")
    def test_high_price_judgment_short_response(self, mock_client):
        mock_client.messages.create.return_value = _mock_response(
            "CONFIDENCE:\nexact_match\n\n"
            "DECISION:\nHigh\n\n"
            "WHY:\nExpect €800–€1200 for a standard impeller replacement on most yacht engines.\n\n"
            "ACTIONS:\n• Ask for an itemised breakdown\n• Get a second quote"
        )
        result = check_market_price("is €2500 reasonable for an impeller replacement")
        self.assertIn("High", result)
        self.assertIn("WHY:", result)
        self.assertNotIn("CONFIDENCE:", result)
        bullet_count = result.count("•")
        self.assertLessEqual(bullet_count, 2)

    @patch("services.market_price_service.client")
    def test_reasonable_price_returns_decision(self, mock_client):
        mock_client.messages.create.return_value = _mock_response(
            "CONFIDENCE:\nexact_match\n\n"
            "DECISION:\nReasonable\n\n"
            "WHY:\nTypical annual service for this engine size runs €600–€1000.\n\n"
            "ACTIONS:\n• Check the service includes filters and impeller\n• Request itemised invoice"
        )
        result = check_market_price("is €800 reasonable for an annual engine service")
        self.assertIn("Reasonable", result)
        self.assertNotIn("CONFIDENCE:", result)


# ---------------------------------------------------------------------------
# Enforcement helpers
# ---------------------------------------------------------------------------

class TestEnforcementHelpers(unittest.TestCase):

    def test_enforce_insufficient_uses_standardised_decision(self):
        sections = {"WHY": "This part is too specific to price reliably."}
        result = _enforce_insufficient(sections)
        self.assertIn("No reliable exact price confirmed", result)
        self.assertIn("too specific to price reliably", result)
        self.assertIn("Send the quoted price", result)

    def test_enforce_insufficient_fallback_why(self):
        result = _enforce_insufficient({})
        self.assertIn("No reliable exact price confirmed", result)
        self.assertIn("could not verify", result)

    def test_enforce_similar_uses_broad_estimate_decision(self):
        sections = {
            "WHY": "Comparable sensors range €200–€500.",
            "ACTIONS": "• Check model number",
        }
        result = _enforce_similar(sections)
        self.assertIn("Broad estimate only", result)
        self.assertIn("€200–€500", result)
        self.assertIn("Check model number", result)


# ---------------------------------------------------------------------------
# Missing CONFIDENCE line inference
# ---------------------------------------------------------------------------

class TestMissingConfidenceLine(unittest.TestCase):

    @patch("services.market_price_service.client")
    def test_inferred_insufficient_from_no_reliable(self, mock_client):
        mock_client.messages.create.return_value = _mock_response(
            "DECISION:\nNo reliable exact price confirmed\n\n"
            "WHY:\nCould not find data for this exact part.\n\n"
            "ACTIONS:\n• Get quotes"
        )
        result = check_market_price("yanmar 196350-04061")
        self.assertIn("No reliable exact price confirmed", result)

    @patch("services.market_price_service.client")
    def test_inferred_similar_from_broad_estimate(self, mock_client):
        mock_client.messages.create.return_value = _mock_response(
            "DECISION:\nBroad estimate only\n\n"
            "WHY:\nSimilar pumps are €200–€400.\n\n"
            "ACTIONS:\n• What make and model?"
        )
        result = check_market_price("how much for a sea water pump service")
        self.assertIn("Broad estimate only", result)

    @patch("services.market_price_service.client")
    def test_inferred_exact_from_reasonable(self, mock_client):
        mock_client.messages.create.return_value = _mock_response(
            "DECISION:\nReasonable\n\n"
            "WHY:\nStandard market rate.\n\n"
            "ACTIONS:\n• Compare 2 quotes"
        )
        result = check_market_price("is €600 reasonable for an annual bilge pump service")
        self.assertIn("Reasonable", result)


# ---------------------------------------------------------------------------
# API error fallback
# ---------------------------------------------------------------------------

class TestApiErrorFallback(unittest.TestCase):

    @patch("services.market_price_service.client")
    def test_api_error_returns_mode_a_format(self, mock_client):
        mock_client.messages.create.side_effect = Exception("Connection timeout")
        result = check_market_price("how much for a yanmar impeller")
        self.assertIn("No reliable exact price confirmed", result)
        self.assertIn("Send the quoted price", result)
        self.assertNotIn("Reasonable", result)


if __name__ == "__main__":
    unittest.main()
