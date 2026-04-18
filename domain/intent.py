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


def classify_text(text: str) -> str:
    """
    Returns one of:
      new_session | quote_compare | why_higher | show_added |
      show_missing | what_to_do | compliance_question | greeting | unknown
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

    # Compliance — checked after commercial intents, before generic fallback
    for trigger in _COMPLIANCE_SUBSTRINGS:
        if trigger in t:
            return "compliance_question"

    for pattern in _COMPLIANCE_WORD_PATTERNS + _COMPLIANCE_QUESTION_PATTERNS:
        if re.search(pattern, t):
            return "compliance_question"

    if t in _GREETINGS:
        return "greeting"

    return "unknown"
