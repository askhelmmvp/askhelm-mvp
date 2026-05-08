import json
import logging
from pathlib import Path
from datetime import datetime, timezone

from storage_paths import get_manuals_index_path, get_yacht_id_for_user, migrate_user_files

logger = logging.getLogger(__name__)


def _manuals_path(user_id: str) -> Path:
    yacht_id = get_yacht_id_for_user(user_id)
    migrate_user_files(user_id, yacht_id)
    return get_manuals_index_path(yacht_id)


def load_manuals(user_id: str) -> dict:
    path = _manuals_path(user_id)
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:
        logger.warning("manual_store: failed to load for user=%s: %s", user_id, exc)
    return {"manuals": []}


def _write(user_id: str, data: dict) -> None:
    path = _manuals_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def save_manual(user_id: str, manual: dict, chunks: list, source_file: str) -> str:
    """Save a manual record. Returns the new manual ID."""
    import uuid as _uuid
    data = load_manuals(user_id)
    manual_id = str(_uuid.uuid4())[:8]

    entry = {
        "id": manual_id,
        "manufacturer": manual.get("manufacturer") or "",
        "product_name": manual.get("product_name") or "",
        "model": manual.get("model") or "",
        "document_type": manual.get("document_type") or "Technical Manual",
        "system": manual.get("system") or "",
        "year": manual.get("year") or "",
        "key_topics": manual.get("key_topics") or [],
        "source_file": source_file,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "chunks": chunks,
    }
    data["manuals"].append(entry)
    _write(user_id, data)
    logger.info(
        "manual_store: saved id=%s manufacturer=%r system=%r chunks=%d",
        manual_id, entry["manufacturer"], entry["system"], len(chunks),
    )
    return manual_id


def get_all_manuals(user_id: str) -> list:
    """Return all manual entries (without chunks for brevity)."""
    data = load_manuals(user_id)
    result = []
    for m in data.get("manuals", []):
        entry = {k: v for k, v in m.items() if k != "chunks"}
        result.append(entry)
    return result


def find_manuals_by_equipment(user_id: str, query: str) -> list:
    """Return manuals whose system, manufacturer, or product_name matches the query."""
    data = load_manuals(user_id)
    q = query.lower().strip()
    results = []
    for m in data.get("manuals", []):
        fields = [
            (m.get("system") or "").lower(),
            (m.get("manufacturer") or "").lower(),
            (m.get("product_name") or "").lower(),
            (m.get("model") or "").lower(),
        ]
        if any(q in f or f in q for f in fields if f):
            results.append(m)
    return results


def delete_manual_by_source(user_id: str, source_file: str) -> bool:
    """Remove all manual entries whose source_file matches. Returns True if any were removed."""
    data = load_manuals(user_id)
    before = len(data.get("manuals", []))
    data["manuals"] = [
        m for m in data.get("manuals", [])
        if m.get("source_file") != source_file
    ]
    after = len(data["manuals"])
    if after < before:
        _write(user_id, data)
        logger.info(
            "manual_store: deleted %d manual(s) source_file=%r user=%s",
            before - after, source_file, user_id,
        )
        return True
    return False


def search_manual_chunks(user_id: str, query: str, top_k: int = 4) -> list:
    """
    Keyword search across all manual chunks.
    Returns up to top_k chunks ranked by keyword hit count, each with manual metadata.
    """
    data = load_manuals(user_id)
    tokens = set(query.lower().split())
    scored = []

    for m in data.get("manuals", []):
        label = " ".join(filter(None, [m.get("manufacturer"), m.get("product_name")]))
        label = label or m.get("document_type") or "Manual"
        for chunk in m.get("chunks", []):
            text = (chunk.get("text") or "").lower()
            score = sum(1 for t in tokens if t in text)
            if score > 0:
                scored.append({
                    "score": score,
                    "manual_id": m["id"],
                    "manual_label": label,
                    "system": m.get("system") or "",
                    "heading": chunk.get("heading") or "",
                    "text": chunk.get("text") or "",
                })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]
