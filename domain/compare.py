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
