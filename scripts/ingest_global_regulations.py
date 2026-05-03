#!/usr/bin/env python3
"""
Bulk-ingest PDF regulations from data/global/regulations/ into the global
compliance knowledge base.

Usage:
    python3 scripts/ingest_global_regulations.py

Source folder:  <DATA_DIR>/global/regulations/
Output:
  <DATA_DIR>/global/compliance/compliance_chunks.jsonl
  <DATA_DIR>/global/compliance/compliance_index.pkl

Source names are derived from filenames:
  ISM_Code.pdf        → ISM Code
  MARPOL-Annex-VI.pdf → MARPOL Annex VI
  REG Yacht Code.pdf  → REG Yacht Code
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _source_name_from_filename(filename: str) -> str:
    """Derive a human-readable source name from a PDF filename."""
    name = re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE)
    name = name.replace("_", " ").replace("-", " ")
    return re.sub(r" +", " ", name).strip()


def main():
    from storage_paths import get_global_regulations_dir, get_compliance_index_path
    from services.compliance_ingest import ingest_compliance_pdf

    reg_dir = get_global_regulations_dir()
    reg_dir.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(reg_dir.glob("*.pdf"))
    if not pdfs:
        print(f"No PDF files found in {reg_dir}")
        print("Place regulation PDFs there and re-run.")
        return

    print(f"Found {len(pdfs)} PDF(s) in {reg_dir}")

    ingested = 0
    total_chunks = 0
    for pdf in pdfs:
        source_name = _source_name_from_filename(pdf.name)
        print(f"  Ingesting: {pdf.name} -> source '{source_name}' ...", end=" ", flush=True)
        try:
            total_chunks = ingest_compliance_pdf(str(pdf), source_name)
            ingested += 1
            print(f"OK (total chunks now: {total_chunks})")
        except Exception as exc:
            print(f"FAILED ({exc})")

    index_path = get_compliance_index_path()
    print(f"\nIngested {ingested}/{len(pdfs)} PDFs")
    print(f"Total chunks in index: {total_chunks}")
    print(f"Index: {index_path}")


if __name__ == "__main__":
    main()
