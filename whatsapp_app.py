import copy
import os
import json
import logging
import requests
import threading
from typing import Optional, Tuple
from dotenv import load_dotenv
from flask import Flask, request
from twilio.rest import Client as TwilioRestClient
from twilio.twiml.messaging_response import MessagingResponse

from domain.extraction import extract_pdf_text, render_pdf_pages_to_images
from services.anthropic_service import extract_commercial_document_with_claude
from services.anthropic_vision_service import (
    extract_commercial_document_from_images,
    summarise_operational_note_from_image,
)
from domain.compare import compare_documents
from domain.session_store import user_id_from_phone, load_user_state, save_user_state
from domain.session_manager import (
    make_document_record,
    get_active_session,
    get_session_docs,
    create_quote_session,
    attach_invoice_to_session,
    create_pending_session,
    reset_user_sessions,
    store_comparison_result,
    find_best_matching_session,
    gather_quote_docs_for_comparison,
    create_quote_vs_quote_session,
    AUTO_MATCH_THRESHOLD,
    AMBIGUOUS_THRESHOLD,
    MAX_QUOTES_PER_SESSION,
)
from domain.intent import classify_text
from domain.compliance_engine import answer_compliance_query, answer_compliance_followup
from services.market_price_service import check_market_price
from services.reminder_service import (
    start_reminder_scheduler,
    strip_reminder_prefix,
    parse_datetime_and_text,
    create_reminder,
    format_due_datetime,
)
import config

load_dotenv(dotenv_path=".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
config.log_startup()
start_reminder_scheduler()

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER")

BASE_CURRENCY = "EUR"

FX_RATES = {
    ("GBP", "EUR"): 1.1483,
    ("EUR", "GBP"): 1 / 1.1483,
}

app = Flask(__name__)


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

_IMAGE_CONTENT_TYPES = {"image/jpeg", "image/jpg", "image/png"}


def download_file(url: str, content_type: str) -> str:
    ext_map = {
        "application/pdf": ".pdf",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }
    ext = ext_map.get(content_type, ".bin")

    config.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"upload_{abs(hash(url))}{ext}"
    filepath = str(config.UPLOADS_DIR / filename)

    r = requests.get(url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN), timeout=30)
    r.raise_for_status()

    with open(filepath, "wb") as f:
        f.write(r.content)

    return filepath


# ---------------------------------------------------------------------------
# Currency and document normalisation
# ---------------------------------------------------------------------------

def convert_currency(amount, from_cur, to_cur):
    if amount is None or not from_cur or not to_cur:
        return amount

    from_cur = from_cur.strip().upper()
    to_cur = to_cur.strip().upper()

    if from_cur == to_cur:
        return amount

    rate = FX_RATES.get((from_cur, to_cur))
    if rate is None:
        return None

    return round(amount * rate, 2)


def normalise_doc_type(doc):
    raw = (doc.get("doc_type") or "").strip().lower()
    if raw in ["quote", "quotation", "estimate", "proposal", "offer", "proforma"]:
        doc["doc_type"] = "quote"
    elif raw in ["invoice", "tax invoice", "commercial invoice", "final invoice"]:
        doc["doc_type"] = "invoice"
    return doc


def format_item_list(items, empty_message):
    if not items:
        return empty_message

    lines = []
    for item in items[:5]:
        desc = (item.get("description") or "Unnamed item").strip()
        if desc:
            lines.append(f"- {desc}")

    if len(items) > 5:
        lines.append(f"- + {len(items) - 5} more")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Response formatting
# ---------------------------------------------------------------------------

def _make_response(*, decision, why, risks=None, actions=None):
    parts = [f"DECISION:\n{decision}", f"WHY:\n{why}"]
    if risks:
        parts.append("KEY RISKS:\n- " + "\n- ".join(risks))
    if actions:
        parts.append("RECOMMENDED ACTIONS:\n- " + "\n- ".join(actions))
    return "\n\n".join(parts)


def _no_comparison_response() -> str:
    return _make_response(
        decision="NO ACTIVE COMPARISON",
        why="There is no completed comparison in the current session.",
        actions=[
            "Upload a quote, then upload a matching invoice",
            "Or upload multiple quotes and say 'compare quotes'",
        ],
    )


# ---------------------------------------------------------------------------
# Comparison logic helpers
# ---------------------------------------------------------------------------

def _compute_delta(total_a, total_b, currency_a, currency_b):
    total_a_conv = convert_currency(total_a, currency_a, BASE_CURRENCY)
    total_b_conv = convert_currency(total_b, currency_b, BASE_CURRENCY)

    if total_a_conv is not None and total_b_conv is not None and total_a_conv != 0:
        delta = round(total_b_conv - total_a_conv, 2)
        delta_percent = (delta / total_a_conv) * 100
    else:
        delta = None
        delta_percent = None

    return total_a_conv, total_b_conv, delta, delta_percent


def _get_item_names(items, limit=2):
    names = []
    for item in items[:limit]:
        name = (item.get("description") or "Unnamed item").strip()
        if name:
            names.append(name)

    extra_count = max(0, len(items) - limit)
    if extra_count > 0:
        names.append(f"+ {extra_count} more")

    return names


def _build_decision_and_why(
    doc_type_a, doc_type_b,
    supplier_a, supplier_b,
    currency_a, currency_b,
    total_a, total_b,
    total_a_conv, total_b_conv,
    delta, delta_percent,
):
    both_quotes = doc_type_a == "quote" and doc_type_b == "quote"
    quote_to_invoice = doc_type_a == "quote" and doc_type_b == "invoice"
    both_invoices = doc_type_a == "invoice" and doc_type_b == "invoice"

    if delta is None:
        if both_quotes:
            return (
                "QUOTATION COMPARISON COMPLETE",
                "Both quotations were read, but the totals could not be compared confidently.",
            )
        if quote_to_invoice:
            return (
                "QUOTE TO INVOICE COMPARISON COMPLETE",
                "The quote and invoice were read, but the totals could not be compared confidently.",
            )
        if both_invoices:
            return (
                "INVOICE COMPARISON COMPLETE",
                "Both invoices were read, but the totals could not be compared confidently.",
            )
        return (
            "DOCUMENT COMPARISON COMPLETE",
            "Both documents were read, but the totals could not be compared confidently.",
        )

    direction = "higher" if delta > 0 else "lower"
    pct = abs(delta_percent)

    def supplier_totals():
        return (
            f"{supplier_a}: {total_a} {currency_a} = {total_a_conv} {BASE_CURRENCY}. "
            f"{supplier_b}: {total_b} {currency_b} = {total_b_conv} {BASE_CURRENCY}."
        )

    def invoice_totals():
        return (
            f"Quoted total: {total_a} {currency_a} = {total_a_conv} {BASE_CURRENCY}. "
            f"Invoiced total: {total_b} {currency_b} = {total_b_conv} {BASE_CURRENCY}."
        )

    if both_quotes:
        if delta > 0:
            decision = f"ALTERNATIVE QUOTATION FROM {supplier_b.upper()} IS HIGHER"
        elif delta < 0:
            decision = f"ALTERNATIVE QUOTATION FROM {supplier_b.upper()} IS LOWER"
        else:
            decision = f"QUOTATIONS MATCH IN {BASE_CURRENCY}"

        if delta == 0:
            why = f"{supplier_totals()} Both quotations align on price."
        else:
            why = f"{supplier_totals()} The second quotation is {pct:.1f}% {direction}."

    elif quote_to_invoice:
        if delta > 0:
            decision = f"INVOICE FROM {supplier_b.upper()} EXCEEDS QUOTE"
        elif delta < 0:
            decision = f"INVOICE FROM {supplier_b.upper()} IS BELOW QUOTE"
        else:
            decision = "INVOICE MATCHES QUOTE"

        if delta == 0:
            why = f"{invoice_totals()} The invoice matches the quote."
        else:
            why = f"{invoice_totals()} The invoice is {pct:.1f}% {direction} the quote."

    elif both_invoices:
        if delta > 0:
            decision = f"SECOND INVOICE FROM {supplier_b.upper()} IS HIGHER"
        elif delta < 0:
            decision = f"SECOND INVOICE FROM {supplier_b.upper()} IS LOWER"
        else:
            decision = f"INVOICES MATCH IN {BASE_CURRENCY}"

        if delta == 0:
            why = f"{supplier_totals()} Both invoices align on price."
        else:
            why = f"{supplier_totals()} The second invoice is {pct:.1f}% {direction}."

    else:
        if delta > 0:
            decision = f"SECOND DOCUMENT FROM {supplier_b.upper()} IS HIGHER"
        elif delta < 0:
            decision = f"SECOND DOCUMENT FROM {supplier_b.upper()} IS LOWER"
        else:
            decision = f"DOCUMENTS MATCH IN {BASE_CURRENCY}"

        if delta == 0:
            why = f"{supplier_totals()} Both documents align on price."
        else:
            why = f"{supplier_totals()} The second document is {pct:.1f}% {direction}."

    return decision, why


def _build_risks(doc_type_a, doc_type_b, supplier_a, supplier_b, added_names, missing_names, delta):
    both_quotes = doc_type_a == "quote" and doc_type_b == "quote"
    quote_to_invoice = doc_type_a == "quote" and doc_type_b == "invoice"

    risks = []
    if added_names:
        risks.append(f"Additional items in {supplier_b}: {', '.join(added_names)}")
    if missing_names:
        risks.append(f"Items not carried over from {supplier_a}: {', '.join(missing_names)}")

    if quote_to_invoice and delta is not None and delta > 0:
        risks.append("Invoice uplift may indicate unapproved extras, scope growth, or billing drift")
    elif both_quotes and delta is not None and delta > 0:
        risks.append("Higher quotation may reflect scope growth, uplift, or poorer value")
    elif delta is not None and delta > 0:
        risks.append("Higher total may indicate added cost or broader scope")

    if not risks:
        risks.append("No obvious commercial risks detected from totals and listed items")

    return risks


def _build_actions(doc_type_a, doc_type_b, supplier_a, supplier_b, delta):
    both_quotes = doc_type_a == "quote" and doc_type_b == "quote"
    quote_to_invoice = doc_type_a == "quote" and doc_type_b == "invoice"

    if quote_to_invoice:
        if delta is not None and delta > 0:
            return [
                f"Challenge {supplier_b} on the invoice uplift against the agreed quote",
                "Check whether the added items were approved before supply",
                "Hold approval until the difference is properly explained",
            ]
        if delta is not None and delta < 0:
            return [
                "Confirm the invoice still covers the full quoted scope",
                "Check that nothing has been omitted or deferred",
                "Approve only once scope and delivery are confirmed",
            ]
        return [
            "Approve only after confirming scope was delivered as quoted",
            "Check exclusions and assumptions one final time",
            "Keep the quote and invoice linked for audit trail",
        ]

    if both_quotes:
        if delta is not None and delta > 0:
            return [
                f"Challenge {supplier_b} on the price gap against {supplier_a}",
                "Check whether the added items are genuinely required",
                "Decide whether the higher offer brings better value or just higher cost",
            ]
        if delta is not None and delta < 0:
            return [
                f"Check why {supplier_b} is cheaper than {supplier_a}",
                "Confirm nothing important has been omitted from scope",
                "Use the lower price only if scope and quality still meet requirement",
            ]
        return [
            "Choose on lead time, delivery confidence, and quality",
            "Check exclusions and assumptions before placing the order",
            "Use supplier reliability as the tie-breaker",
        ]

    return [
        "Check scope differences",
        f"Verify totals in {BASE_CURRENCY} against agreement",
        "Confirm exclusions and assumptions",
    ]


def _rank_docs_by_price(docs):
    entries = []
    for doc in docs:
        supplier = (doc.get("supplier_name") or "Unknown supplier").strip()
        total = doc.get("total")
        currency = (doc.get("currency") or "").strip().upper()
        total_conv = convert_currency(total, currency, BASE_CURRENCY)
        doc_type = (doc.get("doc_type") or "document").strip().lower()
        entries.append({
            "doc": doc,
            "supplier": supplier,
            "total": total,
            "currency": currency,
            "total_conv": total_conv,
            "doc_type": doc_type,
        })
    sortable = [e for e in entries if e["total_conv"] is not None]
    unsortable = [e for e in entries if e["total_conv"] is None]
    sortable.sort(key=lambda e: e["total_conv"])
    return sortable + unsortable


# ---------------------------------------------------------------------------
# Public response builders
# ---------------------------------------------------------------------------

def _build_freight_response(
    supplier_b: str,
    freight_items: list,
    delta,
    delta_percent,
    currency_b: str,
) -> str:
    freight_total = sum(
        float(item.get("line_total") or item.get("unit_rate") or 0)
        for item in freight_items
    )
    freight_descs = [
        (item.get("description") or "freight").strip()
        for item in freight_items
    ]
    freight_label = " + ".join(freight_descs)

    if freight_total:
        amount_str = f"{freight_total:g} {currency_b}".strip()
        why = f"{freight_label} ({amount_str}) added — not in original quote."
    else:
        why = f"{freight_label} added — not in original quote."

    pct = abs(delta_percent) if delta_percent is not None else None
    pct_str = f" (+{pct:.1f}%)" if pct is not None else ""

    return _make_response(
        decision=f"INVOICE FROM {supplier_b.upper()} IS HIGHER — FREIGHT ADDED{pct_str}",
        why=why,
        actions=[
            "Confirm if freight was agreed (e.g. ex works)",
            "Approve if freight was expected",
            "Query with supplier if not pre-agreed",
        ],
    )


def build_comparison_response(doc_a, doc_b, comparison):
    supplier_a = (doc_a.get("supplier_name") or "first supplier").strip()
    supplier_b = (doc_b.get("supplier_name") or "second supplier").strip()
    doc_type_a = (doc_a.get("doc_type") or "document").strip().lower()
    doc_type_b = (doc_b.get("doc_type") or "document").strip().lower()
    currency_a = (doc_a.get("currency") or "").strip().upper()
    currency_b = (doc_b.get("currency") or "").strip().upper()

    total_a = comparison.get("total_a")
    total_b = comparison.get("total_b")
    added_items = comparison.get("added_items") or []
    missing_items = comparison.get("missing_items") or []
    freight_items = comparison.get("freight_items") or []

    total_a_conv, total_b_conv, delta, delta_percent = _compute_delta(
        total_a, total_b, currency_a, currency_b
    )
    added_names = _get_item_names(added_items)
    missing_names = _get_item_names(missing_items)

    # Freight-specific response: invoice higher than quote purely due to freight/delivery addition
    quote_to_invoice = doc_type_a == "quote" and doc_type_b == "invoice"
    if quote_to_invoice and delta is not None and delta > 0 and freight_items:
        return _build_freight_response(supplier_b, freight_items, delta, delta_percent, currency_b)

    if (
        currency_a and currency_b
        and currency_a != currency_b
        and (total_a_conv is None or total_b_conv is None)
    ):
        currency_risks = []
        if added_names:
            currency_risks.append(f"Additional items: {', '.join(added_names)}")
        if missing_names:
            currency_risks.append(f"Missing items: {', '.join(missing_names)}")
        currency_risks.append("Totals are not directly comparable across different currencies")

        return _make_response(
            decision="CURRENCY MISMATCH",
            why=(
                f"{supplier_a} is in {currency_a} and {supplier_b} is in {currency_b}, "
                f"so the totals are not yet directly comparable."
            ),
            risks=currency_risks,
            actions=[
                f"Convert both documents into {BASE_CURRENCY} before deciding",
                "Check scope differences first",
                "Use the same exchange-rate date for both documents",
            ],
        )

    decision, why = _build_decision_and_why(
        doc_type_a, doc_type_b,
        supplier_a, supplier_b,
        currency_a, currency_b,
        total_a, total_b,
        total_a_conv, total_b_conv,
        delta, delta_percent,
    )
    risks = _build_risks(
        doc_type_a, doc_type_b, supplier_a, supplier_b, added_names, missing_names, delta
    )
    actions = _build_actions(doc_type_a, doc_type_b, supplier_a, supplier_b, delta)

    return _make_response(decision=decision, why=why, risks=risks, actions=actions)


def build_three_way_comparison_response(ranked):
    doc_types = {e["doc_type"] for e in ranked}
    if doc_types == {"quote"}:
        doc_label = "quotations"
    elif doc_types == {"invoice"}:
        doc_label = "invoices"
    else:
        doc_label = "documents"

    cheapest = ranked[0]
    priciest = ranked[-1]

    lines = []
    for i, e in enumerate(ranked):
        if e["total_conv"] is not None:
            price_str = f"{e['total']} {e['currency']} = {e['total_conv']} {BASE_CURRENCY}"
            if i == 0:
                suffix = " (baseline)"
            elif cheapest["total_conv"] and cheapest["total_conv"] != 0:
                pct = ((e["total_conv"] - cheapest["total_conv"]) / cheapest["total_conv"]) * 100
                suffix = f" (+{pct:.1f}%)"
            else:
                suffix = ""
        else:
            price_str = f"{e['total']} {e['currency']} (cannot convert)"
            suffix = ""
        lines.append(f"{i + 1}. {e['supplier']}: {price_str}{suffix}")

    ranking_text = "\n".join(lines)
    all_convertible = all(e["total_conv"] is not None for e in ranked)

    if all_convertible and cheapest["total_conv"] and cheapest["total_conv"] != 0:
        spread_pct = ((priciest["total_conv"] - cheapest["total_conv"]) / cheapest["total_conv"]) * 100
        return _make_response(
            decision=f"THREE {doc_label.upper()} COMPARED — {cheapest['supplier'].upper()} IS CHEAPEST",
            why=f"Ranked by price in {BASE_CURRENCY}:\n{ranking_text}",
            risks=[
                f"Price spread of {spread_pct:.1f}% between cheapest and most expensive",
                "Scope differences may account for price variation — check exclusions on all three",
            ],
            actions=[
                f"Verify all three {doc_label} cover identical scope",
                f"Challenge {priciest['supplier']} on the {spread_pct:.1f}% premium over {cheapest['supplier']}",
                f"Choose {cheapest['supplier']} only if scope and quality meet requirements",
            ],
        )

    return _make_response(
        decision=f"THREE {doc_label.upper()} RECEIVED — CURRENCIES COULD NOT BE FULLY COMPARED",
        why=f"Partial ranking in {BASE_CURRENCY}:\n{ranking_text}",
        risks=[
            "Not all totals could be converted to a common currency",
            "Direct comparison is not possible until currencies are resolved",
        ],
        actions=[
            f"Convert all {doc_label} to {BASE_CURRENCY} before comparing",
            "Verify scope is identical across all three",
            "Resubmit once currency data is consistent",
        ],
    )


def build_why_higher_response(comparison_data: Optional[dict]) -> str:
    if not comparison_data:
        return _no_comparison_response()

    doc_a = comparison_data["doc_a"]
    doc_b = comparison_data["doc_b"]
    comparison = comparison_data["comparison"]

    supplier_a = (doc_a.get("supplier_name") or "first supplier").strip()
    supplier_b = (doc_b.get("supplier_name") or "second supplier").strip()
    currency_a = (doc_a.get("currency") or "").strip().upper()
    currency_b = (doc_b.get("currency") or "").strip().upper()

    total_a = comparison.get("total_a")
    total_b = comparison.get("total_b")
    delta = comparison.get("delta")
    delta_percent = comparison.get("delta_percent")
    added_items = comparison.get("added_items") or []
    missing_items = comparison.get("missing_items") or []

    if delta is None:
        why = "The totals could not be compared confidently."
    elif delta > 0:
        why = (
            f"{supplier_b} is higher because it totals {total_b} {currency_b} "
            f"against {supplier_a} at {total_a} {currency_a}, a difference of {delta_percent:.1f}%."
        )
    elif delta < 0:
        why = (
            f"{supplier_b} is lower because it totals {total_b} {currency_b} "
            f"against {supplier_a} at {total_a} {currency_a}, a difference of {abs(delta_percent):.1f}%."
        )
    else:
        why = "Both documents total the same amount after conversion."

    return _make_response(
        decision="COMPARISON EXPLAINED",
        why=why,
        risks=[
            f"{len(added_items)} additional items in the second document",
            f"{len(missing_items)} items from the first document are missing in the second",
        ],
        actions=[
            "Review the added items first",
            "Confirm whether the missing items were intentionally excluded",
        ],
    )


def build_added_items_response(comparison_data: Optional[dict]) -> str:
    if not comparison_data:
        return _no_comparison_response()
    added_items = comparison_data["comparison"].get("added_items") or []
    item_list = format_item_list(added_items, "- No added items found")
    return (
        "DECISION:\nADDED ITEMS IDENTIFIED\n\n"
        "WHY:\nThese items appear in the second document but not the first.\n\n"
        f"RECOMMENDED ACTIONS:\n{item_list}"
    )


def build_missing_items_response(comparison_data: Optional[dict]) -> str:
    if not comparison_data:
        return _no_comparison_response()
    missing_items = comparison_data["comparison"].get("missing_items") or []
    item_list = format_item_list(missing_items, "- No missing items found")
    return (
        "DECISION:\nMISSING ITEMS IDENTIFIED\n\n"
        "WHY:\nThese items were in the first document but do not appear in the second.\n\n"
        f"RECOMMENDED ACTION:\n{item_list}"
    )


def build_what_should_i_do_response(comparison_data: Optional[dict]) -> str:
    if not comparison_data:
        return _no_comparison_response()

    doc_a = comparison_data["doc_a"]
    doc_b = comparison_data["doc_b"]
    comparison = comparison_data["comparison"]

    supplier_a = (doc_a.get("supplier_name") or "first supplier").strip()
    supplier_b = (doc_b.get("supplier_name") or "second supplier").strip()
    doc_type_a = (doc_a.get("doc_type") or "document").strip().lower()
    doc_type_b = (doc_b.get("doc_type") or "document").strip().lower()
    currency_a = (doc_a.get("currency") or "").strip().upper()
    currency_b = (doc_b.get("currency") or "").strip().upper()

    total_a = comparison.get("total_a")
    total_b = comparison.get("total_b")
    added_items = comparison.get("added_items") or []
    missing_items = comparison.get("missing_items") or []

    _, _, delta, delta_percent = _compute_delta(total_a, total_b, currency_a, currency_b)

    both_quotes = doc_type_a == "quote" and doc_type_b == "quote"
    quote_to_invoice = doc_type_a == "quote" and doc_type_b == "invoice"

    if delta is None:
        why = "The totals could not be compared. Act on scope differences first."
    elif both_quotes:
        if delta == 0:
            why = f"Both quotes match on price. Choose {supplier_b} or {supplier_a} on delivery confidence and reliability."
        else:
            pct = abs(delta_percent)
            direction = "higher" if delta > 0 else "lower"
            why = f"{supplier_b} is {pct:.1f}% {direction} than {supplier_a}. Scope and quality differences may explain the gap."
    elif quote_to_invoice:
        if delta == 0:
            why = f"The invoice from {supplier_b} matches the quote. Confirm delivery before approving payment."
        elif delta > 0:
            pct = abs(delta_percent)
            why = f"The invoice is {pct:.1f}% above the quoted price. This needs to be explained before you approve."
        else:
            pct = abs(delta_percent)
            why = f"The invoice is {pct:.1f}% below the quote. Confirm nothing was omitted before approving."
    else:
        if delta == 0:
            why = "Totals match. Verify scope is aligned before deciding."
        elif delta > 0:
            pct = abs(delta_percent)
            why = f"{supplier_b} is {pct:.1f}% higher. Check what is driving the difference."
        else:
            pct = abs(delta_percent)
            why = f"{supplier_b} is {pct:.1f}% lower. Confirm scope is complete before proceeding."

    scope_notes = []
    if added_items:
        scope_notes.append(f"{len(added_items)} added item(s) in {supplier_b}")
    if missing_items:
        scope_notes.append(f"{len(missing_items)} item(s) missing from {supplier_b}")
    if scope_notes:
        why += f" Scope differences: {', '.join(scope_notes)}."

    actions = _build_actions(doc_type_a, doc_type_b, supplier_a, supplier_b, delta)

    return _make_response(decision="HERE IS WHAT TO DO NEXT", why=why, actions=actions)


_VAGUE_DOC_REF_WORDS = frozenset({"this", "these", "it", "them"})


def _has_vague_document_reference(query: str) -> bool:
    """True when the query contains a pronoun that likely refers to an uploaded document."""
    words = {w.strip("?.,!;:") for w in query.lower().split()}
    return bool(words & _VAGUE_DOC_REF_WORDS)


def _build_document_context(state: dict) -> str:
    """
    Build a brief pricing-context string from the most recently uploaded document.
    Returns an empty string when no document is available.
    Used to enrich vague market-check queries ('rough price for this?') with
    the actual items and total so Claude can give a useful assessment.
    """
    docs = state.get("documents", [])
    if not docs:
        return ""
    doc = docs[-1]

    supplier = (doc.get("supplier_name") or "").strip()
    total = doc.get("total")
    currency = (doc.get("currency") or "").strip().upper()
    doc_type = (doc.get("doc_type") or "document").strip()
    line_items = doc.get("line_items") or []

    label = f"{doc_type} from {supplier}" if supplier else doc_type
    parts = [f"Uploaded document: {label}"]

    item_strs = []
    for item in line_items[:6]:
        desc = (item.get("description") or "").strip()
        rate = item.get("line_total") if item.get("line_total") is not None else item.get("unit_rate")
        if desc and rate is not None:
            item_strs.append(f"{desc} ({rate} {currency})".strip())
        elif desc:
            item_strs.append(desc)
    if item_strs:
        parts.append("Items: " + "; ".join(item_strs))

    if total is not None:
        total_str = f"{total} {currency}".strip()
        parts.append(f"Total: {total_str}")

    return "\n".join(parts)


def _enrich_with_doc_context(query: str, state: dict) -> str:
    """
    Prepend document context to a vague pricing query when:
      • the query contains a pronoun referring to 'this'/'these' document, AND
      • a document has been uploaded in the current session.
    Returns the original query unchanged if no enrichment is possible.
    """
    if not _has_vague_document_reference(query):
        return query
    doc_ctx = _build_document_context(state)
    if not doc_ctx:
        return query
    return f"{doc_ctx}\n\nUser question: {query}"


def _image_received_response() -> str:
    return _make_response(
        decision="IMAGE RECEIVED",
        why="The image was uploaded but could not be processed successfully.",
        actions=[
            "Try a clearer image",
            "Crop to the relevant page only",
            "Retry upload",
            "Or send a PDF instead",
        ],
    )


def build_new_session_response() -> str:
    return _make_response(
        decision="COMPARISON RESET",
        why="All previous sessions have been closed. You are starting fresh.",
        actions=[
            "Upload a new document to begin",
            "Upload a quote to start a new comparison",
        ],
    )


def build_extraction_view_response(state: dict) -> str:
    docs = state.get("documents", [])
    if not docs:
        return "No document available for extraction"

    doc = docs[-1]

    supplier = (doc.get("supplier_name") or "Unknown").strip() or "Unknown"
    total = doc.get("total")
    currency = (doc.get("currency") or "").strip().upper()
    doc_type = (doc.get("doc_type") or "unknown").strip().capitalize()
    line_items = doc.get("line_items") or []

    total_str = f"{total} {currency}".strip() if total is not None else "Unknown"

    item_lines = []
    for item in line_items[:5]:
        desc = (item.get("description") or "Unnamed item").strip()
        line_total = item.get("line_total")
        if line_total is not None:
            item_lines.append(f"• {desc} ({line_total} {currency})")
        else:
            item_lines.append(f"• {desc}")

    if len(line_items) > 5:
        item_lines.append(f"• + {len(line_items) - 5} more items")

    items_section = "\n".join(item_lines) if item_lines else "• No line items extracted"

    return (
        f"DECISION:\nEXTRACTION VIEW\n\n"
        f"DATA:\n"
        f"Type: {doc_type}\n"
        f"Supplier: {supplier}\n"
        f"Total: {total_str}\n\n"
        f"Items:\n{items_section}"
    )


# ---------------------------------------------------------------------------
# Upload handling
# ---------------------------------------------------------------------------

def _handle_quote_upload(
    doc_record: dict, supplier: str, total, currency: str, line_count: int, state: dict
) -> Tuple[str, dict]:
    active = get_active_session(state)

    # If the active session is a full quote_vs_quote, start a new session for this quote
    if (
        active
        and active["session_type"] == "quote_vs_quote"
        and len(active["document_ids"]) >= MAX_QUOTES_PER_SESSION
    ):
        state, _ = create_quote_session(doc_record, state)
        return _make_response(
            decision="NEW QUOTE SESSION STARTED",
            why=(
                f"You already have {MAX_QUOTES_PER_SESSION} quotes in the current comparison. "
                f"A new session has been started for this quote from {supplier}."
            ),
            actions=[
                "Say 'compare quotes' to include this quote in a new comparison",
                "Upload another quote to build a fresh comparison set",
                "Say 'new comparison' to reset everything",
            ],
        ), state

    # Default: every new quote gets its own session
    state, _ = create_quote_session(doc_record, state)
    return _make_response(
        decision="QUOTE RECEIVED",
        why=(
            f"New quote from {supplier}. "
            f"Total: {total} {currency}. {line_count} line items extracted. "
            f"A new comparison session has been started for this quote."
        ),
        actions=[
            "Upload a matching invoice to compare quote vs invoice",
            "Upload another quote, then say 'compare quotes' to compare supplier quotes",
            "Say 'compare quotes' to compare all recent quotes side by side",
        ],
    ), state


def _handle_invoice_upload(
    doc_record: dict, supplier: str, total, currency: str, line_count: int, state: dict
) -> Tuple[str, dict]:
    session_id, score, reasons = find_best_matching_session(doc_record, state)

    logger.info(
        "Invoice from '%s' total=%s: best session=%s score=%d reasons=%s",
        supplier, total, session_id, score, reasons,
    )

    if score >= AUTO_MATCH_THRESHOLD:
        session = next(s for s in state["sessions"] if s["session_id"] == session_id)
        state, session = attach_invoice_to_session(doc_record, session, state)

        session_docs = get_session_docs(session, state)
        quote_doc = next((d for d in session_docs if d.get("doc_type") == "quote"), None)

        if quote_doc:
            comparison = compare_documents(quote_doc, doc_record)
            state = store_comparison_result(session, state, quote_doc, doc_record, comparison)
            session = get_active_session(state)

            quote_name = quote_doc.get("supplier_name") or "the same supplier"
            match_note = (
                f"Invoice matched to existing quote from {quote_name} "
                f"(confidence {score}/100: {'; '.join(reasons[:2])})."
            )
            answer = build_comparison_response(quote_doc, doc_record, comparison)
            return f"{match_note}\n\n{answer}", state

    if score >= AMBIGUOUS_THRESHOLD:
        state, _ = create_pending_session(doc_record, state)
        return _make_response(
            decision="INVOICE RECEIVED — MATCH UNCERTAIN",
            why=(
                f"Invoice from {supplier} for {total} {currency}. "
                f"I found a possible quote match but confidence is low ({score}/100). "
                f"No automatic comparison was made."
            ),
            actions=[
                "Upload the matching quote if it was not already sent",
                "Say 'compare quotes' to compare with existing quotes",
                "Say 'new comparison' to start fresh",
            ],
        ), state

    state, _ = create_pending_session(doc_record, state)
    return _make_response(
        decision="INVOICE RECEIVED — NO MATCHING QUOTE",
        why=(
            f"Invoice from {supplier} for {total} {currency} with {line_count} line items. "
            f"No matching quote was found in this session."
        ),
        actions=[
            "Upload the quote that this invoice relates to",
            "Say 'new comparison' to start a fresh session",
        ],
    ), state


def _handle_quote_compare_intent(state: dict) -> Tuple[str, dict]:
    quote_docs = gather_quote_docs_for_comparison(state)

    if len(quote_docs) < 2:
        return _make_response(
            decision="NOT ENOUGH QUOTES TO COMPARE",
            why=f"Found {len(quote_docs)} quote(s) in recent sessions. At least 2 are needed.",
            actions=[
                "Upload a second quote to enable comparison",
                "Upload up to 3 quotes for a full three-way comparison",
            ],
        ), state

    state, session = create_quote_vs_quote_session(quote_docs, state)

    if len(quote_docs) == 2:
        doc_a, doc_b = quote_docs[0], quote_docs[1]
        comparison = compare_documents(doc_a, doc_b)
        state = store_comparison_result(session, state, doc_a, doc_b, comparison)
        return build_comparison_response(doc_a, doc_b, comparison), state

    # Three quotes
    ranked = _rank_docs_by_price(quote_docs)
    cheapest_doc = ranked[0]["doc"]
    priciest_doc = ranked[-1]["doc"]
    comparison = compare_documents(cheapest_doc, priciest_doc)
    state = store_comparison_result(session, state, cheapest_doc, priciest_doc, comparison)
    return build_three_way_comparison_response(ranked), state


_COMMERCIAL_DOC_TYPES = {
    "quote", "quotation", "estimate", "proposal", "offer", "proforma",
    "invoice", "tax invoice", "commercial invoice", "final invoice",
}


def _is_operational_note(extracted: dict) -> bool:
    """Return True when an extraction has no commercial structure — no pricing, no totals, no explicit commercial doc type."""
    raw_type = (extracted.get("doc_type") or "").strip().lower()
    if raw_type in _COMMERCIAL_DOC_TYPES:
        return False
    has_total = extracted.get("total") is not None
    has_subtotal = extracted.get("subtotal") is not None
    has_priced_items = any(
        item.get("unit_rate") is not None or item.get("line_total") is not None
        for item in (extracted.get("line_items") or [])
    )
    return not (has_total or has_subtotal or has_priced_items)


def _send_whatsapp_message(to_phone: str, body: str) -> None:
    """Send a proactive WhatsApp message via Twilio REST API."""
    if not (TWILIO_FROM_NUMBER and TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN):
        logger.warning("Image upload: proactive send skipped — TWILIO_FROM_NUMBER or credentials not set")
        return
    try:
        client = TwilioRestClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(from_=TWILIO_FROM_NUMBER, to=to_phone, body=body)
        logger.info("Image upload: proactive message sent to %s body_length=%d", to_phone, len(body))
    except Exception as exc:
        logger.exception("Image upload: proactive send failed: %s", exc)


def _process_image_background(file_path: str, state: dict, user_id: str, phone: str) -> None:
    """Run image extraction in a background thread and deliver the result via Twilio REST API."""
    fname = os.path.basename(file_path)
    logger.info("Image upload: background extraction started: %s user=%s", fname, user_id)
    try:
        answer, updated_state = _handle_image_upload(file_path, state)
        save_user_state(user_id, updated_state)
        logger.info("Image upload: background extraction completed: %s user=%s", fname, user_id)
    except Exception as exc:
        logger.exception("Image upload: background extraction failed for %s: %s", fname, exc)
        answer = _image_received_response()
    body = f"⚓ AskHelm \n\n{answer}"
    logger.info("Image upload: sending extraction result user=%s body_length=%d", user_id, len(body))
    _send_whatsapp_message(phone, body)


def _image_unknown_response() -> str:
    return _make_response(
        decision="IMAGE PROCESSED",
        why="The image was read, but it does not look like a standard quote or invoice.",
        actions=[
            "Ask: show extraction",
            "Ask for a summary",
            "Upload another document to compare",
        ],
    )


def _handle_image_upload(file_path: str, state: dict) -> Tuple[str, dict]:
    fname = os.path.basename(file_path)
    logger.info("Image upload: extraction started: %s", fname)

    # --- Step 1: extract ---
    try:
        extracted = extract_commercial_document_from_images([file_path])
    except Exception as exc:
        logger.exception("Image upload: extraction failed for %s: %s", fname, exc)
        logger.info("Image upload: branch=failed reply=IMAGE_RECEIVED")
        return _image_received_response(), state

    if not isinstance(extracted, dict):
        logger.warning("Image upload: extraction returned non-dict (type=%s) for %s",
                       type(extracted).__name__, fname)
        logger.info("Image upload: branch=failed reply=IMAGE_RECEIVED")
        return _image_received_response(), state

    logger.info("Image upload: extraction succeeded for %s", fname)

    # --- Step 2: operational note ---
    if _is_operational_note(extracted):
        logger.info("Image upload: branch=operational_note for %s", fname)
        try:
            reply = summarise_operational_note_from_image(file_path)
            logger.info("Image upload: reply=operational_summary created=%s", bool(reply))
            return reply, state
        except Exception as exc:
            logger.exception("Image upload: operational summarisation failed for %s: %s", fname, exc)
            logger.info("Image upload: branch=failed reply=IMAGE_RECEIVED")
            return _image_received_response(), state

    # --- Step 3: commercial document ---
    try:
        extracted = normalise_doc_type(extracted)
        doc_record = make_document_record(extracted, file_path)
        doc_type = doc_record["doc_type"]
        supplier = doc_record["supplier_name"] or "Unknown supplier"
        total = doc_record["total"]
        currency = doc_record["currency"]

        logger.info("Image upload: type=%s supplier=%s total=%s %s for %s",
                    doc_type, supplier, total, currency, fname)

        if doc_type in ("quote", "invoice"):
            logger.info("Image upload: branch=commercial reply=IMAGE_PROCESSED")
            state, _ = create_pending_session(doc_record, state)
            return _make_response(
                decision="IMAGE PROCESSED",
                why=f"Extracted a {doc_type} from {supplier}. Total: {total} {currency}.",
                actions=[
                    "show extraction",
                    "upload another document to compare",
                ],
            ), state

        # doc_type is "unknown" — readable but not a recognisable commercial format
        logger.info("Image upload: branch=unknown reply=IMAGE_PROCESSED_UNKNOWN")
        return _image_unknown_response(), state

    except Exception as exc:
        logger.exception("Image upload: commercial processing failed for %s: %s", fname, exc)
        logger.info("Image upload: branch=failed reply=IMAGE_RECEIVED")
        return _image_received_response(), state


def _handle_pdf_upload(file_path: str, state: dict) -> Tuple[str, dict]:
    text = extract_pdf_text(file_path)
    if not text.strip():
        image_paths = render_pdf_pages_to_images(file_path)
        extracted = extract_commercial_document_from_images(image_paths)
    else:
        extracted = extract_commercial_document_with_claude(text)

    if not isinstance(extracted, dict):
        raise ValueError("Document extraction did not return a JSON object")

    extracted = normalise_doc_type(extracted)
    doc_record = make_document_record(extracted, file_path)

    supplier = doc_record["supplier_name"] or "Unknown supplier"
    total = doc_record["total"]
    currency = doc_record["currency"]
    line_count = len(doc_record["line_items"])
    doc_type = doc_record["doc_type"]

    logger.info("PDF extracted: type=%s supplier=%s total=%s %s", doc_type, supplier, total, currency)

    if doc_type == "quote":
        return _handle_quote_upload(doc_record, supplier, total, currency, line_count, state)

    if doc_type == "invoice":
        return _handle_invoice_upload(doc_record, supplier, total, currency, line_count, state)

    state, _ = create_pending_session(doc_record, state)
    return _make_response(
        decision="DOCUMENT EXTRACTED",
        why=(
            f"Read an unclassified document from {supplier} with {line_count} line items "
            f"and total {total} {currency}. Could not determine if this is a quote or invoice."
        ),
        actions=[
            "Upload a clearly labelled quote or invoice for proper classification",
            "Start with a quote to open a new comparison session",
        ],
    ), state


def _handle_reminder_command(message: str, phone: str, state: dict) -> Tuple[str, dict]:
    remainder = strip_reminder_prefix(message)
    if remainder is None:
        remainder = message
    due_at, text = parse_datetime_and_text(remainder)
    if due_at is None:
        return (
            "REMINDER NOT SET\n\n"
            "Couldn't parse the date/time.\n\n"
            "Try:\n"
            "• remind me in 2 hours to check the engine\n"
            "• remind me tomorrow 9am to call the yard\n"
            "• remind me next Monday 0900 to review the quote\n"
            "• remind me 25 April 14:30 to sign the contract"
        ), state
    if not text:
        return (
            "REMINDER NOT SET\n\n"
            "Please include a reminder message, e.g.:\n"
            "• remind me tomorrow 9am to call the yard"
        ), state
    reminder = create_reminder(phone=phone, due_at=due_at, text=text)
    due_str = format_due_datetime(due_at, reminder["timezone"])
    return f"REMINDER SET\n\n{text}\n\n{due_str}", state


def _handle_text_message(incoming: str, state: dict, phone: str = "") -> Tuple[str, dict]:
    intent = classify_text(incoming)
    last_ctx = state.get("last_context", {})

    if intent == "greeting":
        return "Ready.\n\nSend your question or upload a document.", state

    if intent == "new_session":
        state = reset_user_sessions(state)
        state.pop("last_context", None)
        return build_new_session_response(), state

    if intent == "quote_compare":
        return _handle_quote_compare_intent(state)

    # Context-aware follow-up routing:
    # "what should I do" and compliance-specific follow-ups go to the
    # compliance engine when the last interaction was a compliance question.
    if intent in ("what_to_do", "compliance_followup"):
        topic = last_ctx.get("topic", "") if last_ctx.get("type") == "compliance" else ""
        if topic:
            return answer_compliance_followup(topic), state
        if intent == "compliance_followup":
            return (
                "DECISION: No recent compliance topic found.\n"
                "WHY: No compliance question has been asked in this session yet.\n"
                "SOURCE: N/A\n"
                "ACTIONS: • Ask a compliance question first, then follow up."
            ), state
        # intent == "what_to_do" with no compliance context → fall through to commercial

    # Commercial follow-ups operate on the active session only
    active = get_active_session(state)
    comparison_data = active.get("last_comparison") if active else None

    if intent == "why_higher":
        return build_why_higher_response(comparison_data), state

    if intent == "show_added":
        return build_added_items_response(comparison_data), state

    if intent == "show_missing":
        return build_missing_items_response(comparison_data), state

    if intent == "show_extraction":
        return build_extraction_view_response(state), state

    if intent == "what_to_do":
        return build_what_should_i_do_response(comparison_data), state

    if intent == "compliance_question":
        answer = answer_compliance_query(incoming)
        state["last_context"] = {"type": "compliance", "topic": incoming}
        return answer, state

    if intent == "market_check_followup":
        if last_ctx.get("type") == "market_check":
            original_topic = last_ctx.get("topic", "")
            combined = f"{original_topic}\nUser follow-up: {incoming}" if original_topic else incoming
            answer = check_market_price(combined, allow_broad_estimate=True)
            state["last_context"] = {"type": "market_check", "topic": original_topic or incoming}
            return answer, state
        # No market_check history — try using the most recently uploaded document as context.
        # Handles: upload quote → "give me a rough price for this?" without prior market_check.
        doc_ctx = _build_document_context(state)
        if doc_ctx:
            combined = f"{doc_ctx}\n\nUser question: {incoming}"
            answer = check_market_price(combined, allow_broad_estimate=True)
            state["last_context"] = {"type": "market_check", "topic": incoming}
            return answer, state
        # No usable context — fall through to TEXT RECEIVED

    if intent == "reminder":
        return _handle_reminder_command(incoming, phone, state)

    if intent == "market_check":
        # Enrich vague references ("is this expensive?", "what should this cost?") with
        # context from the most recently uploaded document when one is available.
        query = _enrich_with_doc_context(incoming, state)
        answer = check_market_price(query)
        state["last_context"] = {"type": "market_check", "topic": incoming}
        return answer, state

    return _make_response(
        decision="TEXT RECEIVED",
        why="No file was attached and no follow-up command was recognised.",
        actions=[
            "Send a PDF to begin",
            "Or try: compare quotes",
            "Or try: what should i do",
            "Or try: new comparison",
        ],
    ), state


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    return "ok", 200


@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    phone = request.form.get("From", "unknown")
    user_id = user_id_from_phone(phone)
    state = load_user_state(user_id)

    # Safety-net: always defined so every code path produces a visible reply.
    answer = _make_response(
        decision="SERVICE ERROR",
        why="An unexpected error occurred. Please try again.",
        actions=["Retry your message or upload"],
    )
    # For image uploads the background thread saves state; skip the main-thread save.
    save_state = True

    try:
        incoming = request.form.get("Body", "").strip()
        num_media = int(request.form.get("NumMedia") or 0)

        if num_media > 0:
            media_url = request.form.get("MediaUrl0")
            media_type = (request.form.get("MediaContentType0") or "").strip().lower()
            logger.info(
                "Inbound media: num_media=%d content_type=%r url_present=%s",
                num_media, media_type, bool(media_url),
            )

            file_path = download_file(media_url, media_type)
            logger.info("File saved: %s", file_path)

            if media_type == "application/pdf":
                answer, state = _handle_pdf_upload(file_path, state)

            elif media_type in _IMAGE_CONTENT_TYPES:
                # Return immediately; extraction runs in background thread and
                # delivers the result via a proactive Twilio REST API message.
                thread = threading.Thread(
                    target=_process_image_background,
                    args=(file_path, copy.deepcopy(state), user_id, phone),
                    daemon=True,
                )
                thread.start()
                logger.info("Image upload: background thread started for %s user=%s",
                            os.path.basename(file_path), user_id)
                answer = _make_response(
                    decision="IMAGE RECEIVED",
                    why="Processing your image now.",
                    actions=["Wait for extraction result", "Or send another document"],
                )
                save_state = False  # background thread handles state persistence

            else:
                logger.warning("Unsupported media type: %r", media_type)
                answer = _make_response(
                    decision="FILE RECEIVED",
                    why=f"Document saved as {os.path.basename(file_path)}.",
                    actions=[
                        "PDF and image (JPEG, PNG) reading is enabled",
                        "Upload another document for comparison",
                    ],
                )
        else:
            answer, state = _handle_text_message(incoming, state, phone=phone)

    except Exception as e:
        logger.exception("Error processing request for user %s", user_id)
        answer = _make_response(
            decision="FILE ERROR",
            why=f"AskHelm could not process the uploaded file: {e}",
            actions=[
                "Try sending the file again",
                "Use PDF first for testing",
                "Check extraction setup",
            ],
        )

    if save_state:
        save_user_state(user_id, state)

    body = f"⚓ AskHelm \n\n{answer}"
    logger.info("Twilio reply: body_length=%d save_state=%s user=%s", len(body), save_state, user_id)

    resp = MessagingResponse()
    resp.message(body)
    return str(resp), 200, {"Content-Type": "text/xml"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=False)
