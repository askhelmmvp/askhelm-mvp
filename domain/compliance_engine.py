import logging
import re

logger = logging.getLogger(__name__)

_retriever = None

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
    ("ISM Code",        re.compile(r'\bism\s+code\b', re.I)),
    ("SOLAS",           re.compile(r'\bsolas\b', re.I)),
    ("Large Yacht Code", re.compile(r'\b(?:lyc\s+code|large\s+yacht\s+code|lyx\s+code)\b', re.I)),
]

# Alternative search terms tried when the original query scores below threshold.
_REGULATION_EXPANSIONS = {
    "SOLAS": [
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
    ],
    "Large Yacht Code": [
        "Large Yacht Code safety requirements fire equipment",
        "yacht code construction stability lifesaving",
    ],
}


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
    """Force the retriever singleton to reload on next use (call after index rebuild)."""
    global _retriever
    _retriever = None
    logger.info("compliance_engine: retriever reset — will reload on next query")


def answer_compliance_query(question: str, yacht_id: str = "h3") -> str:
    from domain.operational_playbook import lookup as playbook_lookup
    from services.anthropic_service import answer_compliance_question, NOT_COVERED_FALLBACK
    from services.compliance_profile import get_selected_regulations

    # 1. Try document retrieval (global + yacht-specific).
    selected = []
    try:
        selected = get_selected_regulations(yacht_id)
        chunks, top_score = _try_retrieval(question, yacht_id, selected)
    except Exception as exc:
        logger.exception("compliance_engine: retriever failed: %s", exc)
        chunks, top_score = [], 0.0

    # 2. High-confidence document match → try source answer.
    _doc_tried = False
    if chunks and top_score >= _DOC_CONFIDENCE_THRESHOLD:
        _doc_tried = True
        logger.info(
            "compliance_engine: document answer — chunks=%d top_score=%.4f source=%r",
            len(chunks), top_score, chunks[0].get("source_reference", "")[:60],
        )
        doc_answer = answer_compliance_question(question, chunks)
        if not doc_answer.startswith("DECISION: Not explicitly covered"):
            return _cap_compliance_answer(doc_answer)
        logger.debug(
            "compliance_engine: document returned NOT_COVERED despite score=%.4f — trying expansion",
            top_score,
        )

    # 3. Named regulation detected — try expanded queries when original retrieval is weak.
    reg_name = _detect_named_regulation(question)
    if reg_name and top_score < _DOC_CONFIDENCE_THRESHOLD:
        for expansion in _REGULATION_EXPANSIONS.get(reg_name, []):
            try:
                exp_chunks, exp_score = _try_retrieval(expansion, yacht_id, selected)
            except Exception:
                continue
            if exp_score >= _DOC_CONFIDENCE_THRESHOLD:
                logger.info(
                    "compliance_engine: expansion hit — reg=%r expansion=%r score=%.4f",
                    reg_name, expansion[:50], exp_score,
                )
                doc_answer = answer_compliance_question(question, exp_chunks)
                if not doc_answer.startswith("DECISION: Not explicitly covered"):
                    return _cap_compliance_answer(doc_answer)
                break  # one successful retrieval attempt is enough

    # 4. Operational playbook fallback.
    playbook_answer = playbook_lookup(question)
    if playbook_answer:
        logger.debug(
            "compliance_engine: playbook fallback — top_score=%.4f question=%r",
            top_score, question[:60],
        )
        return _cap_compliance_answer(playbook_answer)

    # 5. Weak document match but no playbook → use best available chunks (if not yet tried).
    if chunks and not _doc_tried:
        logger.info(
            "compliance_engine: low-confidence document answer — chunks=%d top_score=%.4f",
            len(chunks), top_score,
        )
        doc_answer = answer_compliance_question(question, chunks)
        if not doc_answer.startswith("DECISION: Not explicitly covered"):
            return _cap_compliance_answer(doc_answer)

    # 6. Named regulation but retrieval failed → general guidance fallback.
    if reg_name:
        from services.anthropic_service import answer_compliance_general_guidance
        from services.compliance_ingest import list_sources
        loaded_names = [s["source"] for s in list_sources()]
        is_loaded = any(reg_name.lower() in s.lower() for s in loaded_names)
        logger.info(
            "compliance_engine: general guidance fallback — reg=%r is_loaded=%s",
            reg_name, is_loaded,
        )
        guidance = answer_compliance_general_guidance(question, reg_name, is_loaded)
        return _cap_compliance_answer(guidance)

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
