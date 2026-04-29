import json
import logging
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


def find_equipment_by_query(user_id: str, query: str) -> list:
    """Fuzzy match against system, equipment_name, make."""
    q = query.lower().strip()
    results = []
    for item in get_all_equipment(user_id):
        system = (item.get("system") or "").lower()
        name = (item.get("equipment_name") or "").lower()
        make = (item.get("make") or "").lower()
        if (
            q in system or (system and system in q)
            or q in name or (name and name in q)
            or (make and q in make)
        ):
            results.append(item)
    return results
