import json
import logging
from pathlib import Path

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
# Dedup keys
# ---------------------------------------------------------------------------

def _equipment_key(item: dict) -> tuple:
    return (
        (item.get("system") or "").lower().strip(),
        (item.get("equipment_name") or "").lower().strip(),
        (item.get("make") or "").lower().strip(),
        (item.get("model") or "").lower().strip(),
    )


def _stock_key(item: dict) -> tuple:
    pn = (item.get("part_number") or "").strip()
    if pn:
        return ("pn", pn.lower())
    return ("desc", (item.get("description") or "").lower().strip()[:60])


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

def merge_equipment(user_id: str, new_items: list, source_file: str) -> tuple:
    """Upsert equipment records. Returns (added, merged)."""
    data = load_equipment(user_id)
    existing = data["equipment"]
    key_map = {_equipment_key(e): i for i, e in enumerate(existing)}

    added = merged = 0
    for item in new_items:
        key = _equipment_key(item)
        if all(k == "" for k in key):
            continue
        if key in key_map:
            old = existing[key_map[key]]
            for field in ("location", "serial_number", "notes", "make", "model"):
                if item.get(field) and not old.get(field):
                    old[field] = item[field]
            old["confidence"] = round(min(old.get("confidence", 0.7), item.get("confidence", 0.7)), 2)
            merged += 1
        else:
            item["source_file"] = source_file
            item.setdefault("confidence", 0.7)
            existing.append(item)
            key_map[key] = len(existing) - 1
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
