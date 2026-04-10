import re
from pathlib import Path


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
        "compliance risk", "compliance risks", "risk", "risks",
        "documentation", "document", "record", "records", "log", "logs",
        "exposed", "exposure"
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

    defaults = [
        "Check record keeping across all compliance books",
        "Verify overdue maintenance or testing items",
        "Brief crew on key compliance procedures",
    ]

    for item in defaults:
        if len(top_actions) >= 3:
            break
        if item not in top_actions:
            top_actions.append(item)

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


def parse_ows_conditions(question: str) -> dict:
    q = question.lower()

    ppm_match = re.search(r"ppm[^0-9]*([0-9]+(?:\.[0-9]+)?)|([0-9]+(?:\.[0-9]+)?)\s*ppm", q)
    ppm = None
    if ppm_match:
        ppm = ppm_match.group(1) or ppm_match.group(2)
        ppm = float(ppm)

    in_special_area = None
    if "not in a special area" in q or "outside special area" in q or "outside the special area" in q:
        in_special_area = False
    elif "in a special area" in q or "inside a special area" in q or "special area" in q:
        in_special_area = True

    en_route = None
    if "en route" in q:
        en_route = True
    elif "not en route" in q:
        en_route = False

        ocm_stable = None
    if (
        "ocm stable" in q
        or "stable ocm" in q
        or "ocm is stable" in q
        or "readings stable" in q
        or "readings are stable" in q
    ):
        ocm_stable = True
    elif (
        "ocm unstable" in q
        or "unstable ocm" in q
        or "ocm is unstable" in q
        or "readings unstable" in q
        or "readings are unstable" in q
        or "ppm fluctuating" in q
        or "ppm is fluctuating" in q
    ):
        ocm_stable = False

    ows_operational = None
    if "ows operational" in q or "ows working" in q or "equipment operational" in q:
        ows_operational = True
    elif "ows not operational" in q or "ows faulty" in q or "ows failed" in q:
        ows_operational = False

    return {
        "ppm": ppm,
        "in_special_area": in_special_area,
        "en_route": en_route,
        "ocm_stable": ocm_stable,
        "ows_operational": ows_operational,
    }


def build_ows_response(question: str) -> str:
    c = parse_ows_conditions(question)

    if c["in_special_area"] is True:
        return """DECISION: NOT PERMITTED

WHY: Discharge in a special area is not allowed unless strict additional conditions are confirmed.

ACTIONS:
- Do not discharge
- Hold in tank until outside the special area
- Verify position and area boundary before any operation"""

    if c["ppm"] is not None and c["ppm"] >= 15:
        return """DECISION: NOT PERMITTED

WHY: Oily water discharge is only allowed when oil content is below 15 ppm.

ACTIONS:
- Stop discharge
- Investigate OWS/OCM performance
- Hold and recheck before any overboard operation"""

    if c["ocm_stable"] is False:
        return """DECISION: NOT PERMITTED

WHY: Unstable OCM readings make the discharge condition unreliable.

ACTIONS:
- Stop discharge immediately
- Check OCM condition and calibration
- Do not resume until readings are stable"""

    if c["ows_operational"] is False:
        return """DECISION: NOT PERMITTED

WHY: Discharge is not allowed if the OWS is not operational.

ACTIONS:
- Do not discharge
- Repair or troubleshoot the OWS
- Retain bilge until compliant operation is restored"""

    required = [c["ppm"], c["in_special_area"], c["en_route"], c["ocm_stable"]]
    if all(value is not None for value in required):
        if c["ppm"] < 15 and c["in_special_area"] is False and c["en_route"] is True and c["ocm_stable"] is True:
            return """DECISION: PERMITTED

WHY: The stated conditions meet the basic discharge requirements in your current data.

ACTIONS:
- Verify valve lineup and equipment status
- Monitor ppm continuously during discharge
- Record the operation in the Oil Record Book"""

    missing = []
    if c["ppm"] is None:
        missing.append("ppm")
    if c["in_special_area"] is None:
        missing.append("special area status")
    if c["en_route"] is None:
        missing.append("en route status")
    if c["ocm_stable"] is None:
        missing.append("OCM stability")

    missing_text = ", ".join(missing)

    return f"""DECISION: NOT ENOUGH INFO

WHY: A discharge decision cannot be made without confirming {missing_text}.

ACTIONS:
- Confirm missing conditions before proceeding
- Verify OWS/OCM are operational and readings stable
- Hold in tank until all discharge conditions are confirmed"""


def build_fire_response() -> str:
    return """DECISION: INVESTIGATE SYSTEM

WHY: Low fire main pressure usually indicates a pump issue, blockage, air ingress, or system leak.

ACTIONS:
- Check pump operation and suction valves
- Inspect sea chest and strainers for blockage
- Check for leaks or air in the system"""


def build_garbage_response(question: str) -> str:
    q = question.lower()

    if ("5nm" in q or "5 nm" in q or "5 nautical miles" in q) and "comminuted" in q:
        return """DECISION: PERMITTED

WHY: Comminuted food waste may be discharged when more than 3 nautical miles offshore, subject to area restrictions.

ACTIONS:
- Confirm it is food waste only
- Verify vessel is outside any special area
- Record disposal in the Garbage Record Book"""

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
        return build_ows_response(question)

    if q_type == "fire":
        return build_fire_response()

    if q_type == "garbage":
        return build_garbage_response(question)

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