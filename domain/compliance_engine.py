import logging

logger = logging.getLogger(__name__)

_retriever = None

# Minimum retrieval score to use a document answer directly.
# Queries scoring below this fall back to the operational playbook (which gives
# CE-style practical guidance for common safety topics without requiring source
# documents to be loaded).
_DOC_CONFIDENCE_THRESHOLD = 0.15


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


def answer_compliance_query(question: str) -> str:
    from domain.operational_playbook import lookup as playbook_lookup
    from services.anthropic_service import answer_compliance_question, NOT_COVERED_FALLBACK

    # 1. Try document retrieval first.
    try:
        retriever = _get_retriever()
        chunks = retriever.search(question, top_k=5, min_score=0.05)
    except Exception as exc:
        logger.exception("compliance_engine: retriever failed: %s", exc)
        # Retriever down — fall back to playbook, then fail gracefully.
        chunks = []

    top_score = chunks[0].get("score", 0.0) if chunks else 0.0

    # 2. High-confidence document match → try it first.
    if chunks and top_score >= _DOC_CONFIDENCE_THRESHOLD:
        logger.info(
            "compliance_engine: document answer — chunks=%d top_score=%.4f source=%r",
            len(chunks),
            top_score,
            chunks[0].get("source_reference", "")[:60],
        )
        doc_answer = answer_compliance_question(question, chunks)
        # If Claude determined the chunk doesn't actually answer the question,
        # fall through to playbook rather than returning NOT_COVERED.
        if not doc_answer.startswith("DECISION: Not explicitly covered"):
            return doc_answer
        logger.debug(
            "compliance_engine: document returned NOT_COVERED despite score=%.4f — trying playbook",
            top_score,
        )

    # 3. Low/no document match (or doc returned NOT_COVERED) → try operational playbook.
    playbook_answer = playbook_lookup(question)
    if playbook_answer:
        logger.debug(
            "compliance_engine: playbook fallback — top_score=%.4f question=%r",
            top_score, question[:60],
        )
        return playbook_answer

    # 4. Weak document match but no playbook → use whatever retrieval we have.
    if chunks:
        logger.info(
            "compliance_engine: low-confidence document answer — chunks=%d top_score=%.4f",
            len(chunks), top_score,
        )
        return answer_compliance_question(question, chunks)

    # 5. Nothing matched.
    logger.warning(
        "compliance_engine: no coverage — question=%r", question[:80]
    )
    return NOT_COVERED_FALLBACK


def answer_compliance_followup(topic: str) -> str:
    """Action-focused follow-up; re-retrieves context for the original topic."""
    from domain.operational_playbook import lookup as playbook_lookup
    from services.anthropic_service import answer_compliance_followup_question, NOT_COVERED_FALLBACK

    try:
        retriever = _get_retriever()
        chunks = retriever.search(topic, top_k=5, min_score=0.05)
    except Exception as exc:
        logger.exception("compliance_engine: retriever failed on follow-up: %s", exc)
        chunks = []

    top_score = chunks[0].get("score", 0.0) if chunks else 0.0

    if chunks and top_score >= _DOC_CONFIDENCE_THRESHOLD:
        followup = answer_compliance_followup_question(topic, chunks)
        if not followup.startswith("DECISION: Not explicitly covered"):
            return followup

    playbook_answer = playbook_lookup(topic)
    if playbook_answer:
        return playbook_answer

    if chunks:
        return answer_compliance_followup_question(topic, chunks)

    return NOT_COVERED_FALLBACK
