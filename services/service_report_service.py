import os
import re
import json
import uuid
import base64
import hashlib
import logging
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv(dotenv_path=".env")
logger = logging.getLogger(__name__)
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

# Heading-level keywords — one match is enough to confirm a service report.
_SERVICE_REPORT_HEADING_KW = frozenset([
    "service report", "field service report", "attendance report",
    "work report", "commissioning report", "inspection report",
    "maintenance report", "field report", "technical report",
    "attendance record", "job report", "engineer report",
])

# Body-level keywords — two or more required when no heading keyword matched.
_SERVICE_REPORT_BODY_KW = frozenset([
    "work carried out", "technician", "service engineer",
    "findings", "recommendations", "defects found",
    "parts replaced", "parts fitted", "hours on site",
    "job description", "fault description", "root cause",
    "corrective action", "preventive action",
    "attendance", "commissioning", "scope of work",
])


def is_service_report_text(text: str) -> bool:
    """True when raw PDF text contains enough signals to be a service/field report."""
    t = text.lower()
    if any(kw in t for kw in _SERVICE_REPORT_HEADING_KW):
        return True
    return sum(1 for kw in _SERVICE_REPORT_BODY_KW if kw in t) >= 2


# ---------------------------------------------------------------------------
# Extraction prompt
# ---------------------------------------------------------------------------

_EXTRACT_PROMPT = """\
You are extracting structured data from a marine service report.

Return ONLY valid JSON. No commentary, no markdown fencing.

{
  "supplier": "string|null",
  "vessel": "string|null",
  "date": "string|null",
  "system": "string|null",
  "equipment": "string|null",
  "make_model": "string|null",
  "technician": "string|null",
  "work_carried_out": ["string"],
  "findings": ["string"],
  "defects_issues": ["string"],
  "parts_fitted": ["string"],
  "recommendations": ["string"],
  "open_actions": ["string"],
  "next_service": "string|null"
}

Rules:
- Use null for unknown fields. Do not invent values.
- system: marine system worked on (e.g. "OWS", "Main Engine", "Generator", "Watermaker", "HVAC", "Fire Detection")
- equipment: specific equipment name (e.g. "Facet OWS", "Caterpillar C18", "Spectra Newport 400")
- work_carried_out: list every task actually performed
- findings: what was observed, measured, or noted during the work
- defects_issues: specific faults, failures, or problems identified
- parts_fitted: parts actually installed or replaced during the visit
- recommendations: advice or suggestions from the technician
- open_actions: tasks NOT yet done — needing follow-up by the vessel team
- next_service: when the next service is due, if mentioned
"""


def _parse_json_response(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("service_report: JSON parse failed: %s — raw=%r", exc, raw[:200])
        return {}


# ---------------------------------------------------------------------------
# Extraction from text (PDF path)
# ---------------------------------------------------------------------------

def extract_service_report_from_text(text: str) -> dict:
    """Extract structured service report data from plain text."""
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1200,
            system=_EXTRACT_PROMPT,
            messages=[{"role": "user", "content": text[:10000]}],
            timeout=90.0,
        )
        result = _parse_json_response(response.content[0].text)
        logger.info(
            "service_report text extraction: supplier=%r system=%r open_actions=%d",
            result.get("supplier"), result.get("system"), len(result.get("open_actions") or []),
        )
        return result
    except Exception as exc:
        logger.exception("service_report text extraction failed: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Extraction from images (image / scanned PDF path)
# ---------------------------------------------------------------------------

def extract_service_report_from_images(image_paths: list) -> dict:
    """Extract structured service report data from image files."""
    content = []
    for path in image_paths[:3]:
        media_type = "image/png" if path.lower().endswith(".png") else "image/jpeg"
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        })
    content.append({
        "type": "text",
        "text": "Extract structured data from this service report. Return only the JSON object.",
    })
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1200,
            system=_EXTRACT_PROMPT,
            messages=[{"role": "user", "content": content}],
            timeout=90.0,
        )
        result = _parse_json_response(response.content[0].text)
        logger.info(
            "service_report image extraction: supplier=%r system=%r open_actions=%d",
            result.get("supplier"), result.get("system"), len(result.get("open_actions") or []),
        )
        return result
    except Exception as exc:
        logger.exception("service_report image extraction failed: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Handover note builder (no extra API call — constructed from extracted fields)
# ---------------------------------------------------------------------------

def build_handover_note(report: dict) -> str:
    """Build a concise engineering handover note from the extracted service report dict."""
    lines = []

    date = (report.get("date") or "").strip()
    supplier = (report.get("supplier") or "").strip()
    equipment = (report.get("equipment") or "").strip()
    make_model = (report.get("make_model") or "").strip()
    vessel = (report.get("vessel") or "").strip()
    next_service = (report.get("next_service") or "").strip()

    header_parts = []
    if date:
        header_parts.append(date)
    if supplier:
        header_parts.append(supplier)
    if equipment:
        header_parts.append(equipment)
    elif report.get("system"):
        header_parts.append(report["system"])
    if make_model and make_model.lower() not in equipment.lower():
        header_parts.append(f"({make_model})")
    if vessel:
        header_parts.append(f"— {vessel}")
    if header_parts:
        lines.append(" | ".join(header_parts))

    def _section(items, heading, limit=5):
        if not items:
            return
        lines.append(f"\n{heading}:")
        for item in items[:limit]:
            lines.append(f"• {item.strip()}")

    _section(report.get("work_carried_out") or [], "WORK CARRIED OUT")
    _section(report.get("findings") or [], "FINDINGS")
    _section(report.get("defects_issues") or [], "DEFECTS / ISSUES")
    _section(report.get("parts_fitted") or [], "PARTS FITTED")
    _section(report.get("recommendations") or [], "RECOMMENDATIONS")

    if next_service:
        lines.append(f"\nNEXT SERVICE: {next_service}")

    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# WhatsApp response formatter
# ---------------------------------------------------------------------------

def format_whatsapp_response(report: dict, handover_note: str) -> str:
    """Format the WhatsApp message sent after a service report is saved."""
    supplier = (report.get("supplier") or "Unknown supplier").strip()
    system = (report.get("system") or report.get("equipment") or "the system").strip()
    equipment = (report.get("equipment") or system).strip()

    open_actions = report.get("open_actions") or []
    if open_actions:
        action_lines = "\n".join(f"• {a.strip()}" for a in open_actions[:6])
    else:
        action_lines = "• No open actions identified"

    return (
        f"DECISION:\nSERVICE REPORT SUMMARISED\n\n"
        f"WHY:\n{supplier} report for {system} has been converted into a service summary.\n\n"
        f"HANDOVER NOTE:\n{handover_note}\n\n"
        f"OPEN ACTIONS:\n{action_lines}\n\n"
        f"RECOMMENDED ACTIONS:\n"
        f"• File report under {equipment} service records\n"
        f'• Reply "add to handover notes" if this should be included in handover'
    )


# ---------------------------------------------------------------------------
# Doc record builder (keeps whatsapp_app.py free of uuid/hashlib imports)
# ---------------------------------------------------------------------------

def make_service_report_doc_record(report: dict, file_path: str) -> dict:
    """Build a doc_record dict for a service report, compatible with _dispatch_doc_record."""
    fp = hashlib.md5(file_path.encode()).hexdigest()
    return {
        "document_id": str(uuid.uuid4()),
        "file_path": file_path,
        "doc_type": "service_report",
        "supplier_name": (report.get("supplier") or "").strip(),
        "document_number": "",
        "reference_number": "",
        "document_date": (report.get("date") or "").strip(),
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
        "status": "new",
        "service_report_data": report,
    }
