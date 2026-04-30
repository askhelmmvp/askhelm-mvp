"""
Operational compliance playbook for AskHelm.

Intercepts common safety-critical operational questions and returns
CE-style practical guidance BEFORE the document-backed RAG layer.

Returns None when the query does not match any topic — caller falls through
to normal RAG. Never halluccinates specific legal article numbers unless the
source document is loaded.
"""

from typing import Optional

# ---------------------------------------------------------------------------
# Query-type detection
# ---------------------------------------------------------------------------

_OVERDUE_TOKENS = frozenset({
    "overdue", "past due", "not done", "not been done", "missed", "late",
    "behind", "haven't done", "havent done", "haven't tested", "havent tested",
    "not tested", "not completed", "not checked", "not carried out",
    "is this ok", "is it ok", "should i be worried", "not been tested",
    "not been checked", "not been inspected", "not been serviced",
    "hasnt been", "hasn't been",
    "missing", "no record", "not recorded", "not up to date",
})

_FREQUENCY_TOKENS = frozenset({
    "how often", "when should", "what interval", "how frequently",
    "when is it due", "when does it need", "how regularly",
    "what is the interval", "what's the interval", "what frequency",
    "how many times", "how regular", "what is the test frequency",
})

_REGULATION_TOKENS = frozenset({
    "regulation", "regulatory", "required by law", "solas", "marpol",
    "ism code", "ism requirement", "what does the", "what code",
    "legally", "legal requirement", "flag state", "class requirement",
    "what is the regulation", "what are the regulations",
    "what regulation", "what law",
})


def _query_type(t: str) -> str:
    if any(w in t for w in _OVERDUE_TOKENS):
        return "overdue"
    if any(w in t for w in _FREQUENCY_TOKENS):
        return "frequency"
    if any(w in t for w in _REGULATION_TOKENS):
        return "regulation"
    return "general"


# ---------------------------------------------------------------------------
# Response builder
# ---------------------------------------------------------------------------

def _r(decision: str, why: str, actions: list) -> str:
    action_lines = "\n".join(f"• {a}" for a in actions)
    return f"DECISION:\n{decision}\n\nWHY:\n{why}\n\nRECOMMENDED ACTIONS:\n{action_lines}"


# ---------------------------------------------------------------------------
# Playbook entries
# ---------------------------------------------------------------------------
# Each entry: (topic_id, match_sets, responses_dict)
# match_sets: list of word-sets — ANY set where ALL words are found in query → topic matches
# responses_dict: keys = 'overdue' | 'frequency' | 'regulation' | 'general'

_PLAYBOOK = [
    # ── Fire pump ──────────────────────────────────────────────────────────
    (
        "fire_pump_test",
        [{"fire pump"}],
        {
            "overdue": _r(
                "ACTION REQUIRED — DO NOT LEAVE OVERDUE",
                "An overdue fire pump test is a safety-system and survey evidence risk, "
                "even if the exact interval depends on the vessel PMS/SMS.",
                [
                    "Complete the test at the earliest opportunity",
                    "Record the result in PMS / test log",
                    "If the pump fails or cannot be tested, raise a defect and notify Captain/management",
                ],
            ),
            "frequency": _r(
                "CHECK VESSEL PMS/SMS INTERVAL",
                "I do not have the exact onboard fire pump test interval loaded, but the test must "
                "follow the vessel's planned maintenance and safety management procedures.",
                [
                    "Check the PMS task interval for fire pump test",
                    "Confirm test evidence is recorded",
                    "Upload the PMS task or fire safety procedure so I can answer exactly next time",
                ],
            ),
            "regulation": _r(
                "SOURCE NEEDED FOR EXACT REGULATION",
                "I can give practical action guidance, but the exact regulatory reference depends on "
                "the vessel code, flag, class, and onboard safety management procedures.",
                [
                    "Upload the fire safety maintenance procedure or PMS task",
                    "Confirm flag/code/class context from vessel profile",
                    "Treat overdue fire pump tests as action-required until evidence is updated",
                ],
            ),
            "general": _r(
                "FOLLOW VESSEL PMS AND SAFETY PROCEDURES",
                "Fire pump operational readiness is safety-critical. Test records must be maintained "
                "per the vessel's PMS, safety management system, and class/flag requirements.",
                [
                    "Confirm the PMS task interval and last test date",
                    "Ensure test results are recorded in the log",
                    "Check suction, discharge pressure, and flow rate during each test",
                    "Upload the fire safety procedure if exact regulation reference is needed",
                ],
            ),
        },
    ),
    # ── Emergency fire pump ────────────────────────────────────────────────
    (
        "emergency_fire_pump",
        [{"emergency fire pump"}, {"emcy fire pump"}],
        {
            "overdue": _r(
                "ACTION REQUIRED — EMERGENCY EQUIPMENT MUST BE OPERATIONALLY READY",
                "An overdue emergency fire pump test is a critical safety gap. "
                "Emergency equipment must be maintained and regularly tested.",
                [
                    "Test the emergency fire pump immediately if safe to do so",
                    "Record the test in the safety log",
                    "If a fault is found, raise a defect, notify the Captain, and log a non-conformity",
                    "Do not sail if emergency fire-fighting capability is confirmed unavailable",
                ],
            ),
            "frequency": _r(
                "CHECK VESSEL SMS / DRILL SCHEDULE",
                "Emergency fire pump test intervals are set by the vessel's safety management system "
                "and drill schedule. I do not have the specific interval loaded.",
                [
                    "Check the SMS drill schedule and PMS task",
                    "Confirm evidence of last test is recorded",
                    "Upload the emergency fire pump procedure for an exact reference",
                ],
            ),
            "regulation": _r(
                "SOURCE NEEDED FOR EXACT REGULATION",
                "Exact regulatory reference depends on vessel code, flag state, and class. "
                "Emergency equipment testing requirements are typically covered by the vessel's safety management procedures.",
                [
                    "Upload the emergency fire pump procedure or safety drill schedule",
                    "Confirm flag state and class society for the applicable code reference",
                ],
            ),
            "general": _r(
                "FOLLOW SMS AND DRILL SCHEDULE",
                "The emergency fire pump is a critical safety system. It must be tested regularly "
                "and records must be maintained.",
                [
                    "Check the SMS drill schedule for test frequency",
                    "Test in accordance with the PMS task interval",
                    "Record all tests with date, result, and operator signature",
                ],
            ),
        },
    ),
    # ── Bilge alarm ────────────────────────────────────────────────────────
    (
        "bilge_alarm_test",
        [{"bilge alarm"}],
        {
            "overdue": _r(
                "ACTION REQUIRED — DO NOT LEAVE OVERDUE",
                "An overdue bilge alarm test is a safety and MARPOL compliance risk. "
                "Bilge alarms are a first line of defence against undetected flooding and bilge discharge.",
                [
                    "Test the bilge alarm at the earliest opportunity",
                    "Record the test in the deck/engine log and PMS",
                    "If the alarm is defective, raise a defect and notify the Captain",
                    "Do not assume the bilge system is working if the alarm has not been recently tested",
                ],
            ),
            "frequency": _r(
                "CHECK VESSEL PMS/SMS INTERVAL",
                "Bilge alarm test frequency is set by the vessel's PMS and safety management procedures. "
                "I do not have the specific interval loaded.",
                [
                    "Check the PMS task interval for bilge alarm test",
                    "Confirm the last test date is logged",
                    "Upload the bilge system maintenance procedure for an exact reference",
                ],
            ),
            "regulation": _r(
                "SOURCE NEEDED FOR EXACT REGULATION",
                "Exact regulatory reference depends on vessel code, flag state, and class. "
                "I can give practical guidance but not a specific regulation article without the source document loaded.",
                [
                    "Upload the bilge alarm maintenance procedure or applicable flag/class circular",
                    "Check the vessel's SMS for bilge alarm test requirements",
                ],
            ),
            "general": _r(
                "FOLLOW VESSEL PMS AND SAFETY PROCEDURES",
                "Bilge alarms are safety-critical. Regular testing ensures early detection of flooding "
                "and compliance with safety and environmental regulations.",
                [
                    "Check PMS task interval and last test date",
                    "Test by introducing water to the bilge well or actuating the float switch",
                    "Record all test results with date and operator",
                    "Upload the procedure if exact interval or regulation reference is needed",
                ],
            ),
        },
    ),
    # ── Emergency generator ────────────────────────────────────────────────
    (
        "emergency_generator_test",
        [{"emergency generator"}, {"emergency gen"}, {"emcy generator"}, {"emcy gen"}],
        {
            "overdue": _r(
                "ACTION REQUIRED — CRITICAL SAFETY EQUIPMENT OVERDUE",
                "An overdue emergency generator test is a serious safety deficiency. "
                "Emergency power is required for critical systems including navigation lights, "
                "fire detection, and emergency pumps.",
                [
                    "Run the emergency generator test at the earliest opportunity",
                    "Record the test in the safety log and PMS",
                    "Confirm the transfer to emergency power works correctly",
                    "If any fault is found, raise a defect and notify the Captain immediately",
                ],
            ),
            "frequency": _r(
                "CHECK VESSEL PMS/SMS INTERVAL",
                "Emergency generator test intervals are set by the vessel's PMS, safety management system, "
                "and drill schedule. I do not have the specific interval loaded.",
                [
                    "Check the PMS task interval for emergency generator test",
                    "Confirm last test date and result are recorded",
                    "Upload the emergency generator procedure for an exact interval reference",
                ],
            ),
            "regulation": _r(
                "SOURCE NEEDED FOR EXACT REGULATION",
                "Exact regulatory reference for emergency generator testing depends on vessel code, "
                "flag state, and class society. I cannot confirm the specific article without the source document loaded.",
                [
                    "Upload the emergency generator procedure or applicable flag/class requirement",
                    "Confirm flag state and class society for the applicable code reference",
                ],
            ),
            "general": _r(
                "FOLLOW VESSEL PMS AND DRILL SCHEDULE",
                "The emergency generator is a critical safety system providing power to essential "
                "navigation and safety equipment. It must be tested regularly.",
                [
                    "Check the PMS task and SMS drill schedule for test frequency",
                    "Run on load if possible — confirm all emergency switchboard circuits are live",
                    "Record all tests with date, duration, load, and result",
                ],
            ),
        },
    ),
    # ── OWS / OCM / MARPOL bilge water ────────────────────────────────────
    (
        "ows_ocm_marpol",
        [
            {"ows"}, {"oily water separator"}, {"oily bilge separator"},
            {"ocm"}, {"oil content monitor"}, {"omd"}, {"oil monitoring device"},
            {"15ppm"}, {"15 ppm"}, {"bilge", "marpol"}, {"bilge", "orb"},
            {"oil record", "bilge"},
        ],
        {
            "overdue": _r(
                "ACTION REQUIRED — MARPOL COMPLIANCE AND SURVEY RISK",
                "An overdue OWS/OCM check or record is a MARPOL compliance gap. "
                "Failure to maintain ORB entries or test the OCM/OWS can result in port state deficiencies.",
                [
                    "Check the ORB for missing or overdue entries",
                    "Test the OWS and OCM at the earliest opportunity",
                    "Record all results in the ORB with date, position, quantity, and operator",
                    "If equipment is defective, raise a defect and notify the Captain and owner's superintendent",
                ],
            ),
            "frequency": _r(
                "CHECK VESSEL PMS AND MARPOL PROCEDURES",
                "OWS/OCM test and maintenance intervals are set by the vessel's PMS and MARPOL procedures. "
                "I do not have the specific onboard interval loaded.",
                [
                    "Check the PMS task interval for OWS and OCM",
                    "Confirm ORB records are up to date",
                    "Upload the MARPOL bilge water procedure or OWS maintenance record for an exact reference",
                ],
            ),
            "regulation": _r(
                "SOURCE NEEDED FOR EXACT MARPOL REFERENCE",
                "OWS and OCM requirements fall under MARPOL Annex I, but the exact operational and "
                "maintenance requirements depend on vessel type, flag state, and class. "
                "I cannot confirm the specific article without the source document loaded.",
                [
                    "Upload the MARPOL Annex I procedure or vessel's environmental management plan",
                    "Confirm flag state and class society for applicable code",
                    "Ensure the ORB is up to date regardless of source document",
                ],
            ),
            "general": _r(
                "FOLLOW MARPOL PROCEDURES AND VESSEL PMS",
                "OWS and OCM are MARPOL compliance equipment. All operations must be recorded in the ORB.",
                [
                    "Check OWS and OCM are operational and within service intervals",
                    "Ensure ORB entries are complete — date, position, quantity, officer signature",
                    "Test OCM function per the PMS task",
                    "Upload the MARPOL bilge procedure or OCM manual if exact requirements are needed",
                ],
            ),
        },
    ),
    # ── Fire doors ─────────────────────────────────────────────────────────
    (
        "fire_doors",
        [{"fire door"}, {"fire doors"}],
        {
            "overdue": _r(
                "ACTION REQUIRED — FIRE SAFETY EQUIPMENT MUST BE MAINTAINED",
                "Overdue fire door inspections are a safety deficiency. Fire doors are a primary passive fire protection measure.",
                [
                    "Inspect all fire doors at the earliest opportunity",
                    "Check for correct operation, seals, and self-closing mechanisms",
                    "Record inspections in the safety log and PMS",
                    "Raise a defect for any door that does not operate correctly",
                ],
            ),
            "frequency": _r(
                "CHECK VESSEL PMS/SMS INTERVAL",
                "Fire door inspection intervals are set by the vessel's PMS and safety procedures. "
                "I do not have the specific interval loaded.",
                [
                    "Check the PMS task interval for fire door inspection",
                    "Upload the fire safety procedure for an exact reference",
                ],
            ),
            "regulation": _r(
                "SOURCE NEEDED FOR EXACT REGULATION",
                "Exact fire door testing requirements depend on vessel code, flag, and class. "
                "I cannot confirm the specific reference without the source document loaded.",
                [
                    "Upload the fire safety procedure or flag/class circular",
                    "Confirm vessel code (SOLAS, HSC, MCA etc.) for applicable standard",
                ],
            ),
            "general": _r(
                "FOLLOW VESSEL FIRE SAFETY PROCEDURES",
                "Fire doors are passive fire protection. They must be maintained in operational condition "
                "and tested per the vessel's safety management procedures.",
                [
                    "Confirm all fire doors self-close and latch correctly",
                    "Check seals are intact and undamaged",
                    "Record inspection results in the safety log",
                    "Upload the fire safety procedure if exact reference is needed",
                ],
            ),
        },
    ),
    # ── Watertight doors ──────────────────────────────────────────────────
    (
        "watertight_doors",
        [{"watertight door"}, {"watertight doors"}, {"w/t door"}, {"wt door"}],
        {
            "overdue": _r(
                "ACTION REQUIRED — WATERTIGHT INTEGRITY EQUIPMENT",
                "Overdue watertight door checks are a safety deficiency. Watertight integrity is fundamental to vessel survivability.",
                [
                    "Inspect and test watertight doors at the earliest opportunity",
                    "Check operation locally and from the bridge panel",
                    "Record the test in the safety log and PMS",
                    "Raise a defect for any door that does not operate or seal correctly",
                ],
            ),
            "frequency": _r(
                "CHECK VESSEL PMS/SMS INTERVAL",
                "Watertight door test intervals are set by the vessel's PMS and safety procedures. "
                "I do not have the specific interval loaded.",
                [
                    "Check the PMS task interval for watertight door tests",
                    "Upload the watertight door procedure for an exact reference",
                ],
            ),
            "regulation": _r(
                "SOURCE NEEDED FOR EXACT REGULATION",
                "Watertight door requirements depend on vessel type, code, flag, and class. "
                "I cannot confirm the specific requirement without the source document loaded.",
                [
                    "Upload the applicable watertight door procedure or class/flag circular",
                    "Confirm vessel code (SOLAS, HSC, etc.) for the applicable standard",
                ],
            ),
            "general": _r(
                "FOLLOW VESSEL SAFETY PROCEDURES",
                "Watertight doors are critical to vessel survivability. They must be kept operable "
                "and tested per the vessel's safety management procedures.",
                [
                    "Test all watertight doors per the PMS schedule",
                    "Confirm operation from both sides and remote control if fitted",
                    "Record tests in the safety log",
                ],
            ),
        },
    ),
    # ── Fixed fire suppression (CO2 / FM200) ──────────────────────────────
    (
        "fixed_fire_system",
        [
            {"co2", "fire"}, {"co2", "system"}, {"fm200"},
            {"fixed fire system"}, {"co2", "test"}, {"fm200", "test"},
            {"co2", "check"}, {"co2", "overdue"},
        ],
        {
            "overdue": _r(
                "ACTION REQUIRED — FIXED FIRE SUPPRESSION SYSTEM",
                "An overdue fixed fire system check (CO2/FM200) is a serious safety deficiency. "
                "These systems must be maintained ready for immediate activation.",
                [
                    "Carry out the inspection at the earliest opportunity",
                    "Check cylinder weights/pressures and release mechanisms per manufacturer procedure",
                    "Record the inspection in the safety log and PMS",
                    "For CO2/FM200 service, use a certified contractor — notify Captain and management",
                ],
            ),
            "frequency": _r(
                "CHECK VESSEL PMS AND MANUFACTURER REQUIREMENTS",
                "Fixed fire suppression system inspection intervals are set by the vessel's PMS, "
                "manufacturer requirements, and class/flag standards. I do not have the specific interval loaded.",
                [
                    "Check the PMS task interval and last service date",
                    "Confirm the certified service interval from the system manufacturer documentation",
                    "Upload the fire system service record or procedure for an exact reference",
                ],
            ),
            "regulation": _r(
                "SOURCE NEEDED FOR EXACT REGULATION",
                "Fixed fire suppression requirements depend on vessel type, code, flag, and class. "
                "I cannot confirm the specific regulation without the source document loaded.",
                [
                    "Upload the fire system procedure, manufacturer manual, or class/flag circular",
                    "Confirm vessel code and class society for applicable standard",
                ],
            ),
            "general": _r(
                "FOLLOW VESSEL FIRE SAFETY PROCEDURES AND MANUFACTURER REQUIREMENTS",
                "Fixed fire suppression systems (CO2, FM200) protect against engine room and galley fires. "
                "They must be maintained by certified personnel per manufacturer and class requirements.",
                [
                    "Check cylinder weights/pressures and seals per PMS task",
                    "Test audible/visual alarms and safety interlocks",
                    "Use a certified contractor for system service",
                    "Record all inspections in the safety log",
                ],
            ),
        },
    ),
    # ── Emergency lighting ────────────────────────────────────────────────
    (
        "emergency_lighting",
        [{"emergency lighting"}, {"emergency light"}, {"escape lighting"}],
        {
            "overdue": _r(
                "ACTION REQUIRED — EMERGENCY ESCAPE EQUIPMENT",
                "An overdue emergency lighting test is a safety deficiency. "
                "Emergency lighting is essential for safe evacuation.",
                [
                    "Test all emergency lighting at the earliest opportunity",
                    "Check battery backup duration meets the required minimum",
                    "Record the test in the safety log and PMS",
                    "Replace any failed lamps or batteries immediately and raise a defect",
                ],
            ),
            "frequency": _r(
                "CHECK VESSEL PMS/SMS INTERVAL",
                "Emergency lighting test intervals are set by the vessel's PMS and safety procedures. "
                "I do not have the specific interval loaded.",
                [
                    "Check the PMS task interval for emergency lighting tests",
                    "Upload the emergency lighting procedure for an exact reference",
                ],
            ),
            "regulation": _r(
                "SOURCE NEEDED FOR EXACT REGULATION",
                "Emergency lighting requirements depend on vessel code, flag, and class. "
                "I cannot confirm the specific regulation without the source document loaded.",
                [
                    "Upload the fire safety or electrical procedure, or flag/class circular",
                    "Confirm vessel code for applicable standard",
                ],
            ),
            "general": _r(
                "FOLLOW VESSEL SAFETY PROCEDURES",
                "Emergency lighting is critical for safe evacuation. It must be tested regularly "
                "and maintained per the vessel's safety management procedures.",
                [
                    "Test by simulating main power failure",
                    "Check all escape route lighting and muster station lighting",
                    "Record tests in the safety log",
                    "Upload the procedure if exact test interval is needed",
                ],
            ),
        },
    ),
    # ── Steering gear ─────────────────────────────────────────────────────
    (
        "steering_gear_test",
        [{"steering gear"}, {"steering test"}, {"rudder test"}],
        {
            "overdue": _r(
                "ACTION REQUIRED — PRE-DEPARTURE SAFETY CHECK",
                "An overdue steering gear test is a safety deficiency and may be a port state issue. "
                "Steering gear must be tested before departure.",
                [
                    "Test the steering gear before the next departure",
                    "Test both manual and autopilot modes, and full hard-over to hard-over movement",
                    "Record the test in the deck log and PMS",
                    "Do not sail with a defective or untested steering system",
                ],
            ),
            "frequency": _r(
                "CHECK VESSEL PMS AND SMS — TYPICALLY PRE-DEPARTURE",
                "Steering gear is typically tested before each departure and periodically per the vessel's PMS. "
                "I do not have the specific vessel procedure loaded.",
                [
                    "Check the PMS task interval and SMS requirement",
                    "Confirm evidence of last test is in the deck log",
                    "Upload the steering gear procedure for an exact reference",
                ],
            ),
            "regulation": _r(
                "SOURCE NEEDED FOR EXACT REGULATION",
                "Steering gear test requirements depend on vessel code, flag state, and class. "
                "I cannot confirm the specific regulation without the source document loaded.",
                [
                    "Upload the steering gear procedure or flag/class circular",
                    "Confirm vessel code for applicable standard",
                ],
            ),
            "general": _r(
                "FOLLOW VESSEL SAFETY PROCEDURES",
                "Steering gear is safety-critical. It must be tested before departure and maintained "
                "per the vessel's safety management procedures.",
                [
                    "Test hard-over to hard-over in both directions",
                    "Test from bridge and backup steering position",
                    "Record test results in the deck log",
                    "Upload the steering gear procedure if exact requirements are needed",
                ],
            ),
        },
    ),
    # ── Garbage log / ORB records ─────────────────────────────────────────
    (
        "garbage_orb_records",
        [
            {"garbage record"}, {"garbage log"}, {"garbage management plan"},
            {"garbage management"}, {"orb entry"}, {"orb record"},
            {"oil record book"}, {"oil record"},
            {"garbage", "marpol"},
        ],
        {
            "overdue": _r(
                "ACTION REQUIRED — MARPOL RECORD KEEPING",
                "Overdue or missing ORB/garbage log entries are a MARPOL compliance gap. "
                "Incomplete records can result in port state deficiencies.",
                [
                    "Update the ORB / garbage management log at the earliest opportunity",
                    "Back-fill any missing entries with accurate dates, positions, and quantities",
                    "Ensure entries are signed by the responsible officer and countersigned by the Master",
                    "If operations were conducted without recording, note in the log and inform the owner's DPA",
                ],
            ),
            "frequency": _r(
                "RECORDS MUST BE MADE AT TIME OF OPERATION",
                "ORB and garbage log entries must be made at the time of each relevant operation — "
                "not periodically on a schedule. Each discharge, transfer, or operation requires an entry.",
                [
                    "Check the ORB and garbage log are up to date for recent operations",
                    "Upload the MARPOL record-keeping procedure for an exact reference",
                ],
            ),
            "regulation": _r(
                "SOURCE NEEDED FOR EXACT MARPOL REFERENCE",
                "ORB and garbage log requirements fall under MARPOL, but the exact articles and operational "
                "requirements depend on vessel type and flag state. "
                "I cannot confirm the specific article without the source document loaded.",
                [
                    "Upload the MARPOL Annex I / Annex V procedure or vessel's environmental management plan",
                    "Confirm flag state for applicable requirement",
                ],
            ),
            "general": _r(
                "MAINTAIN RECORDS PER MARPOL PROCEDURES",
                "ORB and garbage log records must be accurate, complete, and retained on board. "
                "They are a primary inspection document for port state control.",
                [
                    "Confirm all recent operations are recorded with date, position, quantity, and officer signature",
                    "Check the ORB and garbage management log are available for inspection",
                    "Upload the MARPOL record-keeping procedure if exact requirements are needed",
                ],
            ),
        },
    ),
]


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _match_topic(t: str) -> Optional[dict]:
    """Return response dict for first matching topic, or None."""
    for _topic_id, match_sets, responses in _PLAYBOOK:
        for kw_set in match_sets:
            if all(kw in t for kw in kw_set):
                return responses
    return None


def lookup(query: str) -> Optional[str]:
    """
    Return a CE-style playbook response, or None if no topic matches.
    Caller should fall through to RAG when None is returned.
    """
    t = query.lower()
    responses = _match_topic(t)
    if responses is None:
        return None
    qtype = _query_type(t)
    return responses.get(qtype) or responses.get("general")
