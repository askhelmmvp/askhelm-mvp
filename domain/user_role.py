"""Role management for AskHelm WhatsApp users."""
import re
from typing import Optional

VALID_ROLES = frozenset({"engineer", "deck_officer", "captain", "purser"})

ROLE_DISPLAY = {
    "engineer": "Engineer",
    "deck_officer": "Deck Officer",
    "captain": "Captain",
    "purser": "Purser",
}

# Ordered from most-specific to least-specific to avoid "deck" matching before "deck officer"
_ROLE_PATTERNS = [
    (re.compile(r'\bchief\s+engineer\b', re.I), "engineer"),
    (re.compile(r'\bdeck\s+officer\b', re.I), "deck_officer"),
    (re.compile(r'\bdeck_officer\b', re.I), "deck_officer"),
    (re.compile(r'\bengineer\b', re.I), "engineer"),
    (re.compile(r'\bcaptain\b', re.I), "captain"),
    (re.compile(r'\bmaster\b', re.I), "captain"),
    (re.compile(r'\bpurser\b', re.I), "purser"),
]


def extract_role_from_message(text: str) -> Optional[str]:
    """Return normalised role name from a set-role message, or None."""
    for pattern, role in _ROLE_PATTERNS:
        if pattern.search(text):
            return role
    return None


def get_user_role(state: dict) -> Optional[str]:
    """Return the stored role for this user, or None."""
    return state.get("role")


def set_user_role(state: dict, role: str) -> dict:
    """Store role in the user state dict. Caller must save state."""
    state["role"] = role
    return state
