_retriever = None


def _get_retriever():
    global _retriever
    if _retriever is None:
        from services.askhelm_retriever import AskHelmComplianceRetriever
        _retriever = AskHelmComplianceRetriever()
    return _retriever


def answer_compliance_query(question: str) -> str:
    from domain.operational_playbook import lookup as playbook_lookup
    from services.anthropic_service import answer_compliance_question

    # Operational playbook: handles common safety-critical topics with CE-style
    # practical guidance even when the exact source document is not loaded.
    # Returns None when no topic matches — fall through to RAG.
    playbook_answer = playbook_lookup(question)
    if playbook_answer:
        return playbook_answer

    try:
        retriever = _get_retriever()
        chunks = retriever.search(question, top_k=5, min_score=0.08)
    except Exception:
        return (
            "DECISION: Cannot confirm — knowledge base unavailable.\n"
            "WHY: Compliance index failed to load.\n"
            "SOURCE: N/A\n"
            "ACTIONS: Check the knowledge base index file and restart the service."
        )

    if not chunks:
        from services.anthropic_service import NOT_COVERED_FALLBACK
        return NOT_COVERED_FALLBACK

    return answer_compliance_question(question, chunks)


def answer_compliance_followup(topic: str) -> str:
    """Re-retrieves context for the original topic; returns action-focused follow-up only."""
    from domain.operational_playbook import lookup as playbook_lookup
    from services.anthropic_service import answer_compliance_followup_question, NOT_COVERED_FALLBACK

    # Check playbook first for follow-up questions on safety topics.
    playbook_answer = playbook_lookup(topic)
    if playbook_answer:
        return playbook_answer

    try:
        retriever = _get_retriever()
        chunks = retriever.search(topic, top_k=5, min_score=0.08)
    except Exception:
        return (
            "DECISION: Cannot confirm — knowledge base unavailable.\n"
            "WHY:\nCompliance index failed to load.\n"
            "ACTIONS:\n• Check the knowledge base index file and restart the service."
        )

    if not chunks:
        return NOT_COVERED_FALLBACK

    return answer_compliance_followup_question(topic, chunks)
