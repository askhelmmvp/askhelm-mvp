import os
import json
from typing import List, Dict, Any
from dotenv import load_dotenv
from anthropic import Anthropic

NOT_COVERED_FALLBACK = (
    "DECISION: Not explicitly covered in the loaded documents.\n"
    "WHY: This question is not answered in the loaded compliance sources.\n"
    "SOURCE: No matching loaded source\n"
    "ACTIONS: • Refer to the relevant regulation or onboard procedure if this needs to be confirmed"
)

load_dotenv(dotenv_path=".env")

print("ANTHROPIC_API_KEY loaded:", bool(os.environ.get("ANTHROPIC_API_KEY")))

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


def extract_commercial_document_with_claude(text: str) -> dict:
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=3000,
        system=(
            "You extract yacht commercial documents into strict JSON.\n"
            "Return JSON only.\n"
            "Do not add commentary.\n"
            "Do not invent values.\n"
            "Use null where unknown.\n"
            "\n"
            "Classify doc_type carefully:\n"
            '- Use "quote" when the document is a quotation, proposal, estimate, proforma, offer, or price offer.\n'
            '- Use "invoice" when the document is an invoice, tax invoice, final invoice, commercial invoice, or billing request.\n'
            '- If the document clearly says quote, quotation, estimate, proposal, offer, or proforma, set doc_type to "quote".\n'
            '- If the document clearly says invoice, tax invoice, final invoice, commercial invoice, or invoice number, set doc_type to "invoice".\n'
            '- If uncertain, choose the best grounded value from visible headings, labels, numbering, or wording.\n'
            "\n"
            "Extraction rules:\n"
            "- Prefer values that appear in headers, totals sections, or line-item tables.\n"
            "- Keep supplier_name exactly as shown where possible.\n"
            "- Extract currency from symbols or currency labels.\n"
            "- Extract obvious totals even if line items are incomplete.\n"
            "- Prefer partial grounded extraction over empty fields.\n"
            "\n"
            "Return exactly this JSON schema:\n"
            "Extract billing_address from the document's bill-to / customer section.\n"
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
        messages=[
            {
                "role": "user",
                "content": (
                    "Read this commercial document and extract the JSON.\n"
                    "Pay special attention to whether it is a quote or an invoice.\n\n"
                    + text[:120000]
                ),
            }
        ],
    )

    raw = response.content[0].text.strip()

    if raw.startswith("```json"):
        raw = raw.removeprefix("```json").removesuffix("```").strip()
    elif raw.startswith("```"):
        raw = raw.removeprefix("```").removesuffix("```").strip()

    return json.loads(raw)


def answer_compliance_question(question: str, chunks: List[Dict[str, Any]]) -> str:
    if not chunks:
        return NOT_COVERED_FALLBACK

    context_blocks = []
    for c in chunks:
        context_blocks.append(
            f"[{c.get('source_reference', 'unknown')}]\n{c.get('content', '')}"
        )
    context = "\n\n---\n\n".join(context_blocks)

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        system=(
            "You are answering maritime compliance questions using ONLY the regulation excerpts provided.\n"
            "\n"
            "STRICT RULES — NO EXCEPTIONS:\n"
            "1. Use ONLY the text in the provided excerpts. Nothing else.\n"
            "2. Do NOT use training knowledge about maritime law, IMO conventions, or any external regulations.\n"
            "3. Do NOT mention any convention, regulation, requirement, or authority not stated in the excerpts.\n"
            "4. Do NOT supplement, extend, or add information beyond what is explicitly in the excerpts.\n"
            "5. If the excerpts do not directly answer the question, copy the FALLBACK below verbatim — nothing else.\n"
            "\n"
            "FALLBACK — copy this exactly if the excerpts do not answer the question:\n"
            "DECISION: Not explicitly covered in the loaded documents.\n"
            "WHY: This question is not answered in the loaded compliance sources.\n"
            "SOURCE: No matching loaded source\n"
            "ACTIONS: • Refer to the relevant regulation or onboard procedure if this needs to be confirmed\n"
            "\n"
            "If the excerpts DO answer the question, respond in this exact format:\n"
            "DECISION: <one sentence — yes/no/conditional, grounded in excerpts only>\n"
            "WHY: <one or two sentences — from the excerpts only, no external knowledge>\n"
            "SOURCE: <regulation name, section, and page from the excerpt header>\n"
            "ACTIONS: <bullet list of what the crew/owner must do, from excerpts only>"
        ),
        messages=[
            {
                "role": "user",
                "content": (
                    "Regulation excerpts:\n\n"
                    + context
                    + "\n\n---\n\nQuestion: "
                    + question
                ),
            }
        ],
    )

    return response.content[0].text.strip()


def answer_compliance_followup_question(topic: str, chunks: List[Dict[str, Any]]) -> str:
    """Action-focused follow-up to a compliance answer — no definition, no SOURCE, max 4 bullets."""
    if not chunks:
        return NOT_COVERED_FALLBACK

    context_blocks = []
    for c in chunks:
        context_blocks.append(
            f"[{c.get('source_reference', 'unknown')}]\n{c.get('content', '')}"
        )
    context = "\n\n---\n\n".join(context_blocks)

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        system=(
            "You are a Chief Engineer giving an action brief to the crew.\n"
            "The crew already knows the regulation and the decision. They want to know what to DO.\n"
            "\n"
            "STRICT RULES — NO EXCEPTIONS:\n"
            "1. Use ONLY the provided regulation excerpts. No external knowledge.\n"
            "2. Do NOT repeat or re-explain the regulation definition.\n"
            "3. Do NOT repeat the compliance decision they already received.\n"
            "4. Do NOT include a SOURCE line.\n"
            "5. Maximum 4 bullet points in ACTIONS — immediate, onboard actions only.\n"
            "6. If the excerpts give no actionable guidance, say so in DECISION and stop.\n"
            "\n"
            "Respond in this exact format — nothing before, nothing after:\n"
            "DECISION: <short action statement — what needs to happen next>\n"
            "\n"
            "WHY:\n"
            "<one line — practical reason, not a definition>\n"
            "\n"
            "ACTIONS:\n"
            "• <action 1>\n"
            "• <action 2>\n"
            "• <action 3>\n"
            "• <action 4 — only if needed>"
        ),
        messages=[
            {
                "role": "user",
                "content": (
                    "Regulation excerpts:\n\n"
                    + context
                    + "\n\n---\n\n"
                    + f"The crew has just received a compliance answer about: {topic}\n\n"
                    + "What should they do now? "
                    + "Give immediate, practical, onboard actions only. "
                    + "Do not re-explain the regulation."
                ),
            }
        ],
    )

    return response.content[0].text.strip()