import os
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise ValueError("OPENAI_API_KEY not found in .env")

client = OpenAI(api_key=api_key)

DATA_DIR = Path("data")


def load_text_file(filename: str) -> str:
    path = DATA_DIR / filename
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def classify_question(question: str) -> str:
    q = question.lower()

    if any(term in q for term in [
        "psc", "inspection", "weakness", "deficiency",
        "compliance risk", "compliance risks", "risk", "risks"
    ]):
        return "psc"

    if any(term in q for term in [
        "discharge", "ppm", "oily water", "ows", "bilge", "special area"
    ]):
        return "ows"

    if any(term in q for term in [
        "fire", "pressure", "pump", "hydrant", "fire main"
    ]):
        return "fire"

    if any(term in q for term in [
        "garbage", "food waste", "plastics", "waste"
    ]):
        return "garbage"

    return "general"


def get_note_lines() -> list[str]:
    notes = load_text_file("notes.txt")
    return [line.strip("- ").strip() for line in notes.splitlines() if line.strip()]


def extract_psc_risks(note_lines: list[str]) -> dict:
    themes = {
        "records": [],
        "calibration": [],
        "testing": [],
        "crew_understanding": [],
        "garbage": [],
    }

    for line in note_lines:
        lower = line.lower()

        if any(word in lower for word in ["record", "log", "documentation"]):
            themes["records"].append(line)

        if any(word in lower for word in ["calibration", "overdue", "ocm"]):
            themes["calibration"].append(line)

        if any(word in lower for word in ["test", "tested", "fire pump", "emergency fire pump"]):
            themes["testing"].append(line)

        if any(word in lower for word in ["crew", "unclear", "confusion", "en route", "special area"]):
            themes["crew_understanding"].append(line)

        if any(word in lower for word in ["garbage", "segregation", "waste"]):
            themes["garbage"].append(line)

    return themes


def build_psc_response() -> str:
    note_lines = get_note_lines()
    themes = extract_psc_risks(note_lines)

    risk_count = sum(1 for v in themes.values() if v)

    if risk_count == 0:
        return """DECISION: NOT ENOUGH INFO

WHY: No clear PSC-related weaknesses were found in notes.

ACTIONS:
- Add vessel-specific inspection notes
- Record recurring documentation issues
- Reassess once more operational detail is available"""

    top_actions = []

    if themes["records"] or themes["garbage"]:
        top_actions.append("Check ORB, Garbage Record Book, and fuel changeover logs")

    if themes["calibration"]:
        top_actions.append("Verify OCM calibration status and any overdue items")

    if themes["testing"]:
        top_actions.append("Review fire pump and emergency fire pump test records")

    if themes["crew_understanding"]:
        top_actions.append("Brief crew on special areas, en route status, and procedures")

    if len(top_actions) < 3:
        defaults = [
            "Check record keeping across all compliance books",
            "Verify overdue maintenance or testing items",
            "Brief crew on key compliance procedures",
        ]
        for item in defaults:
            if item not in top_actions:
                top_actions.append(item)
            if len(top_actions) == 3:
                break

    why_parts = []
    if themes["records"] or themes["garbage"]:
        why_parts.append("record keeping")
    if themes["calibration"]:
        why_parts.append("calibration")
    if themes["testing"]:
        why_parts.append("testing")
    if themes["crew_understanding"]:
        why_parts.append("crew understanding")

    why_text = ", ".join(why_parts) if why_parts else "inspection readiness"

    return f"""DECISION: HIGH RISK AREAS IDENTIFIED

WHY: Current notes show repeated weaknesses in {why_text}.

ACTIONS:
- {top_actions[0]}
- {top_actions[1]}
- {top_actions[2]}"""


def build_ows_response() -> str:
    return """DECISION: NOT ENOUGH INFO

WHY: Oily water discharge depends on location, ppm, vessel status, and equipment condition.

ACTIONS:
- Confirm vessel is outside special areas
- Verify OCM/OWS are operational
- Check vessel is en route before discharging"""


def build_fire_response() -> str:
    return """DECISION: INVESTIGATE SYSTEM

WHY: Low fire main pressure usually indicates a pump issue, blockage, air ingress, or system leak.

ACTIONS:
- Check pump operation and suction valves
- Inspect sea chest and strainers for blockage
- Check for leaks or air in the system"""


def build_garbage_response() -> str:
    return """DECISION: NOT ENOUGH INFO

WHY: Garbage discharge depends on waste type, distance offshore, and area restrictions.

ACTIONS:
- Confirm the waste category
- Verify vessel position and area status
- Check Garbage Record Book requirements"""


def ask_askhelm(question: str) -> str:
    q_type = classify_question(question)

    if q_type == "psc":
        return build_psc_response()

    if q_type == "ows":
        return build_ows_response()

    if q_type == "fire":
        return build_fire_response()

    if q_type == "garbage":
        return build_garbage_response()

    return """DECISION: NOT ENOUGH INFO

WHY: This question does not yet match a defined operational category.

ACTIONS:
- Rephrase with more detail
- Add the relevant rule or procedure to the data files
- Reassess once more context is available"""


def main() -> None:
    print("AskHelm Regulation MVP")
    print("Type 'exit' to quit.\n")

    while True:
        question = input("AskHelm > ").strip()

        if question.lower() == "exit":
            print("Exiting AskHelm.")
            break

        if not question:
            continue

        try:
            answer = ask_askhelm(question)
            print(f"\n{answer}\n")
        except Exception as e:
            print(f"\nError: {e}\n")


if __name__ == "__main__":
    main()