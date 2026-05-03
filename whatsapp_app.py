import copy
import os
import re
import json
import logging
import requests
import threading
import time
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
from domain.compliance_engine import (
    answer_compliance_query,
    answer_compliance_followup,
    reset_retriever as _reset_compliance_retriever,
)
from services.compliance_ingest import (
    list_sources as list_compliance_sources,
    rebuild_index as rebuild_compliance_index,
    ingest_compliance_pdf,
    ingest_yacht_compliance_pdf,
    list_yacht_sources,
    classify_compliance_doc,
    make_compliance_doc_record,
    list_global_regulations,
)
from services.compliance_profile import (
    load_profile as load_compliance_profile,
    enable_regulation as enable_compliance_regulation,
    disable_regulation as disable_compliance_regulation,
    get_selected_regulations,
    add_vessel_document,
    list_vessel_documents,
)
from services.market_price_service import check_market_price, commercial_followup_advice
from domain.component_memory import (
    extract_components_from_doc,
    extract_components_from_text,
    merge_components,
    build_component_context,
)
from domain.invoice_address import (
    check_invoice_billing_address,
    check_invoice_delivery_address,
    load_invoice_address,
    save_invoice_address,
    ADDRESS_MATCH_NOTE,
    ADDRESS_MISMATCH_NOTE,
    DELIVERY_MATCH_NOTE,
    DELIVERY_MISMATCH_NOTE,
)
from services.reminder_service import (
    start_reminder_scheduler,
    strip_reminder_prefix,
    parse_datetime_and_text,
    create_reminder,
    format_due_datetime,
)
from services.service_report_service import (
    is_service_report_text,
    extract_service_report_from_text,
    extract_service_report_from_images,
    build_handover_note,
    format_whatsapp_response as format_service_report_response,
    make_service_report_doc_record,
)
from services.manual_service import (
    is_technical_manual_text,
    extract_manual_metadata_from_text,
    extract_manual_metadata_from_images,
    chunk_manual_text,
    make_manual_doc_record,
    format_manual_import_response,
    answer_manual_question,
)
from domain.manual_store import (
    save_manual,
    get_all_manuals,
    find_manuals_by_equipment,
    search_manual_chunks,
)
from domain.handover_store import (
    save_service_report,
    save_notes_summary,
    get_all_open_actions,
    get_all_reports,
    get_reports_for_system,
)
from domain.inventory_store import (
    merge_equipment,
    merge_stock,
    get_all_equipment,
    get_all_stock,
    find_stock_by_query,
    find_stock_for_system,
    find_equipment_by_query,
    clear_equipment,
)
from services.inventory_service import (
    classify_inventory_text,
    extract_inventory_from_text,
    extract_inventory_from_images as extract_inventory_images,
    extract_inventory_from_excel,
    extract_inventory_from_csv,
    make_inventory_doc_record,
    format_inventory_response,
    is_junk_equipment_name,
)
import config
from storage_paths import migrate_all_users
from services.compliance_ingest import seed_if_empty as _seed_compliance

load_dotenv(dotenv_path=".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
config.log_startup()
migrate_all_users()
_seed_compliance()
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
_EXCEL_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/excel",
}
_CSV_CONTENT_TYPES = {"text/csv", "application/csv", "text/comma-separated-values"}


def _looks_like_pdf(file_path: str) -> bool:
    """True when file starts with PDF magic bytes (%PDF) — content-type-agnostic."""
    try:
        with open(file_path, "rb") as fh:
            return fh.read(4) == b"%PDF"
    except Exception:
        return False


def download_file(url: str, content_type: str) -> str:
    ext_map = {
        "application/pdf": ".pdf",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/vnd.ms-excel": ".xls",
        "application/excel": ".xlsx",
        "text/csv": ".csv",
        "application/csv": ".csv",
        "text/comma-separated-values": ".csv",
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
    # Priority: proforma > invoice > quote
    if "proforma" in raw or "pro forma" in raw or "pro-forma" in raw:
        doc["doc_type"] = "proforma"
    elif "invoice" in raw:
        doc["doc_type"] = "invoice"
    elif raw in ["quote", "quotation", "estimate", "proposal", "offer"]:
        doc["doc_type"] = "quote"
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

def _confidence_label(score: int) -> str:
    if score >= 80:
        return "\U0001f7e2 HIGH"
    if score >= 50:
        return "\U0001f7e0 MEDIUM"
    return "\U0001f534 LOW"


def _make_response(*, decision, why, risks=None, actions=None):
    parts = [f"DECISION:\n{decision}", f"WHY:\n{why}"]
    if actions:
        parts.append("RECOMMENDED ACTIONS:\n• " + "\n• ".join(actions))
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


_DOCUMENT_RECEIVED_ACK = (
    "\u2693 AskHelm\n\n"
    "Processing document\u2026\n"
    "I'll send the result shortly."
)

_MARKET_CHECK_CONTEXT_FALLBACK = (
    "DECISION:\nINSUFFICIENT DATA\n\n"
    "WHY:\nI found the last quoted item, but I could not complete a reliable live price check from the current context. Confidence: \U0001f534 LOW\n\n"
    "ACTIONS:\n"
    "• Send the make/model or part number\n"
    "• Or send another supplier quote for comparison"
)

_COMMODITY_KEYWORDS = frozenset(["filter", "matting", "bolt", "nut", "washer", "consumables"])

_COMMODITY_PRICE_CHECK_FALLBACK = (
    "DECISION:\nINSUFFICIENT DATA\n\n"
    "WHY:\nThis appears to be a standard commodity item. Exact pricing varies by brand and volume. Confidence: \U0001f534 LOW.\n\n"
    "ACTIONS:\n"
    "• Get a second supplier quote to compare\n"
    "• Commodity prices are generally within a standard market range"
)

_MARKET_CHECK_DOC_CONTEXT_FALLBACK = (
    "DECISION:\nMORE DETAIL NEEDED\n\n"
    "WHY:\nI found the quoted part and price, but I cannot judge it reliably without the component description or equipment make/model. Confidence: \U0001f534 LOW.\n\n"
    "RECOMMENDED ACTIONS:\n"
    "• Confirm what the part is fitted to\n"
    "• Send the make/model or component description\n"
    "• Or send a second supplier quote for comparison"
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


def _ancillary_category(items: list) -> str:
    """Returns a human label for a group of ancillary items: 'freight', 'delivery', etc."""
    if not items:
        return "ancillary charges"
    descs = " ".join((item.get("description") or "") for item in items).lower()
    categories = []
    if any(kw in descs for kw in ("freight", "shipping", "courier", "transport", "dispatch", "forwarding")):
        categories.append("freight")
    if any(kw in descs for kw in ("delivery", "carriage")):
        categories.append("delivery")
    if any(kw in descs for kw in ("packing", "packaging", "crating")):
        categories.append("packaging")
    if any(kw in descs for kw in ("insurance",)):
        categories.append("insurance")
    if any(kw in descs for kw in ("duty", "customs", "tariff", "import")):
        categories.append("customs duties")
    if any(kw in descs for kw in ("surcharge",)):
        categories.append("surcharge")
    if not categories:
        return "ancillary charges"
    return " and ".join(categories)


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
    missing_names=None,
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
            if missing_names:
                decision = "MATCH CONFIRMED — SCOPE DIFFERENCE"
            else:
                decision = "MATCH CONFIRMED — COST INCREASE"
        elif delta < 0:
            decision = "MATCH CONFIRMED — COST REDUCTION"
        else:
            decision = "MATCH CONFIRMED — NO CHANGE"

        amt = f"{BASE_CURRENCY} {abs(delta):,.2f}"
        if delta == 0:
            why = f"{supplier_b} invoice matches the quoted amount exactly."
        elif delta > 0:
            if missing_names:
                why = (
                    f"{supplier_b} invoice is {pct:.1f}% higher ({amt} increase). "
                    f"Items on the original quote not found in the invoice: {', '.join(missing_names)}."
                )
            else:
                why = f"{supplier_b} invoice is {pct:.1f}% higher than the quote ({amt} increase)."
        else:
            why = f"{supplier_b} invoice is {pct:.1f}% lower than the quote ({amt} reduction)."

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
        return ["Approve — matches agreed quotation"]

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


def _classify_comparison(
    doc_a: dict, doc_b: dict, comparison: dict, match_score: int = 0
) -> dict:
    """
    Returns a structured comparison outcome: decision code, delta, confidence.
    Separates decision logic from response wording — can be called by future
    intent handlers (e.g. 'what should I do') without touching formatting code.
    """
    doc_type_a = (doc_a.get("doc_type") or "document").lower()
    doc_type_b = (doc_b.get("doc_type") or "document").lower()
    currency_a = (doc_a.get("currency") or "").strip().upper()
    currency_b = (doc_b.get("currency") or "").strip().upper()
    supplier_a = (doc_a.get("supplier_name") or "first supplier").strip()
    supplier_b = (doc_b.get("supplier_name") or "second supplier").strip()

    total_a = comparison.get("total_a")
    total_b = comparison.get("total_b")
    missing_items = comparison.get("missing_items") or []
    ancillary_items = (
        comparison.get("ancillary_items") or comparison.get("freight_items") or []
    )
    ancillary_only = comparison.get("all_added_are_ancillary", False)
    added_items = comparison.get("added_items") or []

    _, _, delta, delta_percent = _compute_delta(total_a, total_b, currency_a, currency_b)

    quote_to_invoice = doc_type_a == "quote" and doc_type_b == "invoice"
    quote_to_proforma = doc_type_a == "quote" and doc_type_b == "proforma"
    both_quotes = doc_type_a == "quote" and doc_type_b == "quote"

    if quote_to_invoice:
        if delta is None or delta == 0:
            decision = "MATCH CONFIRMED — NO CHANGE"
        elif delta > 0:
            if missing_items:
                decision = "MATCH CONFIRMED — SCOPE DIFFERENCE"
            elif ancillary_only and ancillary_items:
                decision = "MATCH CONFIRMED — FREIGHT ADDED"
            else:
                decision = "MATCH CONFIRMED — COST INCREASE"
        else:
            decision = "MATCH CONFIRMED — COST REDUCTION"
    elif quote_to_proforma:
        decision = "MATCH CONFIRMED — PROFORMA ALIGNED"
    else:
        decision = None  # other types resolved by _build_decision_and_why

    # Confidence override: ancillary-only delta + full core match → HIGH
    confidence = _confidence_label(match_score)
    if (quote_to_invoice or quote_to_proforma) and ancillary_only and not missing_items:
        confidence = "\U0001f7e2 HIGH"
    # Exact total match is always HIGH confidence — matching totals confirm price
    if delta == 0:
        confidence = "HIGH"
    # NO CHANGE decision means totals are confirmed identical — always HIGH
    if decision == "MATCH CONFIRMED — NO CHANGE":
        confidence = "HIGH"

    return {
        "comparison_type": (
            "quote_vs_invoice" if quote_to_invoice
            else "quote_vs_proforma" if quote_to_proforma
            else "quote_vs_quote" if both_quotes
            else "other"
        ),
        "decision": decision,
        "supplier_a": supplier_a,
        "supplier_b": supplier_b,
        "currency_a": currency_a,
        "currency_b": currency_b,
        "total_a": total_a,
        "total_b": total_b,
        "delta": delta,
        "delta_percent": delta_percent,
        "ancillary_only": ancillary_only,
        "ancillary_items": ancillary_items,
        "missing_items": missing_items,
        "added_items": added_items,
        "confidence": confidence,
    }


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
    confidence_label: str = "",
) -> str:
    ancillary_total = sum(
        float(item.get("line_total") or item.get("unit_rate") or 0)
        for item in freight_items
    )
    category = _ancillary_category(freight_items)
    cur = currency_b or BASE_CURRENCY

    if ancillary_total:
        charge_str = f"{cur} {ancillary_total:,.2f}"
        why = (
            f"The increase is driven by added {category} charges of {charge_str} "
            f"not included in the original quote."
        )
    else:
        why = (
            f"The increase is driven by added {category} charges "
            f"not included in the original quote."
        )

    if confidence_label:
        why = f"{why} Confidence: {confidence_label}."

    return _make_response(
        decision="MATCH CONFIRMED — FREIGHT ADDED",
        why=why,
        actions=[
            "Confirm whether the quote was ex works or included delivery",
            "Assess if the freight cost is reasonable for the supplier location",
            "Approve if consistent with expected logistics cost",
        ],
    )


def build_comparison_response(doc_a, doc_b, comparison, match_score: int = 0):
    outcome = _classify_comparison(doc_a, doc_b, comparison, match_score)
    confidence_label = outcome["confidence"]

    supplier_a = outcome["supplier_a"]
    supplier_b = outcome["supplier_b"]
    currency_a = outcome["currency_a"]
    currency_b = outcome["currency_b"]
    doc_type_a = (doc_a.get("doc_type") or "document").lower()
    doc_type_b = (doc_b.get("doc_type") or "document").lower()

    total_a = outcome["total_a"]
    total_b = outcome["total_b"]
    delta = outcome["delta"]
    delta_percent = outcome["delta_percent"]
    ancillary_items = outcome["ancillary_items"]
    ancillary_only = outcome["ancillary_only"]
    missing_items = outcome["missing_items"]
    added_items = outcome["added_items"]

    total_a_conv, total_b_conv, _, _ = _compute_delta(total_a, total_b, currency_a, currency_b)
    added_names = _get_item_names(added_items)
    missing_names = _get_item_names(missing_items)

    # Proforma: return aligned response with quote reference number if captured.
    quote_to_proforma = outcome["comparison_type"] == "quote_vs_proforma"
    if quote_to_proforma:
        ref_num = (doc_b.get("reference_number") or "").strip()
        if ref_num:
            why = f"{supplier_b} proforma matches the quoted amount and references quote {ref_num}."
        elif delta is None or delta == 0:
            why = f"{supplier_b} proforma matches the quoted amount exactly."
        elif delta > 0:
            pct = abs(delta_percent)
            why = f"{supplier_b} proforma is {pct:.1f}% higher than the quoted amount."
        else:
            pct = abs(delta_percent)
            why = f"{supplier_b} proforma is {pct:.1f}% lower than the quoted amount."
        if confidence_label:
            why = f"{why} Confidence: {confidence_label}."
        return _make_response(
            decision="MATCH CONFIRMED — PROFORMA ALIGNED",
            why=why,
            actions=["Proceed with payment if the proforma is approved for order release"],
        )

    # Ancillary-only uplift: all added items are freight/delivery/etc., core scope unchanged.
    quote_to_invoice = outcome["comparison_type"] == "quote_vs_invoice"
    if quote_to_invoice and delta is not None and delta > 0 and ancillary_items and ancillary_only:
        return _build_freight_response(
            supplier_b, ancillary_items, delta, delta_percent, currency_b,
            confidence_label=confidence_label,
        )

    if (
        currency_a and currency_b
        and currency_a != currency_b
        and (total_a_conv is None or total_b_conv is None)
    ):
        return _make_response(
            decision="CURRENCY MISMATCH",
            why=(
                f"{supplier_a} is in {currency_a} and {supplier_b} is in {currency_b}, "
                f"so the totals are not yet directly comparable."
            ),
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
        missing_names=missing_names,
    )
    if confidence_label:
        why = f"{why} Confidence: {confidence_label}."
    actions = _build_actions(doc_type_a, doc_type_b, supplier_a, supplier_b, delta)

    return _make_response(decision=decision, why=why, actions=actions)


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


def _handle_action_request(
    query: str, last_ctx: dict, comparison_data, state: dict
) -> str:
    """
    Shared handler for 'what should I do?' and 'how many should I order?' queries.
    Priority: comparison context → market check context → component memory alone.
    """
    if comparison_data:
        return build_what_should_i_do_response(comparison_data)

    comp_ctx = build_component_context(state)
    ctx_type = last_ctx.get("type", "")

    if ctx_type == "market_check":
        topic = last_ctx.get("topic", "")
        result = last_ctx.get("result", "")
        doc_ctx = _build_document_context(state)
        parts = []
        if comp_ctx:
            parts.append(comp_ctx)
        if doc_ctx:
            parts.append(doc_ctx)
        if topic:
            parts.append(f"Market check query: {topic}")
        if result:
            parts.append(f"Market price assessment:\n{result}")
        return commercial_followup_advice(query, "\n\n".join(parts))

    # Use whatever context is available: component memory + last document
    doc_ctx = _build_document_context(state)
    parts = []
    if comp_ctx:
        parts.append(comp_ctx)
    if doc_ctx:
        parts.append(doc_ctx)
    if parts:
        return commercial_followup_advice(query, "\n\n".join(parts))

    return _no_comparison_response()


_VAGUE_DOC_REF_WORDS = frozenset({"this", "these", "it", "them"})

# Words that indicate a standalone reply rather than additional spec/context.
_CONTINUATION_STOP_WORDS = frozenset([
    "yes", "no", "ok", "okay", "cancel", "stop", "done", "thanks", "thank",
])


def _is_context_continuation(incoming: str, last_ctx: dict) -> bool:
    """
    True when a short, unrecognised message looks like additional detail
    (spec, location, model number) extending a recent market_check query.

    Heuristic:
    - last_context must be a market_check
    - message must be fewer than 8 words
    - message must not be a stop/acknowledgement word
    """
    if not last_ctx or last_ctx.get("type") != "market_check":
        return False
    words = incoming.split()
    if len(words) >= 8:
        return False
    if any(w.lower().strip("?.,!") in _CONTINUATION_STOP_WORDS for w in words):
        return False
    return True


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

    subtotal = doc.get("subtotal")
    tax = doc.get("tax")
    if subtotal is not None:
        parts.append(f"Subtotal: {subtotal} {currency}".strip())
    if tax is not None and tax > 0:
        parts.append(f"Tax: {tax} {currency}".strip())
    if total is not None:
        parts.append(f"Total: {total} {currency}".strip())

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


def _handle_market_check_clarification(
    clarification: str, pending: dict, state: dict
) -> Tuple[str, dict]:
    """Re-run a pending market check using the user's clarification reply."""
    doc_ctx = pending.get("doc_ctx", "")
    comp_ctx = pending.get("comp_ctx", "")
    original_query = pending.get("original_query", "")
    logger.info(
        "market_check_clarification: original_query=%r clarification=%r",
        original_query[:60], clarification[:60],
    )
    combined_query = (
        f"{original_query}\nUser clarification: {clarification}"
        if original_query else clarification
    )
    return _handle_document_market_check(combined_query, state, doc_ctx, comp_ctx)


def _handle_document_market_check(
    query: str, state: dict, doc_ctx: str = "", comp_ctx: str = ""
) -> Tuple[str, dict]:
    """
    Handle a market-check query when document or component context is available.
    Enriches the query with that context, calls check_market_price, and guards
    against empty / malformed responses with a structured fallback.
    """
    has_last_document = bool(doc_ctx)
    has_component_memory = bool(comp_ctx)
    logger.info(
        "followup_market_check_entered=True has_last_document=%s has_component_memory=%s",
        has_last_document, has_component_memory,
    )
    ctx_parts = []
    if comp_ctx:
        ctx_parts.append(comp_ctx)
    if doc_ctx:
        ctx_parts.append(doc_ctx)
    reused_quote_context = bool(ctx_parts)
    ctx_parts.append(f"User question: {query}")
    enriched = "\n\n".join(ctx_parts)
    logger.info(
        "reused_quote_context=%s external_lookup_called=True enriched_length=%d",
        reused_quote_context, len(enriched),
    )
    answer = ""
    try:
        answer = check_market_price(enriched, allow_broad_estimate=True)
        response_built = bool(answer and answer.strip())
        logger.info(
            "response_built=%s response_length=%d exception=False",
            response_built, len(answer),
        )
        if not response_built:
            logger.warning("followup_market_check: empty response, using fallback")
            answer = _MARKET_CHECK_CONTEXT_FALLBACK
    except Exception as exc:
        logger.exception("followup_market_check exception=True: %s response_built=False", exc)
        answer = _MARKET_CHECK_CONTEXT_FALLBACK
    # We already have the quoted price in the context — never ask the user to resend it.
    # Replace any response that asks for the quoted price with a context-aware fallback.
    _is_commodity = any(kw in (query + " " + doc_ctx).lower() for kw in _COMMODITY_KEYWORDS)
    if reused_quote_context and answer and "Send the quoted price" in answer:
        if _is_commodity:
            logger.info("followup_market_check: commodity item, using commodity fallback")
            answer = _COMMODITY_PRICE_CHECK_FALLBACK
        else:
            logger.info("followup_market_check: replacing 'send quoted price' with doc-context fallback")
            answer = _MARKET_CHECK_DOC_CONTEXT_FALLBACK
            state["pending_clarification"] = {
                "intent": "market_check",
                "doc_ctx": doc_ctx,
                "comp_ctx": comp_ctx,
                "original_query": query,
            }
    state["last_context"] = {"type": "market_check", "topic": query, "result": answer}
    comp = extract_components_from_text(query, "market_check")
    if comp:
        state = merge_components(comp, state)
    return answer, state


def _image_received_response() -> str:
    return _make_response(
        decision="DOCUMENT NOT UNDERSTOOD",
        why="I received the file but could not classify it as a quote, invoice, proforma, or technical note.",
        actions=[
            "Re-upload as PDF if possible",
            "Or say what this document is",
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


def _extract_and_merge_components(doc_record: dict, state: dict) -> dict:
    """Extract components from a doc_record and merge into state working memory."""
    from domain.component_memory import extract_components_from_doc, merge_components
    comps = extract_components_from_doc(doc_record)
    if comps:
        logger.info(
            "Component memory: extracted=%d from doc_id=%s type=%s",
            len(comps), doc_record.get("document_id"), doc_record.get("doc_type"),
        )
        state = merge_components(comps, state)
    return state


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

    # Batch window: if an unmatched invoice was held pending, check now
    # whether the freshly-created quote session provides a match.
    _pending = state.get("pending_invoice")
    if _pending and time.time() - _pending.get("stored_at", 0) <= 60:
        _inv_record = _pending["doc_record"]
        _sid, _score, _ = find_best_matching_session(_inv_record, state)
        if _sid and _score >= AUTO_MATCH_THRESHOLD:
            state.pop("pending_invoice")
            _inv_session = next(s for s in state["sessions"] if s["session_id"] == _sid)
            state, _inv_session = attach_invoice_to_session(_inv_record, _inv_session, state)
            _comparison = compare_documents(doc_record, _inv_record)
            state = store_comparison_result(_inv_session, state, doc_record, _inv_record, _comparison)
            logger.info(
                "pending_invoice matched: quote_supplier=%s invoice_supplier=%s score=%d",
                supplier, _inv_record.get("supplier_name"), _score,
            )
            return build_comparison_response(doc_record, _inv_record, _comparison), state
    elif _pending:
        state.pop("pending_invoice", None)  # expired

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

            ancillary_detected = bool(comparison.get("ancillary_items"))
            logger.info(
                "comparison_ran=True ancillary_charge_detected=%s candidate_quote_id=%s final_confidence=%d",
                ancillary_detected, quote_doc.get("document_id"), score,
            )

            quote_name = quote_doc.get("supplier_name") or "the same supplier"
            answer = build_comparison_response(quote_doc, doc_record, comparison, match_score=score)
            return answer, state

    logger.info("comparison_ran=False final_confidence=%d", score)

    if score >= AMBIGUOUS_THRESHOLD:
        state, _ = create_pending_session(doc_record, state)
        return _make_response(
            decision="INVOICE RECEIVED — MATCH UNCERTAIN",
            why=(
                f"Invoice from {supplier} for {total} {currency}. "
                f"I found a possible quote match but confidence is {_confidence_label(score)}. "
                f"No automatic comparison was made."
            ),
            actions=[
                "Upload the matching quote if it was not already sent",
                "Say 'compare quotes' to compare with existing quotes",
                "Say 'new comparison' to start fresh",
            ],
        ), state

    # Store invoice silently — the matching quote webhook will check here and
    # trigger an immediate comparison.  A fallback thread (started in the
    # webhook handler) sends INVOICE RECEIVED via REST if no quote arrives.
    state["pending_invoice"] = {"doc_record": doc_record, "stored_at": time.time()}
    logger.info("Invoice stored silently pending quote: supplier=%s total=%s", supplier, total)
    return "", state


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

_INVENTORY_DOC_TYPES = {"equipment_list", "stock_inventory", "spare_parts_inventory"}


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
        logger.info(
            "outbound_whatsapp: method=REST to=%s body_length=%d body_empty=%s "
            "reply_body_preview=%r",
            to_phone, len(body), not body.strip(), body[:500],
        )
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
    _send_whatsapp_message(phone, body)


def _process_images_background(file_paths: list, state: dict, user_id: str, phone: str) -> None:
    """
    Background thread entry point for image uploads.
    Passes all pages from the same message to Claude together so multi-page
    documents (e.g. page 1 + page 2 of the same quote) are extracted as one.
    """
    fnames = [os.path.basename(p) for p in file_paths]
    logger.info("Image upload: background started pages=%d files=%s user=%s", len(file_paths), fnames, user_id)
    try:
        answer, updated_state = _handle_images_upload(file_paths, state)
        save_user_state(user_id, updated_state)
        logger.info("Image upload: background completed pages=%d user=%s", len(file_paths), user_id)
    except Exception as exc:
        logger.exception("Image upload: background failed pages=%d: %s", len(file_paths), exc)
        answer = _image_received_response()
    body = f"⚓ AskHelm \n\n{answer}"
    _send_whatsapp_message(phone, body)


def _invoice_pending_fallback(user_id: str, phone: str, fingerprint: str) -> None:
    """
    Send a deferred INVOICE RECEIVED if no quote matched within the debounce window.
    Started when an invoice is stored silently so a concurrent quote webhook can
    claim it first; the fallback fires only if pending_invoice is still unmatched.
    """
    time.sleep(15)
    state = load_user_state(user_id)
    inv = state.get("pending_invoice") or {}
    stored_fp = (inv.get("doc_record") or {}).get("fingerprint", "")
    if stored_fp != fingerprint:
        return  # quote arrived and matched, or newer invoice replaced it
    doc = inv["doc_record"]
    supplier = doc.get("supplier_name") or "Unknown supplier"
    total = doc.get("total")
    currency = doc.get("currency") or ""
    msg = _make_response(
        decision="INVOICE RECEIVED",
        why=(
            f"Invoice from {supplier} for {total} {currency}. "
            "No matching quote found — upload the quote to run a comparison."
        ),
        actions=["Upload the matching quote", "Or say 'new comparison' to reset"],
    )
    _send_whatsapp_message(phone, f"⚓ AskHelm \n\n{msg}")



def _handle_images_upload(file_paths: list, state: dict) -> Tuple[str, dict]:
    """
    Extract and dispatch one or more images as a single document.
    A single path delegates to the existing single-image path unchanged.
    Multiple paths are passed together so Claude sees all pages at once —
    this handles page 1 + page 2 of the same quote as one extraction.
    """
    if len(file_paths) == 1:
        return _handle_image_upload(file_paths[0], state)

    page_count = len(file_paths)
    logger.info("Image upload: multi-page extraction page_count=%d", page_count)
    try:
        extracted = extract_commercial_document_from_images(file_paths)
    except Exception as exc:
        logger.exception("Image upload: multi-page extraction failed: %s", exc)
        return _image_received_response(), state

    if not isinstance(extracted, dict):
        return _image_received_response(), state

    if _is_operational_note(extracted):
        raw_type = (extracted.get("doc_type") or "").lower()
        if raw_type in _INVENTORY_DOC_TYPES:
            logger.info("Image upload (multi-page): branch=inventory doc_type=%s pages=%d", raw_type, len(file_paths))
            return _handle_inventory_image(file_paths, raw_type, state)
        subtype = _classify_non_commercial_subtype(extracted)
        if subtype == "service_report":
            logger.info("Image upload (multi-page): branch=service_report pages=%d", len(file_paths))
            return _handle_service_report_image(file_paths, state)
        logger.info("Image upload (multi-page): branch=%s pages=%d", subtype, len(file_paths))
        return _handle_operational_notes_image(file_paths, state)

    try:
        extracted = normalise_doc_type(extracted)
        doc_record = make_document_record(extracted, file_paths[0])
        doc_type = doc_record["doc_type"]
        supplier = doc_record["supplier_name"] or "Unknown supplier"
        total = doc_record["total"]
        currency = doc_record["currency"]
        line_count = len(doc_record["line_items"])

        logger.info(
            "Image upload (multi-page): type=%s supplier=%s total=%s %s pages=%d items=%d",
            doc_type, supplier, total, currency, page_count, line_count,
        )

        if doc_type == "quote":
            answer, state = _handle_quote_upload(doc_record, supplier, total, currency, line_count, state)
            state = _extract_and_merge_components(doc_record, state)
            return answer, state

        if doc_type == "invoice":
            answer, state = _handle_invoice_upload(doc_record, supplier, total, currency, line_count, state)
            state = _extract_and_merge_components(doc_record, state)
            return answer, state

        return _image_unknown_response(), state

    except Exception as exc:
        logger.exception("Image upload: multi-page dispatch failed: %s", exc)
        return _image_received_response(), state


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

    # --- Step 2: operational note / service report / inventory ---
    if _is_operational_note(extracted):
        raw_type = (extracted.get("doc_type") or "").lower()
        if raw_type in _INVENTORY_DOC_TYPES:
            logger.info("Image upload: branch=inventory doc_type=%s for %s", raw_type, fname)
            return _handle_inventory_image([file_path], raw_type, state)
        subtype = _classify_non_commercial_subtype(extracted)
        if subtype == "service_report":
            logger.info("Image upload: branch=service_report for %s", fname)
            return _handle_service_report_image([file_path], state)
        logger.info("Image upload: branch=%s for %s", subtype, fname)
        return _handle_operational_notes_image([file_path], state)

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

        line_count = len(doc_record["line_items"])
        if doc_type == "quote":
            logger.info("Image upload: branch=quote for %s", fname)
            answer, state = _handle_quote_upload(doc_record, supplier, total, currency, line_count, state)
            return answer, state

        if doc_type == "invoice":
            logger.info("Image upload: branch=invoice for %s", fname)
            answer, state = _handle_invoice_upload(doc_record, supplier, total, currency, line_count, state)
            return answer, state

        # doc_type is "unknown" — readable but not a recognisable commercial format
        logger.info("Image upload: branch=unknown reply=IMAGE_PROCESSED_UNKNOWN")
        return _image_unknown_response(), state

    except Exception as exc:
        logger.exception("Image upload: commercial processing failed for %s: %s", fname, exc)
        logger.info("Image upload: branch=failed reply=IMAGE_RECEIVED")
        return _image_received_response(), state


# ---------------------------------------------------------------------------
# Inventory handlers
# ---------------------------------------------------------------------------

def _handle_inventory_doc(doc_record: dict, state: dict) -> Tuple[str, dict]:
    """Handle an inventory doc_record produced by _extract_pdf_to_doc_record."""
    user_id = state.get("user_id", "")
    data = doc_record.get("inventory_data") or {}
    source = doc_record.get("file_path", "")
    eq_items = data.get("equipment") or []
    st_items = data.get("stock") or []
    parse_error = bool(data.get("parse_error"))
    try:
        eq_added, eq_merged = merge_equipment(user_id, eq_items, source)
        st_added, st_merged = merge_stock(user_id, st_items, source)
    except Exception as exc:
        logger.exception("inventory_doc: merge failed user=%s: %s", user_id, exc)
        eq_added = eq_merged = st_added = st_merged = 0
        parse_error = True
    state["last_context"] = {"type": "inventory_import", "doc_type": doc_record.get("doc_type")}
    return format_inventory_response(eq_added, eq_merged, st_added, st_merged, parse_error), state


def _handle_inventory_image(image_paths: list, doc_type: str, state: dict) -> Tuple[str, dict]:
    """Extract, store, and format an inventory from image files."""
    try:
        data = extract_inventory_images(image_paths)
    except Exception as exc:
        logger.exception("Inventory image handler failed: %s", exc)
        data = {"equipment": [], "stock": [], "parse_error": True}
    user_id = state.get("user_id", "")
    source = image_paths[0] if image_paths else ""
    parse_error = bool(data.get("parse_error"))
    eq_added, eq_merged = merge_equipment(user_id, data.get("equipment") or [], source)
    st_added, st_merged = merge_stock(user_id, data.get("stock") or [], source)
    state["last_context"] = {"type": "inventory_import", "doc_type": doc_type}
    return format_inventory_response(eq_added, eq_merged, st_added, st_merged, parse_error), state


def _handle_inventory_file(file_path: str, content_type: str, state: dict) -> Tuple[str, dict]:
    """Process an Excel or CSV inventory file uploaded directly."""
    lower_ct = content_type.lower()
    try:
        if lower_ct in _EXCEL_CONTENT_TYPES or file_path.lower().endswith((".xlsx", ".xls")):
            data = extract_inventory_from_excel(file_path)
        else:
            data = extract_inventory_from_csv(file_path)
    except Exception as exc:
        logger.exception("Inventory file handler failed file=%s: %s", file_path, exc)
        data = {"equipment": [], "stock": [], "parse_error": True}

    if data.get("encoding_error"):
        return (
            "DECISION:\nCSV ENCODING NOT SUPPORTED\n\n"
            "WHY:\nThe file could not be decoded using common CSV encodings.\n\n"
            "RECOMMENDED ACTIONS:\n"
            "• Re-export as CSV UTF-8\n"
            "• Or upload the Excel file directly"
        ), state

    user_id = state.get("user_id", "")
    parse_error = bool(data.get("parse_error"))
    skipped_rows = data.get("skipped_rows", 0)
    eq_added, eq_merged = merge_equipment(user_id, data.get("equipment") or [], file_path)
    st_added, st_merged = merge_stock(user_id, data.get("stock") or [], file_path)
    state["last_context"] = {"type": "inventory_import"}
    return format_inventory_response(eq_added, eq_merged, st_added, st_merged, parse_error, skipped_rows), state


# ---------------------------------------------------------------------------
# Inventory reset helpers
# ---------------------------------------------------------------------------

def _equipment_reset_response() -> str:
    return (
        "DECISION:\nEQUIPMENT MEMORY RESET\n\n"
        "WHY:\nCleared saved equipment records.\n\n"
        "RECOMMENDED ACTIONS:\n"
        "• Upload a machinery list to rebuild equipment memory\n"
        "• Ask \"show equipment\" after upload"
    )


# ---------------------------------------------------------------------------
# Inventory retrieval helpers
# ---------------------------------------------------------------------------

def _handle_show_equipment(state: dict) -> Tuple[str, dict]:
    user_id = state.get("user_id", "")
    all_items = get_all_equipment(user_id)
    items = [it for it in all_items if not is_junk_equipment_name(it.get("equipment_name") or "")]
    if not items:
        return _make_response(
            decision="NO EQUIPMENT RECORDS",
            why="No equipment has been imported yet.",
            actions=["Upload an equipment list (Excel, CSV, or PDF)"],
        ), state
    lines = [
        "DECISION:",
        "EQUIPMENT FOUND",
        "",
        "WHY:",
        f"Found {len(items)} equipment records in vessel memory.",
        "",
        "EQUIPMENT:",
    ]
    for item in items[:20]:
        name = item.get("equipment_name") or item.get("system") or "Unknown"
        make = item.get("make") or ""
        model = item.get("model") or ""
        serial = item.get("serial_number") or ""
        loc = item.get("location") or ""
        sys = item.get("system") or ""
        detail_parts = [p for p in [make, model] if p]
        detail = f" — {' '.join(detail_parts)}" if detail_parts else ""
        loc_str = f" ({loc})" if loc else ""
        sys_str = f" [{sys}]" if sys and sys != name else ""
        serial_str = f" s/n {serial}" if serial else ""
        lines.append(f"• {name}{detail}{serial_str}{sys_str}{loc_str}")
    if len(items) > 20:
        lines.append(f"... and {len(items) - 20} more")
    return "\n".join(lines), state


def _handle_show_stock(state: dict) -> Tuple[str, dict]:
    user_id = state.get("user_id", "")
    from storage_paths import get_yacht_id_for_user as _gyid, get_stock_memory_path as _gsp
    _yid = _gyid(user_id)
    items = get_all_stock(user_id)
    logger.info(
        "whatsapp_app: show_stock user=%s yacht_id=%s path=%s records=%d",
        user_id, _yid, _gsp(_yid), len(items),
    )
    if not items:
        return _make_response(
            decision="NO STOCK RECORDS",
            why="No stock has been imported yet.",
            actions=["Upload a stock list or spare parts inventory"],
        ), state
    lines = [f"STOCK ({len(items)} items):\n"]
    for item in items[:25]:
        desc = item.get("description") or item.get("part_number") or "Unknown"
        qty = item.get("quantity_onboard")
        unit = item.get("unit") or ""
        loc = item.get("storage_location") or ""
        pn = item.get("part_number") or ""
        qty_str = f" — Qty: {qty}{(' ' + unit).rstrip()}" if qty is not None else ""
        loc_str = f" [{loc}]" if loc else ""
        pn_str = f" ({pn})" if pn and pn != desc else ""
        lines.append(f"• {desc}{pn_str}{qty_str}{loc_str}")
    if len(items) > 25:
        lines.append(f"... and {len(items) - 25} more")
    return "\n".join(lines).strip(), state


def _extract_subject_from_query(query: str, prefixes: list) -> str:
    t = query.lower().strip()
    for prefix in sorted(prefixes, key=len, reverse=True):
        if prefix in t:
            idx = t.index(prefix) + len(prefix)
            return query[idx:].strip().rstrip("?").strip()
    return query.strip()


def _handle_stock_query(query: str, state: dict) -> Tuple[str, dict]:
    user_id = state.get("user_id", "")
    from storage_paths import get_yacht_id_for_user as _gyid, get_stock_memory_path as _gsp
    _yid = _gyid(user_id)
    logger.info(
        "whatsapp_app: stock_query user=%s yacht_id=%s path=%s query=%r",
        user_id, _yid, _gsp(_yid), query,
    )
    subject = _extract_subject_from_query(query, [
        "do we have ", "do we stock ", "have we got ", "is there any ",
        "do we carry ", "how many do we have ", "how much do we have ",
        "how many have we got ", "do we have any ",
    ])
    if not subject:
        subject = query
    results = find_stock_by_query(user_id, subject)
    if not results:
        return _make_response(
            decision="NO ONBOARD STOCK MATCH",
            why=f"No onboard stock match found for '{subject}'.",
            actions=["Check spelling", "Upload your stock list to build onboard memory"],
        ), state
    lines = [f"STOCK FOUND — '{subject}':\n"]
    for item in results[:8]:
        desc = item.get("description") or item.get("part_number") or "Unknown"
        qty = item.get("quantity_onboard")
        unit = item.get("unit") or ""
        loc = item.get("storage_location") or ""
        pn = item.get("part_number") or ""
        conf = item.get("confidence", 0.7)
        conf_label = "✓" if conf >= 0.75 else "~"
        qty_str = f"Qty: {qty}{(' ' + unit).rstrip()}" if qty is not None else "Qty: unknown"
        loc_str = f" | Location: {loc}" if loc else ""
        pn_str = f" | P/N: {pn}" if pn else ""
        lines.append(f"{conf_label} {desc}{pn_str} — {qty_str}{loc_str}")
    return "\n".join(lines).strip(), state


def _handle_spares_query(query: str, state: dict) -> Tuple[str, dict]:
    user_id = state.get("user_id", "")
    from storage_paths import get_yacht_id_for_user as _gyid, get_stock_memory_path as _gsp
    _yid = _gyid(user_id)
    logger.info(
        "whatsapp_app: spares_query user=%s yacht_id=%s path=%s query=%r",
        user_id, _yid, _gsp(_yid), query,
    )
    system = _extract_subject_from_query(query, [
        "show spares for ", "spares for ", "spare parts for ",
        "what spares for ", "what spares do we have for ", "parts for ",
        "what stock do we have for ", "stock for ", "what stock for ",
    ])
    if not system:
        system = query
    results = find_stock_for_system(user_id, system)
    if not results:
        return _make_response(
            decision="NO SPARES FOUND",
            why=f"No spare parts found linked to '{system}'. "
                "This may mean no spares are recorded or the system name doesn't match.",
            actions=[
                "Check the system name matches your stock list",
                "Upload a spare parts list to build onboard memory",
            ],
        ), state
    lines = [f"SPARES FOR '{system.upper()}' ({len(results)} items):\n"]
    for item in results[:10]:
        desc = item.get("description") or "Unknown"
        qty = item.get("quantity_onboard")
        unit = item.get("unit") or ""
        loc = item.get("storage_location") or ""
        pn = item.get("part_number") or ""
        qty_str = f"Qty: {qty}{(' ' + unit).rstrip()}" if qty is not None else ""
        loc_str = f" [{loc}]" if loc else ""
        pn_str = f" ({pn})" if pn else ""
        lines.append(f"• {desc}{pn_str} — {qty_str}{loc_str}")
    return "\n".join(lines).strip(), state


def _handle_equipment_query(query: str, state: dict) -> Tuple[str, dict]:
    user_id = state.get("user_id", "")
    t = query.lower().strip()

    is_serial_query = "serial" in t
    is_count_query = t.startswith("how many")
    is_spec_query = any(w in t for w in ("spec", "specification", "specifications", "specs"))

    # Extract the subject by stripping the longest matching prefix (longest first)
    subject = _extract_subject_from_query(query, [
        # Serial number queries
        "what is the serial number for the ",
        "what is the serial number of the ",
        "what is the serial number for ",
        "what is the serial number of ",
        "serial number for the ",
        "serial number of the ",
        "serial number for ",
        "serial number of ",
        # Spec queries
        "what are the specifications of the ",
        "what are the specifications of ",
        "what are the specs of the ",
        "what are the specs of ",
        "what is the spec of the ",
        "what is the spec of ",
        "specifications of the ",
        "specifications of ",
        "specification of the ",
        "specification of ",
        "specs of the ",
        "specs of ",
        # Make/model/equipment queries
        "what equipment do we have from ",
        "what machinery do we have from ",
        "what equipment from ",
        "what equipment by ",
        "equipment from ",
        "what make is our ",
        "what make is the ",
        "what make is ",
        "what model is our ",
        "what model is the ",
        "what model is ",
        "what is serial number ",
        "what is serial ",
        "what is fitted to ",
        "what is installed on ",
        "fitted to ",
    ])

    # For "how many X do we have / are there", extract X
    if is_count_query:
        m = re.match(
            r'^how many\s+(.+?)(?:\s+do\s+we\s+have|\s+are\s+(?:there|onboard|on\s+board))?\??$',
            t,
        )
        if m:
            subject = m.group(1).strip()

    # Strip leading articles so "the UV unit" → "UV unit"
    for article in ("the ", "our ", "a ", "an "):
        if subject.lower().startswith(article):
            subject = subject[len(article):]
            break

    subject = subject.strip().rstrip("?").strip()
    if not subject:
        subject = query.strip()

    # "fitted to / installed on" queries: find stock linked to that equipment
    if any(p in t for p in ("fitted to", "installed on", "what is this fitted", "what is that fitted")):
        results = find_stock_for_system(user_id, subject)
        if not results:
            return (
                f"No parts found linked to '{subject}' in onboard stock records.\n"
                "Upload your stock list to build this context."
            ), state
        lines = [f"PARTS FITTED TO '{subject.upper()}':\n"]
        for item in results[:8]:
            lines.append(f"• {item.get('description') or item.get('part_number') or 'Unknown'}")
        return "\n".join(lines).strip(), state

    results, broad_note = find_equipment_by_query(user_id, subject)

    if not results:
        return _make_response(
            decision="NO EQUIPMENT MATCH FOUND",
            why=f"I searched vessel equipment memory but could not find a clear match for '{subject}'.",
            actions=[
                "Try the equipment name, make, or model",
                "Upload the relevant machinery list if not already imported",
            ],
        ), state

    def _eq_lines(items, limit, show_serial=False):
        """Build the EQUIPMENT bullet lines."""
        lines = []
        for item in items[:limit]:
            name = item.get("equipment_name") or "Unknown"
            make = item.get("make") or ""
            model = item.get("model") or ""
            sn = item.get("serial_number") or ""
            sys = item.get("system") or ""
            loc = item.get("location") or ""
            detail = f" — {' '.join(p for p in [make, model] if p)}" if (make or model) else ""
            if show_serial:
                sn_str = f", Serial: {sn}" if sn else ", Serial: not recorded"
                sys_str = ""
            else:
                sn_str = f" s/n {sn}" if sn else ""
                sys_str = f" [{sys}]" if sys and sys.lower() != name.lower() else ""
            loc_str = f" ({loc})" if loc else ""
            lines.append(f"• {name}{detail}{sn_str}{sys_str}{loc_str}")
        return lines

    # --- Count response ---
    if is_count_query:
        count = len(results)
        why = f"Found {count} record{'s' if count != 1 else ''} matching '{subject}' in vessel memory."
        if broad_note:
            why += f"\n{broad_note}"
        lines = [
            "DECISION:", f"EQUIPMENT COUNT — {subject.upper()}", "",
            "WHY:", why, "", "EQUIPMENT:",
        ] + _eq_lines(results, 10)
        return "\n".join(lines), state

    # --- Serial number response ---
    if is_serial_query:
        decision = "EQUIPMENT FOUND" if len(results) == 1 else "MULTIPLE EQUIPMENT MATCHES"
        why = "Found matching equipment in vessel memory." if len(results) == 1 else f"Found {len(results)} matching records."
        if broad_note:
            why += f"\n{broad_note}"
        lines = [
            "DECISION:", decision, "",
            "WHY:", why, "", "EQUIPMENT:",
        ] + _eq_lines(results, 5, show_serial=True)
        return "\n".join(lines), state

    # --- Spec / general response ---
    decision = "EQUIPMENT FOUND" if len(results) == 1 else "MULTIPLE EQUIPMENT MATCHES"
    why = f"Found {len(results)} record{'s' if len(results) != 1 else ''} matching '{subject}' in vessel memory."
    if broad_note:
        why += f"\n{broad_note}"
    lines = [
        "DECISION:", decision, "",
        "WHY:", why, "", "EQUIPMENT:",
    ] + _eq_lines(results, 10)
    return "\n".join(lines), state


def _get_stock_ordering_note(query: str, state: dict) -> Optional[str]:
    """
    If we have onboard stock for the item the user is asking about ordering,
    return a 'check stock first' message. Returns None if no match.
    """
    user_id = state.get("user_id", "")
    last_ctx = state.get("last_context", {})

    # Try to derive the subject from document context first
    subject = ""
    doc_ctx = _build_document_context(state)
    if doc_ctx:
        m = re.search(r'(?:description|item)[:\s]+([^\n,]{3,60})', doc_ctx, re.IGNORECASE)
        if m:
            subject = m.group(1).strip()

    if not subject:
        subject = _extract_subject_from_query(query, [
            "how many should i order", "how many should we order",
            "how many to order", "should i order", "should we order",
        ])

    if not subject or len(subject) < 3:
        return None

    results = find_stock_by_query(user_id, subject)
    if not results:
        return None

    best = results[0]
    qty = best.get("quantity_onboard")
    loc = best.get("storage_location") or ""
    desc = best.get("description") or subject
    qty_str = f"quantity {qty}" if qty is not None else "quantity unknown"
    loc_str = f" in {loc}" if loc else ""

    return (
        "DECISION:\nCHECK STOCK BEFORE ORDERING\n\n"
        f"WHY:\n'{desc}' appears to be held onboard with {qty_str}{loc_str}. "
        "Verify current stock before placing an order.\n\n"
        "RECOMMENDED ACTIONS:\n"
        f"• Physically verify stock in{loc_str if loc_str else ' storage'}\n"
        "• Order only the shortfall if stock is below minimum level\n"
        "• Update stock records after consumption"
    )


def _build_equipment_context(state: dict) -> str:
    """
    Return a brief equipment context note if we have onboard records that
    match the last uploaded document's subject — for enriching market checks.
    """
    user_id = state.get("user_id", "")
    last_ctx = state.get("last_context", {})
    supplier = last_ctx.get("supplier", "")
    doc_ctx = _build_document_context(state)
    if not doc_ctx:
        return ""

    # Try to find a match from document context line items / supplier
    search_terms = []
    if supplier:
        search_terms.append(supplier)
    for m in re.finditer(r'(?:description|item|equipment)[:\s]+([^\n,]{3,60})', doc_ctx, re.IGNORECASE):
        search_terms.append(m.group(1).strip())

    for term in search_terms[:3]:
        eq, _ = find_equipment_by_query(user_id, term)
        if eq:
            item = eq[0]
            name = item.get("equipment_name") or item.get("system") or term
            make = item.get("make") or ""
            model = item.get("model") or ""
            detail = ", ".join(p for p in [make, model] if p)
            note = f"Onboard equipment match: {name}"
            if detail:
                note += f" ({detail})"
            return note

    return ""


_NON_COMMERCIAL_SUBTYPES = {"service_report", "operational_notes", "technical_note"}


def _classify_non_commercial_subtype(extracted: dict) -> str:
    """
    Return 'service_report', 'operational_notes', or 'technical_note' for a
    non-commercial document extraction.  Uses the vision model's doc_type when
    it is one of the three valid subtypes; defaults to 'operational_notes'.
    """
    raw = (extracted.get("doc_type") or "").strip().lower()
    return raw if raw in _NON_COMMERCIAL_SUBTYPES else "operational_notes"


def _format_operational_notes_response(summary_data: dict) -> str:
    """Build the NOTES SUMMARISED WhatsApp response from a structured summary dict."""
    summary = (summary_data.get("summary") or "").strip()
    issues = summary_data.get("issues") or []
    open_actions = summary_data.get("open_actions") or []

    issue_lines = "\n".join(f"• {i.strip()}" for i in issues) if issues else "• None identified"
    action_lines = "\n".join(f"• {a.strip()}" for a in open_actions) if open_actions else "• None identified"

    return (
        "DECISION:\nNOTES SUMMARISED\n\n"
        "WHY:\nHandwritten operational notes have been converted into a structured summary.\n\n"
        f"SUMMARY:\n{summary}\n\n"
        f"ISSUES / RISKS:\n{issue_lines}\n\n"
        f"OPEN ACTIONS:\n{action_lines}\n\n"
        "RECOMMENDED ACTIONS:\n"
        '• Reply "add to handover notes" to save this summary\n'
        '• Reply "show open actions" to view outstanding actions'
    )


def _handle_operational_notes_image(file_paths: list, state: dict) -> Tuple[str, dict]:
    """Summarise an operational or technical note image. Does NOT auto-save."""
    try:
        summary_data = summarise_operational_note_from_image(file_paths[0])
    except Exception as exc:
        logger.exception("Operational notes image handler failed: %s", exc)
        return _image_received_response(), state

    response = _format_operational_notes_response(summary_data)
    state["last_context"] = {
        "type": "doc_summary",
        "doc_subtype": summary_data.get("doc_subtype") or "operational_notes",
        "summary": summary_data.get("summary") or "",
        "issues": summary_data.get("issues") or [],
        "open_actions": summary_data.get("open_actions") or [],
        "source_file": file_paths[0] if file_paths else "",
    }
    logger.info(
        "operational_notes: summarised doc_subtype=%r open_actions=%d",
        summary_data.get("doc_subtype"), len(summary_data.get("open_actions") or []),
    )
    return response, state


def _handle_add_to_handover(state: dict) -> Tuple[str, dict]:
    """Explicitly save the most recent doc_summary from last_context to handover notes."""
    last_ctx = state.get("last_context") or {}
    ctx_type = last_ctx.get("type")
    user_id = state.get("user_id", "")

    if ctx_type == "doc_summary":
        save_notes_summary(
            user_id=user_id,
            summary_data=last_ctx,
            source_file=last_ctx.get("source_file") or "",
        )
        return _make_response(
            decision="ADDED TO HANDOVER NOTES",
            why="The latest summary has been saved to handover notes.",
            actions=[
                'Reply "show handover notes" to review saved notes',
                'Reply "show open actions" to review outstanding actions',
            ],
        ), state

    if ctx_type == "service_report":
        return _make_response(
            decision="SERVICE REPORT ALREADY SAVED",
            why="Service reports are saved to service records automatically.",
            actions=[
                'Reply "show handover notes" to review saved service reports',
            ],
        ), state

    return _make_response(
        decision="NO SUMMARY TO SAVE",
        why="There is no recent summary available to add to handover notes.",
        actions=["Upload notes or a service report first"],
    ), state


def _handle_service_report_doc(doc_record: dict, state: dict) -> Tuple[str, dict]:
    """Handle a service_report doc_record produced by _extract_pdf_to_doc_record."""
    report = doc_record.get("service_report_data") or {}
    user_id = state.get("user_id", "")
    handover_note = build_handover_note(report)
    save_service_report(user_id, report, handover_note, doc_record.get("file_path", ""))
    state["last_context"] = {
        "type": "service_report",
        "system": report.get("system") or report.get("equipment") or "",
        "supplier": report.get("supplier") or "",
    }
    return format_service_report_response(report, handover_note), state


def _handle_service_report_image(image_paths: list, state: dict) -> Tuple[str, dict]:
    """Extract, store, and format a service report from image files."""
    try:
        report = extract_service_report_from_images(image_paths)
    except Exception as exc:
        logger.exception("Service report image handler failed: %s", exc)
        report = {}
    user_id = state.get("user_id", "")
    handover_note = build_handover_note(report)
    save_service_report(user_id, report, handover_note, image_paths[0] if image_paths else "")
    state["last_context"] = {
        "type": "service_report",
        "system": report.get("system") or report.get("equipment") or "",
        "supplier": report.get("supplier") or "",
    }
    return format_service_report_response(report, handover_note), state


def _handle_manual_doc(doc_record: dict, state: dict) -> Tuple[str, dict]:
    """Save a technical manual doc_record and return the MANUAL IMPORTED response."""
    manual = doc_record.get("manual_data") or {}
    chunks = doc_record.get("manual_chunks") or []
    user_id = state.get("user_id", "")
    save_manual(user_id, manual, chunks, doc_record.get("file_path", ""))
    state["last_context"] = {
        "type": "manual_imported",
        "manufacturer": manual.get("manufacturer") or "",
        "product_name": manual.get("product_name") or "",
        "system": manual.get("system") or "",
    }
    return format_manual_import_response(manual), state


def _handle_show_manuals(state: dict) -> Tuple[str, dict]:
    """List all saved manuals."""
    user_id = state.get("user_id", "")
    manuals = get_all_manuals(user_id)
    if not manuals:
        return _make_response(
            decision="NO MANUALS SAVED",
            why="No technical manuals have been imported yet.",
            actions=["Upload a PDF manual to add it to the library"],
        ), state

    lines = [f"MANUAL LIBRARY ({len(manuals)} manual(s)):\n"]
    for m in manuals:
        parts = [p for p in [m.get("manufacturer"), m.get("product_name")] if p]
        title = " ".join(parts) if parts else m.get("document_type") or "Manual"
        doc_type = m.get("document_type") or ""
        system = m.get("system") or ""
        meta = " — ".join(filter(None, [doc_type, system]))
        lines.append(f"• {title}" + (f" ({meta})" if meta else ""))

    lines.append("")
    lines.append('Reply "search manual for [topic]" to find information')
    return "\n".join(lines).strip(), state


_MANUAL_QUERY_PREFIXES = (
    "search manual for ", "search the manual for ",
    "in the manual for ", "manual for ",
    "what does the manual say about ",
    "what does the manual ",
    "find in the manual ",
    "look up in the manual ",
    "according to the manual ",
    "in the manual ",
    "from the manual ",
    "search manual ",
    "search the manual ",
)


def _extract_manual_query(text: str) -> str:
    """Strip command prefix and return the bare search query."""
    t = text.lower().strip()
    for prefix in _MANUAL_QUERY_PREFIXES:
        if t.startswith(prefix):
            return text[len(prefix):].strip()
        if prefix.rstrip() in t:
            idx = t.index(prefix.rstrip()) + len(prefix.rstrip())
            return text[idx:].strip()
    return text.strip()


def _handle_manual_search(query: str, state: dict) -> Tuple[str, dict]:
    """Answer a question by searching saved manual chunks."""
    user_id = state.get("user_id", "")
    search_q = _extract_manual_query(query)
    if not search_q:
        search_q = query

    chunks = search_manual_chunks(user_id, search_q, top_k=4)
    if not chunks:
        # Try broader search — all manuals
        all_manuals = get_all_manuals(user_id)
        if not all_manuals:
            return _make_response(
                decision="NO MANUALS AVAILABLE",
                why="No technical manuals have been imported yet.",
                actions=["Upload a PDF manual to search it"],
            ), state
        return _make_response(
            decision="NOT FOUND IN MANUALS",
            why=f"No relevant content found for '{search_q}' in saved manuals.",
            actions=[
                "Try different search terms",
                'Reply "show manuals" to see what manuals are available',
            ],
        ), state

    manual_label = chunks[0].get("manual_label") or "manual"
    answer = answer_manual_question(search_q, chunks, manual_label)

    state["last_context"] = {
        "type": "manual_search",
        "query": search_q,
        "manual_label": manual_label,
    }

    return (
        f"DECISION:\nMANUAL SEARCH RESULT\n\n"
        f"WHY:\nFound relevant content in the {manual_label}.\n\n"
        f"ANSWER:\n{answer}\n\n"
        f"SOURCE:\n• {manual_label}\n\n"
        f"RECOMMENDED ACTIONS:\n"
        f"• Search again with more specific terms\n"
        f'• Reply "show manuals" to see all available manuals'
    ), state


def _parse_system_from_query(query: str) -> str:
    """Extract the system name from retrieval queries like 'handover for OWS'."""
    t = query.lower().strip()
    for prefix in (
        "handover for ", "service reports for ", "service report for ",
        "show handover for ", "handover note for ", "reports for ",
    ):
        if prefix in t:
            idx = t.index(prefix) + len(prefix)
            return query[idx:].strip()
    return ""


def _handle_show_handover_notes(query: str, state: dict) -> Tuple[str, dict]:
    """Return saved service report handover notes, optionally filtered by system."""
    user_id = state.get("user_id", "")
    system_filter = _parse_system_from_query(query)
    if system_filter:
        reports = get_reports_for_system(user_id, system_filter)
    else:
        reports = get_all_reports(user_id)

    if not reports:
        label = f" for '{system_filter}'" if system_filter else ""
        return _make_response(
            decision="NO SERVICE REPORTS FOUND",
            why=f"No service reports have been saved{label}.",
            actions=["Upload a service report to create a handover note"],
        ), state

    lines = [f"HANDOVER NOTES ({len(reports)} report(s)):\n"]
    for r in reports[-5:]:  # most recent five
        system_label = r.get("system") or r.get("equipment") or "Unknown system"
        date = r.get("date") or ""
        supplier = r.get("supplier") or ""
        header = " — ".join(filter(None, [date, supplier, system_label]))
        lines.append(header)
        note = (r.get("handover_note") or r.get("summary") or "").strip()
        if note:
            lines.append(note)
        lines.append("")

    return "\n".join(lines).strip(), state


def _handle_show_open_actions(state: dict) -> Tuple[str, dict]:
    """Return all open actions grouped by system."""
    user_id = state.get("user_id", "")
    grouped = get_all_open_actions(user_id)

    if not grouped:
        return _make_response(
            decision="NO OPEN ACTIONS",
            why="No open actions found in saved service reports.",
            actions=["Upload a service report to track open actions"],
        ), state

    lines = ["OPEN ACTIONS:\n"]
    for group in grouped:
        system = group["system"]
        date = group.get("date") or ""
        header = system + (f" ({date})" if date else "")
        lines.append(header + ":")
        for action in group["open_actions"]:
            lines.append(f"• {action.strip()}")
        lines.append("")

    return "\n".join(lines).strip(), state


def _handle_show_compliance_sources(state: dict) -> Tuple[str, dict]:
    """List all loaded global regulation sources."""
    sources = list_compliance_sources()
    if not sources:
        return _make_response(
            decision="NO REGULATIONS LOADED",
            why="The global compliance knowledge base is empty.",
            actions=[
                "Upload a compliance PDF to add it",
                'Reply "reload compliance" to rebuild the index',
            ],
        ), state

    reg_lines = "\n".join(f"• {s['source']}" for s in sources)
    total_chunks = sum(s["chunks"] for s in sources)
    response = (
        f"DECISION:\nREGULATIONS FOUND\n\n"
        f"WHY:\nGlobal compliance database contains {len(sources)} loaded regulation document(s) "
        f"({total_chunks} sections).\n\n"
        f"REGULATIONS:\n{reg_lines}\n\n"
        f"RECOMMENDED ACTIONS:\n"
        f'• Reply "show compliance profile" to see selected regulations for this vessel\n'
        f'• Reply "search compliance for [topic]" to search\n'
        f'• Reply "reload compliance" to rebuild the index after uploading new documents'
    )
    return response, state


def _handle_reload_compliance(state: dict) -> Tuple[str, dict]:
    """Rebuild the compliance index from stored chunks."""
    try:
        count = rebuild_compliance_index()
        _reset_compliance_retriever()
        return _make_response(
            decision="COMPLIANCE INDEX REBUILT",
            why=f"The compliance knowledge base has been rebuilt from {count} sections.",
            actions=[
                'Reply "show compliance sources" to see loaded regulations',
                'Reply "search compliance for [topic]" to search',
            ],
        ), state
    except Exception as exc:
        logger.exception("reload_compliance failed: %s", exc)
        return _make_response(
            decision="REBUILD FAILED",
            why=f"Could not rebuild the compliance index: {exc}",
            actions=["Check server logs for details"],
        ), state


def _handle_show_compliance_profile(state: dict) -> Tuple[str, dict]:
    user_id = state.get("user_id", "")
    from storage_paths import get_yacht_id_for_user
    yacht_id = get_yacht_id_for_user(user_id)
    profile = load_compliance_profile(yacht_id)
    selected = profile.get("selected_regulations", [])
    vessel_docs = profile.get("vessel_documents", [])

    if not selected and not vessel_docs:
        return _make_response(
            decision="NO COMPLIANCE PROFILE SET",
            why=f"{yacht_id.upper()} has no selected regulations or vessel procedures yet.",
            actions=[
                f'Reply "enable ISM Code for {yacht_id}" to select a regulation',
                "Upload your SMS to add a vessel procedure",
                'Reply "show global regulations" to see available regulations',
            ],
        ), state

    lines = [f"COMPLIANCE PROFILE — {yacht_id.upper()}\n"]
    if selected:
        lines.append("APPLICABLE REGULATIONS:")
        for r in selected:
            lines.append(f"• {r}")
        lines.append("")
    if vessel_docs:
        lines.append("VESSEL PROCEDURES:")
        for d in vessel_docs:
            lines.append(f"• {d.get('name', 'Unknown')}")
        lines.append("")
    if not selected:
        lines.append("No regulations selected yet.")
    if not vessel_docs:
        lines.append("No vessel procedures uploaded yet.")
    return "\n".join(lines).strip(), state


def _handle_show_selected_regulations(state: dict) -> Tuple[str, dict]:
    user_id = state.get("user_id", "")
    from storage_paths import get_yacht_id_for_user
    yacht_id = get_yacht_id_for_user(user_id)
    selected = get_selected_regulations(yacht_id)
    if not selected:
        return _make_response(
            decision="NO REGULATIONS SELECTED",
            why=f"{yacht_id.upper()} has no selected regulations yet.",
            actions=[
                f'Reply "enable ISM Code for {yacht_id}" to select a regulation',
                'Reply "show global regulations" to see available regulations',
            ],
        ), state
    lines = [f"SELECTED REGULATIONS — {yacht_id.upper()} ({len(selected)}):\n"]
    for r in selected:
        lines.append(f"• {r}")
    return "\n".join(lines).strip(), state


def _handle_show_vessel_procedures(state: dict) -> Tuple[str, dict]:
    user_id = state.get("user_id", "")
    from storage_paths import get_yacht_id_for_user
    yacht_id = get_yacht_id_for_user(user_id)
    docs = list_vessel_documents(yacht_id)
    if not docs:
        return _make_response(
            decision="NO VESSEL PROCEDURES",
            why=f"No vessel procedures have been uploaded for {yacht_id.upper()} yet.",
            actions=[
                "Upload your SMS or vessel procedures to get started",
                'Reply "show compliance profile" for the full profile',
            ],
        ), state
    lines = [f"VESSEL PROCEDURES — {yacht_id.upper()} ({len(docs)}):\n"]
    for d in docs:
        name = d.get("name", "Unknown")
        doc_type = d.get("type", "")
        lines.append(f"• {name}" + (f" ({doc_type})" if doc_type else ""))
    return "\n".join(lines).strip(), state


def _handle_enable_regulation(query: str, state: dict) -> Tuple[str, dict]:
    user_id = state.get("user_id", "")
    from storage_paths import get_yacht_id_for_user
    yacht_id = get_yacht_id_for_user(user_id)
    rest = query.strip()
    if rest.lower().startswith("enable "):
        rest = rest[len("enable "):].strip()
    # Extract regulation name before " for "
    lower_rest = rest.lower()
    if " for " in lower_rest:
        idx = lower_rest.rfind(" for ")
        reg_name = rest[:idx].strip()
    else:
        reg_name = rest.strip()
    if not reg_name:
        return _make_response(
            decision="REGULATION NOT SPECIFIED",
            why="Could not determine which regulation to enable.",
            actions=[f'Try: "enable ISM Code for {yacht_id}"'],
        ), state
    added = enable_compliance_regulation(yacht_id, reg_name)
    if added:
        return _make_response(
            decision="REGULATION ENABLED",
            why=f"{reg_name} is now selected for {yacht_id.upper()} compliance answers.",
            actions=[
                f"Ask a {reg_name} question",
                'Reply "show compliance profile" to review the full profile',
            ],
        ), state
    return _make_response(
        decision="ALREADY ENABLED",
        why=f"{reg_name} is already selected for {yacht_id.upper()}.",
        actions=['Reply "show compliance profile" to review the full profile'],
    ), state


def _handle_disable_regulation(query: str, state: dict) -> Tuple[str, dict]:
    user_id = state.get("user_id", "")
    from storage_paths import get_yacht_id_for_user
    yacht_id = get_yacht_id_for_user(user_id)
    rest = query.strip()
    if rest.lower().startswith("disable "):
        rest = rest[len("disable "):].strip()
    lower_rest = rest.lower()
    if " for " in lower_rest:
        idx = lower_rest.rfind(" for ")
        reg_name = rest[:idx].strip()
    else:
        reg_name = rest.strip()
    if not reg_name:
        return _make_response(
            decision="REGULATION NOT SPECIFIED",
            why="Could not determine which regulation to disable.",
            actions=[f'Try: "disable ISM Code for {yacht_id}"'],
        ), state
    removed = disable_compliance_regulation(yacht_id, reg_name)
    if removed:
        return _make_response(
            decision="REGULATION DISABLED",
            why=f"{reg_name} has been removed from {yacht_id.upper()} compliance answers.",
            actions=['Reply "show compliance profile" to review the updated profile'],
        ), state
    return _make_response(
        decision="NOT IN PROFILE",
        why=f"{reg_name} was not in the selected regulations for {yacht_id.upper()}.",
        actions=['Reply "show compliance profile" to see what is selected'],
    ), state


def _handle_yacht_compliance_doc(doc_record: dict, state: dict) -> Tuple[str, dict]:
    """Store a yacht SMS or procedure document under the active yacht's compliance folder."""
    user_id = state.get("user_id", "")
    from storage_paths import get_yacht_id_for_user
    yacht_id = get_yacht_id_for_user(user_id)
    doc_type = doc_record.get("doc_type", "yacht_procedure")
    file_path = doc_record.get("file_path", "")
    source_name = doc_record.get("source_name") or (
        os.path.splitext(os.path.basename(file_path))[0].replace("-", " ").replace("_", " ").strip()
    )
    try:
        ingest_yacht_compliance_pdf(file_path, source_name, yacht_id, doc_type)
    except Exception as exc:
        logger.exception("yacht_compliance_doc: ingest failed yacht=%s: %s", yacht_id, exc)
        return _make_response(
            decision="IMPORT FAILED",
            why=f"Could not process {source_name}: {exc}",
            actions=["Check that the PDF contains selectable text"],
        ), state
    subtype = "sms" if doc_type == "yacht_sms" else "procedure"
    add_vessel_document(yacht_id, {
        "name": source_name,
        "type": subtype,
        "path": f"compliance/{subtype}/{os.path.basename(file_path)}",
    })
    logger.info(
        "whatsapp_app: yacht compliance doc imported name=%r type=%s yacht=%s user=%s",
        source_name, doc_type, yacht_id, user_id,
    )
    if doc_type == "yacht_sms":
        return _make_response(
            decision="YACHT SMS IMPORTED",
            why=f"{source_name} has been stored as yacht-specific compliance guidance "
                f"and is available to all {yacht_id.upper()} users.",
            actions=[
                'Ask "what is our defect reporting procedure?"',
                'Ask "what does our SMS say about maintenance?"',
                'Reply "show vessel procedures" to see all loaded procedures',
            ],
        ), state
    return _make_response(
        decision="YACHT PROCEDURE IMPORTED",
        why=f"{source_name} has been stored as a vessel procedure for {yacht_id.upper()} "
            f"and is available to all users on this vessel.",
        actions=[
            'Ask "what is our procedure for..." to query it',
            'Reply "show vessel procedures" to see all loaded procedures',
        ],
    ), state


def _handle_compliance_pdf_upload(file_path: str, state: dict) -> Tuple[str, dict]:
    """Ingest a compliance PDF into the global knowledge base."""
    import os
    filename = os.path.basename(file_path)
    # Derive a friendly source name from the filename
    source_name = os.path.splitext(filename)[0].replace("-", " ").replace("_", " ").strip()
    try:
        total = ingest_compliance_pdf(file_path, source_name)
        _reset_compliance_retriever()
        return _make_response(
            decision="COMPLIANCE DOCUMENT ADDED",
            why=f"{filename} has been added to the global compliance knowledge base.",
            actions=[
                f'Reply "show compliance sources" to confirm it is listed',
                'Reply "search compliance for [topic]" to search',
            ],
        ), state
    except Exception as exc:
        logger.exception("compliance PDF ingest failed: %s", exc)
        return _make_response(
            decision="INGEST FAILED",
            why=f"Could not process {filename}: {exc}",
            actions=["Check that the PDF contains selectable text"],
        ), state


def _extract_pdf_to_doc_record(file_path: str) -> dict:
    """Extract text/images from a PDF and return a normalised doc_record. Does NOT touch state."""
    text = extract_pdf_text(file_path)

    if text.strip():
        filename = os.path.basename(file_path)

        # Compliance document pre-screen: run first so an SMS is not mis-classified
        # as inventory or a technical manual.
        _compliance_type = classify_compliance_doc(text, filename)
        if _compliance_type in ("yacht_sms", "yacht_procedure"):
            logger.info(
                "PDF: compliance document detected doc_type=%s file=%s, routing to yacht compliance",
                _compliance_type, filename,
            )
            source_name = os.path.splitext(filename)[0].replace("-", " ").replace("_", " ").strip()
            return make_compliance_doc_record(_compliance_type, source_name, file_path)

        # Technical manual pre-screen: must run before inventory (manuals contain
        # "parts list", "serial number", etc. that would otherwise trigger inventory).
        if is_technical_manual_text(text):
            logger.info("PDF: technical manual detected in raw text, routing to manual extraction")
            manual = extract_manual_metadata_from_text(text, filename)
            chunks = chunk_manual_text(text)
            return make_manual_doc_record(manual, file_path, chunks)

        # Inventory pre-screen: check raw text BEFORE service report / commercial extraction.
        inv_type = classify_inventory_text(text)
        if inv_type:
            logger.info("PDF: inventory detected doc_type=%s, routing to inventory extraction", inv_type)
            data = extract_inventory_from_text(text)
            return make_inventory_doc_record(data, inv_type, file_path)

        # Service report pre-screen: check raw text BEFORE commercial extraction.
        if is_service_report_text(text):
            logger.info("PDF: service report detected in raw text, routing to service report extraction")
            report = extract_service_report_from_text(text)
            return make_service_report_doc_record(report, file_path)

    if not text.strip():
        image_paths = render_pdf_pages_to_images(file_path)
        try:
            extracted = extract_commercial_document_from_images(image_paths)
        except json.JSONDecodeError as exc:
            # The classification call returned malformed JSON (typically a large inventory
            # list that was truncated). Fall back to page-by-page inventory extraction
            # so the user gets a partial import instead of FILE ERROR.
            logger.warning(
                "PDF image classification: inventory_json_parse_failed=True "
                "file=%s error=%s — falling back to page-by-page inventory extraction",
                os.path.basename(file_path), exc,
            )
            data = extract_inventory_images(image_paths)
            partial_records_imported = (
                len(data.get("equipment") or []) + len(data.get("stock") or [])
            )
            logger.info(
                "PDF image fallback: partial_records_imported=%d", partial_records_imported,
            )
            return make_inventory_doc_record(data, "equipment_list", file_path)
        if isinstance(extracted, dict):
            raw_type = (extracted.get("doc_type") or "").lower()
            if raw_type in _INVENTORY_DOC_TYPES:
                logger.info("PDF (image): inventory detected doc_type=%s, running extraction", raw_type)
                data = extract_inventory_images(image_paths)
                return make_inventory_doc_record(data, raw_type, file_path)
            # Vision model may also classify as service_report (doc_type option added to prompt)
            if raw_type == "service_report":
                logger.info("PDF (image path): service report detected by vision, running structured extraction")
                report = extract_service_report_from_images(image_paths)
                return make_service_report_doc_record(report, file_path)
            if raw_type == "technical_manual":
                logger.info("PDF (image path): technical manual detected by vision, running manual extraction")
                filename = os.path.basename(file_path)
                manual = extract_manual_metadata_from_images(image_paths, filename)
                return make_manual_doc_record(manual, file_path, [])
    else:
        extracted = extract_commercial_document_with_claude(text)

    if not isinstance(extracted, dict):
        raise ValueError("Document extraction did not return a JSON object")

    extracted = normalise_doc_type(extracted)

    # Second-chance inventory screen: the commercial extractor schema only emits
    # quote/invoice/proforma/null.  If it returned null/unknown and the PDF has
    # extractable text, re-run inventory classification with the full keyword set
    # before falling through to DOCUMENT EXTRACTED.
    _commercial_type = (extracted.get("doc_type") or "").lower()
    if _commercial_type not in ("quote", "invoice", "proforma") and text.strip():
        _inv_type = classify_inventory_text(text)
        if _inv_type:
            logger.info(
                "PDF: inventory detected in second-chance screen doc_type=%s, "
                "routing to inventory extraction",
                _inv_type,
            )
            data = extract_inventory_from_text(text)
            return make_inventory_doc_record(data, _inv_type, file_path)

    return make_document_record(extracted, file_path)


def _dispatch_doc_record(doc_record: dict, state: dict) -> Tuple[str, dict]:
    """Route an already-extracted doc_record through quote/invoice/unknown handling."""
    supplier = doc_record["supplier_name"] or "Unknown supplier"
    total = doc_record["total"]
    currency = doc_record["currency"]
    line_count = len(doc_record["line_items"])
    doc_type = doc_record["doc_type"]

    logger.info("PDF dispatching: type=%s supplier=%s total=%s %s", doc_type, supplier, total, currency)

    # Duplicate detection: skip re-processing a document already in this session.
    # Returns a sentinel so the batch loop can distinguish this from a silent invoice.
    _fp = doc_record.get("fingerprint")
    if _fp and any(
        d.get("fingerprint") == _fp and d.get("doc_type") == doc_type
        for d in state.get("documents", [])
    ):
        logger.info(
            "PDF dispatch: duplicate_skipped=True fingerprint=%s type=%s supplier=%s",
            _fp, doc_type, supplier,
        )
        return "__duplicate__", state

    if doc_type in _INVENTORY_DOC_TYPES:
        answer, state = _handle_inventory_doc(doc_record, state)
        return answer, state

    if doc_type == "service_report":
        answer, state = _handle_service_report_doc(doc_record, state)
        return answer, state

    if doc_type == "technical_manual":
        answer, state = _handle_manual_doc(doc_record, state)
        return answer, state

    if doc_type in ("yacht_sms", "yacht_procedure"):
        answer, state = _handle_yacht_compliance_doc(doc_record, state)
        return answer, state

    if doc_type == "quote":
        answer, state = _handle_quote_upload(doc_record, supplier, total, currency, line_count, state)
        if answer:
            _notes = []
            _deliv = check_invoice_delivery_address(doc_record)
            if _deliv["checked"]:
                _notes.append(DELIVERY_MATCH_NOTE if _deliv["match"] else DELIVERY_MISMATCH_NOTE)
            if _notes:
                answer = answer + "\n\n" + "\n".join(_notes)
    elif doc_type in ("invoice", "proforma"):
        answer, state = _handle_invoice_upload(doc_record, supplier, total, currency, line_count, state)
        if answer:
            _notes = []
            _deliv = check_invoice_delivery_address(doc_record)
            if _deliv["checked"]:
                _notes.append(DELIVERY_MATCH_NOTE if _deliv["match"] else DELIVERY_MISMATCH_NOTE)
            _addr = check_invoice_billing_address(doc_record)
            if _addr["checked"]:
                _notes.append(ADDRESS_MATCH_NOTE if _addr["match"] else ADDRESS_MISMATCH_NOTE)
            if _notes:
                answer = answer + "\n\n" + "\n".join(_notes)
    else:
        state, _ = create_pending_session(doc_record, state)
        answer = _make_response(
            decision="DOCUMENT EXTRACTED",
            why=(
                f"Read an unclassified document from {supplier} with {line_count} line items "
                f"and total {total} {currency}. Could not determine if this is a quote or invoice."
            ),
            actions=[
                "Upload a clearly labelled quote or invoice for proper classification",
                "Start with a quote to open a new comparison session",
            ],
        )

    state = _extract_and_merge_components(doc_record, state)
    if doc_type in ("quote", "invoice", "proforma"):
        state["last_context"] = {
            "type": "document_processed",
            "document_type": doc_type,
            "document_id": doc_record.get("document_id", ""),
            "supplier": supplier,
        }
    return answer, state


def _handle_pdf_upload(file_path: str, state: dict) -> Tuple[str, dict]:
    """Single-attachment path: extract then dispatch immediately."""
    return _dispatch_doc_record(_extract_pdf_to_doc_record(file_path), state)


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
    # Commands handled before intent classification to avoid mis-routing.
    _t = incoming.strip()
    _tl = _t.lower()

    # Equipment memory reset — checked before classify_text so these phrases are
    # never captured by the generic "reset"/"clear" new_session startswith rule.
    # Routing priority: equipment reset > stock reset (future) > comparison reset.
    if _tl in (
        "reset equipment", "clear equipment",
        "reset machinery", "clear machinery",
        "reset equipment memory", "clear equipment memory",
    ):
        _uid = state.get("user_id", "")
        clear_equipment(_uid)
        from storage_paths import get_yacht_id_for_user, get_equipment_memory_path
        _yid = get_yacht_id_for_user(_uid)
        logger.info("equipment_reset: user=%s yacht=%s path=%s", _uid, _yid, get_equipment_memory_path(_yid))
        return _equipment_reset_response(), state

    # Invoice address commands
    if _tl == "show invoice address":
        return f"SAVED INVOICE ADDRESS:\n\n{load_invoice_address()}", state
    if _tl.startswith("set invoice address:"):
        _new_addr = _t[len("set invoice address:"):].strip()
        if not _new_addr:
            return "Please include the new address after 'set invoice address:'", state
        try:
            save_invoice_address(_new_addr)
            return f"INVOICE ADDRESS UPDATED:\n\n{_new_addr}", state
        except Exception:
            return "Failed to save invoice address. Please try again.", state

    intent = classify_text(incoming)
    last_ctx = state.get("last_context", {})

    # Consume pending clarification before any other routing.
    # Only fires for unrecognised messages (intent==unknown) so explicit commands
    # like "new comparison" or "compare quotes" are never swallowed.
    _pending = state.pop("pending_clarification", None)
    if _pending and _pending.get("intent") == "market_check" and intent == "unknown":
        logger.info("market_check_clarification: consuming pending clarification for incoming=%r", incoming[:60])
        return _handle_market_check_clarification(incoming, _pending, state)

    # Context continuation: short additional detail (spec, location, model) that
    # extends a recent market_check when no explicit intent was recognised.
    # Only fires for intent=unknown so commands like "compare quotes" are never
    # swallowed. Merges incoming detail with the stored topic and re-runs the
    # market check with full doc/component context.
    if intent == "unknown" and _is_context_continuation(incoming, last_ctx):
        _orig = last_ctx.get("topic", "")
        _combined = f"{_orig}\nUser clarification: {incoming}" if _orig else incoming
        _doc_ctx = _build_document_context(state)
        _comp_ctx = build_component_context(state)
        logger.info(
            "context_continuation: re-running market_check orig_topic=%r incoming=%r",
            _orig[:60], incoming[:60],
        )
        return _handle_document_market_check(_combined, state, _doc_ctx, _comp_ctx)

    if intent == "greeting":
        return "Ready.\n\nSend your question or upload a document.", state

    if intent == "new_session":
        logger.info(
            "session_reset: reset_trigger_source=user_command incoming=%r",
            incoming[:80],
        )
        state = reset_user_sessions(state, trigger_source="user_command")
        state.pop("last_context", None)
        state.pop("pending_invoice", None)
        state.pop("pending_clarification", None)
        return build_new_session_response(), state

    # Belt-and-suspenders: classify_text now returns reset_equipment for these
    # phrases, but this handler also covers any edge case that slips the pre-intent
    # check above (e.g. trailing punctuation stripped by t_core).
    if intent == "reset_equipment":
        _uid = state.get("user_id", "")
        clear_equipment(_uid)
        from storage_paths import get_yacht_id_for_user, get_equipment_memory_path
        _yid = get_yacht_id_for_user(_uid)
        logger.info("equipment_reset: user=%s yacht=%s path=%s", _uid, _yid, get_equipment_memory_path(_yid))
        return _equipment_reset_response(), state

    if intent == "quote_compare":
        return _handle_quote_compare_intent(state)

    # Commercial context computed early — needed for follow-up routing decisions.
    active = get_active_session(state)
    comparison_data = active.get("last_comparison") if active else None

    # Context-aware follow-up routing:
    if intent in ("what_to_do", "compliance_followup", "commercial_followup"):
        # Stock check: intercept ordering queries before the normal commercial flow.
        _t_lower = incoming.lower()
        if any(p in _t_lower for p in (
            "how many should i order", "how many should we order", "how many to order",
        )):
            _stock_note = _get_stock_ordering_note(incoming, state)
            if _stock_note:
                return _stock_note, state
        if last_ctx.get("type") == "compliance" and intent in ("what_to_do", "compliance_followup"):
            topic = last_ctx.get("topic", "")
            if topic:
                from storage_paths import get_yacht_id_for_user
                _yid = get_yacht_id_for_user(state.get("user_id", ""))
                return answer_compliance_followup(topic, yacht_id=_yid), state
        if intent == "compliance_followup":
            # Re-route to commercial when market check or comparison context exists.
            if last_ctx.get("type") == "market_check" or comparison_data:
                intent = "commercial_followup"
            else:
                return (
                    "DECISION: No recent compliance topic found.\n"
                    "WHY: No compliance question has been asked in this session yet.\n"
                    "SOURCE: N/A\n"
                    "ACTIONS: • Ask a compliance question first, then follow up."
                ), state
        # what_to_do with non-compliance context always routes to commercial follow-up.
        if intent == "what_to_do":
            intent = "commercial_followup"
        return _handle_action_request(incoming, last_ctx, comparison_data, state), state

    if intent == "why_higher":
        return build_why_higher_response(comparison_data), state

    if intent == "show_added":
        return build_added_items_response(comparison_data), state

    if intent == "show_missing":
        return build_missing_items_response(comparison_data), state

    if intent == "show_extraction":
        return build_extraction_view_response(state), state

    if intent == "compliance_question":
        from storage_paths import get_yacht_id_for_user
        _yid = get_yacht_id_for_user(state.get("user_id", ""))
        answer = answer_compliance_query(incoming, yacht_id=_yid)
        state["last_context"] = {"type": "compliance", "topic": incoming}
        return answer, state

    if intent == "market_check_followup":
        if last_ctx.get("type") == "market_check":
            original_topic = last_ctx.get("topic", "")
            combined = f"{original_topic}\nUser follow-up: {incoming}" if original_topic else incoming
            answer = check_market_price(combined, allow_broad_estimate=True)
            state["last_context"] = {"type": "market_check", "topic": original_topic or incoming, "result": answer}
            return answer, state
        # No market_check history — try using the most recently uploaded document as context.
        doc_ctx = _build_document_context(state)
        if doc_ctx:
            combined = f"{doc_ctx}\n\nUser question: {incoming}"
            answer = check_market_price(combined, allow_broad_estimate=True)
            state["last_context"] = {"type": "market_check", "topic": incoming, "result": answer}
            return answer, state
        # No usable context — fall through to DOCUMENT NOT UNDERSTOOD

    if intent == "reminder":
        return _handle_reminder_command(incoming, phone, state)

    if intent == "add_to_handover":
        return _handle_add_to_handover(state)

    if intent == "show_handover_notes":
        return _handle_show_handover_notes(incoming, state)

    if intent == "show_open_actions":
        return _handle_show_open_actions(state)

    if intent == "show_compliance_sources":
        return _handle_show_compliance_sources(state)

    if intent == "reload_compliance":
        return _handle_reload_compliance(state)

    if intent == "show_compliance_profile":
        return _handle_show_compliance_profile(state)

    if intent == "show_selected_regulations":
        return _handle_show_selected_regulations(state)

    if intent == "show_vessel_procedures":
        return _handle_show_vessel_procedures(state)

    if intent == "enable_regulation":
        return _handle_enable_regulation(incoming, state)

    if intent == "disable_regulation":
        return _handle_disable_regulation(incoming, state)

    if intent == "show_manuals":
        return _handle_show_manuals(state)

    if intent == "manual_search":
        return _handle_manual_search(incoming, state)

    if intent == "show_equipment":
        return _handle_show_equipment(state)

    if intent == "show_stock":
        return _handle_show_stock(state)

    if intent == "stock_query":
        return _handle_stock_query(incoming, state)

    if intent == "spares_query":
        return _handle_spares_query(incoming, state)

    if intent == "equipment_query":
        return _handle_equipment_query(incoming, state)

    if intent == "market_check":
        # If we have document or component context, use the dedicated enriched handler
        # which guards against empty responses and logs diagnostic fields.
        doc_ctx = _build_document_context(state)
        comp_ctx = build_component_context(state)
        eq_ctx = _build_equipment_context(state)
        if eq_ctx and doc_ctx:
            doc_ctx = doc_ctx + "\n" + eq_ctx
        elif eq_ctx:
            doc_ctx = eq_ctx
        if doc_ctx or comp_ctx:
            return _handle_document_market_check(incoming, state, doc_ctx, comp_ctx)
        # No context — enrich vague pronoun references and call directly.
        query = _enrich_with_doc_context(incoming, state)
        answer = check_market_price(query)
        if not answer or not answer.strip():
            answer = _MARKET_CHECK_CONTEXT_FALLBACK
        state["last_context"] = {"type": "market_check", "topic": incoming, "result": answer}
        comp = extract_components_from_text(incoming, "market_check")
        if comp:
            state = merge_components(comp, state)
        return answer, state

    # Context-aware fallback: if we have recent document or component context
    # and the message looks like a question, try to answer it using that
    # context rather than returning a generic fallback response.
    _doc_ctx = _build_document_context(state)
    _comp_ctx = build_component_context(state)
    if _doc_ctx or _comp_ctx:
        _q = incoming.strip().lower()
        _is_q = _q.endswith("?") or _q.startswith((
            "is ", "are ", "does ", "do ", "any ",
            "what ", "how ", "should ", "would ", "will ", "can ",
        ))
        if _is_q:
            _ctx_parts = []
            if _comp_ctx:
                _ctx_parts.append(_comp_ctx)
            if _doc_ctx:
                _ctx_parts.append(_doc_ctx)
            _ctx_parts.append(f"User question: {incoming}")
            _enriched = "\n\n".join(_ctx_parts)
            logger.info("Context fallback: routing question to market_check with doc/component context")
            answer = check_market_price(_enriched, allow_broad_estimate=True)
            if not answer or not answer.strip():
                logger.warning("Context fallback: empty response from check_market_price, using fallback")
                answer = _MARKET_CHECK_CONTEXT_FALLBACK
            state["last_context"] = {"type": "market_check", "topic": incoming, "result": answer}
            _comp = extract_components_from_text(incoming, "market_check")
            if _comp:
                state = merge_components(_comp, state)
            return answer, state

    return _make_response(
        decision="DOCUMENT NOT UNDERSTOOD",
        why="No file was attached and no recognised command was sent.",
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
    _was_media = False  # True when request contained media; reply goes via REST not TwiML

    try:
        incoming = request.form.get("Body", "").strip()
        num_media = int(request.form.get("NumMedia") or 0)
        _was_media = num_media > 0

        if num_media > 0:
            logger.info("Inbound media: media_count=%d", num_media)
            _send_whatsapp_message(phone, _DOCUMENT_RECEIVED_ACK)
            # Guard: warn if the message body would have triggered a session reset.
            # This catches filenames/captions like "New Quote RWO.pdf" that match the
            # new_session intent patterns — they must never cause a reset on a media upload.
            if incoming and classify_text(incoming) == "new_session":
                logger.warning(
                    "WARNING: unexpected session reset triggered "
                    "reset_trigger_source=media_upload_body incoming=%r — suppressed",
                    incoming[:80],
                )
            image_started = False

            # Phase 1: download all attachments; extract PDFs eagerly so we know
            # each document's type before dispatching any of them.
            # Images are collected (not yet threaded) so all pages from the same
            # message can be sent to Claude together as one multi-page document.
            pdf_doc_records: list = []
            image_file_paths: list = []
            _spreadsheet_answers: list = []
            for i in range(num_media):
                media_url = request.form.get(f"MediaUrl{i}")
                media_type = (request.form.get(f"MediaContentType{i}") or "").strip().lower()

                if not media_url:
                    logger.warning("MediaUrl%d missing, skipping", i)
                    continue

                logger.info("Media [%d/%d]: content_type=%r", i + 1, num_media, media_type)
                file_path = download_file(media_url, media_type)
                logger.info("File saved [%d/%d]: %s", i + 1, num_media, os.path.basename(file_path))

                if media_type == "application/pdf" or (
                    media_type not in _IMAGE_CONTENT_TYPES and _looks_like_pdf(file_path)
                ):
                    if media_type != "application/pdf":
                        logger.info(
                            "Media [%d/%d]: content_type=%r — PDF magic bytes found, treating as PDF",
                            i + 1, num_media, media_type,
                        )
                    doc_record = _extract_pdf_to_doc_record(file_path)
                    logger.info(
                        "PDF extracted [%d/%d]: type=%s supplier=%s",
                        i + 1, num_media, doc_record["doc_type"], doc_record["supplier_name"],
                    )
                    pdf_doc_records.append(doc_record)

                elif media_type in _IMAGE_CONTENT_TYPES:
                    image_file_paths.append(file_path)
                    image_started = True

                elif (
                    media_type in _EXCEL_CONTENT_TYPES
                    or media_type in _CSV_CONTENT_TYPES
                    or file_path.lower().endswith((".xlsx", ".xls", ".csv"))
                ):
                    logger.info(
                        "Media [%d/%d]: spreadsheet detected content_type=%r",
                        i + 1, num_media, media_type,
                    )
                    inv_answer, state = _handle_inventory_file(file_path, media_type, state)
                    _spreadsheet_answers.append(inv_answer)

                else:
                    logger.warning("Unsupported media type [%d/%d]: %r", i + 1, num_media, media_type)

            # Spawn ONE background thread for all images in this message so that
            # multiple pages of the same document are extracted together.
            if image_file_paths:
                logger.info("Image upload: spawning thread for %d page(s)", len(image_file_paths))
                thread = threading.Thread(
                    target=_process_images_background,
                    args=(image_file_paths, copy.deepcopy(state), user_id, phone),
                    daemon=True,
                )
                thread.start()

            # Phase 2: dispatch PDFs — quotes first so an invoice in the same
            # batch can find the freshly-created quote session.
            # Invoices with no matching quote return "" silently; a fallback thread
            # sends INVOICE RECEIVED via REST if no quote webhook arrives within 15 s.
            pdf_doc_records.sort(key=lambda d: 0 if d.get("doc_type") == "quote" else 1)

            pdf_answers: list = list(_spreadsheet_answers)
            comparison_answer: Optional[str] = None
            _any_silent = False
            _skipped_duplicates: list = []
            for doc_record in pdf_doc_records:
                att_answer, state = _dispatch_doc_record(doc_record, state)
                if att_answer == "__duplicate__":
                    _skipped_duplicates.append(doc_record)
                    continue
                if not att_answer:
                    # Detect silenced invoice/proforma: pending_invoice just stored (<5 s ago)
                    if doc_record.get("doc_type") in ("invoice", "proforma"):
                        _inv = state.get("pending_invoice") or {}
                        _fp = doc_record.get("fingerprint", "")
                        if (
                            _inv
                            and (_inv.get("doc_record") or {}).get("fingerprint") == _fp
                            and time.time() - _inv.get("stored_at", 0) < 5
                        ):
                            threading.Thread(
                                target=_invoice_pending_fallback,
                                args=(user_id, phone, _fp),
                                daemon=True,
                            ).start()
                            _any_silent = True
                    continue  # no user-facing message for this doc
                pdf_answers.append(att_answer)
                if "MATCH CONFIRMED" in att_answer or "Invoice matched" in att_answer:
                    comparison_answer = att_answer
                    break

            _dup_left_no_docs = (
                bool(_skipped_duplicates) and not pdf_answers
                and not _any_silent and not image_started
            )
            logger.info(
                "processed_docs=%d comparison_found=%s image_threads_started=%s "
                "silent_invoice=%s duplicate_skipped=%s duplicate_left_no_processed_docs=%s",
                len(pdf_answers), comparison_answer is not None, image_started, _any_silent,
                bool(_skipped_duplicates), _dup_left_no_docs,
            )

            if comparison_answer is not None:
                answer = comparison_answer
            elif pdf_answers:
                answer = pdf_answers[-1]
            elif image_started:
                answer = None  # ACK already sent; background thread delivers the result
            elif _any_silent:
                answer = None  # invoice stored silently; fallback thread handles response
            elif _dup_left_no_docs:
                _dup = _skipped_duplicates[0]
                _dup_supplier = _dup.get("supplier_name") or "Unknown supplier"
                _dup_type = _dup.get("doc_type") or "document"
                answer = _make_response(
                    decision="DOCUMENT ALREADY PROCESSED",
                    why=f"This {_dup_supplier} {_dup_type} has already been uploaded in this session.",
                    actions=[
                        "Upload the matching invoice or proforma",
                        'Or say "new comparison" to reset',
                    ],
                )
                logger.info("duplicate_response_sent=True supplier=%s type=%s", _dup_supplier, _dup_type)
            else:
                answer = _make_response(
                    decision="DOCUMENT NOT UNDERSTOOD",
                    why="I could not classify this as a quote, invoice, or proforma.",
                    actions=[
                        "Re-upload as PDF",
                        "Or say what this document is",
                    ],
                )

            # Only skip main-thread state save when images were uploaded without PDFs
            # (the background thread handles state persistence in that case).
            if image_started and not pdf_answers and not _any_silent:
                save_state = False
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

    resp = MessagingResponse()
    if answer is not None:
        body = f"⚓ AskHelm \n\n{answer}"
        if _was_media:
            # Document/media replies go via REST so Twilio webhook can return 200 quickly.
            logger.info(
                "outbound_whatsapp: method=REST final_response=True to=%s body_length=%d "
                "body_empty=%s user=%s reply_body_preview=%r",
                phone, len(body), not body.strip(), user_id, body[:500],
            )
            _send_whatsapp_message(phone, body)
            # Return empty TwiML — REST call above delivers the actual reply.
        else:
            logger.info(
                "outbound_whatsapp: method=TwiML to=%s body_length=%d body_empty=%s "
                "save_state=%s user=%s reply_body_preview=%r",
                phone, len(body), not body.strip(), save_state, user_id, body[:500],
            )
            resp.message(body)
    else:
        logger.info(
            "outbound_whatsapp: method=TwiML to=%s body_empty=True deferred=True "
            "save_state=%s user=%s",
            phone, save_state, user_id,
        )
    return str(resp), 200, {"Content-Type": "text/xml"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=False)
