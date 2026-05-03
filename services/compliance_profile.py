"""
Per-yacht compliance profile management.

Stores at DATA_DIR/yachts/<yacht_id>/compliance_profile.json:
{
  "yacht_id": "h3",
  "selected_regulations": ["ISM Code 2018", "MARPOL Annex VI"],
  "vessel_documents": [
    {"name": "H3 SMS", "type": "sms", "path": "compliance/sms/h3_sms.pdf"}
  ]
}

Global regulations answer what the rule says.
Yacht SMS/procedures answer how this yacht complies.
"""

import json
import logging
from pathlib import Path
from typing import List, Dict

logger = logging.getLogger(__name__)


def _profile_path(yacht_id: str) -> Path:
    from storage_paths import get_compliance_profile_path
    return get_compliance_profile_path(yacht_id)


def load_profile(yacht_id: str) -> dict:
    """Load compliance profile for a yacht; creates a default if absent."""
    path = _profile_path(yacht_id)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logger.warning("compliance_profile: failed to load yacht=%s: %s", yacht_id, exc)
    profile = {"yacht_id": yacht_id, "selected_regulations": [], "vessel_documents": []}
    _save_profile(yacht_id, profile)
    return profile


def _save_profile(yacht_id: str, profile: dict) -> None:
    path = _profile_path(yacht_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)
    logger.debug("compliance_profile: saved yacht=%s", yacht_id)


def get_selected_regulations(yacht_id: str) -> List[str]:
    return load_profile(yacht_id).get("selected_regulations", [])


def enable_regulation(yacht_id: str, reg_name: str) -> bool:
    """Add regulation to yacht's selected list. Returns True if added (False if already present)."""
    profile = load_profile(yacht_id)
    selected = profile.setdefault("selected_regulations", [])
    if any(r.lower() == reg_name.lower() for r in selected):
        return False
    selected.append(reg_name)
    _save_profile(yacht_id, profile)
    logger.info("compliance_profile: enabled regulation=%r yacht=%s", reg_name, yacht_id)
    return True


def disable_regulation(yacht_id: str, reg_name: str) -> bool:
    """Remove regulation from yacht's selected list. Returns True if removed."""
    profile = load_profile(yacht_id)
    selected = profile.get("selected_regulations", [])
    new_selected = [r for r in selected if r.lower() != reg_name.lower()]
    if len(new_selected) == len(selected):
        return False
    profile["selected_regulations"] = new_selected
    _save_profile(yacht_id, profile)
    logger.info("compliance_profile: disabled regulation=%r yacht=%s", reg_name, yacht_id)
    return True


def add_vessel_document(yacht_id: str, doc_info: dict) -> None:
    """Add or replace a vessel document entry in the profile."""
    profile = load_profile(yacht_id)
    vessel_docs = profile.setdefault("vessel_documents", [])
    name = (doc_info.get("name") or "").lower()
    vessel_docs = [d for d in vessel_docs if (d.get("name") or "").lower() != name]
    vessel_docs.append(doc_info)
    profile["vessel_documents"] = vessel_docs
    _save_profile(yacht_id, profile)
    logger.info(
        "compliance_profile: added vessel doc=%r type=%r yacht=%s",
        doc_info.get("name"), doc_info.get("type"), yacht_id,
    )


def list_vessel_documents(yacht_id: str) -> List[Dict]:
    return load_profile(yacht_id).get("vessel_documents", [])
