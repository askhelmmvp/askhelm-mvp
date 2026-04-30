import json
import logging
from pathlib import Path
from datetime import datetime, timezone

from storage_paths import get_handover_notes_path, get_yacht_id_for_user, migrate_user_files

logger = logging.getLogger(__name__)


def _handover_path(user_id: str) -> Path:
    yacht_id = get_yacht_id_for_user(user_id)
    migrate_user_files(user_id, yacht_id)
    return get_handover_notes_path(yacht_id)


def load_handover_notes(user_id: str) -> dict:
    path = _handover_path(user_id)
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:
        logger.warning("handover_store: failed to load for user=%s: %s", user_id, exc)
    return {"service_reports": []}


def _write(user_id: str, notes: dict) -> None:
    path = _handover_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(notes, f, indent=2)


def save_service_report(
    user_id: str, report: dict, handover_note: str, source_file: str
) -> str:
    """Save a service report to the handover store. Returns the new report ID."""
    notes = load_handover_notes(user_id)
    import uuid as _uuid
    report_id = str(_uuid.uuid4())[:8]

    work = (report.get("work_carried_out") or [])[:2]
    summary = "; ".join(w[:80] for w in work)

    entry = {
        "id": report_id,
        "date": report.get("date") or "",
        "supplier": report.get("supplier") or "",
        "system": report.get("system") or "",
        "equipment": report.get("equipment") or "",
        "vessel": report.get("vessel") or "",
        "make_model": report.get("make_model") or "",
        "summary": summary,
        "handover_note": handover_note,
        "open_actions": report.get("open_actions") or [],
        "findings": report.get("findings") or [],
        "recommendations": report.get("recommendations") or [],
        "source_file": source_file,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    notes["service_reports"].append(entry)
    _write(user_id, notes)
    logger.info(
        "handover_store: saved id=%s system=%r supplier=%r open_actions=%d",
        report_id, entry["system"], entry["supplier"], len(entry["open_actions"]),
    )
    return report_id


def save_notes_summary(
    user_id: str, summary_data: dict, source_file: str
) -> str:
    """
    Save an operational or technical note summary to the handover store.
    summary_data must contain: doc_subtype, summary, issues, open_actions.
    Returns the new entry ID.
    """
    import uuid as _uuid
    notes = load_handover_notes(user_id)
    entry_id = str(_uuid.uuid4())[:8]

    entry = {
        "id": entry_id,
        "record_type": summary_data.get("doc_subtype") or "operational_notes",
        "date": summary_data.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "supplier": "",
        "system": summary_data.get("system") or "",
        "equipment": "",
        "vessel": "",
        "make_model": "",
        "summary": summary_data.get("summary") or "",
        "handover_note": summary_data.get("summary") or "",
        "open_actions": summary_data.get("open_actions") or [],
        "findings": summary_data.get("issues") or [],
        "recommendations": [],
        "source_file": source_file,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    notes["service_reports"].append(entry)
    _write(user_id, notes)
    logger.info(
        "handover_store: notes_summary saved id=%s type=%r open_actions=%d",
        entry_id, entry["record_type"], len(entry["open_actions"]),
    )
    return entry_id


def get_all_open_actions(user_id: str) -> list:
    """Return all open action groups (one per report that has open actions)."""
    notes = load_handover_notes(user_id)
    results = []
    for r in notes.get("service_reports", []):
        actions = r.get("open_actions") or []
        if actions:
            results.append({
                "system": r.get("system") or r.get("equipment") or "Unknown system",
                "date": r.get("date") or "",
                "supplier": r.get("supplier") or "",
                "open_actions": actions,
            })
    return results


def get_all_reports(user_id: str) -> list:
    notes = load_handover_notes(user_id)
    return notes.get("service_reports", [])


def get_reports_for_system(user_id: str, query: str) -> list:
    """Return reports where system or equipment name matches the query (substring)."""
    notes = load_handover_notes(user_id)
    q = query.lower().strip()
    results = []
    for r in notes.get("service_reports", []):
        system = (r.get("system") or "").lower()
        equipment = (r.get("equipment") or "").lower()
        if q in system or q in equipment or system in q or equipment in q:
            results.append(r)
    return results
