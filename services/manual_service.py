"""
Technical manual detection, extraction, and response formatting.
"""
import os
import re
import json
import uuid
import base64
import hashlib
import logging
from typing import Optional
from dotenv import load_dotenv
from anthropic import Anthropic
from services.llm_usage_logger import log_llm_call

load_dotenv(dotenv_path=".env")
logger = logging.getLogger(__name__)
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

_MANUAL_HEADING_KW = frozenset([
    "owner's manual", "owners manual", "owner manual",
    "operator's manual", "operator manual", "operators manual",
    "installation manual", "installation guide",
    "service manual", "technical manual",
    "instruction manual", "instructions manual",
    "maintenance manual",
    "user manual", "user's manual", "user guide",
    "product manual", "operating manual", "operating guide",
    "operation manual", "operation guide",
    "reference manual",
])

_MANUAL_BODY_KW = frozenset([
    "table of contents",
    "safety instructions", "safety warnings",
    "troubleshooting",
    "chapter",
    "appendix",
    "wiring diagram", "wiring schematic",
    "parts diagram", "exploded view",
    "maintenance schedule", "maintenance procedure",
    "installation procedure", "installation instructions",
    "warranty information", "limited warranty",
    "operating instructions",
    "do not",                    # safety warning language
    "read all instructions",
    "before use",
    "intended use",
])


def is_technical_manual_text(text: str) -> bool:
    """True when raw PDF text contains enough signals to be a technical manual."""
    t = text.lower()
    if any(kw in t for kw in _MANUAL_HEADING_KW):
        return True
    return sum(1 for kw in _MANUAL_BODY_KW if kw in t) >= 3


# ---------------------------------------------------------------------------
# Metadata extraction prompt
# ---------------------------------------------------------------------------

_EXTRACT_PROMPT = """\
You are extracting metadata from a technical document (owner's manual, service manual, installation guide, etc.).

Return ONLY valid JSON. No commentary, no markdown fencing.

{
  "manufacturer": "string|null",
  "product_name": "string|null",
  "model": "string|null",
  "document_type": "string|null",
  "system": "string|null",
  "year": "string|null",
  "key_topics": ["string"]
}

Rules:
- manufacturer: the brand or company that made the product (e.g. "Spectra", "Caterpillar", "Dometic")
- product_name: the product name (e.g. "Newport 400", "C18", "FJ3000")
- model: specific model number or name if different from product_name
- document_type: what kind of document this is (e.g. "Owner's Manual", "Service Manual", "Installation Guide")
- system: the marine system this product belongs to (e.g. "Watermaker", "Generator", "HVAC", "Navigation", "OWS", "Main Engine")
- year: publication or model year if mentioned, else null
- key_topics: list up to 8 main topic headings or sections covered (e.g. "Installation", "Maintenance", "Troubleshooting", "Specifications", "Wiring", "Parts List")
- Use null for unknown fields. Do not invent values.
"""


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("manual_service: JSON parse failed: %s — raw=%r", exc, raw[:200])
        return {}


def extract_manual_metadata_from_text(text: str, filename: str = "") -> dict:
    """Extract manual metadata from plain text using Claude."""
    try:
        user_content = f"Filename: {filename}\n\n{text[:8000]}" if filename else text[:8000]
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            system=_EXTRACT_PROMPT,
            messages=[{"role": "user", "content": user_content}],
            timeout=60.0,
        )
        log_llm_call("manual_extract_text", response, "claude-sonnet-4-6")
        result = _parse_json(response.content[0].text)
        logger.info(
            "manual_service text extraction: manufacturer=%r system=%r topics=%d",
            result.get("manufacturer"), result.get("system"), len(result.get("key_topics") or []),
        )
        return result
    except Exception as exc:
        log_llm_call("manual_extract_text", None, "claude-sonnet-4-6", error=exc)
        logger.exception("manual_service text extraction failed: %s", exc)
        return {}


def extract_manual_metadata_from_images(image_paths: list, filename: str = "") -> dict:
    """Extract manual metadata from image files using Claude vision."""
    content = []
    for path in image_paths[:2]:
        media_type = "image/png" if path.lower().endswith(".png") else "image/jpeg"
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        })
    prompt = f"Filename: {filename}\n\nExtract metadata from this technical manual." if filename else \
             "Extract metadata from this technical manual."
    content.append({"type": "text", "text": prompt})
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            system=_EXTRACT_PROMPT,
            messages=[{"role": "user", "content": content}],
            timeout=60.0,
        )
        log_llm_call("manual_extract_image", response, "claude-sonnet-4-6")
        result = _parse_json(response.content[0].text)
        logger.info(
            "manual_service image extraction: manufacturer=%r system=%r",
            result.get("manufacturer"), result.get("system"),
        )
        return result
    except Exception as exc:
        log_llm_call("manual_extract_image", None, "claude-sonnet-4-6", error=exc)
        logger.exception("manual_service image extraction failed: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

_CHUNK_SIZE = 1000     # target chars per chunk
_CHUNK_OVERLAP = 100   # overlap chars between consecutive chunks


def chunk_manual_text(text: str) -> list:
    """
    Split manual text into overlapping chunks for keyword search.
    Returns list of dicts: {"heading": str, "text": str}.
    """
    if not text or not text.strip():
        return []

    # Split on blank lines to get natural paragraphs / sections.
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]

    chunks = []
    current = []
    current_len = 0
    current_heading = ""

    for para in paragraphs:
        # Detect a heading: short line in UPPER CASE or a numbered section header.
        is_heading = (
            len(para) < 80
            and (para.isupper() or re.match(r"^\d+\.?\s+[A-Z]", para))
        )
        if is_heading:
            if current and current_len >= _CHUNK_SIZE // 2:
                chunks.append({"heading": current_heading, "text": "\n\n".join(current)})
                current = []
                current_len = 0
            current_heading = para

        current.append(para)
        current_len += len(para)

        if current_len >= _CHUNK_SIZE:
            chunks.append({"heading": current_heading, "text": "\n\n".join(current)})
            # Overlap: keep last paragraph in next chunk.
            overlap = [current[-1]] if current else []
            current = overlap
            current_len = sum(len(p) for p in current)

    if current:
        chunks.append({"heading": current_heading, "text": "\n\n".join(current)})

    logger.debug("manual_service: chunked into %d chunks", len(chunks))
    return chunks


# ---------------------------------------------------------------------------
# Doc record builder
# ---------------------------------------------------------------------------

def make_manual_doc_record(manual: dict, file_path: str, chunks: list) -> dict:
    """Build a doc_record for routing through _dispatch_doc_record."""
    fp = hashlib.md5(file_path.encode()).hexdigest()
    return {
        "document_id": str(uuid.uuid4()),
        "file_path": file_path,
        "doc_type": "technical_manual",
        "supplier_name": (manual.get("manufacturer") or "").strip(),
        "document_number": "",
        "reference_number": "",
        "document_date": (manual.get("year") or "").strip(),
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
        "manual_data": manual,
        "manual_chunks": chunks,
    }


# ---------------------------------------------------------------------------
# WhatsApp response formatter
# ---------------------------------------------------------------------------

def format_manual_import_response(manual: dict) -> str:
    """Format the WhatsApp message sent after a manual is saved."""
    manufacturer = (manual.get("manufacturer") or "").strip()
    product = (manual.get("product_name") or manual.get("model") or "").strip()
    doc_type = (manual.get("document_type") or "Technical Manual").strip()
    system = (manual.get("system") or "").strip()
    topics = manual.get("key_topics") or []

    title_parts = [p for p in [manufacturer, product] if p]
    title = " ".join(title_parts) if title_parts else "document"
    system_suffix = f" ({system})" if system else ""

    topic_line = ", ".join(topics[:6]) if topics else "General reference"

    commands = []
    if manufacturer or product:
        search_example = product or manufacturer
        commands.append(f'• Reply "search manual for [topic]" to find information')
    commands.append('• Reply "show manuals" to see all saved manuals')

    return (
        f"DECISION:\nMANUAL IMPORTED\n\n"
        f"WHY:\n{doc_type} for {title}{system_suffix} has been saved to your manual library.\n\n"
        f"DOCUMENT:\n"
        f"• Type: {doc_type}\n"
        + (f"• Manufacturer: {manufacturer}\n" if manufacturer else "")
        + (f"• Product: {product}\n" if product else "")
        + (f"• System: {system}\n" if system else "")
        + f"• Topics covered: {topic_line}\n\n"
        f"RECOMMENDED ACTIONS:\n"
        + "\n".join(commands)
    )


# ---------------------------------------------------------------------------
# Manual Q&A
# ---------------------------------------------------------------------------

_QA_SYSTEM = """\
You are a marine engineering assistant answering questions from a vessel's technical manual.
Answer concisely based ONLY on the provided manual excerpts.
If the answer is not in the excerpts, say so clearly.
Format: plain text, no markdown, max 5 sentences.
"""


def answer_manual_question(question: str, chunks: list, manual_label: str = "") -> str:
    """Answer a question using the provided manual chunks. Returns plain text answer."""
    if not chunks:
        return "No manual content available to answer this question."

    context_parts = []
    for i, chunk in enumerate(chunks[:4], 1):
        heading = chunk.get("heading") or ""
        text = chunk.get("text") or ""
        label = f"[{i}] {heading}:\n{text}" if heading else f"[{i}]\n{text}"
        context_parts.append(label)

    context = "\n\n---\n\n".join(context_parts)
    source_note = f"Manual: {manual_label}\n\n" if manual_label else ""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            system=_QA_SYSTEM,
            messages=[{
                "role": "user",
                "content": f"{source_note}Manual excerpts:\n\n{context}\n\nQuestion: {question}",
            }],
            timeout=45.0,
        )
        log_llm_call("manual_qa", response, "claude-sonnet-4-6")
        return response.content[0].text.strip()
    except Exception as exc:
        log_llm_call("manual_qa", None, "claude-sonnet-4-6", error=exc)
        logger.exception("manual_service: Q&A failed: %s", exc)
        return "Unable to answer from manual at this time."
