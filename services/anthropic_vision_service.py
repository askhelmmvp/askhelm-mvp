import os
import json
import base64
from typing import List
from dotenv import load_dotenv
from anthropic import Anthropic

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
            "Classify doc_type carefully:\n"
            '- Use "quote" when the document is a quotation, proposal, estimate, proforma, offer, or price offer.\n'
            '- Use "invoice" when the document is an invoice, tax invoice, final invoice, commercial invoice, or billing request.\n'
            '- If the document clearly says quote, quotation, estimate, proposal, offer, or proforma, set doc_type to "quote".\n'
            '- If the document clearly says invoice, tax invoice, final invoice, commercial invoice, or invoice number, set doc_type to "invoice".\n'
            '- If uncertain, choose the best grounded value from visible headings, labels, numbering, or wording.\n'
            "\n"
            "Return exactly this JSON schema:\n"
            "{\n"
            '  "doc_type": "quote|invoice|null",\n'
            '  "supplier_name": "string|null",\n'
            '  "document_number": "string|null",\n'
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

    raw = response.content[0].text.strip()

    if raw.startswith("```json"):
        raw = raw.removeprefix("```json").removesuffix("```").strip()
    elif raw.startswith("```"):
        raw = raw.removeprefix("```").removesuffix("```").strip()

    return json.loads(raw)


def summarise_operational_note_from_image(image_path: str) -> str:
    """
    Summarise a handwritten note, meeting note, or operational log image into
    a structured Chief Engineer brief. Returns formatted text, not JSON.
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
                "You are a Chief Engineer reviewing an operational note, meeting note, or log.\n"
                "Extract and summarise the key information into an action-focused brief.\n"
                "\n"
                "STRICT RULES:\n"
                "1. Maximum 6 KEY POINTS — prioritise by operational importance.\n"
                "2. Maximum 5 ACTIONS — immediate, practical, onboard steps only.\n"
                "3. RISKS must be specific to content in the note — no generic filler.\n"
                "4. If the note is unclear or illegible, say so in KEY POINTS.\n"
                "5. Tone: concise, Chief Engineer, practical. No padding.\n"
                "6. Do not repeat the same item in both KEY POINTS and ACTIONS.\n"
                "\n"
                "Respond in this exact format — nothing before or after:\n"
                "DECISION:\n"
                "Operational actions and risks identified\n"
                "\n"
                "KEY POINTS:\n"
                "• <point 1>\n"
                "• <point 2 — up to 6>\n"
                "\n"
                "RISKS:\n"
                "• <risk 1>\n"
                "• <risk 2>\n"
                "\n"
                "ACTIONS:\n"
                "• <action 1>\n"
                "• <action 2 — up to 5>"
            ),
        },
    ]

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": content}],
        timeout=90.0,
    )

    return response.content[0].text.strip()