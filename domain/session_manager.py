import uuid
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple

logger = logging.getLogger(__name__)

AUTO_MATCH_THRESHOLD = 60
AMBIGUOUS_THRESHOLD = 30
MAX_QUOTES_PER_SESSION = 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_fingerprint(line_items: list) -> str:
    descs = sorted(
        item.get("description", "").strip().lower()
        for item in line_items
        if item.get("description")
    )
    return hashlib.md5("|".join(descs).encode()).hexdigest()


def _update_session(session: dict, state: dict) -> dict:
    state["sessions"] = [
        session if s["session_id"] == session["session_id"] else s
        for s in state["sessions"]
    ]
    return state


# ---------------------------------------------------------------------------
# Document record
# ---------------------------------------------------------------------------

def make_document_record(extracted: dict, file_path: str) -> dict:
    line_items = extracted.get("line_items") or []
    return {
        "document_id": str(uuid.uuid4()),
        "file_path": file_path,
        "doc_type": extracted.get("doc_type") or "unknown",
        "supplier_name": (extracted.get("supplier_name") or "").strip(),
        "document_number": (extracted.get("document_number") or "").strip(),
        "document_date": (extracted.get("document_date") or "").strip(),
        "currency": (extracted.get("currency") or "").strip().upper(),
        "total": extracted.get("total"),
        "subtotal": extracted.get("subtotal"),
        "tax": extracted.get("tax"),
        "line_items": line_items,
        "exclusions": extracted.get("exclusions") or [],
        "assumptions": extracted.get("assumptions") or [],
        "fingerprint": make_fingerprint(line_items),
        "status": "new",
        "uploaded_at": _now(),
        "session_id": None,
    }


# ---------------------------------------------------------------------------
# Session creation helpers
# ---------------------------------------------------------------------------

def _make_session(session_type: str, anchor_doc_id: str) -> dict:
    return {
        "session_id": str(uuid.uuid4()),
        "session_type": session_type,
        "status": "active",
        "document_ids": [anchor_doc_id],
        "anchor_doc_id": anchor_doc_id,
        "created_at": _now(),
        "updated_at": _now(),
        "last_comparison": None,
    }


# ---------------------------------------------------------------------------
# State accessors
# ---------------------------------------------------------------------------

def get_active_session(state: dict) -> Optional[dict]:
    active_id = state.get("active_session_id")
    if not active_id:
        return None
    return next((s for s in state["sessions"] if s["session_id"] == active_id), None)


def get_session_docs(session: dict, state: dict) -> List[dict]:
    by_id = {d["document_id"]: d for d in state.get("documents", [])}
    return [by_id[did] for did in session["document_ids"] if did in by_id]


def get_doc(doc_id: str, state: dict) -> Optional[dict]:
    return next((d for d in state["documents"] if d["document_id"] == doc_id), None)


# ---------------------------------------------------------------------------
# Session mutations
# ---------------------------------------------------------------------------

def create_quote_session(doc_record: dict, state: dict) -> Tuple[dict, dict]:
    """Always starts a new session for a quote. Returns (updated_state, session)."""
    session = _make_session("pending", doc_record["document_id"])
    doc_record = {**doc_record, "session_id": session["session_id"], "status": "in_session"}
    state["sessions"].append(session)
    state["documents"].append(doc_record)
    state["active_session_id"] = session["session_id"]
    logger.info("New quote session %s created for %s", session["session_id"], doc_record.get("supplier_name"))
    return state, session


def attach_invoice_to_session(doc_record: dict, session: dict, state: dict) -> Tuple[dict, dict]:
    """Attach an invoice to a matched quote session."""
    session = {**session}
    session["document_ids"] = session["document_ids"] + [doc_record["document_id"]]
    session["session_type"] = "quote_vs_invoice"
    session["updated_at"] = _now()
    doc_record = {**doc_record, "session_id": session["session_id"], "status": "in_session"}
    state["documents"].append(doc_record)
    state = _update_session(session, state)
    state["active_session_id"] = session["session_id"]
    logger.info("Invoice attached to session %s (supplier: %s)", session["session_id"], doc_record.get("supplier_name"))
    return state, session


def create_pending_session(doc_record: dict, state: dict) -> Tuple[dict, dict]:
    """Create a pending session for an unmatched document."""
    session = _make_session("pending", doc_record["document_id"])
    doc_record = {**doc_record, "session_id": session["session_id"], "status": "in_session"}
    state["sessions"].append(session)
    state["documents"].append(doc_record)
    state["active_session_id"] = session["session_id"]
    logger.info("Pending session %s created for unmatched doc from %s", session["session_id"], doc_record.get("supplier_name"))
    return state, session


def reset_user_sessions(state: dict) -> dict:
    """Close all sessions and clear the active pointer."""
    for s in state["sessions"]:
        s["status"] = "closed"
    state["active_session_id"] = None
    logger.info("All sessions reset for user %s", state.get("user_id"))
    return state


def store_comparison_result(
    session: dict, state: dict, doc_a: dict, doc_b: dict, comparison: dict
) -> dict:
    session = {**session, "last_comparison": {"doc_a": doc_a, "doc_b": doc_b, "comparison": comparison}, "updated_at": _now()}
    return _update_session(session, state)


def create_quote_vs_quote_session(quote_docs: List[dict], state: dict) -> Tuple[dict, dict]:
    """
    Build a new quote_vs_quote session from a list of quote doc records.
    Closes any currently active session first.
    """
    active = get_active_session(state)
    if active:
        active = {**active, "status": "closed"}
        state = _update_session(active, state)

    anchor_id = quote_docs[0]["document_id"]
    session = _make_session("quote_vs_quote", anchor_id)
    session["document_ids"] = [d["document_id"] for d in quote_docs]

    for doc in quote_docs:
        for existing in state["documents"]:
            if existing["document_id"] == doc["document_id"]:
                existing["session_id"] = session["session_id"]
                existing["status"] = "in_session"

    state["sessions"].append(session)
    state["active_session_id"] = session["session_id"]
    logger.info(
        "quote_vs_quote session %s created with %d quotes: %s",
        session["session_id"],
        len(quote_docs),
        [d.get("supplier_name") for d in quote_docs],
    )
    return state, session


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def score_invoice_against_session(
    invoice_doc: dict, session: dict, state: dict
) -> Tuple[int, List[str]]:
    """
    Score an invoice against a session's anchor quote.
    Returns (score 0-100, list of reasons).

    Weights:
      supplier match        0-30
      reference linkage     0-25
      similar totals        0-20
      line item overlap     0-15
      date proximity        0-10
    """
    anchor = get_doc(session["anchor_doc_id"], state)
    if anchor is None or anchor.get("doc_type") != "quote":
        return 0, ["no valid anchor quote in session"]

    score = 0
    reasons: List[str] = []

    # 1. Supplier name (0-30)
    inv_sup = invoice_doc.get("supplier_name", "").strip().lower()
    qte_sup = anchor.get("supplier_name", "").strip().lower()
    if inv_sup and qte_sup:
        if inv_sup == qte_sup:
            score += 30
            reasons.append(f"exact supplier match: '{inv_sup}'")
        elif inv_sup in qte_sup or qte_sup in inv_sup:
            score += 20
            reasons.append(f"partial supplier match: '{inv_sup}' ~ '{qte_sup}'")
        else:
            reasons.append(f"supplier mismatch: '{inv_sup}' vs '{qte_sup}'")

    # 2. Document reference linkage (0-25)
    inv_ref = invoice_doc.get("document_number", "").strip().lower()
    qte_ref = anchor.get("document_number", "").strip().lower()
    if inv_ref and qte_ref and (
        inv_ref == qte_ref or inv_ref in qte_ref or qte_ref in inv_ref
    ):
        score += 25
        reasons.append(f"reference linkage: '{inv_ref}' ~ '{qte_ref}'")

    # 3. Similar totals (0-20)
    inv_total = invoice_doc.get("total")
    qte_total = anchor.get("total")
    if inv_total and qte_total and qte_total != 0:
        ratio = abs(inv_total - qte_total) / abs(qte_total)
        if ratio < 0.05:
            score += 20
            reasons.append(f"totals nearly identical: {inv_total} vs {qte_total}")
        elif ratio < 0.20:
            score += 10
            reasons.append(f"totals close ({ratio * 100:.0f}% diff): {inv_total} vs {qte_total}")
        else:
            reasons.append(f"totals diverge ({ratio * 100:.0f}% diff)")

    # 4. Line item overlap (0-15)
    inv_descs = {
        i.get("description", "").strip().lower()
        for i in invoice_doc.get("line_items", [])
        if i.get("description")
    }
    qte_descs = {
        i.get("description", "").strip().lower()
        for i in anchor.get("line_items", [])
        if i.get("description")
    }
    if inv_descs and qte_descs:
        overlap = len(inv_descs & qte_descs) / max(len(inv_descs), len(qte_descs))
        pts = round(overlap * 15)
        if pts > 0:
            score += pts
            reasons.append(f"line item overlap {overlap * 100:.0f}%: +{pts}pts")

    # 5. Date proximity (0-10)
    try:
        d1 = datetime.fromisoformat(invoice_doc.get("document_date", ""))
        d2 = datetime.fromisoformat(anchor.get("document_date", ""))
        days = abs((d1 - d2).days)
        if days <= 90:
            score += 10
            reasons.append(f"dates {days} days apart")
        elif days <= 180:
            score += 5
            reasons.append(f"dates {days} days apart (+5pts)")
    except (ValueError, TypeError):
        pass

    return score, reasons


def find_best_matching_session(
    invoice_doc: dict, state: dict
) -> Tuple[Optional[str], int, List[str]]:
    """
    Find the best open quote session for an incoming invoice.
    Returns (session_id or None, score, reasons).
    """
    open_sessions = [
        s for s in state.get("sessions", [])
        if s["status"] == "active"
        and s["session_type"] in ("pending", "quote_vs_invoice")
        and len(s["document_ids"]) == 1
    ]

    best_id, best_score, best_reasons = None, 0, []
    for session in open_sessions:
        score, reasons = score_invoice_against_session(invoice_doc, session, state)
        logger.debug(
            "Session %s score=%d for invoice from %s: %s",
            session["session_id"], score, invoice_doc.get("supplier_name"), reasons,
        )
        if score > best_score:
            best_id, best_score, best_reasons = session["session_id"], score, reasons

    logger.info(
        "Best session match for invoice from '%s': session=%s score=%d",
        invoice_doc.get("supplier_name"), best_id, best_score,
    )
    return best_id, best_score, best_reasons


def gather_quote_docs_for_comparison(state: dict, max_quotes: int = MAX_QUOTES_PER_SESSION) -> List[dict]:
    """
    Gather quote docs from recent active sessions, newest first, up to max_quotes.
    Used when the user explicitly requests quote-vs-quote comparison.
    """
    by_id = {d["document_id"]: d for d in state.get("documents", [])}
    seen: set = set()
    quotes: List[dict] = []

    for session in reversed(state.get("sessions", [])):
        if session["status"] != "active":
            continue
        for did in session["document_ids"]:
            doc = by_id.get(did)
            if doc and doc.get("doc_type") == "quote" and did not in seen:
                seen.add(did)
                quotes.append(doc)
        if len(quotes) >= max_quotes:
            break

    return quotes[:max_quotes]
