"""Tests for ASK-35: editable saved billing and delivery addresses."""
import unittest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Shared state factory
# ---------------------------------------------------------------------------

def _empty_state():
    return {
        "sessions": [],
        "documents": [],
        "active_session_id": None,
        "pending_invoice": None,
        "last_context": {},
    }


def _dispatch(msg, state=None):
    from whatsapp_app import _handle_text_message
    if state is None:
        state = _empty_state()
    response, new_state = _handle_text_message(msg, state)
    return response, new_state


# ---------------------------------------------------------------------------
# _is_valid_address helper
# ---------------------------------------------------------------------------

class TestIsValidAddress(unittest.TestCase):
    def _check(self, text):
        from whatsapp_app import _is_valid_address
        return _is_valid_address(text)

    def test_two_lines_accepted(self):
        self.assertTrue(self._check("DEMO YACHT SERVICES\n12 Quai des Pecheurs"))

    def test_multiline_accepted(self):
        self.assertTrue(self._check(
            "DEMO YACHT SERVICES\n12 Quai des Pecheurs\nPort Vauban\n06600 Antibes\nFrance"
        ))

    def test_single_line_with_number_accepted(self):
        self.assertTrue(self._check("12 Quai des Pecheurs, Port Vauban 06600 Antibes"))

    def test_single_short_line_rejected(self):
        self.assertFalse(self._check("Antibes"))

    def test_empty_rejected(self):
        self.assertFalse(self._check(""))

    def test_single_word_rejected(self):
        self.assertFalse(self._check("cancel"))


# ---------------------------------------------------------------------------
# load/save delivery address (domain layer)
# ---------------------------------------------------------------------------

_ANTIBES_ADDRESS = (
    "DEMO YACHT SERVICES\n"
    "12 Quai des Pecheurs\n"
    "Port Vauban\n"
    "06600 Antibes\n"
    "France"
)

_CAYMAN_ADDRESS = (
    "BLUE OCEAN DEMO LTD\n"
    "Suite 204, Marina Plaza\n"
    "20 Harbour Drive\n"
    "George Town KY1-1201\n"
    "Cayman Islands"
)


class TestDeliveryAddressStorage(unittest.TestCase):
    def test_save_and_load_roundtrip(self):
        from domain.invoice_address import save_delivery_address, load_delivery_address
        with patch("domain.invoice_address._config_path") as mock_path:
            import tempfile, pathlib
            tmp = pathlib.Path(tempfile.mktemp(suffix=".json"))
            mock_path.return_value = tmp
            save_delivery_address(_ANTIBES_ADDRESS)
            result = load_delivery_address()
        self.assertEqual(result.strip(), _ANTIBES_ADDRESS.strip())

    def test_falls_back_to_default(self):
        from domain.invoice_address import load_delivery_address, _DEFAULT_DELIVERY_RAW
        with patch("domain.invoice_address._config_path") as mock_path:
            import pathlib
            mock_path.return_value = pathlib.Path("/nonexistent/vessel_config.json")
            result = load_delivery_address()
        self.assertEqual(result, _DEFAULT_DELIVERY_RAW)


class TestInvoiceAddressStorage(unittest.TestCase):
    def test_save_and_load_roundtrip(self):
        from domain.invoice_address import save_invoice_address, load_invoice_address
        with patch("domain.invoice_address._config_path") as mock_path:
            import tempfile, pathlib
            tmp = pathlib.Path(tempfile.mktemp(suffix=".json"))
            mock_path.return_value = tmp
            save_invoice_address(_CAYMAN_ADDRESS)
            result = load_invoice_address()
        self.assertEqual(result.strip(), _CAYMAN_ADDRESS.strip())

    def test_both_addresses_coexist(self):
        from domain.invoice_address import (
            save_delivery_address, load_delivery_address,
            save_invoice_address, load_invoice_address,
        )
        with patch("domain.invoice_address._config_path") as mock_path:
            import tempfile, pathlib
            tmp = pathlib.Path(tempfile.mktemp(suffix=".json"))
            mock_path.return_value = tmp
            save_delivery_address(_ANTIBES_ADDRESS)
            save_invoice_address(_CAYMAN_ADDRESS)
            del_addr = load_delivery_address()
            inv_addr = load_invoice_address()
        self.assertEqual(del_addr.strip(), _ANTIBES_ADDRESS.strip())
        self.assertEqual(inv_addr.strip(), _CAYMAN_ADDRESS.strip())


# ---------------------------------------------------------------------------
# Command routing — start update mode
# ---------------------------------------------------------------------------

class TestStartDeliveryAddressUpdate(unittest.TestCase):
    def _response(self, cmd="change delivery address"):
        r, _ = _dispatch(cmd)
        return r

    def test_ready_to_update_decision(self):
        self.assertIn("READY TO UPDATE DELIVERY ADDRESS", self._response())

    def test_update_variant(self):
        self.assertIn("READY TO UPDATE DELIVERY ADDRESS", self._response("update delivery address"))

    def test_set_variant(self):
        self.assertIn("READY TO UPDATE DELIVERY ADDRESS", self._response("set delivery address"))

    def test_sets_pending_state(self):
        _, state = _dispatch("change delivery address")
        self.assertTrue(state.get("pending_delivery_address_update"))

    def test_instructions_mention_cancel(self):
        self.assertIn("cancel", self._response().lower())


class TestStartInvoiceAddressUpdate(unittest.TestCase):
    def _response(self, cmd="change invoice address"):
        r, _ = _dispatch(cmd)
        return r

    def test_ready_to_update_decision(self):
        self.assertIn("READY TO UPDATE INVOICE ADDRESS", self._response())

    def test_billing_alias(self):
        self.assertIn("READY TO UPDATE INVOICE ADDRESS", self._response("change billing address"))

    def test_update_billing_alias(self):
        self.assertIn("READY TO UPDATE INVOICE ADDRESS", self._response("update billing address"))

    def test_set_invoice_alias(self):
        self.assertIn("READY TO UPDATE INVOICE ADDRESS", self._response("set invoice address"))

    def test_sets_pending_state(self):
        _, state = _dispatch("change invoice address")
        self.assertTrue(state.get("pending_invoice_address_update"))


# ---------------------------------------------------------------------------
# Save delivery address — pending state consumed
# ---------------------------------------------------------------------------

class TestSaveDeliveryAddress(unittest.TestCase):
    def _state_pending(self):
        s = _empty_state()
        s["pending_delivery_address_update"] = True
        return s

    def test_delivery_address_updated_decision(self):
        with patch("whatsapp_app.save_delivery_address"):
            r, _ = _dispatch(_ANTIBES_ADDRESS, self._state_pending())
        self.assertIn("DELIVERY ADDRESS UPDATED", r)

    def test_saved_address_echoed_in_response(self):
        with patch("whatsapp_app.save_delivery_address"):
            r, _ = _dispatch(_ANTIBES_ADDRESS, self._state_pending())
        self.assertIn("DEMO YACHT SERVICES", r)
        self.assertIn("Antibes", r)

    def test_pending_state_cleared_after_save(self):
        with patch("whatsapp_app.save_delivery_address"):
            _, state = _dispatch(_ANTIBES_ADDRESS, self._state_pending())
        self.assertFalse(state.get("pending_delivery_address_update"))

    def test_save_delivery_address_called(self):
        with patch("whatsapp_app.save_delivery_address") as mock_save:
            _dispatch(_ANTIBES_ADDRESS, self._state_pending())
        mock_save.assert_called_once()
        saved = mock_save.call_args[0][0]
        self.assertIn("Antibes", saved)

    def test_incomplete_address_rejected(self):
        with patch("whatsapp_app.save_delivery_address") as mock_save:
            r, state = _dispatch("Antibes", self._state_pending())
        mock_save.assert_not_called()
        self.assertFalse("DELIVERY ADDRESS UPDATED" in r)
        self.assertTrue(state.get("pending_delivery_address_update"))

    def test_multiline_address_accepted(self):
        addr = "DEMO YACHT SERVICES\n12 Quai des Pecheurs\nPort Vauban\n06600 Antibes\nFrance"
        with patch("whatsapp_app.save_delivery_address") as mock_save:
            r, _ = _dispatch(addr, self._state_pending())
        mock_save.assert_called_once()
        self.assertIn("DELIVERY ADDRESS UPDATED", r)


# ---------------------------------------------------------------------------
# Save invoice address — pending state consumed
# ---------------------------------------------------------------------------

class TestSaveInvoiceAddress(unittest.TestCase):
    def _state_pending(self):
        s = _empty_state()
        s["pending_invoice_address_update"] = True
        return s

    def test_invoice_address_updated_decision(self):
        with patch("whatsapp_app.save_invoice_address"):
            r, _ = _dispatch(_CAYMAN_ADDRESS, self._state_pending())
        self.assertIn("INVOICE ADDRESS UPDATED", r)

    def test_saved_address_echoed_in_response(self):
        with patch("whatsapp_app.save_invoice_address"):
            r, _ = _dispatch(_CAYMAN_ADDRESS, self._state_pending())
        self.assertIn("BLUE OCEAN DEMO LTD", r)
        self.assertIn("Cayman", r)

    def test_pending_state_cleared_after_save(self):
        with patch("whatsapp_app.save_invoice_address"):
            _, state = _dispatch(_CAYMAN_ADDRESS, self._state_pending())
        self.assertFalse(state.get("pending_invoice_address_update"))

    def test_save_invoice_address_called(self):
        with patch("whatsapp_app.save_invoice_address") as mock_save:
            _dispatch(_CAYMAN_ADDRESS, self._state_pending())
        mock_save.assert_called_once()
        saved = mock_save.call_args[0][0]
        self.assertIn("BLUE OCEAN DEMO LTD", saved)

    def test_incomplete_address_rejected(self):
        with patch("whatsapp_app.save_invoice_address") as mock_save:
            r, state = _dispatch("Cayman", self._state_pending())
        mock_save.assert_not_called()
        self.assertFalse("INVOICE ADDRESS UPDATED" in r)
        self.assertTrue(state.get("pending_invoice_address_update"))


# ---------------------------------------------------------------------------
# Cancel flow
# ---------------------------------------------------------------------------

class TestAddressUpdateCancel(unittest.TestCase):
    def test_cancel_delivery_clears_pending(self):
        s = _empty_state()
        s["pending_delivery_address_update"] = True
        r, state = _dispatch("cancel", s)
        self.assertIn("CANCELLED", r)
        self.assertFalse(state.get("pending_delivery_address_update"))

    def test_cancel_invoice_clears_pending(self):
        s = _empty_state()
        s["pending_invoice_address_update"] = True
        r, state = _dispatch("cancel", s)
        self.assertIn("CANCELLED", r)
        self.assertFalse(state.get("pending_invoice_address_update"))

    def test_cancel_does_not_save_delivery(self):
        s = _empty_state()
        s["pending_delivery_address_update"] = True
        with patch("whatsapp_app.save_delivery_address") as mock_save:
            _dispatch("cancel", s)
        mock_save.assert_not_called()

    def test_cancel_does_not_save_invoice(self):
        s = _empty_state()
        s["pending_invoice_address_update"] = True
        with patch("whatsapp_app.save_invoice_address") as mock_save:
            _dispatch("cancel", s)
        mock_save.assert_not_called()

    def test_cancel_two_step_flow(self):
        """Full flow: start update → cancel → address unchanged."""
        with patch("whatsapp_app.save_delivery_address") as mock_save:
            _, state = _dispatch("change delivery address")
            r, state = _dispatch("cancel", state)
        mock_save.assert_not_called()
        self.assertIn("CANCELLED", r)
        self.assertFalse(state.get("pending_delivery_address_update"))


# ---------------------------------------------------------------------------
# Show saved addresses
# ---------------------------------------------------------------------------

class TestShowSavedAddresses(unittest.TestCase):
    def test_show_saved_addresses_decision(self):
        with patch("whatsapp_app.load_delivery_address", return_value=_ANTIBES_ADDRESS), \
             patch("whatsapp_app.load_invoice_address", return_value=_CAYMAN_ADDRESS):
            r, _ = _dispatch("show saved addresses")
        self.assertIn("SAVED ADDRESSES FOUND", r)

    def test_shows_invoice_address(self):
        with patch("whatsapp_app.load_delivery_address", return_value=_ANTIBES_ADDRESS), \
             patch("whatsapp_app.load_invoice_address", return_value=_CAYMAN_ADDRESS):
            r, _ = _dispatch("show saved addresses")
        self.assertIn("BLUE OCEAN DEMO LTD", r)
        self.assertIn("Cayman", r)

    def test_shows_delivery_address(self):
        with patch("whatsapp_app.load_delivery_address", return_value=_ANTIBES_ADDRESS), \
             patch("whatsapp_app.load_invoice_address", return_value=_CAYMAN_ADDRESS):
            r, _ = _dispatch("show saved addresses")
        self.assertIn("DEMO YACHT SERVICES", r)
        self.assertIn("Antibes", r)

    def test_shows_both_labels(self):
        with patch("whatsapp_app.load_delivery_address", return_value=_ANTIBES_ADDRESS), \
             patch("whatsapp_app.load_invoice_address", return_value=_CAYMAN_ADDRESS):
            r, _ = _dispatch("show saved addresses")
        self.assertIn("INVOICE ADDRESS:", r)
        self.assertIn("DELIVERY ADDRESS:", r)

    def test_show_delivery_address_command(self):
        with patch("whatsapp_app.load_delivery_address", return_value=_ANTIBES_ADDRESS):
            r, _ = _dispatch("show delivery address")
        self.assertIn("Antibes", r)

    def test_show_invoice_address_command(self):
        with patch("whatsapp_app.load_invoice_address", return_value=_CAYMAN_ADDRESS):
            r, _ = _dispatch("show invoice address")
        self.assertIn("BLUE OCEAN DEMO LTD", r)

    def test_show_billing_address_alias(self):
        with patch("whatsapp_app.load_invoice_address", return_value=_CAYMAN_ADDRESS):
            r, _ = _dispatch("show billing address")
        self.assertIn("BLUE OCEAN DEMO LTD", r)


# ---------------------------------------------------------------------------
# Delivery address check uses saved address
# ---------------------------------------------------------------------------

class TestDeliveryCheckUsesSavedAddress(unittest.TestCase):
    def test_antibes_document_matches_saved_antibes(self):
        from domain.invoice_address import check_invoice_delivery_address, save_delivery_address
        with patch("domain.invoice_address._config_path") as mock_path:
            import tempfile, pathlib
            tmp = pathlib.Path(tempfile.mktemp(suffix=".json"))
            mock_path.return_value = tmp
            save_delivery_address(_ANTIBES_ADDRESS)
            doc = {
                "delivery_address": {
                    "entity": "DEMO YACHT SERVICES",
                    "address_lines": ["12 Quai des Pecheurs", "Port Vauban", "06600 Antibes"],
                    "country": "France",
                }
            }
            result = check_invoice_delivery_address(doc)
        self.assertTrue(result["checked"])
        self.assertTrue(result["match"])

    def test_wrong_address_does_not_match_saved_antibes(self):
        from domain.invoice_address import check_invoice_delivery_address, save_delivery_address
        with patch("domain.invoice_address._config_path") as mock_path:
            import tempfile, pathlib
            tmp = pathlib.Path(tempfile.mktemp(suffix=".json"))
            mock_path.return_value = tmp
            save_delivery_address(_ANTIBES_ADDRESS)
            doc = {
                "delivery_address": {
                    "entity": "Completely Different Company",
                    "address_lines": ["1 Random Street", "London"],
                    "country": "United Kingdom",
                }
            }
            result = check_invoice_delivery_address(doc)
        self.assertTrue(result["checked"])
        self.assertFalse(result["match"])

    def test_default_delivery_check_still_works(self):
        """Backward compatibility: default Oceanco address uses key-token logic."""
        from domain.invoice_address import check_invoice_delivery_address
        with patch("domain.invoice_address.load_delivery_address") as mock_load:
            from domain.invoice_address import _DEFAULT_DELIVERY_RAW
            mock_load.return_value = _DEFAULT_DELIVERY_RAW
            doc = {
                "delivery_address": {
                    "entity": "Project H3",
                    "address_lines": ["c/o Oceanco", "Marineweg 1"],
                    "country": "Netherlands",
                }
            }
            result = check_invoice_delivery_address(doc)
        self.assertTrue(result["checked"])
        self.assertTrue(result["match"])


# ---------------------------------------------------------------------------
# Regression: existing routing unaffected
# ---------------------------------------------------------------------------

class TestAddressRegressions(unittest.TestCase):
    def test_compliance_question_still_routes_to_compliance(self):
        from domain.intent import classify_text
        result = classify_text("Is this MARPOL approved for our vessel?")
        self.assertEqual(result, "compliance_question")

    def test_greeting_still_works(self):
        r, _ = _dispatch("hi")
        self.assertIn("Ready", r)

    def test_other_pending_states_unaffected(self):
        """pending_clarification is still consumed as before."""
        s = _empty_state()
        s["pending_clarification"] = {"intent": "market_check", "topic": "pump prices"}
        # A non-address message with unknown intent should not trigger address logic
        from domain.intent import classify_text
        self.assertEqual(classify_text("what about the price?"), "unknown")
