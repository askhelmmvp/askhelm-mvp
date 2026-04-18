import os
import json
import hashlib

from config import USERS_DIR


def user_id_from_phone(phone: str) -> str:
    return hashlib.sha256(phone.encode()).hexdigest()[:16]


def _state_path(user_id: str) -> str:
    return str(USERS_DIR / user_id / "state.json")


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
