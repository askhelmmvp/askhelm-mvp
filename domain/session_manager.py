import re
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
# Description-matching helpers
# ---------------------------------------------------------------------------

def _normalize_desc(s: str) -> str:
    """
    Normalise a line-item description for resilient matching.
    Lowercase → strip punctuation → collapse whitespace.

    Handles common extraction/OCR variations such as:
      "ANTIFREEZE/CORR. 50/50 20L"  →  "antifreeze corr 50 50 20l"
      "Antifreeze Corr 50/50 20 L"  →  "antifreeze corr 50 50 20 l"
    Both reduce to something that can be compared by significant-word overlap.
    """
    s = s.strip().lower()
    s = re.sub(r'[^\w\s]', ' ', s)   # punctuation → space
    return re.sub(r'\s+', ' ', s).strip()


def _sig_words(s: str) -> set:
    """Words longer than 2 characters from a normalised description string."""
    return {w for w in s.split() if len(w) > 2}


def _desc_matches(a: str, b: str) -> bool:
    """
    True when two line-item descriptions are considered the same item.
    1. Exact match after normalisation.
    2. Fallback: Jaccard similarity of significant words >= 0.5.
    """
    na, nb = _normalize_desc(a), _normalize_desc(b)
    if na == nb:
        return True
    wa, wb = _sig_words(na), _sig_words(nb)
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) >= 0.5


def _supplier_score(a: str, b: str) -> Tuple[int, str]:
    """
    Score supplier name similarity (0, 20, or 30).
    Uses normalized text + significant-word Jaccard so variants like
    'Sandfirden Technics b.v.' and 'Sandfirden Technics BV' both score 30.
    """
    na, nb = _normalize_desc(a), _normalize_desc(b)
    if na == nb:
        return 30, f"exact supplier match: '{a}'"
    if na in nb or nb in na:
        return 20, f"partial supplier match: '{a}' ~ '{b}'"
    wa, wb = _sig_words(na), _sig_words(nb)
    if wa and wb:
        jaccard = len(wa & wb) / len(wa | wb)
        if jaccard >= 0.7:
            return 30, f"supplier word-overlap {jaccard:.0%}: '{a}' ~ '{b}'"
        if jaccard >= 0.4:
            return 20, f"supplier partial word-overlap {jaccard:.0%}: '{a}' ~ '{b}'"
    return 0, f"supplier mismatch: '{a}' vs '{b}'"


def _count_matching_quote_items(inv_items: list, qte_items: list) -> int:
    """
    Count how many quote items have a matching counterpart in the invoice.
    Uses normalised + word-overlap matching so OCR/extraction variations
    ("ANTIFREEZE/CORR. 50/50 20L" vs "Antifreeze Corr 50/50 20 L") still match.
    """
    matched = 0
    for qte in qte_items:
        qte_desc = (qte.get("description") or "").strip()
        if not qte_desc:
            continue
        if any(
            _desc_matches(qte_desc, (inv.get("description") or "").strip())
            for inv in inv_items
            if inv.get("description")
        ):
            matched += 1
    return matched


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
      reference linkage     0-20
      similar totals        0-10  (secondary signal — must not dominate)
      line item overlap     0-30  (quote-relative: fraction of quoted items present in invoice)
      date proximity        0-10

    Item overlap uses the quote as the denominator: an invoice that covers all
    quoted scope and adds only ancillary charges scores 100%, not a penalised
    fraction based on the extra lines.
    """
    anchor = get_doc(session["anchor_doc_id"], state)
    if anchor is None or anchor.get("doc_type") != "quote":
        return 0, ["no valid anchor quote in session"]

    score = 0
    reasons: List[str] = []

    # 1. Supplier name (0-30)
    inv_sup = (invoice_doc.get("supplier_name") or "").strip()
    qte_sup = (anchor.get("supplier_name") or "").strip()
    supplier_pts = 0
    if inv_sup and qte_sup:
        supplier_pts, sup_reason = _supplier_score(inv_sup, qte_sup)
        score += supplier_pts
        reasons.append(sup_reason)
    logger.debug("supplier_score=%d inv='%s' qte='%s'", supplier_pts, inv_sup, qte_sup)

    # 2. Document reference linkage (0-20)
    inv_ref = invoice_doc.get("document_number", "").strip().lower()
    qte_ref = anchor.get("document_number", "").strip().lower()
    if inv_ref and qte_ref and (
        inv_ref == qte_ref or inv_ref in qte_ref or qte_ref in inv_ref
    ):
        score += 20
        reasons.append(f"reference linkage: '{inv_ref}' ~ '{qte_ref}'")

    # 3. Similar totals (0-10) — secondary signal only
    # Use invoice subtotal when it is closer to the quote total — handles freight-on-top invoices
    # where the subtotal matches the quoted scope and the delta is just the freight charge.
    inv_total = invoice_doc.get("total")
    qte_total = anchor.get("total")
    inv_subtotal = invoice_doc.get("subtotal")

    compare_total = inv_total
    if (
        inv_subtotal is not None
        and inv_total is not None
        and qte_total is not None
        and qte_total != 0
    ):
        total_gap = abs(inv_total - qte_total) / abs(qte_total)
        subtotal_gap = abs(inv_subtotal - qte_total) / abs(qte_total)
        if subtotal_gap < total_gap:
            compare_total = inv_subtotal

    if compare_total is not None and qte_total is not None and qte_total != 0:
        ratio = abs(compare_total - qte_total) / abs(qte_total)
        if ratio < 0.05:
            score += 10
            reasons.append(f"totals nearly identical: {compare_total} vs {qte_total}")
        elif ratio < 0.20:
            score += 5
            reasons.append(f"totals close ({ratio * 100:.0f}% diff): {compare_total} vs {qte_total}")
        else:
            reasons.append(f"totals diverge ({ratio * 100:.0f}% diff)")

    # 4. Line item overlap (0-30) — quote-relative, OCR-resilient
    # Measures: what fraction of the quoted items appear in the invoice?
    # Uses normalised + word-overlap matching so extraction variations
    # ("ANTIFREEZE/CORR. 50/50 20L" vs "Antifreeze Corr 50/50 20 L") still match.
    # Extra ancillary lines (freight, delivery) on the invoice do not reduce this score.
    inv_items = invoice_doc.get("line_items") or []
    qte_items = anchor.get("line_items") or []
    item_overlap_pts = 0
    if inv_items and qte_items:
        matched = _count_matching_quote_items(inv_items, qte_items)
        overlap = matched / len(qte_items)
        item_overlap_pts = round(overlap * 30)
        if item_overlap_pts > 0:
            score += item_overlap_pts
            reasons.append(f"line item overlap {overlap * 100:.0f}% of quoted scope: +{item_overlap_pts}pts")
    logger.debug("item_overlap_score=%d", item_overlap_pts)

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

    logger.info(
        "Scored invoice '%s' vs quote '%s': supplier_score=%d item_overlap_score=%d final_confidence=%d",
        invoice_doc.get("supplier_name"), anchor.get("supplier_name"),
        supplier_pts, item_overlap_pts, score,
    )
    return score, reasons


def _should_force_compare(invoice_doc: dict, session: dict, state: dict) -> bool:
    """
    Return True when the invoice obviously belongs to this session even if the
    numeric score is below AUTO_MATCH_THRESHOLD.

    Conditions (both must hold):
      • Supplier name matches exactly or one contains the other.
      • ≥50% of the quote's line items are present in the invoice (quote-relative).

    Uses quote-relative overlap (same as the scorer) so that an invoice covering
    all quoted scope plus ancillary charges still qualifies for force-compare.
    """
    anchor = get_doc(session["anchor_doc_id"], state)
    if anchor is None or anchor.get("doc_type") != "quote":
        return False

    inv_sup = (invoice_doc.get("supplier_name") or "").strip()
    qte_sup = (anchor.get("supplier_name") or "").strip()
    if not inv_sup or not qte_sup:
        return False
    sup_pts, _ = _supplier_score(inv_sup, qte_sup)
    if sup_pts == 0:
        return False

    inv_items = invoice_doc.get("line_items") or []
    qte_items = anchor.get("line_items") or []
    if not inv_items or not qte_items:
        return False

    matched = _count_matching_quote_items(inv_items, qte_items)
    return matched / len(qte_items) >= 0.5


def find_best_matching_session(
    invoice_doc: dict, state: dict
) -> Tuple[Optional[str], int, List[str]]:
    """
    Find the best open quote session for an incoming invoice.
    Returns (session_id or None, score, reasons).

    Force-compare override: if the best-scoring session has supplier match AND
    ≥50% line-item overlap but its numeric score is still below AUTO_MATCH_THRESHOLD,
    the score is boosted to threshold so the invoice is always compared rather than
    silently dropped into a pending session.
    """
    open_sessions = [
        s for s in state.get("sessions", [])
        if s["status"] == "active"
        and s["session_type"] in ("pending", "quote_vs_invoice")
        and len(s["document_ids"]) == 1
    ]

    best_id, best_score, best_reasons, best_session = None, 0, [], None
    for session in open_sessions:
        score, reasons = score_invoice_against_session(invoice_doc, session, state)
        logger.debug(
            "Session %s score=%d for invoice from %s: %s",
            session["session_id"], score, invoice_doc.get("supplier_name"), reasons,
        )
        if score > best_score:
            best_id, best_score, best_reasons, best_session = (
                session["session_id"], score, reasons, session
            )

    # Force-compare: boost below-threshold matches that clearly belong together
    if best_id is not None and best_score < AUTO_MATCH_THRESHOLD and best_session is not None:
        if _should_force_compare(invoice_doc, best_session, state):
            best_score = AUTO_MATCH_THRESHOLD
            best_reasons.append("force-matched: supplier match + ≥50% line item overlap")
            logger.info(
                "Force-compare applied: session=%s new_score=%d invoice_from=%s",
                best_id, best_score, invoice_doc.get("supplier_name"),
            )

    candidate_quote_id = None
    if best_session is not None:
        anchor = get_doc(best_session["anchor_doc_id"], state)
        if anchor:
            candidate_quote_id = anchor.get("document_id")

    logger.info(
        "Best session match for invoice from '%s': session=%s score=%d "
        "session_ids_checked=%d candidate_quote_id=%s",
        invoice_doc.get("supplier_name"), best_id, best_score,
        len(open_sessions), candidate_quote_id,
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
