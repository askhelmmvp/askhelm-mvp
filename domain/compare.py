import re


def _normalize_desc(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — mirrors session_manager version."""
    s = s.strip().lower()
    s = re.sub(r'[^\w\s]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def _sig_words(s: str) -> set:
    """Words longer than 2 characters from a normalised description."""
    return {w for w in s.split() if len(w) > 2}


def _desc_matches(a: str, b: str) -> bool:
    """
    True when two line-item descriptions refer to the same item.
    1. Exact match after normalisation.
    2. Fallback: Jaccard similarity of significant words >= 0.5.

    Handles OCR/extraction variations such as a part number appearing in one
    extraction but not the other, or minor formatting differences like
    '20L' vs '20 L' or 'CORR.' vs 'CORR'.
    """
    na, nb = _normalize_desc(a), _normalize_desc(b)
    if na == nb:
        return True
    wa, wb = _sig_words(na), _sig_words(nb)
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) >= 0.5


# Ancillary commercial charges that may appear on an invoice but not on the original
# quote. Their presence should not reduce match confidence or trigger a scope-change
# warning — they are expected additions to the agreed scope.
_ANCILLARY_KEYWORDS = {
    # physical movement
    "freight", "delivery", "transport", "shipping", "courier",
    "carriage", "dispatch", "postage", "forwarding", "logistics",
    # packaging
    "packing", "crating", "packaging",
    # port / duty
    "handling", "port charge", "berth", "demurrage",
    "duty", "customs", "tariff", "import",
    # admin / compliance
    "surcharge", "fuel surcharge", "insurance",
    "documentation", "certification",
}

_FREIGHT_KEYWORDS = _ANCILLARY_KEYWORDS  # backward compatibility alias


def _is_ancillary_item(desc: str) -> bool:
    lower = desc.lower()
    return any(kw in lower for kw in _ANCILLARY_KEYWORDS)


_is_freight_item = _is_ancillary_item  # backward compatibility alias


def compare_documents(doc_a: dict, doc_b: dict) -> dict:
    total_a = doc_a.get("total")
    total_b = doc_b.get("total")

    delta = None
    delta_percent = None
    if total_a is not None and total_b is not None and total_a != 0:
        delta = total_b - total_a
        delta_percent = (delta / total_a) * 100

    items_a = doc_a.get("line_items") or []
    items_b = doc_b.get("line_items") or []

    def _found_in(desc: str, item_list: list) -> bool:
        """True when desc fuzzy-matches any description in item_list."""
        return any(
            _desc_matches(desc, (other.get("description") or ""))
            for other in item_list
            if other.get("description")
        )

    added_items = [
        item for item in items_b
        if item.get("description") and not _found_in(item["description"], items_a)
    ]
    missing_items = [
        item for item in items_a
        if item.get("description") and not _found_in(item["description"], items_b)
    ]

    ancillary_items = [
        item for item in added_items
        if _is_ancillary_item(item.get("description", ""))
    ]
    # Added items that are not ancillary charges — genuine scope additions
    non_ancillary_added = [
        item for item in added_items
        if not _is_ancillary_item(item.get("description", ""))
    ]

    return {
        "total_a": total_a,
        "total_b": total_b,
        "delta": delta,
        "delta_percent": delta_percent,
        "added_items": added_items,
        "missing_items": missing_items,
        "freight_items": ancillary_items,        # backward compatibility
        "ancillary_items": ancillary_items,
        "non_ancillary_added_items": non_ancillary_added,
        # True when invoice adds items but ALL of them are ancillary charges
        "all_added_are_ancillary": bool(added_items) and not non_ancillary_added,
    }


_EMBEDDED_PN_RE = re.compile(r'\b\d{5,}\b')


def _extract_embedded_pn(desc: str) -> str:
    """First 5+-digit sequence in a description, as a probable part number."""
    m = _EMBEDDED_PN_RE.search(desc or "")
    return m.group(0) if m else ""


def _desc_without_embedded_pn(desc: str) -> str:
    """Remove all 5+-digit part-number sequences from a description."""
    return re.sub(r'\b\d{5,}\b', '', desc or "").strip()


def _desc_matches_stripped(a: str, b: str) -> bool:
    """Match descriptions after stripping embedded part numbers."""
    return _desc_matches(_desc_without_embedded_pn(a), _desc_without_embedded_pn(b))


def build_line_item_comparison(doc_a: dict, doc_b: dict) -> dict:
    """
    Compare line items between two documents with embedded-part-number detection.

    Matching priority per item:
    1. Identical embedded 5+-digit PN → definite match
    2. Fuzzy description match (existing _desc_matches)
    3. Fuzzy match after stripping embedded PNs → catches same-item different-PN

    Returns:
    {
      "matched": [{"item_a", "item_b", "pn_a", "pn_b", "pn_differs", "qty_differs"}],
      "only_in_a": [...],
      "only_in_b": [...],
      "has_pn_differences": bool,
      "has_qty_differences": bool,
    }
    """
    items_a = [i for i in (doc_a.get("line_items") or []) if (i.get("description") or "").strip()]
    items_b = [i for i in (doc_b.get("line_items") or []) if (i.get("description") or "").strip()]

    matched = []
    remaining_b = list(range(len(items_b)))
    only_in_a = []

    for item_a in items_a:
        desc_a = (item_a.get("description") or "").strip()
        pn_a = _extract_embedded_pn(desc_a)

        found_idx = None
        pn_match = False

        # 1. Exact embedded-PN match
        if pn_a:
            for idx in remaining_b:
                pn_b_candidate = _extract_embedded_pn((items_b[idx].get("description") or "").strip())
                if pn_b_candidate and pn_a == pn_b_candidate:
                    found_idx = idx
                    pn_match = True
                    break

        # 2. Fuzzy description match
        if found_idx is None:
            for idx in remaining_b:
                desc_b = (items_b[idx].get("description") or "").strip()
                if _desc_matches(desc_a, desc_b):
                    found_idx = idx
                    break

        # 3. Fuzzy match after stripping embedded PNs (same item, different PN)
        if found_idx is None:
            for idx in remaining_b:
                desc_b = (items_b[idx].get("description") or "").strip()
                if _desc_matches_stripped(desc_a, desc_b):
                    found_idx = idx
                    break

        if found_idx is not None:
            remaining_b.remove(found_idx)
            item_b = items_b[found_idx]
            pn_b = _extract_embedded_pn((item_b.get("description") or "").strip())
            pn_differs = not pn_match and bool(pn_a and pn_b and pn_a != pn_b)
            qty_a = item_a.get("quantity")
            qty_b = item_b.get("quantity")
            qty_differs = qty_a is not None and qty_b is not None and qty_a != qty_b
            matched.append({
                "item_a": item_a,
                "item_b": item_b,
                "pn_a": pn_a,
                "pn_b": pn_b,
                "pn_differs": pn_differs,
                "qty_differs": qty_differs,
            })
        else:
            only_in_a.append(item_a)

    only_in_b = [items_b[idx] for idx in remaining_b]

    return {
        "matched": matched,
        "only_in_a": only_in_a,
        "only_in_b": only_in_b,
        "has_pn_differences": any(m["pn_differs"] for m in matched),
        "has_qty_differences": any(m["qty_differs"] for m in matched),
    }
