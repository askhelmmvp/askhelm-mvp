_retriever = None


def _get_retriever():
    global _retriever
    if _retriever is None:
        from services.askhelm_retriever import AskHelmComplianceRetriever
        _retriever = AskHelmComplianceRetriever()
    return _retriever


def answer_compliance_query(question: str) -> str:
    from services.anthropic_service import answer_compliance_question

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
    """Re-retrieves context from the original compliance topic, answers 'what should I do'."""
    from services.anthropic_service import answer_compliance_question, NOT_COVERED_FALLBACK

    try:
        retriever = _get_retriever()
        chunks = retriever.search(topic, top_k=5, min_score=0.08)
    except Exception:
        return (
            "DECISION: Cannot confirm — knowledge base unavailable.\n"
            "WHY: Compliance index failed to load.\n"
            "SOURCE: N/A\n"
            "ACTIONS: Check the knowledge base index file and restart the service."
        )

    if not chunks:
        return NOT_COVERED_FALLBACK

    followup_q = f"What actions are required regarding: {topic}"
    return answer_compliance_question(followup_q, chunks)
