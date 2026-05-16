import json
import logging
import re
import datetime
from pathlib import Path
from typing import Optional

from storage_paths import (
    get_equipment_memory_path,
    get_stock_memory_path,
    get_yacht_id_for_user,
    migrate_user_files,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Paths  (yacht-level: one equipment/stock store shared across all crew)
# ---------------------------------------------------------------------------

def _equipment_path(user_id: str) -> Path:
    yacht_id = get_yacht_id_for_user(user_id)
    migrate_user_files(user_id, yacht_id)
    return get_equipment_memory_path(yacht_id)


def _stock_path(user_id: str) -> Path:
    yacht_id = get_yacht_id_for_user(user_id)
    migrate_user_files(user_id, yacht_id)
    path = get_stock_memory_path(yacht_id)
    logger.debug(
        "inventory_store: stock_path user=%s yacht_id=%s path=%s", user_id, yacht_id, path
    )
    return path


# ---------------------------------------------------------------------------
# Load / write
# ---------------------------------------------------------------------------

def load_equipment(user_id: str) -> dict:
    path = _equipment_path(user_id)
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:
        logger.warning("inventory_store: failed to load equipment user=%s: %s", user_id, exc)
    return {"equipment": []}


def load_stock(user_id: str) -> dict:
    path = _stock_path(user_id)
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.info(
                "inventory_store: load_stock user=%s path=%s records=%d",
                user_id, path, len(data.get("stock", [])),
            )
            return data
    except Exception as exc:
        logger.warning("inventory_store: failed to load stock user=%s: %s", user_id, exc)
    return {"stock": []}


def _write_equipment(user_id: str, data: dict) -> None:
    path = _equipment_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _write_stock(user_id: str, data: dict) -> None:
    path = _stock_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Equipment matching — multi-strategy
# ---------------------------------------------------------------------------

def _build_equipment_indices(existing: list) -> tuple:
    """
    Build four lookup dicts from the existing equipment list for O(1) matching.
    Returns (serial_idx, name_make_idx, name_model_idx, system_name_idx).
    Each maps a normalised key → list index.
    """
    serial_idx: dict = {}
    name_make_idx: dict = {}
    name_model_idx: dict = {}
    system_name_idx: dict = {}

    for i, e in enumerate(existing):
        serial = (e.get("serial_number") or "").lower().strip()
        name = (e.get("equipment_name") or "").lower().strip()
        make = (e.get("make") or "").lower().strip()
        model = (e.get("model") or "").lower().strip()
        system = (e.get("system") or "").lower().strip()

        if serial:
            serial_idx.setdefault(serial, i)
        if name and make:
            name_make_idx.setdefault((name, make), i)
        if name and model:
            name_model_idx.setdefault((name, model), i)
        if system and name:
            system_name_idx.setdefault((system, name), i)

    return serial_idx, name_make_idx, name_model_idx, system_name_idx


def _find_equipment_match(
    item: dict,
    serial_idx: dict,
    name_make_idx: dict,
    name_model_idx: dict,
    system_name_idx: dict,
) -> Optional[int]:
    """
    Return the index of the best-matching existing record, or None.

    Priority order (strongest signal first):
      1. serial_number  — unique hardware identifier
      2. equipment_name + make  — same model from same manufacturer
      3. equipment_name + model  — same named item, same model designation
      4. system + equipment_name  — weakest: same name within the same system
    """
    serial = (item.get("serial_number") or "").lower().strip()
    name = (item.get("equipment_name") or "").lower().strip()
    make = (item.get("make") or "").lower().strip()
    model = (item.get("model") or "").lower().strip()
    system = (item.get("system") or "").lower().strip()

    if serial and serial in serial_idx:
        return serial_idx[serial]
    if name and make and (name, make) in name_make_idx:
        return name_make_idx[(name, make)]
    if name and model and (name, model) in name_model_idx:
        return name_model_idx[(name, model)]
    if system and name and (system, name) in system_name_idx:
        return system_name_idx[(system, name)]
    return None


def _merge_equipment_fields(old: dict, new_item: dict, source_file: str) -> None:
    """
    Update `old` in-place with values from `new_item`.
    For each field: keep the more complete (longer non-empty) value.
    Always update source_file and last_seen_at.
    """
    for field in ("equipment_name", "make", "model", "serial_number", "location", "system", "notes"):
        new_val = (new_item.get(field) or "").strip()
        old_val = (old.get(field) or "").strip()
        if new_val and (not old_val or len(new_val) > len(old_val)):
            old[field] = new_item[field]
    old["confidence"] = round(
        min(old.get("confidence", 0.7), new_item.get("confidence", 0.7)), 2
    )
    old["source_file"] = source_file
    old["last_seen_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Stock dedup key
# ---------------------------------------------------------------------------

def _stock_key(item: dict) -> tuple:
    pn = (item.get("part_number") or "").strip()
    if pn:
        return ("pn", pn.lower())
    return ("desc", (item.get("description") or "").lower().strip()[:60])


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

def merge_equipment(user_id: str, new_items: list, source_file: str) -> tuple:
    """
    Upsert equipment records using multi-strategy matching. Returns (added, merged).

    Matching priority:
      1. serial_number
      2. equipment_name + make
      3. equipment_name + model
      4. system + equipment_name (weakest)

    On match: update all fields, keeping the more complete value.
    No match: append as a new record.
    """
    data = load_equipment(user_id)
    existing = data["equipment"]
    serial_idx, name_make_idx, name_model_idx, system_name_idx = _build_equipment_indices(existing)

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    added = merged = 0

    for item in new_items:
        name = (item.get("equipment_name") or "").lower().strip()
        system = (item.get("system") or "").lower().strip()

        if not name and not system:
            continue

        match_idx = _find_equipment_match(
            item, serial_idx, name_make_idx, name_model_idx, system_name_idx
        )

        if match_idx is not None:
            _merge_equipment_fields(existing[match_idx], item, source_file)
            merged += 1
        else:
            item = dict(item)
            item["source_file"] = source_file
            item["last_seen_at"] = now
            item.setdefault("confidence", 0.7)
            existing.append(item)
            idx = len(existing) - 1

            # Update indices so later items in the same batch can match this one
            serial = (item.get("serial_number") or "").lower().strip()
            make = (item.get("make") or "").lower().strip()
            model = (item.get("model") or "").lower().strip()
            if serial:
                serial_idx.setdefault(serial, idx)
            if name and make:
                name_make_idx.setdefault((name, make), idx)
            if name and model:
                name_model_idx.setdefault((name, model), idx)
            if system and name:
                system_name_idx.setdefault((system, name), idx)
            added += 1

    _write_equipment(user_id, data)
    logger.info(
        "inventory_store: equipment added=%d merged=%d user=%s",
        added, merged, user_id,
    )
    return added, merged


def merge_stock(user_id: str, new_items: list, source_file: str) -> tuple:
    """Upsert stock records. Returns (added, merged)."""
    data = load_stock(user_id)
    existing = data["stock"]
    key_map = {_stock_key(e): i for i, e in enumerate(existing)}

    added = merged = 0
    for item in new_items:
        key = _stock_key(item)
        if key == ("desc", ""):
            continue
        if key in key_map:
            old = existing[key_map[key]]
            new_qty = item.get("quantity_onboard")
            if new_qty is not None:
                if old.get("quantity_onboard") is None:
                    old["quantity_onboard"] = new_qty
                else:
                    # Conflicting quantities — lower confidence
                    old["confidence"] = round(max(0.4, old.get("confidence", 0.7) - 0.15), 2)
            for field in ("storage_location", "unit", "linked_equipment",
                          "make", "model", "supplier", "part_number", "notes"):
                if item.get(field) and not old.get(field):
                    old[field] = item[field]
            merged += 1
        else:
            item["source_file"] = source_file
            item.setdefault("confidence", 0.7)
            existing.append(item)
            key_map[key] = len(existing) - 1
            added += 1

    _write_stock(user_id, data)
    yacht_id = get_yacht_id_for_user(user_id)
    logger.info(
        "inventory_store: stock added=%d merged=%d total=%d user=%s yacht_id=%s path=%s",
        added, merged, len(existing), user_id, yacht_id, get_stock_memory_path(yacht_id),
    )
    return added, merged


# ---------------------------------------------------------------------------
# Equipment linking for stock items
# ---------------------------------------------------------------------------

def link_stock_to_equipment(user_id: str, stock_items: list) -> tuple:
    """
    For each stock item that has a linked_equipment text, try to match it to
    an equipment record in memory using simple substring matching.
    Adds an equipment_link dict to matched items.
    Returns (updated_items, linked_count).
    """
    equipment = get_all_equipment(user_id)
    if not equipment or not stock_items:
        return stock_items, 0

    eq_names = [
        (e.get("equipment_name") or e.get("system") or "").lower().strip()
        for e in equipment
    ]

    linked_count = 0
    updated = []
    for item in stock_items:
        item = dict(item)
        linked_text = (item.get("linked_equipment") or "").lower().strip()
        if linked_text and not item.get("equipment_link"):
            matched_eq = None
            for i, eq_name in enumerate(eq_names):
                if not eq_name:
                    continue
                if linked_text == eq_name or linked_text in eq_name or eq_name in linked_text:
                    matched_eq = equipment[i]
                    break
            if matched_eq:
                item["equipment_link"] = {
                    "equipment_name": matched_eq.get("equipment_name") or matched_eq.get("system") or "",
                    "confidence": 0.85,
                }
                linked_count += 1
        updated.append(item)

    logger.info(
        "inventory_store: link_stock_to_equipment user=%s items=%d linked=%d",
        user_id, len(stock_items), linked_count,
    )
    return updated, linked_count


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

def clear_equipment(user_id: str) -> None:
    """Wipe equipment memory for a user. Does not touch stock or any other data."""
    _write_equipment(user_id, {"equipment": []})
    logger.info("inventory_store: equipment cleared user=%s", user_id)


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def get_all_equipment(user_id: str) -> list:
    return load_equipment(user_id).get("equipment", [])


def get_all_stock(user_id: str) -> list:
    return load_stock(user_id).get("stock", [])


def find_stock_by_part_number(user_id: str, part_number: str) -> list:
    """Exact case-insensitive match on part_number field only.

    Used for part-code lookups to avoid fuzzy false-positives from short
    part numbers matching as substrings of the query string.
    """
    pn_lower = part_number.lower().strip()
    results = [
        item for item in get_all_stock(user_id)
        if (item.get("part_number") or "").lower() == pn_lower
    ]
    logger.info(
        "inventory_store: find_stock_by_part_number user=%s pn=%r results=%d",
        user_id, part_number, len(results),
    )
    return results


def find_stock_by_query(user_id: str, query: str) -> list:
    """Fuzzy substring match against description, part_number, linked_equipment."""
    q = query.lower().strip()
    results = []
    for item in get_all_stock(user_id):
        desc = (item.get("description") or "").lower()
        pn = (item.get("part_number") or "").lower()
        linked = (item.get("linked_equipment") or "").lower()
        if (
            q in desc or (desc and desc in q)
            or q in pn or (pn and pn in q)
            or (linked and q in linked)
        ):
            results.append(item)
    logger.info(
        "inventory_store: find_stock_by_query user=%s query=%r results=%d",
        user_id, query, len(results),
    )
    return results


def find_stock_for_system(user_id: str, query: str) -> list:
    """Return stock items whose linked_equipment, description, supplier or make matches query."""
    q = query.lower().strip()
    results = []
    for item in get_all_stock(user_id):
        linked = (item.get("linked_equipment") or "").lower()
        desc = (item.get("description") or "").lower()
        supplier = (item.get("supplier") or "").lower()
        make = (item.get("make") or "").lower()
        if (
            (linked and (q in linked or linked in q))
            or q in desc
            or (supplier and (q in supplier or supplier in q))
            or (make and (q in make or make in q))
        ):
            results.append(item)
    logger.info(
        "inventory_store: find_stock_for_system user=%s query=%r results=%d",
        user_id, query, len(results),
    )
    return results


# ---------------------------------------------------------------------------
# Equipment search — normalisation + token scoring
# ---------------------------------------------------------------------------

# Compiled alias substitutions applied to both query and field values.
# Order matters: multi-word patterns must come before their abbreviations.
_ALIAS_SUBS = (
    (re.compile(r'\([^)]*\)'), ' '),                         # strip parenthetical content
    (re.compile(r'\bsea[-\s]water\b'), 'seawater'),
    (re.compile(r'\bfresh[-\s]water\b'), 'freshwater'),
    (re.compile(r'\bblack[-\s]water\b'), 'blackwater'),
    (re.compile(r'\bgr[ae]y[-\s]water\b'), 'greywater'),
    (re.compile(r'\bair[-\s]conditioning\b'), 'airconditioning'),
    (re.compile(r'\bs/w\b'), 'seawater'),
    (re.compile(r'\bf/w\b'), 'freshwater'),
    (re.compile(r'\bb/w\b'), 'blackwater'),
    (re.compile(r'\bg/w\b'), 'greywater'),
    (re.compile(r'\ba/c\b'), 'airconditioning'),
    (re.compile(r'\bstabili[sz]ers?\b'), 'stabilizer'),
    (re.compile(r'\bsterili[sz]ers?\b'), 'sterilizer'),
    (re.compile(r'\baircon\b'), 'airconditioning'),
    # OWS / oily water separator variants (must come before generic "separator")
    (re.compile(r'\b15\s*ppm\s+separator\b'), 'oily bilge separator'),
    (re.compile(r'\boily\s+bilge\s+sep[ae]rators?\b'), 'oily bilge separator'),
    (re.compile(r'\boily\s+water\s+sep[ae]rators?\b'), 'oily bilge separator'),
    (re.compile(r'\bbilge\s+water\s+sep[ae]rators?\b'), 'oily bilge separator'),
    (re.compile(r'\bbilge\s+sep[ae]rators?\b'), 'oily bilge separator'),
    (re.compile(r'\bows\b'), 'oily bilge separator'),
    # Typo: seperator → separator (catch-all after multi-word aliases above)
    (re.compile(r'\bsep[e]rators?\b'), 'separator'),
    # OCM / oil content monitor variants
    (re.compile(r'\b15\s*ppm\s+(monitor|alarm|meter)\b'), 'oil content monitor'),
    (re.compile(r'\boil\s+content\s+(monitor|meter)\b'), 'oil content monitor'),
    (re.compile(r'\boil\s+monitoring\s+device\b'), 'oil content monitor'),
    (re.compile(r'\bbilge\s+alarm\b'), 'oil content monitor'),
    (re.compile(r'\bocm\b'), 'oil content monitor'),
    (re.compile(r'\bomd\b'), 'oil content monitor'),
    (re.compile(r'\s+'), ' '),                               # collapse whitespace
)


# ---------------------------------------------------------------------------
# System alias normalisation for spares/stock queries
# ---------------------------------------------------------------------------

_SYSTEM_ALIAS_MAP: dict = {
    "mtu":    ["mtu", "main engine"],
    "me":     ["me", "main engine"],
    "dg":     ["dg", "generator", "diesel generator"],
    "genset": ["genset", "generator"],
    "ro":     ["ro", "reverse osmosis"],
    "stp":    ["stp", "sewage treatment"],
    "ows":    ["ows", "oily water separator", "oily bilge separator"],
    "ocm":    ["ocm", "oil content monitor"],
    "omd":    ["omd", "oil content monitor"],
    "cat":    ["cat", "caterpillar"],
}


def normalise_system_alias(query: str) -> list:
    """Return search terms for a system query, expanding known abbreviations.

    normalise_system_alias("MTU")    → ["mtu", "main engine"]
    normalise_system_alias("filter") → ["filter"]
    """
    q = query.lower().strip()
    return list(_SYSTEM_ALIAS_MAP.get(q, [q]))


# ---------------------------------------------------------------------------
# Stock-to-equipment link inference
# ---------------------------------------------------------------------------

def infer_stock_equipment_link(stock_item: dict, equipment_records: list) -> dict:
    """Infer which equipment record a stock item most likely belongs to.

    Returns:
        confidence  "exact" | "likely" | "none"
        equipment   list of matched equipment record dicts
        label       human-readable string, e.g. "Likely linked to MTU 16V4000"

    Priority:
    1. Existing equipment_link field → exact
    2. linked_equipment substring-matches equipment_name/system → exact
    3. Stock make matches equipment make → likely
    4. System keyword in description/linked_equipment matches equipment system → likely
    """
    _none: dict = {"confidence": "none", "equipment": [], "label": ""}
    if not equipment_records:
        return _none

    def _eq_display(eq: dict) -> str:
        name = eq.get("equipment_name") or eq.get("system") or ""
        make = eq.get("make") or ""
        model = eq.get("model") or ""
        parts = [p for p in [name, make, model] if p]
        return " — ".join(parts[:2]) if len(parts) > 1 else (parts[0] if parts else "")

    # Phase 0 – already linked by link_stock_to_equipment()
    if stock_item.get("equipment_link"):
        existing = stock_item["equipment_link"]
        name = existing.get("equipment_name") or ""
        return {
            "confidence": "exact",
            "equipment": [existing],
            "label": f"Linked to {name}" if name else "",
        }

    item_make = (stock_item.get("make") or "").lower().strip()
    item_linked = (stock_item.get("linked_equipment") or "").lower().strip()
    item_desc = (stock_item.get("description") or "").lower()

    # Phase 1 – linked_equipment field matches equipment_name OR system
    # Collect ALL matching records (e.g. both Main Engine PS and SB)
    if item_linked:
        phase1: list = []
        for eq in equipment_records:
            candidates = [
                (eq.get("equipment_name") or "").lower().strip(),
                (eq.get("system") or "").lower().strip(),
            ]
            for eq_name in filter(None, candidates):
                if eq_name and (
                    item_linked == eq_name
                    or item_linked in eq_name
                    or eq_name in item_linked
                ):
                    phase1.append(eq)
                    break  # don't add same record twice for name vs system
        if phase1:
            label = _eq_display(phase1[0])
            return {
                "confidence": "exact",
                "equipment": phase1[:4],
                "label": f"Linked to {label}" if label else "",
            }

    # Phase 2 – manufacturer (make) match
    if item_make and len(item_make) > 2:
        matched = [
            eq for eq in equipment_records
            if item_make in (eq.get("make") or "").lower()
            or (eq.get("make") or "").lower() in item_make
        ]
        if matched:
            label = _eq_display(matched[0])
            return {
                "confidence": "likely",
                "equipment": matched[:3],
                "label": f"Likely linked to {label}" if label else "Likely linked to equipment",
            }

    # Phase 3 – system keyword in description/linked_equipment matches equipment system/name
    search_text = f"{item_desc} {item_linked}".strip()
    if search_text:
        for eq in equipment_records:
            for field in ("system", "equipment_name"):
                candidate = (eq.get(field) or "").lower().strip()
                if candidate and len(candidate) > 3 and candidate in search_text:
                    label = _eq_display(eq)
                    return {
                        "confidence": "likely",
                        "equipment": [eq],
                        "label": f"Likely linked to {label}" if label else "",
                    }

    return _none


def _normalise(s: str) -> str:
    """Lower-case and apply alias/spelling normalisations."""
    s = s.lower()
    for pattern, repl in _ALIAS_SUBS:
        s = pattern.sub(repl, s)
    return s.strip()


# Tokens that carry no equipment-identity signal and should not trigger matches.
# Includes English question words ("what model is…") that describe the query
# intent rather than identifying the equipment.
# "ac" alone is excluded: it appears inside words like "vacuum" / "reactor",
# whereas "a/c" (typed by a user) normalises to "airconditioning" (meaningful).
_SEARCH_WEAK_TOKENS = frozenset({
    # User-specified weak tokens
    "what", "are", "the", "specs", "spec", "specification", "specifications",
    "of", "for", "unit", "equipment", "system",
    # Question-intent words (tell me the X, not search for named-X)
    "model", "type", "make", "manufacturer", "number", "serial",
    # Common English query noise
    "a", "an", "is", "in", "on", "to", "at", "by", "do", "we", "me",
    "our", "how", "many", "have", "get", "and", "or", "with", "its",
    "this", "that", "there", "any", "all", "show",
    # "ac" alone is ambiguous
    "ac",
})

# (field_key, score_weight) — higher weight → stronger ranking signal.
_FIELD_WEIGHTS = (("name", 3), ("model", 2), ("sys", 1), ("make", 1))


def _score_norm(norm: dict, tokens: list) -> tuple:
    """
    Score a pre-normalised field dict against search tokens.
    Returns (total_score: float, matched_token_count: int).
    Per token, the highest-weighted field that matches is counted once.
    """
    score = 0.0
    matched = 0
    for token in tokens:
        best = 0.0
        for field, weight in _FIELD_WEIGHTS:
            if norm[field] and token in norm[field]:
                best = max(best, float(weight))
        if best == 0.0 and norm["serial"] and token in norm["serial"]:
            best = 1.0
        score += best
        if best > 0.0:
            matched += 1
    return score, matched


def find_equipment_by_query(user_id: str, query: str) -> tuple:
    """
    Search equipment records with normalisation and token scoring.

    Returns (results: list, broad_note: str | None).

    Strategy:
      Phase 1 — exact normalised-phrase match: returns immediately if any
                 field contains (or is contained by) the full normalised query.
      Phase 2 — single-token: direct match + singular-form fallback.
      Phase 3 — multi-token scoring: each item is scored by how many
                 meaningful tokens match and in which fields.  Items must
                 match at least 2 distinct tokens when the query has 3+.
                 Results are trimmed to 10 with a note when >10 match weakly.
    """
    q_norm = _normalise(query)
    all_items = get_all_equipment(user_id)

    # Pre-compute normalised fields once for all items
    norms = [
        {
            "name":   _normalise(it.get("equipment_name") or ""),
            "model":  _normalise(it.get("model") or ""),
            "sys":    _normalise(it.get("system") or ""),
            "make":   _normalise(it.get("make") or ""),
            "serial": (it.get("serial_number") or "").lower(),
        }
        for it in all_items
    ]

    # ── Phase 1: exact normalised phrase ──────────────────────────────────
    # Unidirectional: q_norm must appear IN a field (not the other way round).
    # Bidirectional matching caused false positives — e.g. "seawater cooling pump"
    # appearing as a substring of the query "chiller seawater cooling pump".
    exact = [
        item for item, norm in zip(all_items, norms)
        if (norm["name"]   and q_norm in norm["name"])
        or (norm["sys"]    and q_norm in norm["sys"])
        or (norm["make"]   and q_norm in norm["make"])
        or (norm["model"]  and q_norm in norm["model"])
        or (norm["serial"] and q_norm in norm["serial"])
    ]
    if exact:
        return exact, None

    # ── Extract meaningful tokens ─────────────────────────────────────────
    # Strip trailing punctuation so "pump?" is treated as "pump".
    raw_tokens = [t.rstrip("?!.,;:") for t in q_norm.split()]
    tokens = [
        t for t in raw_tokens
        if len(t) >= 2 and t not in _SEARCH_WEAK_TOKENS
    ]
    if not tokens:
        return [], None

    # ── Phase 2: single token (+ singular fallback) ───────────────────────
    if len(tokens) == 1:
        tok = tokens[0]
        singular = tok[:-1] if tok.endswith("s") and len(tok) > 4 else None
        results = []
        for item, norm in zip(all_items, norms):
            hit = any(norm[f] and tok in norm[f] for f in ("name", "model", "sys", "make", "serial"))
            if not hit and singular:
                hit = any(norm[f] and singular in norm[f] for f in ("name", "model", "sys", "make"))
            if hit:
                results.append(item)
        return results, None

    # ── Phase 3: multi-token scoring ──────────────────────────────────────
    # Require ALL meaningful tokens to match — this keeps results precise
    # for specific queries like "chiller seawater cooling pump" while
    # correctly excluding items that share only some tokens.
    min_matched = len(tokens)
    scored = []
    for item, norm in zip(all_items, norms):
        sc, matched = _score_norm(norm, tokens)
        if matched >= min_matched:
            scored.append((sc, matched, item))

    scored.sort(key=lambda x: (-x[0], -x[1]))

    broad_note = None
    if len(scored) > 10:
        weak = sum(1 for sc, m, _ in scored if m == min_matched)
        if weak >= 5:
            broad_note = (
                f"Found {len(scored)} matches — showing the top 10. "
                "Refine by make, model, or system for a more specific result."
            )
            scored = scored[:10]

    return [item for _, _, item in scored], broad_note
