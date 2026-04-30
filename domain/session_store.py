import os
import json
import hashlib

from storage_paths import get_user_context_path, get_yacht_id_for_user, migrate_user_files


def user_id_from_phone(phone: str) -> str:
    return hashlib.sha256(phone.encode()).hexdigest()[:16]


def _state_path(user_id: str) -> str:
    yacht_id = get_yacht_id_for_user(user_id)
    migrate_user_files(user_id, yacht_id)
    return str(get_user_context_path(yacht_id, user_id))


def load_user_state(user_id: str) -> dict:
    path = _state_path(user_id)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "user_id": user_id,
        "active_session_id": None,
        "sessions": [],
        "documents": [],
    }


def save_user_state(user_id: str, state: dict) -> None:
    path = _state_path(user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
