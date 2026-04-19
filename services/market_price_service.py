import re
import os
import logging
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv(dotenv_path=".env")

logger = logging.getLogger(__name__)
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# ---------------------------------------------------------------------------
# Confidence levels
# ---------------------------------------------------------------------------

CONFIDENCE_EXACT = "exact_match"
CONFIDENCE_SIMILAR = "similar_item_estimate"
CONFIDENCE_INSUFFICIENT = "insufficient_confidence"

# Matches part numbers like "196350-04061" or "NJ-1234/56"
_PART_NUMBER_RE = re.compile(r'\b[A-Z0-9]{2,}-[A-Z0-9]{3,}\b', re.IGNORECASE)

_PART_NUMBER_MARKERS = ("p/n", "part number", "part no", "part#", "pn:", "oem code")


def _has_part_number(query: str) -> bool:
    t = query.lower()
    if bool(_PART_NUMBER_RE.search(query)):
        return True
    return any(marker in t for marker in _PART_NUMBER_MARKERS)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a Chief Engineer with extensive experience in yacht procurement and marine parts pricing.
A crew member or owner has asked about pricing for a marine part or service.

Your job:
1. Identify the exact item or service from their question.
2. Assess your CONFIDENCE in the price data you can provide:
   - exact_match: You have reliable pricing data for this exact item (specific OEM part number, model, service type).
   - similar_item_estimate: You have data for similar/comparable items but not this exact one.
   - insufficient_confidence: The item is too specific (e.g. an exact OEM part number) for you to give a reliable price without verified sources, OR the query is too vague.
3. Provide appropriate pricing information based on your confidence level.

IMPORTANT — confidence assignment rules:
- If the query contains a specific OEM part number (e.g. "196350-04061", "NJ-1234"), you MUST use similar_item_estimate or insufficient_confidence. Never claim exact_match for a specific part number unless you have a verified market price for that exact code.
- If the query is for a generic service (e.g. "windlass service", "hull cleaning") without a specific model, similar_item_estimate is acceptable.
- Use insufficient_confidence when: the part number is too specific to price reliably, the item is highly variable, or you cannot give a useful range.

STRICT RULES:
- Be specific with numbers only when confidence is exact_match or similar_item_estimate.
- For insufficient_confidence: do NOT give price ranges. Direct the user to get quotes instead.
- Always acknowledge variability: marine pricing depends on brand, urgency, location, yacht size.
- Tone: cautious but useful. Chief Engineer style. No padding.

Respond in this EXACT format — nothing before or after:
CONFIDENCE:
<one of: exact_match / similar_item_estimate / insufficient_confidence>

DECISION:
<see rules below>
- exact_match: estimated market range with cautious wording (e.g. "Within expected range — typical cost €X–€Y")
- similar_item_estimate: "Estimate only — based on similar items, not exact verified match"
- insufficient_confidence: "Unclear — exact market price not confidently verified"

WHY:
<one or two sentences: what you found and why confidence is what it is>

ACTIONS:
• <action 1>
• <action 2>
• <action 3>
• <action 4>
"""

# ---------------------------------------------------------------------------
# Parsing and enforcement
# ---------------------------------------------------------------------------

_CONFIDENCE_RE = re.compile(
    r'^CONFIDENCE:\s*\n(exact_match|similar_item_estimate|insufficient_confidence)',
    re.MULTILINE | re.IGNORECASE,
)

_SECTION_RE = re.compile(
    r'^(CONFIDENCE|DECISION|WHY|ACTIONS):\s*\n(.*?)(?=\n(?:CONFIDENCE|DECISION|WHY|ACTIONS):|$)',
    re.MULTILINE | re.DOTALL,
)


def _parse_confidence(raw: str) -> tuple:
    """Returns (confidence_level, raw_without_confidence_line)."""
    m = _CONFIDENCE_RE.search(raw)
    if m:
        level = m.group(1).strip().lower()
        return level, raw
    return None, raw


def _parse_sections(raw: str) -> dict:
    sections = {}
    for m in _SECTION_RE.finditer(raw):
        sections[m.group(1).upper()] = m.group(2).strip()
    return sections


def _build_response(sections: dict, confidence: str) -> str:
    parts = []
    if "DECISION" in sections:
        parts.append(f"DECISION:\n{sections['DECISION']}")
    if "WHY" in sections:
        parts.append(f"WHY:\n{sections['WHY']}")
    if "ACTIONS" in sections:
        parts.append(f"ACTIONS:\n{sections['ACTIONS']}")
    return "\n\n".join(parts)


_INSUFFICIENT_ACTIONS = (
    "• Get 2 quotes against the exact part number\n"
    "• Check OEM dealer pricing\n"
    "• Confirm whether an aftermarket equivalent is acceptable\n"
    "• Request an itemised breakdown from any supplier"
)

_INSUFFICIENT_RESPONSE = (
    "DECISION:\nUnclear — exact market price not confidently verified\n\n"
    "WHY:\nI could not confirm a reliable price for the exact part number from strong matching sources.\n\n"
    f"ACTIONS:\n{_INSUFFICIENT_ACTIONS}"
)


def _enforce_insufficient(sections: dict) -> str:
    why = sections.get("WHY", "I could not confirm a reliable price from strong matching sources.")
    return (
        f"DECISION:\nUnclear — exact market price not confidently verified\n\n"
        f"WHY:\n{why}\n\n"
        f"ACTIONS:\n{_INSUFFICIENT_ACTIONS}"
    )


def _enforce_similar(sections: dict) -> str:
    why = sections.get("WHY", "Pricing is based on comparable items, not this exact specification.")
    actions = sections.get("ACTIONS", "• Get at least 2 supplier quotes\n• Verify OEM vs aftermarket pricing")
    return (
        f"DECISION:\nEstimate only — based on similar items, not exact verified match\n\n"
        f"WHY:\n{why}\n\n"
        f"ACTIONS:\n{actions}"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_market_price(query: str) -> str:
    """
    Assess whether a quoted price is fair for a marine part or service.
    Applies confidence-level enforcement so specific part numbers never get
    false-precise price ranges.
    Returns a formatted DECISION / WHY / ACTIONS response.
    """
    logger.info("Market check: query=%r", query[:120])
    query_has_part_number = _has_part_number(query)

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": query}],
            timeout=60.0,
        )
        raw = response.content[0].text.strip()
        logger.info("Market check: response_length=%d", len(raw))

        confidence, raw = _parse_confidence(raw)
        sections = _parse_sections(raw)

        # Infer confidence from sections if missing from response
        if confidence is None:
            decision = sections.get("DECISION", "").lower()
            if "unclear" in decision or "not confidently" in decision:
                confidence = CONFIDENCE_INSUFFICIENT
            elif "estimate only" in decision or "similar" in decision:
                confidence = CONFIDENCE_SIMILAR
            else:
                confidence = CONFIDENCE_EXACT if not query_has_part_number else CONFIDENCE_SIMILAR

        # Downgrade: specific part number + only similar data → insufficient.
        # Use standardized WHY to avoid leaking Claude's uncertain price ranges.
        if query_has_part_number and confidence == CONFIDENCE_SIMILAR:
            logger.info("Market check: downgrading similar→insufficient (part number present)")
            return _INSUFFICIENT_RESPONSE

        if confidence == CONFIDENCE_INSUFFICIENT:
            return _enforce_insufficient(sections)

        if confidence == CONFIDENCE_SIMILAR:
            return _enforce_similar(sections)

        # exact_match: return Claude's full response, stripping the CONFIDENCE line
        return _build_response(sections, confidence)

    except Exception as exc:
        logger.exception("Market check failed: %s", exc)
        return _INSUFFICIENT_RESPONSE
