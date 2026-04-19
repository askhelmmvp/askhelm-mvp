import os
import logging
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv(dotenv_path=".env")

logger = logging.getLogger(__name__)
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

_SYSTEM_PROMPT = """\
You are a Chief Engineer with extensive experience in yacht procurement and marine parts pricing.
A crew member or owner has asked whether a price is fair.

Your job:
1. Identify the item or service from their question.
2. If a price was stated, classify it as: Below market / Within expected range / Above market / Unclear.
3. Provide a realistic market price range based on typical marine industry rates.
4. Note the key variables that affect the price (OEM vs aftermarket, brand, urgency, location, yacht size).
5. Give practical actions.

STRICT RULES:
- Be specific with numbers where you can.
- If the item is too vague to price, say so and ask for clarification.
- If no price was given, provide an estimated range only — do not classify.
- Always acknowledge variability: marine pricing is highly dependent on brand, urgency, and availability.
- Tone: cautious but useful. Chief Engineer style. No padding.

Respond in this EXACT format — nothing before or after:
DECISION:
<one of: Below market / Within expected range / Above market / Unclear>
If no price was given, write: Estimated range only — no price to assess

WHY:
<one or two sentences: market range and main variables>

ACTIONS:
• <action 1>
• <action 2>
• <action 3>
• <action 4>
"""


def check_market_price(query: str) -> str:
    """
    Assess whether a quoted price is fair for a marine part or service.
    Accepts a natural language query containing item, optional price, optional currency.
    Returns a formatted DECISION / WHY / ACTIONS response.
    """
    logger.info("Market check: query=%r", query[:120])
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": query}],
            timeout=60.0,
        )
        result = response.content[0].text.strip()
        logger.info("Market check: response_length=%d", len(result))
        return result
    except Exception as exc:
        logger.exception("Market check failed: %s", exc)
        return (
            "DECISION:\nUnclear\n\n"
            "WHY:\nMarket price lookup is temporarily unavailable.\n\n"
            "ACTIONS:\n"
            "• Request an itemised breakdown from the supplier\n"
            "• Get at least two additional quotes\n"
            "• Check OEM vs aftermarket pricing online\n"
            "• Ask the supplier for urgency or volume discount"
        )
