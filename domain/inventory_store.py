import json
import logging
import re
import datetime
from pathlib import Path
from typing import Optional

from config import USERS_DIR

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _equipment_path(user_id: str) -> Path:
    return USERS_DIR / user_id / "equipment_memory.json"


def _stock_path(user_id: str) -> Path:
    return USERS_DIR / user_id / "stock_memory.json"


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
                return json.load(f)
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
    logger.info(
        "inventory_store: stock added=%d merged=%d user=%s",
        added, merged, user_id,
    )
    return added, merged


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
    return results


def find_stock_for_system(user_id: str, query: str) -> list:
    """Return stock items whose linked_equipment or description matches query."""
    q = query.lower().strip()
    results = []
    for item in get_all_stock(user_id):
        linked = (item.get("linked_equipment") or "").lower()
        desc = (item.get("description") or "").lower()
        linked_match = linked and (q in linked or linked in q)
        desc_match = q in desc
        if linked_match or desc_match:
            results.append(item)
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
    (re.compile(r'\s+'), ' '),                               # collapse whitespace
)


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
