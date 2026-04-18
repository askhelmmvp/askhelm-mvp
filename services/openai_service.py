import os
import json
from dotenv import load_dotenv
load_dotenv(dotenv_path=".env")
from openai import OpenAI

load_dotenv()

print("OPENAI_API_KEY loaded in openai_service:", bool(os.environ.get("OPENAI_API_KEY")))
print("OPENAI_API_KEY starts with:", (os.environ.get("OPENAI_API_KEY") or "")[:10])

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))


def llm_rephrase_why(context: str) -> str:
    try:
        response = client.responses.create(
            model="gpt-5",
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are AskHelm. Rewrite the explanation into one short, practical sentence. "
                        "Do not add facts. Keep it under 18 words."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Rewrite this explanation: {context}",
                },
            ],
        )
        text = getattr(response, "output_text", "").strip()
        return text or context
    except Exception:
        return context


def llm_interpret_budget_question(question: str) -> str:
    try:
        response = client.responses.create(
            model="gpt-5",
            input=[
                {
                    "role": "system",
                    "content": (
                        "Classify the yacht budget question into exactly one label:\n"
                        "over_budget_items\n"
                        "remaining_by_category\n"
                        "committed_by_category\n"
                        "categories_at_risk\n"
                        "biggest_budget_concern\n"
                        "overall_budget\n"
                        "unknown\n\n"
                        "Return only the label."
                    ),
                },
                {
                    "role": "user",
                    "content": question,
                },
            ],
        )
        label = getattr(response, "output_text", "").strip().lower()
        allowed = {
            "over_budget_items",
            "remaining_by_category",
            "committed_by_category",
            "categories_at_risk",
            "biggest_budget_concern",
            "overall_budget",
            "unknown",
        }
        return label if label in allowed else "unknown"
    except Exception:
        return "unknown"


def llm_interpret_general_question(question: str) -> str:
    try:
        response = client.responses.create(
            model="gpt-5",
            input=[
                {
                    "role": "system",
                    "content": (
                        "Classify the yacht operations question into exactly one label:\n"
                        "budget\npsc\nows\nfire\ngarbage\ngeneral\n\n"
                        "Return only the label."
                    ),
                },
                {
                    "role": "user",
                    "content": question,
                },
            ],
        )
        label = getattr(response, "output_text", "").strip().lower()
        allowed = {"budget", "psc", "ows", "fire", "garbage", "general"}
        return label if label in allowed else "general"
    except Exception:
        return "general"


def extract_commercial_document(text: str) -> dict:
    response = client.responses.create(
        model="gpt-5",
        input=[
            {
                "role": "system",
                "content": (
                    "You extract quote and invoice documents into strict JSON.\n"
                    "Return JSON only.\n"
                    "Do not invent values.\n"
                    "Use null where unknown.\n"
                    "Schema:\n"
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
                    "}\n"
                ),
            },
            {
                "role": "user",
                "content": text[:120000],
            },
        ],
    )
    return json.loads(response.output_text)