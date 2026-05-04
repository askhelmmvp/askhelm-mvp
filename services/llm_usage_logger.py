"""
Lightweight per-call usage logger for Anthropic Claude API calls.

Appends one JSON line per call to STORAGE_DIR/logs/llm_usage.jsonl.
Never logs prompt or document text. Never raises — logging failures are
swallowed so they cannot crash the app.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pricing table  (USD per 1M tokens)
# ---------------------------------------------------------------------------
_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5":  {"input": 1.00, "output":  5.00},
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
}

_INPUT_TOKEN_WARNING_THRESHOLD  = 20_000
_OUTPUT_TOKEN_WARNING_THRESHOLD =  5_000


def _resolve_log_path() -> Path:
    from config import STORAGE_DIR
    return Path(STORAGE_DIR) / "logs" / "llm_usage.jsonl"


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> tuple[float, bool]:
    """
    Return (cost_usd, pricing_assumed).
    pricing_assumed is True when the model is not in the known pricing table.
    """
    pricing = _PRICING.get(model)
    assumed = pricing is None
    if assumed:
        pricing = _PRICING["claude-sonnet-4-6"]
    cost = (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000
    return cost, assumed


def log_llm_call(
    feature: str,
    response,
    model: str,
    error: Optional[Exception] = None,
) -> None:
    """
    Log one Anthropic API call.

    Parameters
    ----------
    feature   : short name identifying the call site, e.g. "extract_document"
    response  : the Anthropic Message response object, or None on failure
    model     : the model string passed to the API call
    error     : exception from a failed call, or None for success
    """
    try:
        entry: dict = {
            "timestamp_iso": datetime.now(timezone.utc).isoformat(),
            "feature": feature,
            "model": model,
            "success": error is None and response is not None,
        }

        if error is not None:
            entry["error"] = type(error).__name__

        if response is not None and error is None:
            usage = getattr(response, "usage", None)
            input_tokens  = getattr(usage, "input_tokens",  0) if usage else 0
            output_tokens = getattr(usage, "output_tokens", 0) if usage else 0
            cost, assumed = estimate_cost(model, input_tokens, output_tokens)

            entry["input_tokens"]  = input_tokens
            entry["output_tokens"] = output_tokens
            entry["estimated_cost_usd"] = round(cost, 6)
            entry["warning_large_input"]  = input_tokens  > _INPUT_TOKEN_WARNING_THRESHOLD
            entry["warning_large_output"] = output_tokens > _OUTPUT_TOKEN_WARNING_THRESHOLD
            if assumed:
                entry["pricing_assumed"] = True

            msg_id = getattr(response, "id", None)
            if msg_id:
                entry["request_id"] = msg_id

        log_path = _resolve_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    except Exception as exc:
        logger.warning("llm_usage_logger: failed to write log entry: %s", exc)
