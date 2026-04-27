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

_COMMODITY_KEYWORDS = frozenset(["filter", "matting", "bolt", "nut", "washer", "consumables"])


def _is_commodity_item(query: str) -> bool:
    """True when query describes a commodity/consumable — price can be estimated without exact OEM validation."""
    q = query.lower()
    return any(kw in q for kw in _COMMODITY_KEYWORDS)


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
# OEM context detection
# Fired when a part number is present alongside a known brand or system type.
# Enables a cautious OEM judgment instead of a generic "cannot verify" response.
# ---------------------------------------------------------------------------

_OEM_BRAND_KEYWORDS = frozenset([
    "yanmar", "mtu", "caterpillar", "danfoss", "nanni",
    "volvo penta", "kohler", "cummins", "perkins", "westerbeke",
    "jabsco", "vetus", "wartsila", "zf marine", "twin disc",
    "scania", "man diesel", "northern lights", "onan", "sleipner",
    "parker", "hem", "spectra", "dessalator", "idromar",
    "alfa laval", "facet", "racor", "fleetguard",
    "mercruiser", "john deere", "grundfos",
])

# Marine systems and kit types specific enough to imply OEM supply
_COMPONENT_TYPE_KEYWORDS = frozenset([
    "watermaker", "water maker",
    "ows", "oily water separator", "oil water separator",
    "service kit", "overhaul kit", "repair kit", "seal kit",
    "service pack", "service set",
    "impeller", "injector", "turbocharger", "intercooler",
    "heat exchanger", "gearbox", "thruster", "windlass",
    "genset", "membrane", "shaft seal", "lip seal", "solenoid",
])


def _has_oem_context(query: str) -> bool:
    """
    True when the query contains a recognised OEM brand or marine system type
    alongside the part number — enough context for a cautious OEM judgment.
    """
    q = query.lower()
    if any(brand in q for brand in _OEM_BRAND_KEYWORDS):
        return True
    if any(comp in q for comp in _COMPONENT_TYPE_KEYWORDS):
        return True
    return False


_OEM_ASSESSMENT_PROMPT = """\
You are a Chief Engineer assessing OEM marine parts pricing via WhatsApp.

A specific OEM part number is present. Exact market price data is unavailable,
but you have enough context (brand, component type, or system) to give a
cautious, grounded judgment.

RULES:
- Do NOT state or invent specific prices or price ranges.
- Reason from the component type, OEM brand behaviour, and system criticality.
- DECISION must be exactly one of:
    LIKELY ACCEPTABLE — OEM PRICING
    HIGH — CHECK REQUIRED
    UNUSUALLY CHEAP — VERIFY SOURCE
- Use LIKELY ACCEPTABLE when OEM parts for this component are typically
  single-source and priced at a manufacturer premium.
- Use HIGH — CHECK REQUIRED when the component type or brand suggests the
  price (if given) may warrant a second quote or negotiation.
- Use UNUSUALLY CHEAP — VERIFY SOURCE only when a price is given that seems
  well below OEM norms for the brand — flag authenticity or supply risk.
- When no price is given in the query, use LIKELY ACCEPTABLE — OEM PRICING.
- WHY: one sentence. End with "Confidence: \U0001f7e0 MEDIUM".
- RECOMMENDED ACTIONS: max 2 bullets. Be practical and specific to the part.
- Tone: concise, Chief Engineer. No preamble.

Respond in this exact format:
DECISION:
<decision>

WHY:
<one sentence ending with Confidence: \U0001f7e0 MEDIUM>

RECOMMENDED ACTIONS:
• <action 1>
• <action 2>
"""


def _assess_oem_part_price(query: str) -> str:
    """
    Cautious OEM pricing judgment when brand/component context exists but
    exact market price data is unavailable.
    """
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            system=_OEM_ASSESSMENT_PROMPT,
            messages=[{"role": "user", "content": query}],
            timeout=60.0,
        )
        result = response.content[0].text.strip()
        if result:
            logger.info("OEM assessment: response_length=%d", len(result))
            return result
        logger.warning("OEM assessment: empty response, using fallback")
    except Exception as exc:
        logger.exception("OEM assessment failed: %s", exc)
    return _INSUFFICIENT_RESPONSE


# ---------------------------------------------------------------------------
# System prompt — three response modes, WhatsApp-concise
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a Chief Engineer. A crew member has asked about marine parts or service pricing via WhatsApp.

Pick ONE mode based on the query — nothing else:

MODE A — Exact OEM part number present, cannot verify exact market price:
DECISION:
INSUFFICIENT DATA

WHY:
I could not verify a market price for this exact part number. Confidence: \U0001f534 LOW

ACTIONS:
• Send the quoted price and I'll judge it
• Or get 2 quotes against the exact part number

MODE B — Generic item or service, no specific price given in the query:
DECISION:
INSUFFICIENT DATA

WHY:
Typical range is €X–€Y depending on [main variable] — need more detail to assess. Confidence: \U0001f7e0 MEDIUM

ACTIONS:
• [One short clarifying question — ask only the minimum needed]

MODE C — A specific price appears in the query:
DECISION:
<ACCEPTABLE PRICE | HIGH PRICE — QUERY | LOW PRICE — OPPORTUNITY | INSUFFICIENT DATA>

WHY:
<one sentence max — end with "Confidence: \U0001f7e2 HIGH / \U0001f7e0 MEDIUM / \U0001f534 LOW">

ACTIONS:
• <action 1>
• <action 2 — max 2 bullets>

RULES:
- Use MODE A when a specific OEM part number is present and you cannot confidently price it.
- Use MODE B when no price was given and the item is estimable (e.g. service type, general component).
- Use MODE C when a specific price appears in the question.
- If the query has a price but the item is too vague to assess: use MODE C with DECISION INSUFFICIENT DATA and one short clarifying question.
- DECISION must always be one of: ACCEPTABLE PRICE, HIGH PRICE — QUERY, LOW PRICE — OPPORTUNITY, INSUFFICIENT DATA. Never use "High", "Low", "Reasonable", "Unclear", or any other label.
- WHY: one sentence only. End with "Confidence: \U0001f7e2 HIGH", "Confidence: \U0001f7e0 MEDIUM", or "Confidence: \U0001f534 LOW". No lists, no caveats, no padding.
- ACTIONS: max 2 bullets. For MODE B, one bullet is the clarifying question.
- Never include CONFIDENCE: as a separate section — confidence belongs only inside the WHY sentence.
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
    "DECISION:\nINSUFFICIENT DATA\n\n"
    "WHY:\nI could not verify a market price for this exact part number. Confidence: \U0001f534 LOW\n\n"
    f"ACTIONS:\n{_INSUFFICIENT_ACTIONS}"
)


def _enforce_insufficient(sections: dict) -> str:
    why = sections.get("WHY", "I could not verify a market price for this exact part number. Confidence: \U0001f534 LOW")
    return (
        f"DECISION:\nINSUFFICIENT DATA\n\n"
        f"WHY:\n{why}\n\n"
        f"ACTIONS:\n{_INSUFFICIENT_ACTIONS}"
    )


def _enforce_similar(sections: dict) -> str:
    why = sections.get("WHY", "Pricing varies by brand, model, and urgency. Confidence: \U0001f7e0 MEDIUM")
    actions = sections.get("ACTIONS", "• Send more details for a better estimate")
    return (
        f"DECISION:\nINSUFFICIENT DATA\n\n"
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

        # Infer confidence from DECISION text (new prompt omits CONFIDENCE section entirely)
        if confidence is None:
            decision = sections.get("DECISION", "").lower()
            if "insufficient" in decision or "unclear" in decision:
                confidence = CONFIDENCE_INSUFFICIENT
            elif "acceptable price" in decision or "high price" in decision or "low price" in decision:
                confidence = CONFIDENCE_EXACT
            else:
                confidence = CONFIDENCE_EXACT if not query_has_part_number else CONFIDENCE_SIMILAR

        # Downgrade: specific part number + only similar data → insufficient.
        # Exception: when OEM brand/component context is present, use a cautious
        # OEM assessment instead of a generic "cannot verify" response.
        # Skipped when allow_broad_estimate=True (user has explicitly asked for a best guess).
        if query_has_part_number and confidence == CONFIDENCE_SIMILAR and not allow_broad_estimate:
            if _has_oem_context(query):
                logger.info("Market check: part number + OEM context → OEM assessment (similar)")
                return _assess_oem_part_price(query)
            logger.info("Market check: downgrading similar→insufficient (part number, no OEM context)")
            return _INSUFFICIENT_RESPONSE

        if confidence == CONFIDENCE_INSUFFICIENT:
            # For commodity items pass Claude's WHY through instead of the generic
            # "Send the quoted price" instruction — the price is already in context.
            if _is_commodity_item(query):
                result = _build_response(sections)
                if result.strip():
                    return result
            # OEM part with brand/component context: give cautious judgment instead of
            # "cannot verify" — only when not already in broad-estimate mode.
            if query_has_part_number and _has_oem_context(query):
                logger.info("Market check: part number + OEM context → OEM assessment (insufficient)")
                return _assess_oem_part_price(query)
            return _enforce_insufficient(sections)

        if confidence == CONFIDENCE_SIMILAR:
            return _enforce_similar(sections)

        # exact_match — return Claude's response without the CONFIDENCE line
        result = _build_response(sections)
        if not result.strip():
            logger.warning("Market check: empty parsed response (sections=%s), using fallback", list(sections.keys()))
            return _INSUFFICIENT_RESPONSE
        return result

    except Exception as exc:
        logger.exception("Market check failed: %s", exc)
        return _INSUFFICIENT_RESPONSE

# ---------------------------------------------------------------------------
# Commercial follow-up advice (post market price assessment)
# ---------------------------------------------------------------------------

_COMMERCIAL_FOLLOWUP_SYSTEM = """You are a Chief Engineer advising crew on a procurement decision via WhatsApp.

The crew member has received a market price assessment and is now asking a follow-up
procurement question such as "what should I do?" or "how many should I order?".

Respond in this exact format:

DECISION:
<PROCEED — ORDER REQUIRED | HOLD — QUERY FIRST | ACCEPTABLE — ORDER AT DISCRETION | INVESTIGATE FURTHER>

WHY:
<one sentence — be specific to the item if context is available>

RECOMMENDED ACTIONS:
• <action 1>
• <action 2>
• <action 3 — optional, omit if not needed>

RULES:
- Choose PROCEED — ORDER REQUIRED when price was acceptable and the item is clearly needed.
- Choose HOLD — QUERY FIRST when price was high or the quote needs challenge before ordering.
- Choose ACCEPTABLE — ORDER AT DISCRETION when price was acceptable but quantity is flexible.
- Choose INVESTIGATE FURTHER when more information is needed before deciding.
- For quantity questions: default is 1 unit plus 1 spare minimum; adjust for critical systems.
- WHY: one sentence only. Be specific to the item if known. No lists, no caveats.
- RECOMMENDED ACTIONS: max 3 bullets. Be specific to the item if context allows.
- Tone: brief, direct. No preamble.
"""


def commercial_followup_advice(query: str, context_summary: str) -> str:
    """
    Generate a procurement decision for a follow-up question such as
    "what should I do?" or "how many should I order?" using whatever
    commercial context (market price result, component details) is available.
    """
    full_query = f"{context_summary}\n\nUser follow-up: {query}" if context_summary else query
    logger.info("Commercial followup: query=%r ctx_length=%d", query[:80], len(context_summary))
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            system=_COMMERCIAL_FOLLOWUP_SYSTEM,
            messages=[{"role": "user", "content": full_query}],
            timeout=60.0,
        )
        return response.content[0].text.strip()
    except Exception as exc:
        logger.exception("Commercial followup advice failed: %s", exc)
        return (
            "DECISION:\nPROCEED — VERIFY FIRST\n\n"
            "WHY:\nUnable to generate specific advice — verify price and scope before ordering.\n\n"
            "RECOMMENDED ACTIONS:\n"
            "• Confirm the quoted price is acceptable\n"
            "• Check vessel requirements for quantity\n"
            "• Place order once satisfied"
        )
