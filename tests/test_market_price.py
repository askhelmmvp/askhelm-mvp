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


class TestHasPartNumber(unittest.TestCase):

    def test_hyphenated_part_number(self):
        self.assertTrue(_has_part_number("yanmar 196350-04061"))

    def test_part_number_with_pn_marker(self):
        self.assertTrue(_has_part_number("p/n 196350-04061"))

    def test_part_number_with_part_number_marker(self):
        self.assertTrue(_has_part_number("part number NJ-1234/56"))

    def test_short_code_not_matched(self):
        # Two-char prefix with only 2-char suffix — below threshold
        self.assertFalse(_has_part_number("AB-12 pump"))

    def test_no_part_number_generic_query(self):
        self.assertFalse(_has_part_number("how much for a windlass service"))

    def test_oem_code_marker(self):
        self.assertTrue(_has_part_number("oem code AB-12345"))

    def test_part_no_marker(self):
        self.assertTrue(_has_part_number("part no 196350-04061"))


class TestParseConfidence(unittest.TestCase):

    def test_exact_match_parsed(self):
        raw = "CONFIDENCE:\nexact_match\n\nDECISION:\nWithin expected range"
        level, _ = _parse_confidence(raw)
        self.assertEqual(level, CONFIDENCE_EXACT)

    def test_similar_item_estimate_parsed(self):
        raw = "CONFIDENCE:\nsimilar_item_estimate\n\nDECISION:\nEstimate only"
        level, _ = _parse_confidence(raw)
        self.assertEqual(level, CONFIDENCE_SIMILAR)

    def test_insufficient_confidence_parsed(self):
        raw = "CONFIDENCE:\ninsufficient_confidence\n\nDECISION:\nUnclear"
        level, _ = _parse_confidence(raw)
        self.assertEqual(level, CONFIDENCE_INSUFFICIENT)

    def test_missing_confidence_returns_none(self):
        raw = "DECISION:\nWithin expected range\n\nWHY:\nTest\n\nACTIONS:\n• Do something"
        level, _ = _parse_confidence(raw)
        self.assertIsNone(level)


class TestParseSections(unittest.TestCase):

    def test_all_sections_parsed(self):
        raw = (
            "CONFIDENCE:\nexact_match\n\n"
            "DECISION:\nWithin expected range\n\n"
            "WHY:\nTypical market rate\n\n"
            "ACTIONS:\n• Get quotes\n• Check OEM"
        )
        sections = _parse_sections(raw)
        self.assertIn("DECISION", sections)
        self.assertIn("WHY", sections)
        self.assertIn("ACTIONS", sections)
        self.assertEqual(sections["DECISION"], "Within expected range")

    def test_missing_section_not_in_dict(self):
        raw = "DECISION:\nUnclear\n\nACTIONS:\n• Do something"
        sections = _parse_sections(raw)
        self.assertNotIn("WHY", sections)


class TestPartNumberWithWeakMatch(unittest.TestCase):
    """Part number present + Claude returns similar_item_estimate → downgrade to insufficient."""

    @patch("services.market_price_service.client")
    def test_part_number_similar_downgraded_to_insufficient(self, mock_client):
        mock_client.messages.create.return_value = _mock_response(
            "CONFIDENCE:\nsimilar_item_estimate\n\n"
            "DECISION:\nEstimate only — based on similar items, not exact verified match\n\n"
            "WHY:\nYanmar universal joints typically cost €80–€150, but this exact part number is not verified.\n\n"
            "ACTIONS:\n• Contact Yanmar dealer\n• Get 2 quotes"
        )
        result = check_market_price("how much for a yanmar universal joint p/n 196350-04061")
        self.assertIn("Unclear — exact market price not confidently verified", result)
        self.assertNotIn("€80", result)
        self.assertNotIn("€150", result)
        self.assertIn("exact part number", result)

    @patch("services.market_price_service.client")
    def test_part_number_insufficient_stays_insufficient(self, mock_client):
        mock_client.messages.create.return_value = _mock_response(
            "CONFIDENCE:\ninsufficient_confidence\n\n"
            "DECISION:\nUnclear — exact market price not confidently verified\n\n"
            "WHY:\nThis exact part number cannot be reliably priced without OEM data.\n\n"
            "ACTIONS:\n• Get 2 quotes\n• Check OEM dealer"
        )
        result = check_market_price("yanmar 196350-04061 price")
        self.assertIn("Unclear", result)
        self.assertIn("Get 2 quotes against the exact part number", result)

    @patch("services.market_price_service.client")
    def test_part_number_exact_match_not_downgraded(self, mock_client):
        """exact_match stays even with a part number (rare but valid if data is strong)."""
        mock_client.messages.create.return_value = _mock_response(
            "CONFIDENCE:\nexact_match\n\n"
            "DECISION:\nWithin expected range — typical cost €120–€180\n\n"
            "WHY:\nOEM Yanmar universal joints are well-documented at this price.\n\n"
            "ACTIONS:\n• Verify with local dealer\n• Compare aftermarket"
        )
        result = check_market_price("yanmar universal joint 196350-04061 price")
        self.assertIn("Within expected range", result)
        self.assertIn("€120", result)


class TestGenericItemWithoutPartNumber(unittest.TestCase):
    """Generic service/item without part number → broad estimate is acceptable."""

    @patch("services.market_price_service.client")
    def test_generic_service_similar_estimate_preserved(self, mock_client):
        mock_client.messages.create.return_value = _mock_response(
            "CONFIDENCE:\nsimilar_item_estimate\n\n"
            "DECISION:\nEstimate only — based on similar items, not exact verified match\n\n"
            "WHY:\nWindlass servicing varies widely: €300–€900 depending on size and make.\n\n"
            "ACTIONS:\n• Get 2 quotes\n• Specify windlass model\n• Ask for itemised quote"
        )
        result = check_market_price("what should a windlass service cost")
        self.assertIn("Estimate only — based on similar items", result)
        # WHY content preserved
        self.assertIn("Windlass", result)

    @patch("services.market_price_service.client")
    def test_exact_match_for_common_service(self, mock_client):
        mock_client.messages.create.return_value = _mock_response(
            "CONFIDENCE:\nexact_match\n\n"
            "DECISION:\nWithin expected range — typical hull cleaning €15–€25 per metre\n\n"
            "WHY:\nHull cleaning is a commoditised service with transparent market rates.\n\n"
            "ACTIONS:\n• Compare 3 local yard rates\n• Ask what's included"
        )
        result = check_market_price("is €2000 reasonable for hull cleaning on a 40m yacht")
        self.assertIn("Within expected range", result)
        self.assertNotIn("CONFIDENCE:", result)


class TestInsufficientConfidenceEnforcement(unittest.TestCase):

    def test_enforce_insufficient_uses_why_from_sections(self):
        sections = {"WHY": "This part is too specific to price reliably."}
        result = _enforce_insufficient(sections)
        self.assertIn("Unclear — exact market price not confidently verified", result)
        self.assertIn("too specific to price reliably", result)
        self.assertIn("Get 2 quotes against the exact part number", result)

    def test_enforce_insufficient_fallback_why(self):
        result = _enforce_insufficient({})
        self.assertIn("Unclear", result)
        self.assertIn("strong matching sources", result)

    def test_enforce_similar_uses_actions_from_sections(self):
        sections = {
            "WHY": "Comparable parts range €100–€200.",
            "ACTIONS": "• Check OEM pricing\n• Ask for aftermarket option",
        }
        result = _enforce_similar(sections)
        self.assertIn("Estimate only — based on similar items, not exact verified match", result)
        self.assertIn("Check OEM pricing", result)


class TestMissingConfidenceLine(unittest.TestCase):
    """When Claude omits the CONFIDENCE line, confidence is inferred from DECISION text."""

    @patch("services.market_price_service.client")
    def test_inferred_insufficient_from_decision(self, mock_client):
        mock_client.messages.create.return_value = _mock_response(
            "DECISION:\nUnclear — exact market price not confidently verified\n\n"
            "WHY:\nCould not find data for this exact part.\n\n"
            "ACTIONS:\n• Get quotes"
        )
        result = check_market_price("yanmar 196350-04061")
        self.assertIn("Unclear", result)

    @patch("services.market_price_service.client")
    def test_inferred_similar_from_decision(self, mock_client):
        mock_client.messages.create.return_value = _mock_response(
            "DECISION:\nEstimate only — based on similar items, not exact verified match\n\n"
            "WHY:\nSimilar pumps are €200–€400.\n\n"
            "ACTIONS:\n• Get 2 quotes"
        )
        # No part number → similar_item_estimate is preserved
        result = check_market_price("how much for a sea water pump service")
        self.assertIn("Estimate only — based on similar items", result)

    @patch("services.market_price_service.client")
    def test_inferred_exact_without_part_number(self, mock_client):
        mock_client.messages.create.return_value = _mock_response(
            "DECISION:\nWithin expected range — €500–€800\n\n"
            "WHY:\nStandard market rate.\n\n"
            "ACTIONS:\n• Compare 2 quotes"
        )
        result = check_market_price("is €600 reasonable for an annual bilge pump service")
        self.assertIn("Within expected range", result)


class TestApiErrorFallback(unittest.TestCase):

    @patch("services.market_price_service.client")
    def test_api_error_returns_insufficient_format(self, mock_client):
        mock_client.messages.create.side_effect = Exception("Connection timeout")
        result = check_market_price("how much for a yanmar impeller")
        self.assertIn("Unclear", result)
        self.assertIn("Get 2 quotes against the exact part number", result)
        self.assertNotIn("Within expected range", result)


if __name__ == "__main__":
    unittest.main()
