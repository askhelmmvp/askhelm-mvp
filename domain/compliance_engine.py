import hashlib
import logging
import re
import time

logger = logging.getLogger(__name__)

_retriever = None

# 24-hour in-process compliance response cache.
# Key: md5 of (yacht_id, role_context_prefix, normalised_question, sorted_selected_regs).
# Value: (answer, expires_at_unix).
# Cleared by reset_retriever() so an index rebuild also invalidates cached answers.
_compliance_cache: dict = {}
_CACHE_TTL = 86400  # seconds


def _make_cache_key(
    question: str, yacht_id: str, role_context: str, selected: list
) -> str:
    normalized = " ".join(question.lower().split())
    reg_key = ",".join(sorted(str(s) for s in selected))
    raw = f"{yacht_id}|{role_context[:50] if role_context else ''}|{normalized}|{reg_key}"
    return hashlib.md5(raw.encode()).hexdigest()

# Minimum retrieval score to use a document answer directly.
# Queries scoring below this fall back to the operational playbook (which gives
# CE-style practical guidance for common safety topics without requiring source
# documents to be loaded).
_DOC_CONFIDENCE_THRESHOLD = 0.15

# Hard limit for the answer body (before the "⚓ AskHelm \n\n" header is added).
# WhatsApp silently drops messages that are too long; 1175 chars + 12-char header ≤ 1200 target.
_WHATSAPP_ANSWER_LIMIT = 1175

# ---------------------------------------------------------------------------
# Named regulation detection — used to decide whether to offer general
# guidance when retrieval fails for a well-known regulation.
# ---------------------------------------------------------------------------

# Ordered most-specific first (e.g. "annex vi" before bare "annex v").
_NAMED_REGULATIONS = [
    ("MARPOL Annex VI", re.compile(r'\bmarpol\s+annex\s+vi\b', re.I)),
    ("MARPOL Annex IV", re.compile(r'\bmarpol\s+annex\s+iv\b', re.I)),
    ("MARPOL Annex V",  re.compile(r'\bmarpol\s+annex\s+v\b(?!i)', re.I)),
    ("MARPOL Annex I",  re.compile(r'\bmarpol\s+annex\s+(?:i|1)\b(?!v)', re.I)),
    ("ISM Code",        re.compile(r'\b(?:ism\s+code|ism\s+(?:say|says|chapter|maintenance|require|requires|audit))\b', re.I)),
    ("SOLAS",           re.compile(r'\bsolas\b', re.I)),
    ("Large Yacht Code", re.compile(r'\b(?:lyc\s+code|lyc|large\s+yacht\s+code|lyx\s+code|yacht\s+code)\b', re.I)),
    # Topic-inference entries — infer regulation from well-known terms when explicit name is absent.
    # Handles both American (sulfur) and British (sulphur) spellings via sul(?:f|ph)ur.
    ("MARPOL Annex VI", re.compile(
        r'\b(?:tier\s+(?:i{1,3}|[123])|eiapp|nox|neca|fuel\s+sul(?:f|ph)ur|'
        r'sul(?:f|ph)ur\s+(?:cap|limit|content|requirement)|emission\s+control\s+area|'
        r'eca\s+(?:requirement|requirements|rule|rules|standard|limit|sul(?:f|ph)ur|fuel)|'
        r'engine\s+air\s+pollution)\b', re.I,
    )),
]

# Matches "what does X say about..." / "what do X require..." — regulation source inquiries.
# Used to prevent the operational playbook from hijacking compliance source questions.
_REGULATION_SOURCE_INQUIRY_RE = re.compile(
    r'\bwhat (?:does|do)\b.{0,80}\b(?:say|says|require|requires|about|state|contain|cover)\b',
    re.I,
)

# Alternative search terms tried when the original query scores below threshold.
_REGULATION_EXPANSIONS = {
    "SOLAS": [
        "SOLAS Chapter II-2 structural fire protection A-60 A-class divisions fire rated bulkhead",
        "SOLAS Chapter II-2 fire dampers ventilation closures fire flaps fire safety",
        "SOLAS fire safety structural fire protection divisions",
        "SOLAS lifesaving appliances equipment liferaft lifeboat",
        "SOLAS construction ship safety stability",
        "SOLAS navigation radio communications safety equipment",
        "SOLAS chapter II fire detection suppression",
    ],
    "MARPOL Annex VI": [
        "MARPOL Annex VI NOx diesel engine Tier regulation 13",
        "MARPOL Annex VI sulphur SOx ECA emission control area regulation 14",
        "MARPOL Annex VI IAPP certificate fuel oil record BDN",
        "MARPOL Annex VI Tier III NOx emission standard NECA",
        "MARPOL Annex VI EIAPP certificate engine air pollution prevention",
        "MARPOL Annex VI ECA emission control area sulphur fuel requirement",
    ],
    "MARPOL Annex I": [
        "MARPOL Annex I oily water separator OWS OCM bilge discharge",
        "MARPOL Annex I oil record book ORB machinery space",
    ],
    "MARPOL Annex IV": [
        "MARPOL Annex IV sewage discharge holding tank treatment",
    ],
    "MARPOL Annex V": [
        "MARPOL Annex V garbage plastics food waste management",
    ],
    "ISM Code": [
        "ISM Code maintenance ship equipment non-conformity corrective action",
        "ISM Code company responsibilities DPA audit SMS",
        "ISM Code chapter 10 maintenance planned maintenance system",
    ],
    "Large Yacht Code": [
        "Large Yacht Code safety requirements fire equipment",
        "yacht code construction stability lifesaving",
        "Large Yacht Code fire pump fire main pressure requirements",
    ],
}


# Deterministic routing: topic-specific retrieval queries tried FIRST when the question
# contains well-known subject terms, before falling back to general expansion list.
# Each entry: (pattern, regulation_name, retrieval_query).
_TOPIC_DIRECT_QUERIES = [
    (re.compile(
        r'\b(?:fuel\s+sul(?:f|ph)ur|sul(?:f|ph)ur\s+(?:limits?|caps?)|sox|'
        r'eca\s+fuel|emission\s+control\s+area\s+fuel|fuel\s+oil\s+sul(?:f|ph)ur)\b', re.I,
    ), "MARPOL Annex VI",
     "MARPOL Annex VI sulphur SOx ECA emission control area regulation 14 fuel oil"),
    (re.compile(
        r'\b(?:nox|tier\s+(?:i{1,3}|[123])|eiapp|iapp|nox\s+technical\s+code|'
        r'diesel\s+engine\s+emission|neca)\b', re.I,
    ), "MARPOL Annex VI",
     "MARPOL Annex VI NOx Tier III EIAPP diesel engine regulation 13 NECA"),
    (re.compile(
        r'\b(?:a[-\s]?60|a[-\s]?class\s+division|bulkhead\s+fire\s+rating|'
        r'structural\s+fire\s+protection|fire[-\s]?rated\s+bulkhead)\b', re.I,
    ), "SOLAS",
     "SOLAS Chapter II-2 structural fire protection A-60 A-class divisions fire rating bulkhead"),
    (re.compile(
        r'\b(?:fire\s+dampers?|ventilation\s+damper|fire\s+flap|fire\s+ventilation\s+closure)\b',
        re.I,
    ), "SOLAS",
     "SOLAS Chapter II-2 fire dampers ventilation closures fire flaps fire safety"),
]


def _get_expansion_queries(reg_name: str, question: str) -> list:
    """Return expansion queries.
    When a direct-topic match exists, return only those queries so that A60 / fire-damper
    questions do not trigger unrelated SOLAS lifesaving/navigation/radio searches.
    When no direct match, fall back to the full general expansion list."""
    direct = [q for pat, reg, q in _TOPIC_DIRECT_QUERIES if reg == reg_name and pat.search(question)]
    if direct:
        return direct
    return _REGULATION_EXPANSIONS.get(reg_name, [])


def _detect_named_regulation(question: str):
    """Return display name for the first known regulation found in question, or None."""
    t = question.lower()
    for name, pattern in _NAMED_REGULATIONS:
        if pattern.search(t):
            return name
    return None


def _try_retrieval(question: str, yacht_id: str, selected):
    """Run retrieval for one query. Raises on failure (caller must handle)."""
    retriever = _get_retriever()
    chunks = retriever.search_with_yacht(
        question,
        yacht_id=yacht_id,
        selected_regulations=selected if selected else None,
        top_k=5,
        min_score=0.05,
    )
    top_score = chunks[0].get("score", 0.0) if chunks else 0.0
    return chunks, top_score


def _cap_compliance_answer(text: str) -> str:
    if len(text) <= _WHATSAPP_ANSWER_LIMIT:
        return text
    truncated = text[:_WHATSAPP_ANSWER_LIMIT]
    last_newline = truncated.rfind("\n")
    if last_newline > _WHATSAPP_ANSWER_LIMIT // 2:
        truncated = truncated[:last_newline]
    return truncated.rstrip()


def _get_retriever():
    global _retriever
    if _retriever is None:
        from services.askhelm_retriever import AskHelmComplianceRetriever
        _retriever = AskHelmComplianceRetriever()
    return _retriever


def reset_retriever():
    """Force the retriever singleton to reload on next use (call after index rebuild).
    Also clears the compliance response cache so stale answers are not served."""
    global _retriever
    _retriever = None
    _compliance_cache.clear()
    logger.info("compliance_engine: retriever reset + cache cleared — will reload on next query")


def answer_compliance_query(
    question: str, yacht_id: str = "h3", role_context: str = ""
) -> str:
    from domain.operational_playbook import lookup as playbook_lookup
    from services.anthropic_service import answer_compliance_question, NOT_COVERED_FALLBACK
    from services.compliance_profile import get_selected_regulations

    # Role context is kept out of the retrieval query — prepending it degrades TF-IDF scores.
    # It is passed to the LLM to influence answer tone only.
    _llm_q = f"{role_context}\n\n{question}" if role_context else question

    # Detect named regulation early — needed for playbook guard and expansion queries.
    reg_name = _detect_named_regulation(question)

    # Load profile first — needed for cache key and retrieval filter.
    selected = []
    try:
        selected = get_selected_regulations(yacht_id)
    except Exception as exc:
        logger.exception("compliance_engine: failed to load selected regulations: %s", exc)

    # Cache check — skip expensive retrieval + LLM for repeated identical queries.
    _cache_k = _make_cache_key(question, yacht_id, role_context, selected)
    _cached = _compliance_cache.get(_cache_k)
    if _cached and time.time() < _cached[1]:
        logger.debug("compliance_engine: cache hit — key=%s", _cache_k[:8])
        return _cached[0]

    def _done(answer: str) -> str:
        """Cache and return a compliance answer."""
        _compliance_cache[_cache_k] = (answer, time.time() + _CACHE_TTL)
        return answer

    # Include detected regulation in retrieval even if absent from the yacht's
    # profile — a user asking explicitly about SOLAS must not have SOLAS filtered out.
    _retrieval_selected = list(selected)
    if reg_name and not any(reg_name.lower() in s.lower() for s in _retrieval_selected):
        _retrieval_selected.append(reg_name)

    # 1. Try document retrieval with the raw question only (not role-prefixed).
    chunks, top_score = [], 0.0
    try:
        chunks, top_score = _try_retrieval(question, yacht_id, _retrieval_selected)
    except Exception as exc:
        logger.exception("compliance_engine: retriever failed: %s", exc)

    # 2. High-confidence document match → one LLM call.
    _doc_tried = False
    if chunks and top_score >= _DOC_CONFIDENCE_THRESHOLD:
        _doc_tried = True
        logger.info(
            "compliance_engine: document answer — chunks=%d top_score=%.4f source=%r",
            len(chunks), top_score, chunks[0].get("source_reference", "")[:60],
        )
        doc_answer = answer_compliance_question(_llm_q, chunks)
        if not doc_answer.startswith("DECISION: Not explicitly covered"):
            return _done(_cap_compliance_answer(doc_answer))
        logger.debug(
            "compliance_engine: document returned NOT_COVERED despite score=%.4f — trying expansion",
            top_score,
        )

    # 3. Named regulation — collect ALL expansion scores (TF-IDF only), then ONE LLM call.
    # _get_expansion_queries returns only direct-routed queries when a topic match exists,
    # so A60/fire-damper questions do not trigger unrelated SOLAS lifesaving/navigation searches.
    _best_exp_chunks: list = []
    _best_exp_score = 0.0
    if reg_name and top_score < _DOC_CONFIDENCE_THRESHOLD:
        for expansion in _get_expansion_queries(reg_name, question):
            try:
                exp_chunks, exp_score = _try_retrieval(expansion, yacht_id, _retrieval_selected)
            except Exception:
                continue
            if exp_score >= _DOC_CONFIDENCE_THRESHOLD and exp_score > _best_exp_score:
                _best_exp_chunks = exp_chunks
                _best_exp_score = exp_score
                logger.info(
                    "compliance_engine: expansion hit — reg=%r expansion=%r score=%.4f",
                    reg_name, expansion[:50], exp_score,
                )
        # One final Anthropic call with the highest-scoring expansion chunks.
        if _best_exp_chunks:
            doc_answer = answer_compliance_question(_llm_q, _best_exp_chunks)
            if not doc_answer.startswith("DECISION: Not explicitly covered"):
                return _done(_cap_compliance_answer(doc_answer))

    # 4. Operational playbook fallback — skipped for regulation source inquiries to prevent
    # canned operational responses hijacking "what does X say about Y?" questions.
    if not (reg_name and _REGULATION_SOURCE_INQUIRY_RE.search(question)):
        playbook_answer = playbook_lookup(question)
        if playbook_answer:
            logger.debug(
                "compliance_engine: playbook fallback — top_score=%.4f question=%r",
                top_score, question[:60],
            )
            return _done(_cap_compliance_answer(playbook_answer))

    # 5. Weak initial chunks only — no expansion succeeded; try initial low-confidence results.
    if chunks and not _doc_tried and not _best_exp_chunks:
        logger.info(
            "compliance_engine: low-confidence document answer — chunks=%d top_score=%.4f",
            len(chunks), top_score,
        )
        doc_answer = answer_compliance_question(_llm_q, chunks)
        if not doc_answer.startswith("DECISION: Not explicitly covered"):
            return _done(_cap_compliance_answer(doc_answer))

    # 6. Named regulation but retrieval failed → general guidance fallback.
    if reg_name:
        from services.anthropic_service import answer_compliance_general_guidance
        from services.compliance_ingest import list_sources
        loaded_names = [s["source"] for s in list_sources()]
        is_loaded = any(reg_name.lower() in s.lower() for s in loaded_names)
        had_strong_hit = _best_exp_score >= _DOC_CONFIDENCE_THRESHOLD
        logger.info(
            "compliance_engine: general guidance fallback — reg=%r is_loaded=%s "
            "best_exp_score=%.4f had_strong_hit=%s",
            reg_name, is_loaded, _best_exp_score, had_strong_hit,
        )
        guidance = answer_compliance_general_guidance(
            _llm_q, reg_name, is_loaded, had_strong_hit=had_strong_hit
        )
        return _done(_cap_compliance_answer(guidance))

    # 7. Nothing matched.
    logger.warning("compliance_engine: no coverage — question=%r", question[:80])
    return NOT_COVERED_FALLBACK


def answer_compliance_followup(topic: str, yacht_id: str = "h3") -> str:
    """Action-focused follow-up; re-retrieves context for the original topic."""
    from domain.operational_playbook import lookup as playbook_lookup
    from services.anthropic_service import answer_compliance_followup_question, NOT_COVERED_FALLBACK

    try:
        retriever = _get_retriever()
        from services.compliance_profile import get_selected_regulations
        selected = get_selected_regulations(yacht_id)
        chunks = retriever.search_with_yacht(
            topic,
            yacht_id=yacht_id,
            selected_regulations=selected if selected else None,
            top_k=5,
            min_score=0.05,
        )
    except Exception as exc:
        logger.exception("compliance_engine: retriever failed on follow-up: %s", exc)
        chunks = []

    top_score = chunks[0].get("score", 0.0) if chunks else 0.0

    if chunks and top_score >= _DOC_CONFIDENCE_THRESHOLD:
        followup = answer_compliance_followup_question(topic, chunks)
        if not followup.startswith("DECISION: Not explicitly covered"):
            return _cap_compliance_answer(followup)

    playbook_answer = playbook_lookup(topic)
    if playbook_answer:
        return _cap_compliance_answer(playbook_answer)

    if chunks:
        return _cap_compliance_answer(answer_compliance_followup_question(topic, chunks))

    return NOT_COVERED_FALLBACK
