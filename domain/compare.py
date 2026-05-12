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


def _is_logistics_note(item: dict) -> bool:
    """True when an item carries no monetary value — a logistics note, not a charge."""
    total = item.get("line_total")
    rate = item.get("unit_rate")
    try:
        has_price = (total is not None and float(total) != 0) or (
            rate is not None and float(rate) != 0
        )
    except (TypeError, ValueError):
        has_price = bool(total or rate)
    return not has_price


def _qty_matches(a, b) -> bool:
    """True when two quantity values are equal within a small tolerance, or one is absent."""
    if a is None or b is None:
        return True
    try:
        return abs(float(a) - float(b)) <= 0.001
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def _amount_matches(a, b) -> bool:
    """True when two monetary amounts match within EUR 0.01 tolerance, or either is absent."""
    if a is None or b is None:
        return True
    try:
        return abs(float(a) - float(b)) <= 0.005
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Quote category detection
# ---------------------------------------------------------------------------

_PROVISIONING_KEYWORDS = frozenset({
    # fish & seafood
    "salmon", "bass", "tuna", "prawn", "squid", "octopus", "brill", "cod",
    "haddock", "halibut", "monkfish", "sole", "turbot", "crab", "lobster",
    "scallop", "oyster", "mackerel", "herring", "trout", "plaice",
    # meat & poultry
    "beef", "chicken", "lamb", "pork", "veal", "duck", "turkey",
    # product form
    "fillet", "fillets", "sides", "loin", "portion", "portions",
    "smoked", "frozen", "peeled", "shelled", "boneless", "skinless",
    # provisioning / galley
    "galley", "provisions", "provisioning", "catering", "food", "grocery",
    "beverage",
})

_ENGINEERING_KEYWORDS = frozenset({
    "pump", "valve", "motor", "seal", "bearing", "filter", "gasket",
    "impeller", "shaft", "gearbox", "compressor", "fan", "alternator",
    "starter", "belt", "fitting", "coupling", "flange", "thruster",
    "winch", "windlass", "hydraulic", "pneumatic", "sensor", "transducer",
})

_TENDER_KEYWORDS = frozenset({
    "tender", "dinghy", "rib", "inflatable", "davit", "outboard",
    "zodiac", "highfield",
})

_REFIT_KEYWORDS = frozenset({
    "labour", "refit", "drydock", "antifouling", "gelcoat", "fairing",
    "scaffolding", "osmosis", "painting",
})


def categorize_quote(doc: dict) -> str:
    """
    Classify a quote into a broad category using line-item keyword signals.
    Returns one of: "provisioning", "engineering", "tender", "refit", "unknown".
    Requires at least 2 keyword hits to avoid misclassifying on sparse items.
    """
    text = " ".join(
        (item.get("description") or "").lower()
        for item in (doc.get("line_items") or [])
    ) + " " + (doc.get("supplier_name") or "").lower()

    scores = {
        "provisioning": sum(1 for kw in _PROVISIONING_KEYWORDS if kw in text),
        "engineering":  sum(1 for kw in _ENGINEERING_KEYWORDS  if kw in text),
        "tender":       sum(1 for kw in _TENDER_KEYWORDS        if kw in text),
        "refit":        sum(1 for kw in _REFIT_KEYWORDS         if kw in text),
    }
    best = max(scores, key=scores.get)
    return best if scores[best] >= 2 else "unknown"


# ---------------------------------------------------------------------------
# Quote relevance filtering
# ---------------------------------------------------------------------------

_GENERIC_QUOTE_WORDS = frozenset({
    "supply", "of", "and", "for", "the", "with", "per", "set",
    "unit", "pcs", "qty", "price", "total", "cost", "item",
    "including", "includes", "service", "repair", "installation",
    "part", "parts", "spare", "spares", "number", "ref",
    "inc", "vat", "labour", "material", "materials",
})


def _quote_keywords(doc: dict) -> frozenset:
    """Distinctive words from all line-item descriptions in a quote document."""
    words: set = set()
    for item in (doc.get("line_items") or []):
        desc = item.get("description") or ""
        normalized = _normalize_desc(desc)
        words.update(w for w in normalized.split() if len(w) > 2 and w not in _GENERIC_QUOTE_WORDS)
    supplier = _normalize_desc(doc.get("supplier_name") or "")
    words.update(w for w in supplier.split() if len(w) > 2 and w not in _GENERIC_QUOTE_WORDS)
    return frozenset(words)


def _overlap_coefficient(a: frozenset, b: frozenset) -> float:
    """Overlap coefficient: |A ∩ B| / min(|A|, |B|)."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def filter_quotes_by_relevance(quotes: list, threshold: float = 0.15) -> tuple:
    """
    From a list of quote dicts, find the most topically-similar pair and
    return (selected_quotes, excluded_quotes).

    Two layers of filtering:
    1. Category: quotes in a different known category from the best pair are excluded.
    2. Keyword overlap: remaining quotes below the overlap threshold are excluded.

    With only 2 quotes there is nothing to filter — both are always selected.
    Safety: if the best-pair overlap score < 0.05, skip filtering (too sparse to judge).
    """
    if len(quotes) <= 2:
        return quotes, []

    kw_sets = [_quote_keywords(q) for q in quotes]
    categories = [categorize_quote(q) for q in quotes]

    # Find best-scoring pair by keyword overlap
    best_score = -1.0
    best_pair = (0, 1)
    for i in range(len(quotes)):
        for j in range(i + 1, len(quotes)):
            score = _overlap_coefficient(kw_sets[i], kw_sets[j])
            if score > best_score:
                best_score = score
                best_pair = (i, j)

    if best_score < 0.05:
        return quotes, []

    pair_cat_a = categories[best_pair[0]]
    pair_cat_b = categories[best_pair[1]]
    pair_category = pair_cat_a if pair_cat_a == pair_cat_b else "unknown"

    best_indices = set(best_pair)
    selected = []
    excluded = []
    for idx, quote in enumerate(quotes):
        if idx in best_indices:
            selected.append(quote)
            continue
        cat = categories[idx]
        # Different known category from the best pair → always exclude
        if pair_category != "unknown" and cat != "unknown" and cat != pair_category:
            excluded.append(quote)
            continue
        pair_score = max(
            _overlap_coefficient(kw_sets[idx], kw_sets[best_pair[0]]),
            _overlap_coefficient(kw_sets[idx], kw_sets[best_pair[1]]),
        )
        if pair_score >= threshold:
            selected.append(quote)
        else:
            excluded.append(quote)

    return selected, excluded


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

    # Line-level detail: bijective (1-to-1) greedy best-similarity matching.
    # Build scores for all (quote_idx, invoice_idx) candidate pairs.
    _pairs: list = []
    for _qi, _qte in enumerate(items_a):
        _qd = (_qte.get("description") or "").strip()
        if not _qd:
            continue
        _na = _normalize_desc(_qd)
        _wa = _sig_words(_na)
        for _ii, _inv in enumerate(items_b):
            _id = (_inv.get("description") or "").strip()
            if not _id:
                continue
            _nb = _normalize_desc(_id)
            if _na == _nb:
                _sc = 1.0
            else:
                _wb = _sig_words(_nb)
                _sc = len(_wa & _wb) / len(_wa | _wb) if (_wa and _wb) else 0.0
            if _sc >= 0.5:
                _pairs.append((_sc, _qi, _ii))
    _pairs.sort(reverse=True)
    _used_q: set = set()
    _used_i: set = set()
    _lc_map: dict = {}   # quote_idx → invoice_idx
    for _sc, _qi, _ii in _pairs:
        if _qi not in _used_q and _ii not in _used_i:
            _lc_map[_qi] = _ii
            _used_q.add(_qi)
            _used_i.add(_ii)

    line_check = []
    quantity_mismatches = []
    price_mismatches = []
    for _qi, qte in enumerate(items_a):
        qte_desc = (qte.get("description") or "").strip()
        if not qte_desc:
            continue
        if _qi not in _lc_map:
            line_check.append({"description": qte_desc, "status": "missing"})
            continue
        inv_match = items_b[_lc_map[_qi]]
        qty_ok = _qty_matches(qte.get("quantity"), inv_match.get("quantity"))
        rate_ok = _amount_matches(qte.get("unit_rate"), inv_match.get("unit_rate"))
        total_ok = _amount_matches(qte.get("line_total"), inv_match.get("line_total"))
        status = "match" if (qty_ok and rate_ok and total_ok) else "mismatch"
        entry = {
            "description": qte_desc,
            "status": status,
            "qty_ok": qty_ok,
            "rate_ok": rate_ok,
            "total_ok": total_ok,
            "quote_qty": qte.get("quantity"),
            "invoice_qty": inv_match.get("quantity"),
            "quote_total": qte.get("line_total"),
            "invoice_total": inv_match.get("line_total"),
        }
        line_check.append(entry)
        if not qty_ok:
            quantity_mismatches.append(entry)
        if not rate_ok or not total_ok:
            price_mismatches.append(entry)

    # Items with no monetary value are logistics notes (e.g. pallet dimensions), not charges.
    logistics_notes = [item for item in added_items if _is_logistics_note(item)]
    priced_non_ancillary = [item for item in non_ancillary_added if not _is_logistics_note(item)]
    lines_all_match = (
        all(e["status"] == "match" for e in line_check) if line_check else None
    )

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
        # line-level analysis
        "line_check": line_check,
        "quantity_mismatches": quantity_mismatches,
        "price_mismatches": price_mismatches,
        "logistics_notes": logistics_notes,
        "priced_non_ancillary_added_items": priced_non_ancillary,
        "lines_all_match": lines_all_match,
    }
