"""
Tests for scripts/ingest_global_regulations.py — bulk PDF ingest into
the global compliance knowledge base.
"""
import os
import importlib
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestSourceNameFromFilename(unittest.TestCase):
    """_source_name_from_filename converts PDF filenames to human-readable names."""

    def setUp(self):
        from scripts.ingest_global_regulations import _source_name_from_filename
        self.fn = _source_name_from_filename

    def test_underscore_to_space(self):
        self.assertEqual(self.fn("ISM_Code.pdf"), "ISM Code")

    def test_hyphen_to_space(self):
        self.assertEqual(self.fn("MARPOL-Annex-VI.pdf"), "MARPOL Annex VI")

    def test_space_preserved(self):
        self.assertEqual(self.fn("REG Yacht Code.pdf"), "REG Yacht Code")

    def test_mixed_separators(self):
        self.assertEqual(self.fn("MARPOL_Annex-I.pdf"), "MARPOL Annex I")

    def test_extension_case_insensitive(self):
        self.assertEqual(self.fn("ISM_Code.PDF"), "ISM Code")

    def test_no_extra_spaces(self):
        name = self.fn("ISM__Code.pdf")
        self.assertNotIn("  ", name)


class TestGlobalRegulationsDirPath(unittest.TestCase):
    """get_global_regulations_dir() returns the correct path under DATA_DIR."""

    def test_path_ends_in_global_regulations(self):
        from storage_paths import get_global_regulations_dir
        p = get_global_regulations_dir()
        self.assertTrue(str(p).endswith(os.path.join("global", "regulations")))

    def test_path_under_data_dir(self):
        from storage_paths import get_data_dir, get_global_regulations_dir
        self.assertTrue(str(get_global_regulations_dir()).startswith(str(get_data_dir())))


class TestIngestScript(unittest.TestCase):
    """Integration tests for the ingest script using a temp DATA_DIR."""

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

    def test_creates_regulations_dir_if_missing(self):
        from scripts.ingest_global_regulations import main
        from storage_paths import get_global_regulations_dir
        reg_dir = get_global_regulations_dir()
        self.assertFalse(reg_dir.exists())
        main()
        self.assertTrue(reg_dir.exists())

    def test_no_pdfs_prints_message(self):
        from scripts.ingest_global_regulations import main
        from io import StringIO
        with patch("sys.stdout", new_callable=StringIO) as out:
            main()
        self.assertIn("No PDF files found", out.getvalue())

    def test_pdf_produces_chunks_and_index(self):
        from scripts.ingest_global_regulations import main
        from storage_paths import (
            get_global_regulations_dir,
            get_compliance_chunks_path,
            get_compliance_index_path,
        )
        reg_dir = get_global_regulations_dir()
        reg_dir.mkdir(parents=True, exist_ok=True)
        # Placeholder file — real text provided via mock below
        (reg_dir / "Test_Regulation.pdf").write_bytes(b"%PDF-1.4 placeholder")

        sample_text = (
            "CHAPTER 1 — DEFINITIONS\n\n"
            "Vessel means any ship subject to this regulation.\n\n"
            "CHAPTER 2 — REQUIREMENTS\n\n"
            "The master shall maintain a log of all operations."
        )
        with patch("domain.extraction.extract_pdf_text", return_value=sample_text):
            main()

        self.assertTrue(get_compliance_chunks_path().exists())
        self.assertTrue(get_compliance_index_path().exists())

    def test_ingested_chunks_carry_correct_source(self):
        from scripts.ingest_global_regulations import main
        from storage_paths import get_global_regulations_dir
        from services.compliance_ingest import load_chunks
        reg_dir = get_global_regulations_dir()
        reg_dir.mkdir(parents=True, exist_ok=True)
        (reg_dir / "MARPOL_Annex_VI.pdf").write_bytes(b"%PDF-1.4 placeholder")

        sample_text = (
            "REGULATION 14 — SULPHUR\n\n"
            "The global sulphur cap is 0.50% m/m since 1 January 2020.\n\n"
            "REGULATION 13 — NOX\n\n"
            "Tier III applies in designated NOx ECAs."
        )
        with patch("domain.extraction.extract_pdf_text", return_value=sample_text):
            main()

        chunks = load_chunks()
        sources = {c.get("source") for c in chunks}
        self.assertIn("MARPOL Annex VI", sources)

    def test_failed_extraction_does_not_crash(self):
        from scripts.ingest_global_regulations import main
        from storage_paths import get_global_regulations_dir
        reg_dir = get_global_regulations_dir()
        reg_dir.mkdir(parents=True, exist_ok=True)
        (reg_dir / "bad_file.pdf").write_bytes(b"not a real pdf")

        with patch("domain.extraction.extract_pdf_text", side_effect=RuntimeError("parse error")):
            try:
                main()
            except Exception as exc:
                self.fail(f"main() raised an exception on extraction failure: {exc}")

    def test_second_run_replaces_chunks(self):
        """Re-running the script replaces chunks for the same PDF (no duplicates)."""
        from scripts.ingest_global_regulations import main
        from storage_paths import get_global_regulations_dir
        from services.compliance_ingest import load_chunks
        reg_dir = get_global_regulations_dir()
        reg_dir.mkdir(parents=True, exist_ok=True)
        (reg_dir / "ISM_Code.pdf").write_bytes(b"%PDF-1.4 placeholder")

        text = "CHAPTER 1\n\nDefinitions of the ISM Code.\n\nCHAPTER 2\n\nSafety policy."
        with patch("domain.extraction.extract_pdf_text", return_value=text):
            main()
        count_after_first = len(load_chunks())

        with patch("domain.extraction.extract_pdf_text", return_value=text):
            main()
        count_after_second = len(load_chunks())

        self.assertEqual(count_after_first, count_after_second,
                         "Re-running must not duplicate chunks")
