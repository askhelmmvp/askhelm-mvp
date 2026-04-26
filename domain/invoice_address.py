import re
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default vessel billing address
# ---------------------------------------------------------------------------

_DEFAULT_ADDRESS_RAW = (
    "Light Tranquility Ltd\n"
    "4th Floor, Harbour Place\n"
    "103 South Church Street\n"
    "PO Box 10240\n"
    "KY1-1002 Grand Cayman\n"
    "Cayman Islands"
)

ADDRESS_MATCH_NOTE = "\u2705 Address check: Invoice address matches saved billing details."

_MISMATCH_RESPONSE = (
    "DECISION:\nINVOICE ADDRESS MISMATCH\n\n"
    "WHY:\nInvoice billing details do not match the saved vessel invoice address.\n\n"
    "RECOMMENDED ACTIONS:\n"
    "\u2022 Ask supplier to reissue before payment\n"
    "\u2022 Confirm correct legal entity and address\n"
    "\u2022 Do not approve until corrected"
)

# ---------------------------------------------------------------------------
# Default vessel delivery / project details
# ---------------------------------------------------------------------------

_DEFAULT_DELIVERY_RAW = (
    "Project H3\n"
    "c/o Oceanco\n"
    "Marineweg 1 & 5\n"
    "2952 BX Alblasserdam\n"
    "The Netherlands"
)

# Key identifiers accepted as a match — any two of these present = match
_DELIVERY_KEY_TOKENS = frozenset([
    "h3", "oceanco", "marineweg", "alblasserdam", "netherlands",
])

DELIVERY_MATCH_NOTE = "\u2705 Delivery check: Delivery address matches saved vessel/project details."
DELIVERY_MISMATCH_NOTE = "\u26a0\ufe0f Delivery check: Delivery address does not match saved vessel/project details."


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _config_path() -> Path:
    from config import STORAGE_DIR
    return Path(STORAGE_DIR) / "vessel_config.json"


def load_invoice_address() -> str:
    """Returns the saved invoice address as a raw string. Falls back to the default."""
    path = _config_path()
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            addr = cfg.get("invoice_address", {}).get("raw", "").strip()
            if addr:
                return addr
    except Exception as exc:
        logger.warning("invoice_address: failed to load vessel_config.json: %s", exc)
    return _DEFAULT_ADDRESS_RAW


def save_invoice_address(raw_text: str) -> None:
    """Persist a new invoice address to vessel_config.json."""
    path = _config_path()
    try:
        cfg = {}
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        cfg["invoice_address"] = {"raw": raw_text.strip()}
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        logger.info("invoice_address: saved new address length=%d", len(raw_text))
    except Exception as exc:
        logger.exception("invoice_address: failed to save address: %s", exc)
        raise


# ---------------------------------------------------------------------------
# Normalisation and matching helpers
# ---------------------------------------------------------------------------

_PO_BOX_RE = re.compile(r'\bp\.?\s*o\.?\s*box\b', re.IGNORECASE)


def _normalize(text: str) -> str:
    """Lowercase, normalise PO Box variants, strip punctuation, collapse whitespace."""
    t = text.lower()
    t = _PO_BOX_RE.sub("po box", t)
    t = re.sub(r'[^\w\s]', ' ', t)
    return re.sub(r'\s+', ' ', t).strip()


def _tokens(text: str) -> set:
    return {w for w in _normalize(text).split() if len(w) > 1}


def _overlap_score(a: str, b: str) -> float:
    """Overlap coefficient: |intersection| / min(|a|, |b|)."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def _entity_matches(extracted_entity: str, saved_address: str) -> bool:
    """True when the extracted billing entity name is present in the saved address."""
    if not extracted_entity:
        return False
    return _overlap_score(extracted_entity, saved_address) >= 0.5


# ---------------------------------------------------------------------------
# Billing address check
# ---------------------------------------------------------------------------

def check_invoice_billing_address(doc_record: dict) -> dict:
    """
    Compare billing address extracted from doc_record against the saved vessel address.

    Returns {checked, match, score, entity, mismatch_response}.
    checked=False when no billing address was extracted — caller must not flag mismatch.
    """
    billing = doc_record.get("billing_address") or {}
    entity = (billing.get("entity") or "").strip()
    address_lines = billing.get("address_lines") or []
    country = (billing.get("country") or "").strip()

    if not entity and not address_lines and not country:
        logger.info("invoice_address_check=False reason=no_billing_address_extracted")
        return {"checked": False, "match": True, "score": 0.0, "entity": "", "mismatch_response": None}

    extracted_full = " ".join(filter(None, [entity] + list(address_lines) + [country]))
    saved_address = load_invoice_address()

    score = _overlap_score(extracted_full, saved_address)
    entity_ok = _entity_matches(entity, saved_address) if entity else True
    match = score >= 0.70 and entity_ok

    logger.info(
        "invoice_address_check=True extracted_billing_entity=%r address_match_score=%.2f address_match=%s",
        entity, score, match,
    )

    return {
        "checked": True,
        "match": match,
        "score": score,
        "entity": entity,
        "mismatch_response": None if match else _MISMATCH_RESPONSE,
    }


# ---------------------------------------------------------------------------
# Delivery address check
# ---------------------------------------------------------------------------

def check_invoice_delivery_address(doc_record: dict) -> dict:
    """
    Check whether the delivery/ship-to address on the invoice matches the saved
    vessel/project details.  Uses a lenient keyword match (2+ key identifiers).

    Returns {checked, match}.
    checked=False when no delivery address was extracted — caller must not flag mismatch.
    """
    delivery = doc_record.get("delivery_address") or {}
    entity = (delivery.get("entity") or "").strip()
    address_lines = delivery.get("address_lines") or []
    country = (delivery.get("country") or "").strip()

    if not entity and not address_lines and not country:
        logger.info("delivery_address_check=False reason=no_delivery_address_extracted")
        return {"checked": False, "match": True}

    full_text = " ".join(filter(None, [entity] + list(address_lines) + [country]))
    normalised = _normalize(full_text)
    tokens = set(normalised.split())

    matched_keys = _DELIVERY_KEY_TOKENS & tokens
    match = len(matched_keys) >= 2

    logger.info(
        "delivery_address_check=True matched_keys=%s delivery_match=%s",
        matched_keys, match,
    )

    return {"checked": True, "match": match}
