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

# Phrases that indicate the user wants a follow-up on a market price question.
# Routing in _handle_text_message checks last_context to confirm market_check context.
_MARKET_CHECK_FOLLOWUP_EXACT = {
    "ok give me an estimate",
    "give me an estimate",
    "just give me an estimate",
    "what do you think",
    "is that high",
    "is that low",
    "roughly what then",
    "best guess",
    "give me a range",
    "just a range",
    "ok roughly",
    "ok ballpark",
    "ballpark that",
    "roughly speaking",
    "what's your estimate",
    "what is your estimate",
    "any rough idea",
}

_MARKET_CHECK_FOLLOWUP_SUBSTRINGS = [
    "just give me a ballpark",
    "rough estimate",
    "rough range",
    "give me a rough",
]

# Phrases that are commercial procurement follow-ups (ordering, proceeding).
# Routing in _handle_text_message checks last_context to provide relevant commercial advice.
_COMMERCIAL_FOLLOWUP_SUBSTRINGS = [
    "how many should i order",
    "how many should we order",
    "how many to order",
    "should i order",
    "should we order",
    "should i proceed",
    "should we proceed",
    "should i buy",
    "should we buy",
    "ok to order",
    "safe to order",
    "is it worth ordering",
    "worth ordering",
    # Decision follow-ups: "go ahead", "approve", proceed variants
    "should i go ahead",
    "should we go ahead",
    "go ahead with this",
    "go ahead with it",
    "shall we go ahead",
    "ok to go ahead",
    "should i approve",
    "should we approve",
    "can we approve",
    "approve this",
    "approve the",
    "safe to approve",
]

# Short decision phrases matched exactly (after stripping trailing punctuation).
_COMMERCIAL_FOLLOWUP_EXACT = {
    "approve",
    "approved",
    "go ahead",
    "go ahead then",
    "proceed",
    "proceed then",
    "let's go ahead",
    "lets go ahead",
    "yes go ahead",
    "yes proceed",
}

_GREETINGS = {"hi", "hello", "start", "hey"}

# ---------------------------------------------------------------------------
# Market price check classification
# ---------------------------------------------------------------------------

# Substring triggers: matched anywhere in the lowercased message.
# Checked BEFORE compliance substrings so pricing questions ("is this expensive",
# "is this reasonable") are not accidentally routed to the compliance engine.
_MARKET_CHECK_SUBSTRINGS = [
    # "is X a fair/reasonable/expensive" forms
    "is this a fair price",
    "is that a fair price",
    "fair price for",
    "is this reasonable",
    "is that reasonable",
    "reasonable price for",
    "does this look expensive",
    "is this overpriced",
    "is that overpriced",
    "is this good value",
    "is that good value",
    "is this expensive",
    "is that expensive",
    "are these expensive",
    # direct pricing queries
    "how much for",
    "how much is this",
    "how much are these",
    "cost of",
    "price for",
    "price of",
    "rough price",
    "what does it cost",
    "what's the cost",
    "what is the cost",
    "what's the price",
    "what is the price",
    # price quality / good-value queries
    "is this a good price",
    "is that a good price",
    "good price for",
    "is this good value",
    "is this value for money",
    "is this competitive",
    "is that competitive",
    "is this cheap",
    "is that cheap",
    # existing
    "what should this cost",
    "what should that cost",
    "ballpark cost for",
    "market price for",
    "typical cost for",
    "expected cost for",
]

# Regex patterns: cover natural forms that can't be matched by fixed substrings.
_MARKET_CHECK_PATTERNS = [
    r"\bwhat should\b.{0,80}\bcost\b",                           # "what should a windlass service cost"
    r"\bwhat (would|does|will)\b.{0,60}\bcost\b",                # "what would this cost"
    r"\bhow much (should|would|does|will|is|are|for)\b",         # "how much for/is/does X"
    r"\bis .{0,60}\b(reasonable|overpriced|fair|expensive|pricey|costly|good value|good price|competitive)\b",  # "is €4500 reasonable/expensive"
    r"\bshould (this|that|it|these)\b.{0,40}\bcost\b",           # "should this cost €400"
    r"\bhow much should (this|that|it|these)\b",                  # "how much should this be"
]

# ---------------------------------------------------------------------------
# Marine parts / OEM brand heuristic
# ---------------------------------------------------------------------------

# Known OEM engine and equipment manufacturers.
_OEM_BRANDS = {
    "yanmar", "mtu", "caterpillar", "danfoss", "nanni", "volvo penta",
    "kohler", "cummins", "detroit diesel", "mercury", "perkins",
    "john deere", "westerbeke", "jabsco", "vetus", "wartsila",
    "zf marine", "twin disc", "scania", "man diesel", "rolls royce",
    "northern lights", "onan", "sleipner", "ray marine", "furuno",
}

# Common marine mechanical parts whose presence implies a pricing question.
_MARINE_PART_WORDS = {
    "pump", "sensor", "valve", "joint", "compressor", "impeller",
    "bearing", "seal", "gasket", "filter", "belt", "injector",
    "alternator", "gearbox", "propeller", "shaft", "coupling",
    "thermostat", "intercooler", "turbocharger", "solenoid",
    "heat exchanger", "o-ring", "overhaul kit", "service kit",
    "repair kit", "spare part", "actuator", "transducer", "throttle",
    "fuel pump", "water pump", "oil pump", "sea pump", "bilge pump",
    "expansion tank", "heat exchanger",
}

# Part numbers: patterns like "196350-04061" or "NJ-1234/56"
_PART_NUMBER_RE = re.compile(r'\b[A-Z0-9]{2,}-[A-Z0-9]{3,}\b', re.IGNORECASE)

# Words that indicate the user is asking about pricing (not just mentioning a part).
_PRICING_WORDS = {
    "cost", "price", "much", "rate", "worth", "value",
    "expensive", "cheap", "cheapest", "budget", "quote",
}


def _is_marine_pricing_question(t: str) -> bool:
    """
    True when the query references a marine part, OEM brand, or part number
    AND contains explicit pricing intent. Catches statement-style queries like
    "yanmar 196350-04061 price" as well as questions like "caterpillar pump — how much?"

    Note: the caller wraps this in a compliance-substring guard, so regulatory
    questions ("how much are we allowed to discharge") never reach this function.
    """
    has_oem = any(brand in t for brand in _OEM_BRANDS)
    has_part = any(p in t for p in _MARINE_PART_WORDS)
    has_part_number = bool(_PART_NUMBER_RE.search(t))
    has_pricing = any(w in t for w in _PRICING_WORDS)

    if not (has_oem or has_part or has_part_number):
        return False

    # Part number alone in an open question implies a price lookup.
    if has_part_number and _is_open_question(t):
        return True

    # Part number + explicit pricing word — no question form needed.
    # "p/n 196350-04061 price" is clearly a pricing query.
    if has_part_number and has_pricing:
        return True

    # OEM brand or part word + explicit pricing word.
    # Question form not required: "yanmar pump price" is unambiguous.
    if (has_oem or has_part) and has_pricing:
        return True

    return False

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
    "order",
    "proceed",
    "approve",
    "go ahead",
}

# Message starters that indicate an open question.
_QUESTION_STARTERS = (
    "is ", "are ", "does ", "do ", "can ", "will ", "should ",
    "was ", "were ", "has ", "have ", "had ",
    "what ", "why ", "how ", "when ", "where ",
)


def _is_open_question(t: str) -> bool:
    return t.endswith("?") or t.startswith(_QUESTION_STARTERS)


_REMINDER_PREFIXES = (
    "!remindme",
    "!remind me",
    "remindme ",
    "remind me ",
    "set a reminder",
)


def classify_text(text: str) -> str:
    """
    Returns one of:
      new_session | quote_compare | why_higher | show_added |
      show_missing | what_to_do | show_extraction | compliance_followup |
      commercial_followup | compliance_question | market_check | reminder |
      greeting | unknown
    """
    t = text.strip().lower()
    # Strip trailing punctuation for exact-match lookups so "what should i do?"
    # and "what should i do" both resolve to the same intent.
    t_core = t.rstrip("?!").strip()

    for prefix in _REMINDER_PREFIXES:
        if t.startswith(prefix):
            return "reminder"

    if t in _NEW_SESSION_EXACT:
        return "new_session"

    for phrase in _NEW_SESSION_EXACT:
        if len(phrase) > 4 and t.startswith(phrase):
            return "new_session"

    if t_core in _FOLLOW_UPS:
        return _FOLLOW_UPS[t_core]

    if t_core in _COMPLIANCE_FOLLOWUP_EXACT:
        return "compliance_followup"

    if t_core in _MARKET_CHECK_FOLLOWUP_EXACT:
        return "market_check_followup"

    for phrase in _MARKET_CHECK_FOLLOWUP_SUBSTRINGS:
        if phrase in t:
            return "market_check_followup"

    for phrase in _COMMERCIAL_FOLLOWUP_SUBSTRINGS:
        if phrase in t:
            return "commercial_followup"

    if t_core in _COMMERCIAL_FOLLOWUP_EXACT:
        return "commercial_followup"

    for trigger in _QUOTE_COMPARE_SUBSTRINGS:
        if trigger in t:
            return "quote_compare"

    # Market price check — before compliance so pricing questions ("is this expensive",
    # "is this reasonable", "how much for X") are not mis-routed to the compliance
    # engine. A compliance-substring guard prevents regulatory questions (e.g. "how
    # much are we allowed to discharge") from being wrongly classified here.
    if not any(c in t for c in _COMPLIANCE_SUBSTRINGS):
        for trigger in _MARKET_CHECK_SUBSTRINGS:
            if trigger in t:
                return "market_check"

        for pattern in _MARKET_CHECK_PATTERNS:
            if re.search(pattern, t):
                return "market_check"

        if _is_marine_pricing_question(t):
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
