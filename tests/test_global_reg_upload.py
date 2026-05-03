"""
Tests for ASK-18B — global regulation PDF upload via WhatsApp caption.
"""
import importlib
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch


class TestParseGlobalRegulationCaption(unittest.TestCase):
    """_parse_global_regulation_caption detects the trigger phrases."""

    def setUp(self):
        from whatsapp_app import _parse_global_regulation_caption
        self.fn = _parse_global_regulation_caption

    def test_upload_as_global_regulation_colon(self):
        self.assertEqual(self.fn("Upload as global regulation: ISM Code"), "ISM Code")

    def test_global_regulation_colon(self):
        self.assertEqual(self.fn("global regulation: MARPOL Annex VI"), "MARPOL Annex VI")

    def test_ingest_global_regulation_colon(self):
        self.assertEqual(self.fn("ingest global regulation: SOLAS"), "SOLAS")

    def test_case_insensitive(self):
        self.assertEqual(self.fn("UPLOAD AS GLOBAL REGULATION: ISM Code"), "ISM Code")

    def test_no_match_returns_none(self):
        self.assertIsNone(self.fn("Please find attached the invoice"))

    def test_empty_string_returns_none(self):
        self.assertIsNone(self.fn(""))

    def test_strips_whitespace(self):
        self.assertEqual(self.fn("global regulation:   REG Yacht Code  "), "REG Yacht Code")

    def test_dash_separator(self):
        self.assertEqual(self.fn("global regulation - MARPOL Annex I"), "MARPOL Annex I")

    def test_no_separator(self):
        self.assertEqual(self.fn("global regulation ISM Code"), "ISM Code")


class TestHandleGlobalRegulationUpload(unittest.TestCase):
    """_handle_global_regulation_upload saves PDF and ingests it."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.tmpdir
        import storage_paths
        importlib.reload(storage_paths)

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        import storage_paths
        importlib.reload(storage_paths)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_pdf(self):
        f = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=self.tmpdir)
        f.write(b"%PDF-1.4 placeholder")
        f.close()
        return f.name

    def test_returns_global_regulation_imported_decision(self):
        from whatsapp_app import _handle_global_regulation_upload
        pdf = self._make_pdf()
        with patch("whatsapp_app.ingest_compliance_pdf", return_value=5) as mock_ingest, \
             patch("whatsapp_app._reset_compliance_retriever"):
            answer, _ = _handle_global_regulation_upload(pdf, "ISM Code", {})
        self.assertIn("GLOBAL REGULATION IMPORTED", answer)

    def test_saves_pdf_to_regulations_dir(self):
        from whatsapp_app import _handle_global_regulation_upload
        from storage_paths import get_global_regulations_dir
        pdf = self._make_pdf()
        with patch("whatsapp_app.ingest_compliance_pdf", return_value=3), \
             patch("whatsapp_app._reset_compliance_retriever"):
            _handle_global_regulation_upload(pdf, "MARPOL Annex VI", {})
        dest = get_global_regulations_dir() / "marpol_annex_vi.pdf"
        self.assertTrue(dest.exists())

    def test_slug_normalisation(self):
        from whatsapp_app import _handle_global_regulation_upload
        from storage_paths import get_global_regulations_dir
        pdf = self._make_pdf()
        with patch("whatsapp_app.ingest_compliance_pdf", return_value=2), \
             patch("whatsapp_app._reset_compliance_retriever"):
            _handle_global_regulation_upload(pdf, "REG Yacht Code 2018", {})
        dest = get_global_regulations_dir() / "reg_yacht_code_2018.pdf"
        self.assertTrue(dest.exists())

    def test_calls_reset_retriever(self):
        from whatsapp_app import _handle_global_regulation_upload
        pdf = self._make_pdf()
        with patch("whatsapp_app.ingest_compliance_pdf", return_value=4), \
             patch("whatsapp_app._reset_compliance_retriever") as mock_reset:
            _handle_global_regulation_upload(pdf, "SOLAS", {})
        mock_reset.assert_called_once()

    def test_ingest_failure_returns_ingest_failed(self):
        from whatsapp_app import _handle_global_regulation_upload
        pdf = self._make_pdf()
        with patch("whatsapp_app.ingest_compliance_pdf", side_effect=RuntimeError("bad")), \
             patch("whatsapp_app._reset_compliance_retriever"):
            answer, _ = _handle_global_regulation_upload(pdf, "ISM Code", {})
        self.assertIn("INGEST FAILED", answer)

    def test_chunk_count_in_response(self):
        from whatsapp_app import _handle_global_regulation_upload
        pdf = self._make_pdf()
        with patch("whatsapp_app.ingest_compliance_pdf", return_value=42), \
             patch("whatsapp_app._reset_compliance_retriever"):
            answer, _ = _handle_global_regulation_upload(pdf, "ISM Code", {})
        self.assertIn("42", answer)


class TestGlobalRegWebhookRouting(unittest.TestCase):
    """Webhook routes global-regulation PDF captions without calling vision."""

    def _build_form(self, caption, pdf_bytes=b"%PDF-1.4 x"):
        import io
        return {
            "Body": caption,
            "From": "whatsapp:+15550001111",
            "NumMedia": "1",
            "MediaUrl0": "https://fake.twilio.com/media/test.pdf",
            "MediaContentType0": "application/pdf",
        }

    def test_vision_not_called_for_global_regulation_caption(self):
        from whatsapp_app import app
        form = self._build_form("global regulation: ISM Code")
        with app.test_request_context("/whatsapp", method="POST", data=form):
            with patch("whatsapp_app.download_file", return_value="/tmp/test_reg.pdf"), \
                 patch("whatsapp_app._looks_like_pdf", return_value=False), \
                 patch("whatsapp_app._handle_global_regulation_upload",
                       return_value=("GLOBAL REGULATION IMPORTED\nISM Code", {})) as mock_handler, \
                 patch("whatsapp_app.extract_commercial_document_from_images") as mock_vision, \
                 patch("whatsapp_app.render_pdf_pages_to_images") as mock_render, \
                 patch("whatsapp_app.load_user_state", return_value={}), \
                 patch("whatsapp_app.save_user_state"), \
                 patch("whatsapp_app.user_id_from_phone", return_value="u1"), \
                 patch("whatsapp_app._send_whatsapp_message"), \
                 patch("whatsapp_app._DOCUMENT_RECEIVED_ACK", "ACK"):
                from whatsapp_app import whatsapp_reply
                resp = whatsapp_reply()
        mock_vision.assert_not_called()
        mock_render.assert_not_called()
        mock_handler.assert_called_once()

    def test_handler_receives_reg_name_from_caption(self):
        from whatsapp_app import app
        form = self._build_form("Upload as global regulation: MARPOL Annex VI")
        with app.test_request_context("/whatsapp", method="POST", data=form):
            with patch("whatsapp_app.download_file", return_value="/tmp/test_reg.pdf"), \
                 patch("whatsapp_app._looks_like_pdf", return_value=False), \
                 patch("whatsapp_app._handle_global_regulation_upload",
                       return_value=("GLOBAL REGULATION IMPORTED\nMARPOL Annex VI", {})) as mock_handler, \
                 patch("whatsapp_app.load_user_state", return_value={}), \
                 patch("whatsapp_app.save_user_state"), \
                 patch("whatsapp_app.user_id_from_phone", return_value="u1"), \
                 patch("whatsapp_app._send_whatsapp_message"), \
                 patch("whatsapp_app._DOCUMENT_RECEIVED_ACK", "ACK"):
                from whatsapp_app import whatsapp_reply
                resp = whatsapp_reply()
        _, call_kwargs = mock_handler.call_args
        call_args = mock_handler.call_args[0]
        self.assertEqual(call_args[1], "MARPOL Annex VI")
