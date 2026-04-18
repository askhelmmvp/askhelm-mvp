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
    )

    raw = response.content[0].text.strip()

    if raw.startswith("```json"):
        raw = raw.removeprefix("```json").removesuffix("```").strip()
    elif raw.startswith("```"):
        raw = raw.removeprefix("```").removesuffix("```").strip()

    return json.loads(raw)