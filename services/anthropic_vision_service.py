import os
import json
import base64
from typing import List
from dotenv import load_dotenv
from anthropic import Anthropic
from services.llm_usage_logger import log_llm_call

load_dotenv(dotenv_path=".env")

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


def _image_to_base64(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def extract_commercial_document_from_images(image_paths: List[str]) -> dict:
    content = []

    for image_path in image_paths[:3]:
        media_type = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": _image_to_base64(image_path),
            },
        })

    content.append({
        "type": "text",
        "text": (
            "Read these commercial document images and extract strict JSON only.\n"
            "Pay special attention to whether the document is a quote or an invoice.\n"
            "Use headings, labels, totals, numbering, and wording to decide.\n"
            "Do not add commentary.\n"
            "Do not invent values.\n"
            "Use null where unknown.\n"
            "Prefer partial grounded extraction over empty fields.\n"
            "\n"
            "Classify doc_type carefully. Priority order:\n"
            '- Use "proforma" when the document heading says "proforma invoice", "proforma", "pro forma", or "pro-forma". This overrides "quote".\n'
            '- Use "invoice" when the document is an invoice, tax invoice, final invoice, commercial invoice, or billing request (and is NOT a proforma).\n'
            '- Use "quote" when the document is a quotation, proposal, estimate, offer, or price offer (and is NOT a proforma or invoice).\n'
            '- Use "service_report" ONLY when: a supplier/contractor attended; a technician or service engineer is named; specific equipment was serviced; work carried out is listed; findings or recommendations are from a service provider. NOT a quote or invoice from a service company.\n'
            '- Use "operational_notes" when the document is: a handwritten daily note, meeting note, yard meeting note, coordination note, task list, mixed open actions, operational planning, schedule, or any note that is NOT clearly a supplier service report.\n'
            '- Use "technical_note" when the document is: a defect note, technical observation, inspection note, photo caption, or note about a single issue or system — not a commercial document and not a service report.\n'
            '- Use "equipment_list" when the document is an equipment list, machinery list, asset register, or installed equipment inventory.\n'
            '- Use "stock_inventory" when the document is a stock list, inventory, stores list, or consumables list.\n'
            '- Use "spare_parts_inventory" when the document is a spare parts list, spares inventory, or parts inventory.\n'
            '- If uncertain, choose the best grounded value from visible headings, labels, numbering, or wording.\n'
            "- Extract reference_number from 'ref:', 'your ref:', 'our ref:', 'customer ref:', 'quote ref:', or 'order ref:' fields — on a proforma this is typically the original quote number.\n"
            "\n"
            "Return exactly this JSON schema:\n"
            "{\n"
            '  "doc_type": "quote|invoice|proforma|service_report|operational_notes|technical_note|equipment_list|stock_inventory|spare_parts_inventory|null",\n'
            '  "supplier_name": "string|null",\n'
            '  "document_number": "string|null",\n'
            '  "reference_number": "string|null",\n'
            '  "document_date": "string|null",\n'
            '  "currency": "string|null",\n'
            '  "subtotal": "number|null",\n'
            '  "tax": "number|null",\n'
            '  "total": "number|null",\n'
            '  "exclusions": ["string"],\n'
            '  "assumptions": ["string"],\n'
            '  "billing_address": {\n'
            '    "entity": "string|null",\n'
            '    "address_lines": ["string"],\n'
            '    "country": "string|null",\n'
            '    "vat_number": "string|null"\n'
            '  },\n'
            '  "delivery_address": {\n'
            '    "entity": "string|null",\n'
            '    "address_lines": ["string"],\n'
            '    "country": "string|null"\n'
            '  },\n'
            '  "line_items": [\n'
            "    {\n"
            '      "description": "string",\n'
            '      "quantity": "number|null",\n'
            '      "unit": "string|null",\n'
            '      "unit_rate": "number|null",\n'
            '      "line_total": "number|null"\n'
            "    }\n"
            "  ]\n"
            "}"
        ),
    })

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=3000,
        messages=[{"role": "user", "content": content}],
        timeout=90.0,
    )
    log_llm_call("vision_extract_document", response, "claude-sonnet-4-6")

    raw = response.content[0].text.strip()

    if raw.startswith("```json"):
        raw = raw.removeprefix("```json").removesuffix("```").strip()
    elif raw.startswith("```"):
        raw = raw.removeprefix("```").removesuffix("```").strip()

    return json.loads(raw)


def summarise_operational_note_from_image(image_path: str) -> dict:
    """
    Summarise a handwritten/operational note image into a structured dict.

    Returns:
        {
            "doc_subtype": "operational_notes" | "technical_note",
            "summary": str,
            "issues": [str, ...],
            "open_actions": [str, ...],
        }
    Falls back to a plain text summary in "summary" if JSON parsing fails.
    """
    media_type = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"
    content = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": _image_to_base64(image_path),
            },
        },
        {
            "type": "text",
            "text": (
                "You are a Chief Engineer reviewing a handwritten or operational note.\n"
                "Extract and summarise the key information. Return ONLY valid JSON — no commentary.\n"
                "\n"
                "Classify doc_subtype:\n"
                '- "operational_notes": daily note, meeting note, coordination note, mixed tasks, planning, schedules\n'
                '- "technical_note": defect note, inspection note, observation about one issue or system\n'
                "\n"
                "Rules:\n"
                "- summary: 2-4 sentences, practical Chief Engineer tone, no padding\n"
                "- issues: up to 4 specific risks or concerns from the note — no generic filler\n"
                "- open_actions: up to 5 actionable tasks not yet complete\n"
                "- If the note is illegible, set summary to 'Note is unclear or illegible'\n"
                "\n"
                "Return exactly this JSON:\n"
                "{\n"
                '  "doc_subtype": "operational_notes|technical_note",\n'
                '  "summary": "string",\n'
                '  "issues": ["string"],\n'
                '  "open_actions": ["string"]\n'
                "}"
            ),
        },
    ]

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        messages=[{"role": "user", "content": content}],
        timeout=90.0,
    )
    log_llm_call("vision_operational_note", response, "claude-sonnet-4-6")

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0].strip()

    try:
        result = json.loads(raw)
        return {
            "doc_subtype": result.get("doc_subtype") or "operational_notes",
            "summary": result.get("summary") or "",
            "issues": result.get("issues") or [],
            "open_actions": result.get("open_actions") or [],
        }
    except (json.JSONDecodeError, Exception):
        return {
            "doc_subtype": "operational_notes",
            "summary": raw,
            "issues": [],
            "open_actions": [],
        }