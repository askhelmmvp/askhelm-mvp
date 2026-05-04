"""Tests for services/llm_usage_logger.py."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_response(input_tokens: int, output_tokens: int, msg_id: str = "msg_abc123") -> MagicMock:
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    response = MagicMock()
    response.usage = usage
    response.id = msg_id
    return response


class TestEstimateCost(unittest.TestCase):

    def test_sonnet_cost(self):
        from services.llm_usage_logger import estimate_cost
        cost, assumed = estimate_cost("claude-sonnet-4-6", 10_000, 1_000)
        expected = (10_000 * 3.00 + 1_000 * 15.00) / 1_000_000
        self.assertAlmostEqual(cost, expected, places=7)
        self.assertFalse(assumed)

    def test_haiku_cost(self):
        from services.llm_usage_logger import estimate_cost
        cost, assumed = estimate_cost("claude-haiku-4-5", 10_000, 1_000)
        expected = (10_000 * 1.00 + 1_000 * 5.00) / 1_000_000
        self.assertAlmostEqual(cost, expected, places=7)
        self.assertFalse(assumed)

    def test_unknown_model_uses_sonnet_pricing_and_sets_assumed(self):
        from services.llm_usage_logger import estimate_cost
        cost_unknown, assumed = estimate_cost("claude-some-future-model", 10_000, 1_000)
        cost_sonnet, _ = estimate_cost("claude-sonnet-4-6", 10_000, 1_000)
        self.assertAlmostEqual(cost_unknown, cost_sonnet, places=7)
        self.assertTrue(assumed)

    def test_zero_tokens_gives_zero_cost(self):
        from services.llm_usage_logger import estimate_cost
        cost, _ = estimate_cost("claude-sonnet-4-6", 0, 0)
        self.assertEqual(cost, 0.0)


class TestLogLlmCall(unittest.TestCase):

    def _log_to_tmpdir(self, tmpdir: str):
        """Patch config.STORAGE_DIR to tmpdir and return expected log path."""
        return Path(tmpdir) / "logs" / "llm_usage.jsonl"

    def _read_last_entry(self, log_path: Path) -> dict:
        lines = log_path.read_text().strip().splitlines()
        return json.loads(lines[-1])

    def test_log_written_under_storage_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("services.llm_usage_logger._resolve_log_path",
                       return_value=Path(tmpdir) / "logs" / "llm_usage.jsonl"):
                from services.llm_usage_logger import log_llm_call
                log_llm_call("test_feature", _make_response(100, 50), "claude-sonnet-4-6")
            log_path = Path(tmpdir) / "logs" / "llm_usage.jsonl"
            self.assertTrue(log_path.exists())

    def test_successful_call_entry_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "logs" / "llm_usage.jsonl"
            with patch("services.llm_usage_logger._resolve_log_path", return_value=log_path):
                from services.llm_usage_logger import log_llm_call
                log_llm_call("extract_document", _make_response(500, 200), "claude-sonnet-4-6")
            entry = self._read_last_entry(log_path)
        self.assertTrue(entry["success"])
        self.assertEqual(entry["feature"], "extract_document")
        self.assertEqual(entry["model"], "claude-sonnet-4-6")
        self.assertEqual(entry["input_tokens"], 500)
        self.assertEqual(entry["output_tokens"], 200)
        self.assertIn("estimated_cost_usd", entry)
        self.assertIn("timestamp_iso", entry)
        self.assertEqual(entry["request_id"], "msg_abc123")

    def test_large_input_warning(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "logs" / "llm_usage.jsonl"
            with patch("services.llm_usage_logger._resolve_log_path", return_value=log_path):
                from services.llm_usage_logger import log_llm_call
                log_llm_call("big_call", _make_response(25_000, 100), "claude-sonnet-4-6")
            entry = self._read_last_entry(log_path)
        self.assertTrue(entry["warning_large_input"])
        self.assertFalse(entry["warning_large_output"])

    def test_large_input_warning_false_below_threshold(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "logs" / "llm_usage.jsonl"
            with patch("services.llm_usage_logger._resolve_log_path", return_value=log_path):
                from services.llm_usage_logger import log_llm_call
                log_llm_call("small_call", _make_response(100, 50), "claude-sonnet-4-6")
            entry = self._read_last_entry(log_path)
        self.assertFalse(entry["warning_large_input"])

    def test_failed_call_logs_error_without_cost(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "logs" / "llm_usage.jsonl"
            with patch("services.llm_usage_logger._resolve_log_path", return_value=log_path):
                from services.llm_usage_logger import log_llm_call
                log_llm_call("failing_feature", None, "claude-sonnet-4-6",
                             error=ValueError("timeout"))
            entry = self._read_last_entry(log_path)
        self.assertFalse(entry["success"])
        self.assertEqual(entry["error"], "ValueError")
        self.assertNotIn("estimated_cost_usd", entry)
        self.assertNotIn("input_tokens", entry)

    def test_unknown_model_sets_pricing_assumed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "logs" / "llm_usage.jsonl"
            with patch("services.llm_usage_logger._resolve_log_path", return_value=log_path):
                from services.llm_usage_logger import log_llm_call
                log_llm_call("some_feature", _make_response(100, 50), "claude-unknown-99")
            entry = self._read_last_entry(log_path)
        self.assertTrue(entry.get("pricing_assumed"))

    def test_no_prompt_or_document_content_in_entry(self):
        """Log entry must never contain prompt text, document text, or API keys."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "logs" / "llm_usage.jsonl"
            with patch("services.llm_usage_logger._resolve_log_path", return_value=log_path):
                from services.llm_usage_logger import log_llm_call
                log_llm_call("extract_document", _make_response(100, 50), "claude-sonnet-4-6")
            raw = log_path.read_text()
        allowed_keys = {
            "timestamp_iso", "feature", "model", "success", "input_tokens",
            "output_tokens", "estimated_cost_usd", "warning_large_input",
            "warning_large_output", "request_id", "pricing_assumed", "error",
        }
        entry = json.loads(raw.strip())
        unexpected = set(entry.keys()) - allowed_keys
        self.assertEqual(unexpected, set(), f"Unexpected keys in log entry: {unexpected}")

    def test_logger_does_not_crash_app_on_io_error(self):
        """A write failure must not raise — it must be silently swallowed."""
        with patch("services.llm_usage_logger._resolve_log_path",
                   side_effect=RuntimeError("disk full")):
            from services.llm_usage_logger import log_llm_call
            try:
                log_llm_call("feature", _make_response(10, 5), "claude-sonnet-4-6")
            except Exception as exc:
                self.fail(f"log_llm_call raised unexpectedly: {exc}")

    def test_multiple_entries_appended_to_same_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "logs" / "llm_usage.jsonl"
            with patch("services.llm_usage_logger._resolve_log_path", return_value=log_path):
                from services.llm_usage_logger import log_llm_call
                log_llm_call("feature_a", _make_response(100, 50), "claude-sonnet-4-6")
                log_llm_call("feature_b", _make_response(200, 80), "claude-haiku-4-5")
            lines = log_path.read_text().strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["feature"], "feature_a")
        self.assertEqual(json.loads(lines[1])["feature"], "feature_b")


if __name__ == "__main__":
    unittest.main()
