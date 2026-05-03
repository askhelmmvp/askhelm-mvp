"""
Compliance knowledge base management.

Stores chunks at DATA_DIR/global/compliance/compliance_chunks.jsonl
and index at DATA_DIR/global/compliance/compliance_index.pkl.

This is global (shared across all vessels/users) and persists on the
DATA_DIR persistent disk in production.

On first boot with no compliance data:
  - migrates any existing data/knowledge_base/ chunks
  - seeds built-in ISM Code + MARPOL Annex I–VI content
  - builds TF-IDF index

Subsequent uploads (via ingest_compliance_pdf or ingest_compliance_text)
append to the JSONL and rebuild the index in-place.
"""

import json
import logging
import pickle
import re
import os
import uuid
from pathlib import Path
from typing import List, Dict, Optional
from collections import Counter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _chunks_path() -> Path:
    from storage_paths import get_compliance_chunks_path
    return get_compliance_chunks_path()


def _index_path() -> Path:
    from storage_paths import get_compliance_index_path
    return get_compliance_index_path()


# ---------------------------------------------------------------------------
# Chunk I/O
# ---------------------------------------------------------------------------

def load_chunks() -> List[Dict]:
    p = _chunks_path()
    if not p.exists():
        return []
    chunks = []
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    chunks.append(json.loads(line))
    except Exception as exc:
        logger.warning("compliance_ingest: failed to load chunks: %s", exc)
    return chunks


def save_chunks(chunks: List[Dict]) -> None:
    p = _chunks_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk) + "\n")


# ---------------------------------------------------------------------------
# Index build / rebuild
# ---------------------------------------------------------------------------

def build_index(chunks: List[Dict]) -> None:
    """Build TF-IDF index from chunks and write to compliance_index.pkl."""
    if not chunks:
        logger.warning("compliance_ingest: no chunks — skipping index build")
        return
    from sklearn.feature_extraction.text import TfidfVectorizer
    texts = []
    for c in chunks:
        # Combine content + topic + keywords for richer matching
        kw = " ".join(c.get("keywords") or [])
        topic = c.get("topic") or ""
        section = c.get("section") or ""
        content = c.get("content") or ""
        texts.append(f"{section} {topic} {kw} {content}")
    vectorizer = TfidfVectorizer(
        ngram_range=(1, 2),
        max_features=10000,
        sublinear_tf=True,
        stop_words="english",
    )
    matrix = vectorizer.fit_transform(texts)
    p = _index_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as f:
        pickle.dump({"vectorizer": vectorizer, "matrix": matrix, "metadata": chunks}, f)
    logger.info(
        "compliance_ingest: index built — chunks=%d vocab=%d path=%s",
        len(chunks), len(vectorizer.vocabulary_), p,
    )


def rebuild_index() -> int:
    """Reload chunks from JSONL and rebuild the index. Returns chunk count."""
    chunks = load_chunks()
    build_index(chunks)
    return len(chunks)


# ---------------------------------------------------------------------------
# Source management
# ---------------------------------------------------------------------------

def list_sources() -> List[Dict]:
    """Return unique source documents with chunk counts."""
    chunks = load_chunks()
    counts: Counter = Counter()
    for c in chunks:
        src = c.get("source") or c.get("document") or "Unknown"
        # Strip file extension and path noise
        src = re.sub(r"\.pdf$", "", src, flags=re.I).strip()
        counts[src] += 1
    return [{"source": s, "chunks": n} for s, n in sorted(counts.items())]


# ---------------------------------------------------------------------------
# PDF ingestion
# ---------------------------------------------------------------------------

def ingest_compliance_pdf(pdf_path: str, source_name: str) -> int:
    """
    Extract text from a compliance PDF, chunk it, add to knowledge base.
    Replaces any existing chunks from the same source. Returns new chunk count.
    """
    from domain.extraction import extract_pdf_text
    text = extract_pdf_text(pdf_path)
    if not text.strip():
        logger.warning("compliance_ingest: no text extracted from %s", pdf_path)
        return 0
    filename = os.path.basename(pdf_path)
    new_chunks = _chunk_compliance_text(text, source_name, filename)
    if not new_chunks:
        logger.warning("compliance_ingest: zero chunks produced from %s", pdf_path)
        return 0
    return _add_chunks(new_chunks, replace_source_file=filename)


def ingest_compliance_text(
    text: str, source_name: str, filename: str = "", replace: bool = False
) -> int:
    """Add plain text as compliance chunks. Returns total chunk count after ingest."""
    fname = filename or f"{source_name}.txt"
    new_chunks = _chunk_compliance_text(text, source_name, fname)
    return _add_chunks(new_chunks, replace_source_file=fname if replace else None)


def _add_chunks(
    new_chunks: List[Dict], replace_source_file: Optional[str] = None
) -> int:
    existing = load_chunks()
    if replace_source_file:
        existing = [
            c for c in existing
            if (c.get("document") or "").lower() != replace_source_file.lower()
        ]
    combined = existing + new_chunks
    save_chunks(combined)
    build_index(combined)
    logger.info(
        "compliance_ingest: added %d chunks (total=%d)", len(new_chunks), len(combined)
    )
    return len(combined)


def _chunk_compliance_text(
    text: str, source_name: str, filename: str, chunk_size: int = 700
) -> List[Dict]:
    """Split compliance text into chunks with section detection."""
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    chunks = []
    current_section = ""
    current_lines: List[str] = []
    current_len = 0

    def _flush():
        if not current_lines:
            return
        content = "\n\n".join(current_lines)
        ref = f"{source_name} — {current_section}" if current_section else source_name
        chunks.append({
            "id": str(uuid.uuid4())[:8],
            "document": filename,
            "section": current_section,
            "page": "",
            "topic": current_section or source_name,
            "keywords": [],
            "content": content,
            "source_reference": ref,
            "source": source_name,
        })

    for para in paragraphs:
        is_heading = (
            len(para) < 120 and (
                re.match(r"^\d+\.?\s+[A-Z]", para) or
                re.match(r"^(chapter|regulation|rule|article|annex)\s+\d+", para, re.I) or
                (para.isupper() and 4 < len(para) < 80)
            )
        )
        if is_heading and current_len >= chunk_size // 2:
            _flush()
            current_lines = []
            current_len = 0
            current_section = para

        current_lines.append(para)
        current_len += len(para)

        if current_len >= chunk_size:
            _flush()
            current_lines = current_lines[-1:]  # overlap with last para
            current_len = len(current_lines[0]) if current_lines else 0

    _flush()
    return chunks


# ---------------------------------------------------------------------------
# Built-in seed data (ISM Code 2018 + MARPOL Annex I–VI)
# ---------------------------------------------------------------------------

_SEED_CHUNKS: List[Dict] = [
    # ── ISM Code ──────────────────────────────────────────────────────────
    {
        "id": "ism-c1-def",
        "document": "ISM-Code-2018.pdf",
        "section": "Chapter 1 — Definitions",
        "page": "3",
        "topic": "ISM Code Chapter 1 — definitions company SMS DOC SMC non-conformity major non-conformity",
        "keywords": ["ISM", "definitions", "company", "master", "SMS", "DOC", "SMC", "non-conformity", "major non-conformity"],
        "content": (
            "ISM Code Chapter 1 — Definitions.\n\n"
            "Company: The owner of the ship or any other organisation or person such as the manager or "
            "bareboat charterer who has assumed responsibility for operation of the ship from the shipowner.\n\n"
            "Safety management system (SMS): A structured and documented system enabling company personnel "
            "to implement effectively the company safety and environmental protection policy.\n\n"
            "Document of Compliance (DOC): A document issued to a company complying with the requirements "
            "of the ISM Code.\n\n"
            "Safety Management Certificate (SMC): A document issued to a ship certifying that the company "
            "and its shipboard management operate in accordance with an approved SMS.\n\n"
            "Non-conformity: An observed situation where objective evidence indicates the non-fulfilment "
            "of a specified requirement.\n\n"
            "Major non-conformity: An identifiable deviation that poses a serious threat to the safety of "
            "personnel or the ship or a serious risk to the environment that requires immediate corrective action.\n\n"
            "Observation: A statement of fact made during a safety management audit and substantiated by "
            "objective evidence."
        ),
        "source_reference": "ISM Code 2018 — Chapter 1, Definitions",
        "source": "ISM Code 2018",
    },
    {
        "id": "ism-c1-obj",
        "document": "ISM-Code-2018.pdf",
        "section": "Chapter 1.2 — Objectives",
        "page": "4",
        "topic": "ISM Code Chapter 1.2 — objectives safety at sea pollution prevention human injury",
        "keywords": ["ISM", "objectives", "safety", "pollution prevention", "SMS", "safe practices", "risk assessment"],
        "content": (
            "ISM Code Chapter 1.2 — Objectives.\n\n"
            "The objectives of the ISM Code are to ensure safety at sea, prevention of human injury or "
            "loss of life, and avoidance of damage to the environment, in particular the marine environment, "
            "and to property.\n\n"
            "Safety management objectives of the company should:\n"
            "1. provide for safe practices in ship operation and a safe working environment;\n"
            "2. assess all identified risks to its ships, personnel and the environment and establish "
            "appropriate safeguards; and\n"
            "3. continuously improve safety management skills of personnel ashore and aboard ships, "
            "including preparing for emergencies related both to safety and environmental protection.\n\n"
            "The ISM Code requires the SMS to ensure:\n"
            ".1 compliance with mandatory rules and regulations; and\n"
            ".2 that applicable codes, guidelines and standards recommended by the Organization, "
            "Administrations, classification societies and maritime industry organisations are taken into account."
        ),
        "source_reference": "ISM Code 2018 — Chapter 1.2, Objectives",
        "source": "ISM Code 2018",
    },
    {
        "id": "ism-c2",
        "document": "ISM-Code-2018.pdf",
        "section": "Chapter 2 — Safety and Environmental Protection Policy",
        "page": "5",
        "topic": "ISM Code Chapter 2 — safety policy environmental protection policy implementation",
        "keywords": ["ISM", "chapter 2", "safety policy", "environmental policy", "pollution prevention policy"],
        "content": (
            "ISM Code Chapter 2 — Safety and Environmental Protection Policy.\n\n"
            "2.1 The Company shall establish a safety and environmental protection policy which describes "
            "how the objectives given in paragraph 1.2 will be achieved.\n\n"
            "2.2 The company shall ensure that the policy is implemented and maintained at all levels of "
            "the organisation, both ship-based and shore-based."
        ),
        "source_reference": "ISM Code 2018 — Chapter 2, Safety and Environmental Protection Policy",
        "source": "ISM Code 2018",
    },
    {
        "id": "ism-c3",
        "document": "ISM-Code-2018.pdf",
        "section": "Chapter 3 — Company Responsibilities and Authority",
        "page": "6",
        "topic": "ISM Code Chapter 3 — company responsibilities authority SMS resources shore support",
        "keywords": ["ISM", "chapter 3", "company responsibilities", "authority", "SMS", "shore support", "resources"],
        "content": (
            "ISM Code Chapter 3 — Company Responsibilities and Authority.\n\n"
            "3.1 If the entity who is responsible for the operation of the ship is other than the owner, "
            "the owner must report the full name and details of such entity to the Administration.\n\n"
            "3.2 The company shall define and document the responsibility, authority and interrelation of "
            "all personnel who manage, perform and verify work relating to and affecting safety and "
            "pollution prevention.\n\n"
            "3.3 The company is responsible for ensuring that adequate resources and shore-based support "
            "are provided to enable the Designated Person or Persons to carry out their functions."
        ),
        "source_reference": "ISM Code 2018 — Chapter 3, Company Responsibilities and Authority",
        "source": "ISM Code 2018",
    },
    {
        "id": "ism-c4",
        "document": "ISM-Code-2018.pdf",
        "section": "Chapter 4 — Designated Person(s)",
        "page": "6",
        "topic": "ISM Code Chapter 4 — designated person ashore DPA safety link management access",
        "keywords": ["ISM", "chapter 4", "DPA", "designated person", "ashore", "safety link", "management access"],
        "content": (
            "ISM Code Chapter 4 — Designated Person(s).\n\n"
            "To ensure the safe operation of each ship and to provide a link between the company and those "
            "on board, every company, as appropriate, should designate a person or persons ashore having "
            "direct access to the highest level of management.\n\n"
            "The responsibility and authority of the Designated Person or Persons shall include monitoring "
            "the safety and pollution prevention aspects of the operation of each ship and ensuring that "
            "adequate resources and shore-based support are applied, as required."
        ),
        "source_reference": "ISM Code 2018 — Chapter 4, Designated Person(s)",
        "source": "ISM Code 2018",
    },
    {
        "id": "ism-c5",
        "document": "ISM-Code-2018.pdf",
        "section": "Chapter 5 — Master's Responsibility and Authority",
        "page": "7",
        "topic": "ISM Code Chapter 5 — master responsibility authority overriding authority safety decisions",
        "keywords": ["ISM", "chapter 5", "master", "responsibility", "overriding authority", "safety decisions"],
        "content": (
            "ISM Code Chapter 5 — Master's Responsibility and Authority.\n\n"
            "5.1 The company shall clearly define and document the master's responsibility with regard to "
            "implementing the safety and environmental protection policy of the company.\n\n"
            "5.2 The company shall ensure that the SMS operated on board the ship contains a clear statement "
            "emphasising the master's authority. The company shall establish in the SMS that the master has "
            "the overriding authority and the responsibility to make decisions with respect to safety and "
            "pollution prevention and to request the company's assistance as may be necessary."
        ),
        "source_reference": "ISM Code 2018 — Chapter 5, Master's Responsibility and Authority",
        "source": "ISM Code 2018",
    },
    {
        "id": "ism-c6",
        "document": "ISM-Code-2018.pdf",
        "section": "Chapter 6 — Resources and Personnel",
        "page": "7-8",
        "topic": "ISM Code Chapter 6 — resources personnel qualifications training familiarisation crew",
        "keywords": ["ISM", "chapter 6", "resources", "personnel", "qualifications", "training", "familiarisation", "working language"],
        "content": (
            "ISM Code Chapter 6 — Resources and Personnel.\n\n"
            "6.1 The company shall ensure that the master is: properly qualified for command; fully "
            "conversant with the company's SMS; and given the necessary support so that the master's "
            "duties can be safely performed.\n\n"
            "6.2 The company shall ensure that each ship is manned with qualified, certificated and "
            "medically fit seafarers in accordance with national and international requirements.\n\n"
            "6.3 The company shall establish procedures to ensure that new personnel and personnel "
            "transferred to new assignments related to safety and protection of the environment are "
            "given proper familiarisation with their duties.\n\n"
            "6.4 The company shall ensure that all personnel involved in the company's SMS have an "
            "adequate understanding of relevant rules, regulations, codes and guidelines.\n\n"
            "6.5 The company shall establish and maintain procedures for identifying any training which "
            "may be required in support of the SMS and ensure that such training is provided for all "
            "personnel concerned.\n\n"
            "6.6 The company shall establish procedures by which the ship's personnel receive relevant "
            "information on the SMS in a working language or languages understood by them.\n\n"
            "6.7 The company shall ensure that the ship's personnel are able to communicate effectively "
            "in the execution of their duties related to the SMS."
        ),
        "source_reference": "ISM Code 2018 — Chapter 6, Resources and Personnel",
        "source": "ISM Code 2018",
    },
    {
        "id": "ism-c7",
        "document": "ISM-Code-2018.pdf",
        "section": "Chapter 7 — Shipboard Operations",
        "page": "8",
        "topic": "ISM Code Chapter 7 — shipboard operations procedures plans instructions checklists key operations",
        "keywords": ["ISM", "chapter 7", "shipboard operations", "procedures", "plans", "instructions", "checklists"],
        "content": (
            "ISM Code Chapter 7 — Shipboard Operations.\n\n"
            "The company shall establish procedures, plans and instructions, including checklists as "
            "appropriate, for key shipboard operations concerning the safety of personnel, the ship and "
            "protection of the environment. The various tasks involved shall be defined and assigned to "
            "qualified personnel."
        ),
        "source_reference": "ISM Code 2018 — Chapter 7, Shipboard Operations",
        "source": "ISM Code 2018",
    },
    {
        "id": "ism-c8",
        "document": "ISM-Code-2018.pdf",
        "section": "Chapter 8 — Emergency Preparedness",
        "page": "8-9",
        "topic": "ISM Code Chapter 8 — emergency preparedness drills exercises muster emergency response",
        "keywords": ["ISM", "chapter 8", "emergency preparedness", "drills", "exercises", "muster", "emergency response"],
        "content": (
            "ISM Code Chapter 8 — Emergency Preparedness.\n\n"
            "8.1 The company shall establish procedures to identify, describe and respond to potential "
            "emergency shipboard situations.\n\n"
            "8.2 The company shall establish programmes for drills and exercises to prepare for emergency "
            "actions.\n\n"
            "8.3 The SMS shall provide for measures ensuring that the company's organisation can respond "
            "at any time to hazards, accidents and emergency situations involving its ships."
        ),
        "source_reference": "ISM Code 2018 — Chapter 8, Emergency Preparedness",
        "source": "ISM Code 2018",
    },
    {
        "id": "ism-c9",
        "document": "ISM-Code-2018.pdf",
        "section": "Chapter 9 — Reports and Analysis of Non-conformities",
        "page": "9",
        "topic": "ISM Code Chapter 9 — non-conformities accidents hazardous occurrences reporting investigation corrective action",
        "keywords": ["ISM", "chapter 9", "non-conformity", "accident", "near miss", "hazardous occurrence", "reporting", "investigation", "corrective action", "recurrence"],
        "content": (
            "ISM Code Chapter 9 — Reports and Analysis of Non-conformities, Accidents and Hazardous "
            "Occurrences.\n\n"
            "9.1 The SMS shall include procedures ensuring that non-conformities, accidents and hazardous "
            "situations are reported to the company, investigated and analysed with the objective of "
            "improving safety and pollution prevention.\n\n"
            "9.2 The company shall establish procedures for the implementation of corrective action, "
            "including measures intended to prevent recurrence."
        ),
        "source_reference": "ISM Code 2018 — Chapter 9, Reports and Analysis",
        "source": "ISM Code 2018",
    },
    {
        "id": "ism-c10",
        "document": "ISM-Code-2018.pdf",
        "section": "Chapter 10 — Maintenance of the Ship and Equipment",
        "page": "9-10",
        "topic": "ISM Code Chapter 10 — maintenance ship equipment inspections non-conformity corrective action records critical equipment standby testing reliability",
        "keywords": [
            "ISM", "chapter 10", "maintenance", "ship", "equipment",
            "inspections", "intervals", "non-conformity", "corrective action",
            "records", "critical equipment", "standby", "reliability", "testing",
            "rules", "regulations", "conform",
        ],
        "content": (
            "ISM Code Chapter 10 — Maintenance of the Ship and Equipment.\n\n"
            "10.1 The company shall establish procedures to ensure that the ship is maintained in "
            "conformity with the provisions of the relevant rules and regulations and with any additional "
            "requirements which may be established by the Company.\n\n"
            "10.2 In meeting these requirements the Company shall ensure that:\n"
            ".1 inspections are held at appropriate intervals;\n"
            ".2 any non-conformity is reported, with its possible cause, if known;\n"
            ".3 corrective action is taken; and\n"
            ".4 records of these activities are maintained.\n\n"
            "10.3 The company shall establish procedures in its SMS to identify equipment and technical "
            "systems the sudden operational failure of which may result in hazardous situations. The SMS "
            "shall provide for specific measures aimed at promoting the reliability of such equipment or "
            "systems. These measures shall include the regular testing of stand-by arrangements and "
            "equipment or technical systems that are not in continuous use."
        ),
        "source_reference": "ISM Code 2018 — Chapter 10, Maintenance of the Ship and Equipment",
        "source": "ISM Code 2018",
    },
    {
        "id": "ism-c11",
        "document": "ISM-Code-2018.pdf",
        "section": "Chapter 11 — Documentation",
        "page": "10",
        "topic": "ISM Code Chapter 11 — documentation records SMS documents controlled safety management manual",
        "keywords": ["ISM", "chapter 11", "documentation", "records", "controlled documents", "safety management manual", "SMS documents"],
        "content": (
            "ISM Code Chapter 11 — Documentation.\n\n"
            "11.1 The company shall establish and maintain procedures to control all documents and data "
            "which are relevant to the SMS.\n\n"
            "11.2 The company shall ensure that:\n"
            ".1 valid documents are available at all relevant locations;\n"
            ".2 changes to documents are reviewed and approved by authorised personnel; and\n"
            ".3 obsolete documents are promptly removed.\n\n"
            "11.3 The documents used to describe and implement the SMS may be referred to as the Safety "
            "Management Manual. Documentation should be kept in a form that the company considers most "
            "effective. Each ship should carry on board all documentation relevant to that ship."
        ),
        "source_reference": "ISM Code 2018 — Chapter 11, Documentation",
        "source": "ISM Code 2018",
    },
    {
        "id": "ism-c12",
        "document": "ISM-Code-2018.pdf",
        "section": "Chapter 12 — Company Verification, Review and Evaluation",
        "page": "10-11",
        "topic": "ISM Code Chapter 12 — internal audit company verification review evaluation SMS effectiveness",
        "keywords": ["ISM", "chapter 12", "internal audit", "verification", "review", "evaluation", "SMS effectiveness", "corrective actions"],
        "content": (
            "ISM Code Chapter 12 — Company Verification, Review and Evaluation.\n\n"
            "12.1 The company shall carry out internal safety audits to verify whether safety and "
            "pollution prevention activities comply with the SMS.\n\n"
            "12.2 The company shall periodically evaluate the efficiency of and, when needed, review "
            "the SMS in accordance with procedures established by the company.\n\n"
            "12.3 The audits and possible corrective actions shall be carried out in accordance with "
            "documented procedures.\n\n"
            "12.4 Personnel carrying out audits shall be independent of the areas being audited, unless "
            "this is impracticable due to the size and the nature of the company.\n\n"
            "12.5 The results of the audits and reviews shall be brought to the attention of all "
            "personnel having responsibility in the area involved.\n\n"
            "12.6 The management personnel responsible for the area involved shall take timely "
            "corrective action on deficiencies found."
        ),
        "source_reference": "ISM Code 2018 — Chapter 12, Company Verification, Review and Evaluation",
        "source": "ISM Code 2018",
    },
    # ── MARPOL Annex I ────────────────────────────────────────────────────
    {
        "id": "marpol-a1-ows",
        "document": "MARPOL-Annex-I.pdf",
        "section": "Annex I — OWS, OCM and bilge water discharge",
        "page": "14-15",
        "topic": "MARPOL Annex I OWS oily water separator oil content monitor OCM 15ppm bilge water discharge machinery space",
        "keywords": [
            "MARPOL", "Annex I", "OWS", "oily water separator", "OCM", "oil content monitor",
            "15ppm", "bilge water", "machinery space", "discharge", "special area",
        ],
        "content": (
            "MARPOL Annex I — Oily-water Separating Equipment and Bilge Water Discharge "
            "(Regulations 14–15).\n\n"
            "Regulation 14 — Oily-water separating and oil filtering equipment:\n"
            "Ships of 400 GT and above shall be fitted with oily-water separating equipment (OWS) "
            "capable of producing effluent with oil content not exceeding 15 parts per million (ppm). "
            "An Oil Content Monitor (OCM/OMD) must be fitted to automatically stop overboard discharge "
            "when the oil content exceeds 15 ppm and activate an alarm.\n\n"
            "Regulation 15 — Control of discharge of oil:\n"
            "Discharge of bilge water from machinery spaces is ONLY permitted when ALL of the "
            "following conditions are met:\n"
            "• The ship is proceeding en route (not at anchor or in port);\n"
            "• The oil content of the effluent does not exceed 15 ppm;\n"
            "• The ship has in operation an OWS and an oil content meter fitted with a recording device;\n"
            "• The ship is not within a special area.\n\n"
            "No discharge is permitted whatsoever in special areas (Mediterranean, Baltic, Black Sea, "
            "Red Sea, Gulfs Area, Gulf of Aden, Antarctic, North West European Waters, Oman Sea, "
            "South South African Waters)."
        ),
        "source_reference": "MARPOL Annex I — Regulations 14–15, OWS and Bilge Water",
        "source": "MARPOL Annex I",
    },
    {
        "id": "marpol-a1-orb",
        "document": "MARPOL-Annex-I.pdf",
        "section": "Annex I — Oil Record Book",
        "page": "20-21",
        "topic": "MARPOL Annex I Oil Record Book ORB entries records machinery space 3 years retention",
        "keywords": [
            "MARPOL", "Annex I", "ORB", "oil record book", "entries", "records",
            "bilge operations", "3 years", "master signature", "retention",
        ],
        "content": (
            "MARPOL Annex I — Oil Record Book Part I (Machinery Space Operations) — Regulation 17.\n\n"
            "Every ship of 400 GT and above must maintain an Oil Record Book (ORB) Part I.\n\n"
            "Entries must be made for:\n"
            "• Ballasting or cleaning of fuel oil tanks;\n"
            "• Discharge of dirty ballast or cleaning water from fuel oil tanks;\n"
            "• Collection, transfer or discharge of oil residues (sludge);\n"
            "• Discharge overboard or disposal of bilge water from machinery spaces (including OWS operations);\n"
            "• Transfer to reception facility;\n"
            "• Accidental or other exceptional discharges of oil;\n"
            "• OWS operational condition and any malfunctions.\n\n"
            "Each entry must be signed by the officer in charge of the operation, and each completed "
            "page must be countersigned by the master.\n\n"
            "The ORB must be kept on board the vessel and be readily available for inspection by the "
            "Administration at any time. It must be retained for a period of three years after the "
            "last entry."
        ),
        "source_reference": "MARPOL Annex I — Regulation 17, Oil Record Book",
        "source": "MARPOL Annex I",
    },
    # ── MARPOL Annex IV ───────────────────────────────────────────────────
    {
        "id": "marpol-a4",
        "document": "MARPOL-Annex-IV.pdf",
        "section": "Annex IV — Sewage",
        "page": "1",
        "topic": "MARPOL Annex IV sewage discharge holding tank treatment plant 3 nm 12 nm Baltic special area",
        "keywords": [
            "MARPOL", "Annex IV", "sewage", "discharge", "holding tank",
            "treatment plant", "3 nautical miles", "12 nautical miles", "Baltic", "special area",
        ],
        "content": (
            "MARPOL Annex IV — Prevention of Pollution by Sewage from Ships.\n\n"
            "Applies to ships of 400 GT and above, and ships certified to carry more than 15 persons.\n\n"
            "Regulation 11 — Discharge of sewage:\n"
            "Discharge of sewage into the sea is prohibited unless:\n"
            "• Ship has in operation an approved sewage treatment plant AND is more than 3 nm from "
            "nearest land (treated sewage); OR\n"
            "• Sewage is comminuted and disinfected AND the ship is more than 12 nm from nearest land; OR\n"
            "• Sewage comes from a holding tank and is discharged at moderate rate while underway "
            "AND the ship is more than 12 nm from nearest land.\n\n"
            "Special area — Baltic Sea: Passenger ships must not discharge sewage into the Baltic "
            "regardless of distance from land. They must use port reception facilities or an approved "
            "sewage treatment plant meeting the stricter Baltic standards.\n\n"
            "Required equipment: Sewage treatment plant, or comminuting and disinfecting system, "
            "or sewage holding tank with adequate capacity for all sewage while in port, at anchor, "
            "or within restricted areas."
        ),
        "source_reference": "MARPOL Annex IV — Sewage Prevention",
        "source": "MARPOL Annex IV",
    },
    # ── MARPOL Annex V ────────────────────────────────────────────────────
    {
        "id": "marpol-a5",
        "document": "MARPOL-Annex-V.pdf",
        "section": "Annex V — Garbage",
        "page": "1",
        "topic": "MARPOL Annex V garbage plastics food waste garbage management plan garbage record book discharge prohibited",
        "keywords": [
            "MARPOL", "Annex V", "garbage", "plastics", "food waste",
            "garbage management plan", "garbage record book", "discharge prohibited",
            "3 nm", "12 nm", "special area", "100 GT",
        ],
        "content": (
            "MARPOL Annex V — Prevention of Pollution by Garbage from Ships.\n\n"
            "Regulation 3 — Discharge of garbage outside special areas:\n"
            "ALL plastics — including synthetic ropes, fishing gear, plastic bags and incinerator ashes "
            "from plastic products — may NEVER be discharged into the sea.\n\n"
            "Food waste may be discharged when:\n"
            "• More than 3 nm from nearest land if passed through a comminuter/grinder (< 25 mm); OR\n"
            "• More than 12 nm from nearest land if not comminuted or ground.\n\n"
            "Cargo residues, cleaning agents and additives, animal carcasses: subject to specific rules.\n\n"
            "Regulation 10 — Garbage management plan and record:\n"
            "Every ship of 100 GT and above, and every ship certified to carry 15 or more persons, "
            "shall carry:\n"
            "• A Garbage Management Plan (specifying procedures for collection, storage, processing "
            "and disposal, and designating a person in charge); and\n"
            "• A Garbage Record Book (recording all discharges to sea or reception facility, "
            "and all completed incinerations).\n\n"
            "Special areas (Baltic, Black Sea, Mediterranean, Red Sea, Gulfs, North Sea, Antarctic, "
            "Wider Caribbean): In special areas only food waste may be discharged, and only when "
            "more than 12 nm from nearest land."
        ),
        "source_reference": "MARPOL Annex V — Garbage Prevention",
        "source": "MARPOL Annex V",
    },
    # ── MARPOL Annex VI ───────────────────────────────────────────────────
    {
        "id": "marpol-a6-sulphur",
        "document": "MARPOL-Annex-VI.pdf",
        "section": "Annex VI Regulation 14 — Sulphur",
        "page": "14",
        "topic": "MARPOL Annex VI sulphur SOx limit 0.5% 0.10% ECA emission control area SECA scrubber low sulphur fuel",
        "keywords": [
            "MARPOL", "Annex VI", "sulphur", "SOx", "0.5%", "0.10%",
            "ECA", "SECA", "emission control area", "scrubber", "low sulphur fuel",
            "Baltic", "North Sea", "North American ECA",
        ],
        "content": (
            "MARPOL Annex VI — Sulphur Oxide Emissions (Regulation 14).\n\n"
            "Global sulphur cap: Since 1 January 2020, the sulphur content of fuel oil used on board "
            "any ship must not exceed 0.50% m/m.\n\n"
            "Emission Control Areas (ECAs) — sulphur limit: The sulphur content of fuel oil used on "
            "board ships in an ECA must not exceed 0.10% m/m.\n\n"
            "Established SOx ECAs: Baltic Sea, North Sea, North American ECA (including US coastal "
            "waters), US Caribbean Sea ECA.\n\n"
            "Compliance options:\n"
            "• Use compliant low-sulphur fuel oil (LSFO/MGO) meeting the applicable limit;\n"
            "• Fit an approved exhaust gas cleaning system (scrubber) that achieves equivalent "
            "emission reductions — evidence of compliance must be maintained on board.\n\n"
            "Fuel changeover: When entering or leaving an ECA, a written fuel changeover procedure "
            "must be followed and the changeover (date, time, position, fuel grade, tank volumes) "
            "recorded in the logbook."
        ),
        "source_reference": "MARPOL Annex VI — Regulation 14, Sulphur Emissions",
        "source": "MARPOL Annex VI",
    },
    {
        "id": "marpol-a6-nox",
        "document": "MARPOL-Annex-VI.pdf",
        "section": "Annex VI Regulation 13 — NOx",
        "page": "13",
        "topic": "MARPOL Annex VI NOx Tier I Tier II Tier III ECA North American Baltic Norwegian",
        "keywords": [
            "MARPOL", "Annex VI", "NOx", "Tier I", "Tier II", "Tier III",
            "ECA", "North American ECA", "Baltic Sea ECA", "Norwegian Sea",
            "diesel engine", "2000", "2011", "2016",
        ],
        "content": (
            "MARPOL Annex VI — NOx Emissions from Diesel Engines (Regulation 13).\n\n"
            "Tier I: Applies to marine diesel engines installed on ships constructed on or after "
            "1 January 2000.\n\n"
            "Tier II: Applies to marine diesel engines installed on ships constructed on or after "
            "1 January 2011. Approximately 15–20% lower NOx limit than Tier I.\n\n"
            "Tier III: Applies in NOx Tier III Emission Control Areas (NECAs) for marine diesel "
            "engines installed on ships constructed on or after 1 January 2016. Approximately 80% "
            "lower NOx limit than Tier I.\n\n"
            "Established NOx ECAs (NECAs): North American ECA (including US coastal waters), "
            "US Caribbean Sea ECA, Baltic Sea ECA (from 1 January 2021), "
            "North Sea ECA (from 1 January 2021).\n\n"
            "Norwegian Sea ECA: Under MEPC.392(82), adopted 2024, the Norwegian Sea area around "
            "Svalbard is designated as a NOx Tier III ECA."
        ),
        "source_reference": "MARPOL Annex VI — Regulation 13, NOx Emissions",
        "source": "MARPOL Annex VI",
    },
    {
        "id": "marpol-a6-iapp",
        "document": "MARPOL-Annex-VI.pdf",
        "section": "Annex VI — IAPP Certificate and fuel record keeping",
        "page": "6-18",
        "topic": "MARPOL Annex VI IAPP certificate fuel oil record keeping BDN bunker delivery note sampling 3 years",
        "keywords": [
            "MARPOL", "Annex VI", "IAPP", "certificate", "fuel oil",
            "bunker delivery note", "BDN", "sample", "3 years", "400 GT",
        ],
        "content": (
            "MARPOL Annex VI — IAPP Certificate and Fuel Oil Record Keeping.\n\n"
            "IAPP Certificate (Regulation 6): Ships of 400 GT and above must hold a valid "
            "International Air Pollution Prevention (IAPP) Certificate.\n\n"
            "Bunker Delivery Note (Regulation 18): A BDN must be provided for every fuel oil "
            "delivery. The BDN must be retained on board for at least 3 years after delivery.\n\n"
            "MARPOL sample: A representative sample of fuel oil delivered must be retained on "
            "board for at least 12 months from the date of delivery.\n\n"
            "Log records: Date, time, position, and fuel grade changeover details must be recorded "
            "in the official log book on ECA entry and exit.\n\n"
            "EEDI / SEEMP (Regulations 20–22): Ships of 400 GT and above must have a Ship Energy "
            "Efficiency Management Plan (SEEMP) on board. New ships are subject to the Energy "
            "Efficiency Design Index (EEDI)."
        ),
        "source_reference": "MARPOL Annex VI — IAPP Certificate and Fuel Records",
        "source": "MARPOL Annex VI",
    },
]


def seed_if_empty() -> bool:
    """
    Write built-in seed chunks and rebuild index if no compliance data exists at the
    global compliance path. Also migrates chunks from the old data/knowledge_base/
    location if present.
    Returns True if seeding was performed.
    """
    chunks_path = _chunks_path()
    if chunks_path.exists():
        existing = load_chunks()
        if existing:
            logger.debug("compliance_ingest: data exists (%d chunks), skipping seed", len(existing))
            return False

    logger.info("compliance_ingest: seeding built-in compliance knowledge base")
    old_chunks = _migrate_old_kb()
    existing_ids = {c.get("id", "") for c in old_chunks}
    new_chunks = [c for c in _SEED_CHUNKS if c["id"] not in existing_ids]
    all_chunks = old_chunks + new_chunks
    save_chunks(all_chunks)
    build_index(all_chunks)
    logger.info(
        "compliance_ingest: seed complete — total=%d (old_kb=%d, built_in=%d)",
        len(all_chunks), len(old_chunks), len(new_chunks),
    )
    return True


# ---------------------------------------------------------------------------
# Yacht-level compliance chunks and index
# ---------------------------------------------------------------------------

def _yacht_chunks_path(yacht_id: str) -> Path:
    from storage_paths import get_yacht_compliance_chunks_path
    return get_yacht_compliance_chunks_path(yacht_id)


def _yacht_index_path(yacht_id: str) -> Path:
    from storage_paths import get_yacht_compliance_index_path
    return get_yacht_compliance_index_path(yacht_id)


def load_yacht_chunks(yacht_id: str) -> List[Dict]:
    p = _yacht_chunks_path(yacht_id)
    if not p.exists():
        return []
    chunks = []
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    chunks.append(json.loads(line))
    except Exception as exc:
        logger.warning("compliance_ingest: failed to load yacht chunks yacht=%s: %s", yacht_id, exc)
    return chunks


def save_yacht_chunks(yacht_id: str, chunks: List[Dict]) -> None:
    p = _yacht_chunks_path(yacht_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk) + "\n")


def build_yacht_index(yacht_id: str, chunks: List[Dict]) -> None:
    if not chunks:
        logger.warning("compliance_ingest: no yacht chunks — skipping index build yacht=%s", yacht_id)
        return
    from sklearn.feature_extraction.text import TfidfVectorizer
    texts = []
    for c in chunks:
        kw = " ".join(c.get("keywords") or [])
        texts.append(
            f"{c.get('section', '')} {c.get('topic', '')} {kw} {c.get('content', '')}"
        )
    vectorizer = TfidfVectorizer(
        ngram_range=(1, 2),
        max_features=10000,
        sublinear_tf=True,
        stop_words="english",
    )
    matrix = vectorizer.fit_transform(texts)
    p = _yacht_index_path(yacht_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as fh:
        pickle.dump({"vectorizer": vectorizer, "matrix": matrix, "metadata": chunks}, fh)
    logger.info(
        "compliance_ingest: yacht index built chunks=%d vocab=%d yacht=%s path=%s",
        len(chunks), len(vectorizer.vocabulary_), yacht_id, p,
    )


def rebuild_yacht_index(yacht_id: str) -> int:
    chunks = load_yacht_chunks(yacht_id)
    build_yacht_index(yacht_id, chunks)
    return len(chunks)


def list_yacht_sources(yacht_id: str) -> List[Dict]:
    """Return unique source documents in the yacht compliance knowledge base."""
    chunks = load_yacht_chunks(yacht_id)
    counts: Counter = Counter()
    for c in chunks:
        src = c.get("source") or c.get("document") or "Unknown"
        src = re.sub(r"\.pdf$", "", src, flags=re.I).strip()
        counts[src] += 1
    return [{"source": s, "chunks": n} for s, n in sorted(counts.items())]


def ingest_yacht_compliance_pdf(
    file_path: str, source_name: str, yacht_id: str, doc_type: str = "yacht_procedure"
) -> int:
    """
    Extract and chunk a yacht-specific compliance PDF.
    Stores chunks + index at DATA_DIR/yachts/<yacht_id>/compliance/.
    Returns new total chunk count.
    """
    from domain.extraction import extract_pdf_text
    text = extract_pdf_text(file_path)
    if not text.strip():
        logger.warning("compliance_ingest: no text from yacht compliance PDF file=%s", file_path)
        return 0
    filename = os.path.basename(file_path)
    new_chunks = _chunk_compliance_text(text, source_name, filename)
    for c in new_chunks:
        c["yacht_id"] = yacht_id
        c["doc_type"] = doc_type
    if not new_chunks:
        logger.warning("compliance_ingest: zero chunks from yacht compliance PDF file=%s", file_path)
        return 0
    existing = load_yacht_chunks(yacht_id)
    existing = [c for c in existing if (c.get("document") or "").lower() != filename.lower()]
    combined = existing + new_chunks
    save_yacht_chunks(yacht_id, combined)
    build_yacht_index(yacht_id, combined)
    logger.info(
        "compliance_ingest: yacht compliance ingested chunks=%d total=%d yacht=%s source=%s",
        len(new_chunks), len(combined), yacht_id, source_name,
    )
    return len(combined)


# ---------------------------------------------------------------------------
# Compliance document type classification
# ---------------------------------------------------------------------------

_SMS_KEYWORDS = frozenset([
    "safety management system",
    "safety management manual",
    "sms manual",
    "master's responsibility",
    "designated person",
    "company safety policy",
])

_PROCEDURE_KEYWORDS = frozenset([
    "garbage management plan",
    "fuel changeover procedure",
    "fuel oil changeover",
    "standing order",
    "emergency procedure",
    "environmental procedure",
    "maintenance procedure",
    "bunkering procedure",
])


def classify_compliance_doc(text: str, filename: str) -> Optional[str]:
    """
    Heuristic classifier for uploaded compliance documents.
    Returns 'yacht_sms', 'yacht_procedure', or None.
    Conservative — only classifies when confident.
    """
    fname = filename.lower()
    t = text.lower()[:8000]

    if any(kw in fname for kw in ("sms", "safety management", "safety mgmt", "safety manual")):
        return "yacht_sms"
    if any(kw in fname for kw in (
        "garbage management", "fuel changeover", "fuel oil changeover",
        "standing order", "emergency procedure", "maintenance procedure",
        "environmental procedure",
    )):
        return "yacht_procedure"

    if sum(1 for kw in _SMS_KEYWORDS if kw in t) >= 2:
        return "yacht_sms"
    if any(kw in t for kw in _PROCEDURE_KEYWORDS):
        return "yacht_procedure"

    return None


def make_compliance_doc_record(doc_type: str, source_name: str, file_path: str) -> dict:
    """Build a minimal doc_record for a yacht SMS or procedure document."""
    import hashlib
    fp = hashlib.md5(file_path.encode()).hexdigest()
    return {
        "document_id": str(uuid.uuid4()),
        "file_path": file_path,
        "doc_type": doc_type,
        "source_name": source_name,
        "supplier_name": "",
        "document_number": "",
        "reference_number": "",
        "document_date": "",
        "currency": "",
        "total": None,
        "subtotal": None,
        "tax": None,
        "line_items": [],
        "exclusions": [],
        "assumptions": [],
        "fingerprint": fp,
        "billing_address": {},
        "delivery_address": {},
    }


def list_global_regulations() -> List[Dict]:
    """Return unique source names from the global compliance knowledge base."""
    return list_sources()


def _migrate_old_kb() -> List[Dict]:
    """Import chunks from the old data/knowledge_base/ location if they exist."""
    try:
        import config
        old_path = config.KB_CHUNKS_PATH
        if not old_path.exists():
            return []
        chunks = []
        with open(old_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    c = json.loads(line)
                    # Ensure 'source' field exists
                    if "source" not in c:
                        doc = c.get("document") or c.get("source_reference") or "Unknown"
                        c["source"] = doc.split(" page")[0].split(" p.")[0].strip()
                    chunks.append(c)
        logger.info(
            "compliance_ingest: migrated %d chunks from old KB at %s",
            len(chunks), old_path,
        )
        return chunks
    except Exception as exc:
        logger.warning("compliance_ingest: old KB migration failed: %s", exc)
        return []
