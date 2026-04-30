"""
Central configuration for AskHelm.

Environment variables:
  DATA_DIR      Runtime data root (uploads, rendered images, session state,
                equipment/stock memory, handover notes).
                Default: ./data  (project-local, fine for development)
                Production: set to the Render persistent disk mount path,
                e.g. /var/data/askhelm
                Alias: STORAGE_DIR accepted for backward compatibility.

  KB_DIR        Knowledge-base directory (vector index + JSONL chunks).
                Default: <project_root>/data/knowledge_base
                Override only if KB files live on the persistent disk instead
                of being bundled with the repo.
"""

import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Runtime storage  (user-generated, must survive redeploys → persistent disk)
# ---------------------------------------------------------------------------
# DATA_DIR is the canonical env var; STORAGE_DIR accepted as a legacy alias.
_data_env = os.environ.get("DATA_DIR") or os.environ.get("STORAGE_DIR")
DATA_DIR     = Path(_data_env) if _data_env else _PROJECT_ROOT / "data"
STORAGE_DIR  = DATA_DIR   # backward-compat alias

UPLOADS_DIR  = DATA_DIR / "uploads"
RENDERED_DIR = DATA_DIR / "rendered"
USERS_DIR    = DATA_DIR / "users"   # legacy path; new code uses storage_paths helpers

# ---------------------------------------------------------------------------
# Knowledge base  (static app data bundled with the repository)
# ---------------------------------------------------------------------------
_kb_env = os.environ.get("KB_DIR")
KB_DIR         = Path(_kb_env) if _kb_env else _PROJECT_ROOT / "data" / "knowledge_base"
KB_INDEX_PATH  = KB_DIR / "askhelm_vector_index.pkl"
KB_CHUNKS_PATH = KB_DIR / "askhelm_compliance_chunks.jsonl"


def log_startup() -> None:
    """Log resolved paths at startup so the deployed state is visible in logs."""
    from storage_paths import log_storage_paths
    logger.info("── AskHelm startup ──────────────────────────────────────────")
    logger.info("DATA_DIR     : %s", DATA_DIR)
    logger.info("UPLOADS_DIR  : %s", UPLOADS_DIR)
    logger.info("RENDERED_DIR : %s", RENDERED_DIR)
    logger.info("KB_INDEX     : %s  [exists=%s]", KB_INDEX_PATH, KB_INDEX_PATH.exists())
    logger.info("─────────────────────────────────────────────────────────────")
    log_storage_paths()
