import re

_NEW_SESSION_EXACT = {
    "new quote",
    "new comparison",
    "start new comparison",
    "this is a different supplier",
    "separate job",
    "fresh start",
    "new job",
    "different job",
    "reset",
    "start over",
    "clear",
}

_QUOTE_COMPARE_SUBSTRINGS = [
    "compare these quotes",
    "compare quotes",
    "compare these 3 quotes",
    "compare 3 quotes",
    "compare all quotes",
    "compare two quotes",
    "compare 2 quotes",
    "compare the quotes",
    "compare supplier quotes",
    "which quote is better",
    "which quote should i choose",
    "which supplier is better",
    "which is cheaper",
    "which is cheapest",
    "pick the best quote",
    "show differences between quotes",
]

_FOLLOW_UPS = {
    "why is it higher": "why_higher",
    "show added items": "show_added",
    "show missing items": "show_missing",
    "what should i do": "what_to_do",
    "show extraction": "show_extraction",
    "show extracted data": "show_extraction",
    "what did you extract": "show_extraction",
}

# Phrases that request follow-up actions/clarification after a compliance answer.
# Routing checks last_context to decide whether to send to compliance or commercial.
_COMPLIANCE_FOLLOWUP_EXACT = {
    "what now",
    "next steps",
    "what does this mean",
    "what are the next steps",
    "what are my next steps",
    "what do i do now",
    "what do we do",
    "what do we do now",
}

_GREETINGS = {"hi", "hello", "start", "hey"}

# ---------------------------------------------------------------------------
# Market price check classification
# ---------------------------------------------------------------------------

# Substring triggers: matched anywhere in the lowercased message.
# Checked BEFORE compliance substrings so pricing questions ("is this expensive",
# "is this reasonable") are not accidentally routed to the compliance engine.
_MARKET_CHECK_SUBSTRINGS = [
    "is this a fair price",
    "is that a fair price",
    "fair price for",
    "is this reasonable",
    "is that reasonable",
    "reasonable price for",
    "does this look expensive",
    "is this overpriced",
    "is that overpriced",
    "what should this cost",
    "what should that cost",
    "ballpark cost for",
    "market price for",
    "typical cost for",
    "expected cost for",
    "is this good value",
    "is that good value",
]

# Regex patterns: catch "what should X cost", "how much should X be" and
# similar natural forms that can't be matched by a fixed substring.
_MARKET_CHECK_PATTERNS = [
    r"\bwhat should\b.{0,80}\bcost\b",
    r"\bwhat (would|does|will)\b.{0,60}\bcost\b",
    r"\bhow much (should|would|does|will|is|are)\b",
]

# ---------------------------------------------------------------------------
# Compliance classification
# ---------------------------------------------------------------------------

# Substring triggers: matched anywhere in the lowercased message.
# Checked after commercial intents so commercial routing always wins.
_COMPLIANCE_SUBSTRINGS = [
    # MARPOL Annex VI / emissions
    "marpol",
    "annex vi",
    "tier iii",
    "tier 3",
    "tier ii",
    "tier 2",
    "sulphur",
    "sulfur",
    "seca",
    "emission control area",
    # ISM Code
    "ism code",
    "ism say",
    "ism require",
    "non-conformity",
    "non conformity",
    "safety management system",
    # LYC / REG Yacht Code
    "lyc code",
    "lyc require",
    "reg yacht code",
    "yacht code",
    "liferaft",
    "life raft",
    # General compliance phrasing
    "are we compliant",
    "is this compliant",
    "compliance",
    "regulation",
    "requirement",
    "allowed",
    "permitted",
]

# Word-boundary patterns: used for short terms that would false-positive
# as substrings of common words (e.g. "eca" inside "because").
_COMPLIANCE_WORD_PATTERNS = [
    r"\beca\b",       # Emission Control Area abbreviation
    r"\bnox\b",       # Nitrogen oxides
    r"\blyx\b",       # alternate LYC abbreviation
]

# Question-shape patterns: catch compliance questions regardless of
# which specific regulation is mentioned.
_COMPLIANCE_QUESTION_PATTERNS = [
    r"\bdoes\b.{1,60}\bapply\b",           # "does X apply ..."
    r"\bdo\b.{1,30}\bapply\b",             # "do X apply ..."
    r"\bwhat does\b.{1,60}\bsay\b",        # "what does X say about ..."
    r"\bwhat do\b.{1,60}\brequire\b",      # "what do X require ..."
    r"\bis this (allowed|permitted)\b",     # "is this allowed/permitted"
    r"\bare we (allowed|permitted)\b",      # "are we allowed/permitted"
    r"\bwhat is required\b",               # "what is required for ..."
    r"\bwhat are the requirements\b",      # "what are the requirements ..."
    r"\bam i (required|obliged)\b",        # "am i required to ..."
    r"\bdo we need to comply\b",
    r"\bdo we (need|have) to\b.{1,40}\bregulat",
]

# Natural operational language patterns — detect compliance meaning without
# requiring specific regulation terminology.
_COMPLIANCE_NATURAL_PATTERNS = [
    r"\bis (this|that|it) (ok|okay|acceptable|fine)\b",
    r"\bis (this|that|it) (a problem|an issue|a concern|a violation|a deficiency)\b",
    r"\bcan we (operate|continue|sail|discharge|proceed|run)\b",
    r"\bwhat happens if\b",
    r"\bis this against\b",
    r"\bare we (ok|okay|fine|in trouble|at risk)\b",
    r"\bis (this|it) (safe|unsafe|dangerous|an? (risk|hazard))\b",
    r"\bis (this|that|it) (a )?(breach|offence|offense|violation)\b",
]

# ---------------------------------------------------------------------------
# Heuristic: maintenance/operational state + safety equipment
# ---------------------------------------------------------------------------

# Indicates that something is overdue, missed, or needs attention.
# Only triggers compliance classification when combined with safety equipment.
_MAINTENANCE_STATE = [
    "overdue",
    "not done",
    "not completed",
    "hasn't been",
    "have not",
    "haven't",
    "has not been",
    "missed",
    "behind on",
    "failed to",
    "out of date",
    "expired",
    "past due",
    "not tested",
    "not inspected",
    "not serviced",
    "test",         # only fires when combined with safety equipment below
    "maintenance",
    "inspection",
    "service",
]

# Onboard safety equipment and systems whose status is compliance-relevant.
_SAFETY_EQUIPMENT = [
    "fire pump",
    "fire main",
    "fire alarm",
    "fire extinguisher",
    "fire detection",
    "co2 system",
    "lifeboat",
    "life boat",
    "rescue boat",
    "immersion suit",
    "lifejacket",
    "life jacket",
    "epirb",
    "sart",
    "smoke detector",
    "smoke alarm",
    "bilge pump",
    "bilge alarm",
    "emergency generator",
    "emergency lighting",
    "muster station",
    "watertight door",
    "fire damper",
]

# ---------------------------------------------------------------------------
# Question fallback guard
# ---------------------------------------------------------------------------

# If a message looks like a question but contains these commercial/document
# keywords, do NOT fall back to compliance — leave it for other routing.
_COMMERCIAL_GUARD = {
    "price",
    "quote",
    "invoice",
    "cost",
    "budget",
    "comparison",
    "cheaper",
    "expensive",
    "offer",
    "payment",
    "billing",
    "charge",
    "upload",
    "pdf",
    "file",
    "document",
    "attachment",
}

# Message starters that indicate an open question.
_QUESTION_STARTERS = (
    "is ", "are ", "does ", "do ", "can ", "will ", "should ",
    "was ", "were ", "has ", "have ", "had ",
    "what ", "why ", "how ", "when ", "where ",
)


def _is_open_question(t: str) -> bool:
    return t.endswith("?") or t.startswith(_QUESTION_STARTERS)


def classify_text(text: str) -> str:
    """
    Returns one of:
      new_session | quote_compare | why_higher | show_added |
      show_missing | what_to_do | show_extraction | compliance_followup |
      compliance_question | market_check | greeting | unknown
    """
    t = text.strip().lower()

    if t in _NEW_SESSION_EXACT:
        return "new_session"

    for phrase in _NEW_SESSION_EXACT:
        if len(phrase) > 4 and t.startswith(phrase):
            return "new_session"

    if t in _FOLLOW_UPS:
        return _FOLLOW_UPS[t]

    if t in _COMPLIANCE_FOLLOWUP_EXACT:
        return "compliance_followup"

    for trigger in _QUOTE_COMPARE_SUBSTRINGS:
        if trigger in t:
            return "quote_compare"

    # Market price check — before compliance so pricing questions ("is this expensive",
    # "is this reasonable") are not mis-routed to the compliance engine.
    for trigger in _MARKET_CHECK_SUBSTRINGS:
        if trigger in t:
            return "market_check"

    for pattern in _MARKET_CHECK_PATTERNS:
        if re.search(pattern, t):
            return "market_check"

    # Compliance — checked after commercial intents, before generic fallback
    for trigger in _COMPLIANCE_SUBSTRINGS:
        if trigger in t:
            return "compliance_question"

    for pattern in _COMPLIANCE_WORD_PATTERNS + _COMPLIANCE_QUESTION_PATTERNS:
        if re.search(pattern, t):
            return "compliance_question"

    # Natural compliance language patterns
    for pattern in _COMPLIANCE_NATURAL_PATTERNS:
        if re.search(pattern, t):
            return "compliance_question"

    # Heuristic: maintenance/operational state AND safety equipment present
    if (
        any(m in t for m in _MAINTENANCE_STATE)
        and any(s in t for s in _SAFETY_EQUIPMENT)
    ):
        return "compliance_question"

    if t in _GREETINGS:
        return "greeting"

    # Question fallback: open question, not clearly commercial/document-related
    # Routes unknown questions to the compliance engine (returns "not covered"
    # if irrelevant) rather than the generic "TEXT RECEIVED" response.
    if _is_open_question(t) and not any(g in t for g in _COMMERCIAL_GUARD):
        return "compliance_question"

    return "unknown"
