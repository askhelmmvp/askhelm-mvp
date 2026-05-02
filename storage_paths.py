"""
Central storage path helpers for AskHelm.

Hierarchy
---------
DATA_DIR/
  global/                          ← shared across all yachts (regulations, KB)
  yachts/<yacht_id>/               ← per-yacht data (equipment, stock, handover)
    users/<user_id>/               ← per-user data (session state, context)
  users_index.json                 ← maps user_id → yacht_id

Environment
-----------
  DATA_DIR   Runtime data root. Default: ./data (local dev)
             Production: set to Render persistent disk mount, e.g. /var/data/askhelm

Default yacht
-------------
  All users currently default to yacht_id="h3".
  Override via users_index.json: {"<user_id>": "<yacht_id>", ...}
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------

def get_data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", str(_PROJECT_ROOT / "data")))


def get_global_dir() -> Path:
    return get_data_dir() / "global"


# ---------------------------------------------------------------------------
# Yacht-level paths
# ---------------------------------------------------------------------------

def get_yacht_dir(yacht_id: str) -> Path:
    return get_data_dir() / "yachts" / yacht_id


def get_equipment_memory_path(yacht_id: str) -> Path:
    return get_yacht_dir(yacht_id) / "equipment_memory.json"


def get_stock_memory_path(yacht_id: str) -> Path:
    return get_yacht_dir(yacht_id) / "stock_memory.json"


def get_handover_notes_path(yacht_id: str) -> Path:
    return get_yacht_dir(yacht_id) / "handover_notes.json"


def get_service_reports_dir(yacht_id: str) -> Path:
    return get_yacht_dir(yacht_id) / "service_reports"


def get_comparison_sessions_dir(yacht_id: str) -> Path:
    return get_yacht_dir(yacht_id) / "comparison_sessions"


def get_manuals_dir(yacht_id: str) -> Path:
    return get_yacht_dir(yacht_id) / "manuals"


def get_manuals_index_path(yacht_id: str) -> Path:
    return get_manuals_dir(yacht_id) / "manuals_index.json"


# ---------------------------------------------------------------------------
# User-level paths
# ---------------------------------------------------------------------------

def get_yacht_user_dir(yacht_id: str, user_id: str) -> Path:
    return get_yacht_dir(yacht_id) / "users" / user_id


def get_user_context_path(yacht_id: str, user_id: str) -> Path:
    return get_yacht_user_dir(yacht_id, user_id) / "state.json"


# ---------------------------------------------------------------------------
# Yacht ID resolution
# ---------------------------------------------------------------------------

_DEFAULT_YACHT_ID = "h3"


def get_yacht_id_for_user(user_id: str) -> str:
    """Return the yacht_id for a user. Defaults to 'h3' if not in users_index."""
    index_path = get_data_dir() / "users_index.json"
    try:
        if index_path.exists():
            with open(index_path, "r", encoding="utf-8") as f:
                index = json.load(f)
            if user_id in index:
                return index[user_id]
    except Exception as exc:
        logger.warning("storage_paths: failed to read users_index.json: %s", exc)
    return _DEFAULT_YACHT_ID


def set_yacht_id_for_user(user_id: str, yacht_id: str) -> None:
    """Persist a user → yacht_id mapping in users_index.json."""
    index_path = get_data_dir() / "users_index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index = {}
    try:
        if index_path.exists():
            with open(index_path, "r", encoding="utf-8") as f:
                index = json.load(f)
    except Exception as exc:
        logger.warning("storage_paths: failed to read users_index.json: %s", exc)
    index[user_id] = yacht_id
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)


# ---------------------------------------------------------------------------
# Migration: old data/users/<user_id>/ → new hierarchy
# ---------------------------------------------------------------------------

def migrate_user_files(user_id: str, yacht_id: str) -> None:
    """
    One-time migration of per-user files from old layout to new layout.
    Old:  DATA_DIR/users/<user_id>/<file>
    New:  DATA_DIR/yachts/<yacht_id>/<file>  (yacht-level)
          DATA_DIR/yachts/<yacht_id>/users/<user_id>/state.json  (user-level)
    Only copies if the new path doesn't already exist.
    """
    data_dir = get_data_dir()
    old_user_dir = data_dir / "users" / user_id
    if not old_user_dir.exists():
        return

    yacht_dir = get_yacht_dir(yacht_id)
    user_dir = get_yacht_user_dir(yacht_id, user_id)

    yacht_level = {
        "equipment_memory.json": get_equipment_memory_path(yacht_id),
        "stock_memory.json": get_stock_memory_path(yacht_id),
        "handover_notes.json": get_handover_notes_path(yacht_id),
    }
    user_level = {
        "state.json": get_user_context_path(yacht_id, user_id),
    }

    for filename, new_path in {**yacht_level, **user_level}.items():
        old_path = old_user_dir / filename
        if old_path.exists() and not new_path.exists():
            new_path.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(str(old_path), str(new_path))
            logger.info(
                "storage_paths: migrated %s → %s", old_path, new_path
            )


def migrate_all_users() -> None:
    """Migrate all users found under DATA_DIR/users/ to the new hierarchy."""
    old_users_dir = get_data_dir() / "users"
    if not old_users_dir.exists():
        return
    for user_dir in old_users_dir.iterdir():
        if user_dir.is_dir():
            user_id = user_dir.name
            yacht_id = get_yacht_id_for_user(user_id)
            migrate_user_files(user_id, yacht_id)


# ---------------------------------------------------------------------------
# Startup logging
# ---------------------------------------------------------------------------

def log_storage_paths() -> None:
    data_dir = get_data_dir()
    logger.info("── AskHelm storage paths ────────────────────────────────────")
    logger.info("DATA_DIR     : %s", data_dir)
    logger.info("GLOBAL_DIR   : %s", get_global_dir())
    logger.info("DEFAULT_YACHT: %s → %s", _DEFAULT_YACHT_ID, get_yacht_dir(_DEFAULT_YACHT_ID))
    logger.info("EQ_PATH      : %s", get_equipment_memory_path(_DEFAULT_YACHT_ID))
    logger.info("STOCK_PATH   : %s", get_stock_memory_path(_DEFAULT_YACHT_ID))
    logger.info("HANDOVER_PATH: %s", get_handover_notes_path(_DEFAULT_YACHT_ID))
    logger.info("─────────────────────────────────────────────────────────────")
