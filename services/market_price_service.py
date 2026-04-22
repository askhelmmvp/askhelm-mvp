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
# Assembly-scope detection
# Fired when the query covers a major mechanical assembly but has no model
# or drive identifier — we need that info before we can judge price fairly.
# ---------------------------------------------------------------------------

_STERN_DRIVE_SCOPE = frozenset([
    "transom", "stern drive", "sterndrive", "gimbal", "outdrive",
    "transom plate", "gimbal housing", "gimbal ring", "transom housing",
])

# Known drive/engine models and brands that act as identifiers
_DRIVE_MODEL_INDICATORS = [
    "zt370", "zt320", "zt280", "zt260", "zt240",
    "volvo penta", "volvo d", "volvo b",
    "mercruiser", "bravo", "alpha drive",
    "yanmar", "cummins", "caterpillar", "man ", " mtu",
    "nanni", "scania", "duoprop", "aquamatic",
]


def _is_stern_drive_scope_without_model(query: str) -> bool:
    """
    True when the query concerns stern drive / transom assembly work AND no
    drive model or part identifier is present.  Without a model number a
    reliable price check is impossible — we should ask first.
    """
    q = query.lower()
    has_scope = any(kw in q for kw in _STERN_DRIVE_SCOPE)
    if not has_scope:
        return False
    has_identifier = _has_part_number(query) or any(m in q for m in _DRIVE_MODEL_INDICATORS)
    return not has_identifier


_STERN_DRIVE_CONTEXT_RESPONSE = (
    "DECISION:\n"
    "MORE DETAIL NEEDED FOR A RELIABLE PRICE CHECK\n\n"
    "WHY:\n"
    "This appears to cover a stern drive transom / gimbal repair. "
    "Fair pricing depends mainly on the drive model and key assembly part numbers.\n\n"
    "RECOMMENDED ACTIONS:\n"
    "• Send the stern drive make/model (e.g. ZT370)\n"
    "• Send any transom plate or housing part number\n"
    "• Then I'll assess whether the parts and labour look fair"
)


# ---------------------------------------------------------------------------
# System prompt — three response modes, WhatsApp-concise
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a Chief Engineer. A crew member has asked about marine parts or service pricing via WhatsApp.

Pick ONE mode based on the query — nothing else:

MODE A — Exact OEM part number present, cannot verify exact market price:
CONFIDENCE:
insufficient_confidence

DECISION:
No reliable exact price confirmed

WHY:
I could not verify a strong market price from exact matches alone.

ACTIONS:
• Send the quoted price and I'll judge it
• Or get 2 quotes against the exact part number

MODE B — Generic item or service, no specific price given in the query:
CONFIDENCE:
similar_item_estimate

DECISION:
Broad estimate only

WHY:
Typical range is €X–€Y depending on [main variable].

ACTIONS:
• [One short clarifying question — ask only the minimum needed]

MODE C — A specific price appears in the query:
CONFIDENCE:
exact_match

DECISION:
<Reasonable / High / Low / Unclear>

WHY:
<one sentence max>

ACTIONS:
• <action 1>
• <action 2 — max 2 bullets>

RULES:
- Use MODE A when a specific OEM part number is present and you cannot confidently price it.
- Use MODE B when no price was given and the item is estimable (e.g. service type, general component).
- Use MODE C when a specific price appears in the question.
- If the query has a price but the item is too vague to assess: use MODE C with DECISION Unclear and one short clarifying question.
- WHY: one sentence only. No lists, no caveats, no padding.
- ACTIONS: max 2 bullets. For MODE B, one bullet is the clarifying question.
- Never give a price range when mode A applies.
- Tone: brief, practical. No preamble.
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
    """Returns (confidence_level, raw_text)."""
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


def _build_response(sections: dict) -> str:
    parts = []
    if "DECISION" in sections:
        parts.append(f"DECISION:\n{sections['DECISION']}")
    if "WHY" in sections:
        parts.append(f"WHY:\n{sections['WHY']}")
    if "ACTIONS" in sections:
        parts.append(f"ACTIONS:\n{sections['ACTIONS']}")
    return "\n\n".join(parts)


_INSUFFICIENT_ACTIONS = (
    "• Send the quoted price and I'll judge it\n"
    "• Or get 2 quotes against the exact part number"
)

_INSUFFICIENT_RESPONSE = (
    "DECISION:\nNo reliable exact price confirmed\n\n"
    "WHY:\nI could not verify a strong market price from exact matches alone.\n\n"
    f"ACTIONS:\n{_INSUFFICIENT_ACTIONS}"
)


def _enforce_insufficient(sections: dict) -> str:
    why = sections.get("WHY", "I could not verify a strong market price from exact matches alone.")
    return (
        f"DECISION:\nNo reliable exact price confirmed\n\n"
        f"WHY:\n{why}\n\n"
        f"ACTIONS:\n{_INSUFFICIENT_ACTIONS}"
    )


def _enforce_similar(sections: dict) -> str:
    why = sections.get("WHY", "Pricing varies by brand, model, and urgency.")
    actions = sections.get("ACTIONS", "• Send more details for a better estimate")
    return (
        f"DECISION:\nBroad estimate only\n\n"
        f"WHY:\n{why}\n\n"
        f"ACTIONS:\n{actions}"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_market_price(query: str, allow_broad_estimate: bool = False) -> str:
    """
    Assess whether a quoted price is fair for a marine part or service.

    allow_broad_estimate: when True (follow-up context where user has already
    acknowledged uncertainty), skips the part-number → insufficient downgrade
    so Claude can return a best-effort similar_item_estimate.
    """
    logger.info("Market check: query=%r allow_broad_estimate=%s", query[:120], allow_broad_estimate)
    query_has_part_number = _has_part_number(query)

    # Assembly-scope check: stern drive / transom work with no model → ask first.
    # Skipped on follow-up calls (allow_broad_estimate=True) so the user's reply
    # with model/part info is passed straight through to Claude.
    if not allow_broad_estimate and _is_stern_drive_scope_without_model(query):
        logger.info("Market check: stern drive scope without model identifier → requesting context")
        return _STERN_DRIVE_CONTEXT_RESPONSE

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": query}],
            timeout=60.0,
        )
        raw = response.content[0].text.strip()
        logger.info("Market check: response_length=%d", len(raw))

        confidence, raw = _parse_confidence(raw)
        sections = _parse_sections(raw)

        # Infer confidence from DECISION text if Claude omitted the CONFIDENCE line
        if confidence is None:
            decision = sections.get("DECISION", "").lower()
            if "no reliable" in decision or "unclear" in decision or "not confidently" in decision:
                confidence = CONFIDENCE_INSUFFICIENT
            elif "broad estimate" in decision or "estimate only" in decision or "similar" in decision:
                confidence = CONFIDENCE_SIMILAR
            else:
                confidence = CONFIDENCE_EXACT if not query_has_part_number else CONFIDENCE_SIMILAR

        # Downgrade: specific part number + only similar data → insufficient.
        # Use the fully standardized response to prevent any price ranges leaking through.
        # Skipped when allow_broad_estimate=True (user has explicitly asked for a best guess).
        if query_has_part_number and confidence == CONFIDENCE_SIMILAR and not allow_broad_estimate:
            logger.info("Market check: downgrading similar→insufficient (part number present)")
            return _INSUFFICIENT_RESPONSE

        if confidence == CONFIDENCE_INSUFFICIENT:
            return _enforce_insufficient(sections)

        if confidence == CONFIDENCE_SIMILAR:
            return _enforce_similar(sections)

        # exact_match — return Claude's response without the CONFIDENCE line
        return _build_response(sections)

    except Exception as exc:
        logger.exception("Market check failed: %s", exc)
        return _INSUFFICIENT_RESPONSE
