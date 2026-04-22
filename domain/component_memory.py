import re
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Component type keyword mapping
# ---------------------------------------------------------------------------

_COMPONENT_KEYWORDS = {
    "anti_siphon_valve": [
        "anti-siphon valve", "anti siphon valve", "antisiphon valve",
        "antisiphonventiel", "clapet anti-siphon",
    ],
    "watermaker": [
        "watermaker", "water maker", "reverse osmosis", "ro unit", "desalinator",
        "osmosis unit",
    ],
    "deckwash_pump": [
        "deck wash pump", "deckwash pump", "deck wash", "washdown pump",
        "wash down pump",
    ],
    "stern_drive": [
        "stern drive", "sterndrive", "outdrive", "gimbal housing",
        "transom assembly", "gimbal ring", "alpha drive", "bravo drive",
    ],
    "transom_plate": [
        "transom plate", "transom housing",
    ],
    "fire_pump": [
        "fire pump", "fire fighting pump", "fire main pump",
    ],
    "chiller": [
        "chiller", "hvac chiller", "marine chiller", "air conditioning unit",
        "ac unit", "climate control unit",
    ],
    "sewage_treatment_plant": [
        "sewage treatment", "sewage plant", "stp", "marine sanitation",
        "biological treatment unit", "blackwater treatment",
    ],
    "ows": [
        "oily water separator", "ows", "15 ppm bilge separator",
        "bilge water separator",
    ],
    "black_water_pump": [
        "black water pump", "blackwater pump", "sewage pump",
        "holding tank pump", "macerator pump",
    ],
    "hydraulic_pump": [
        "hydraulic pump", "hydraulic unit", "hydraulic power pack",
        "hydraulic system pump",
    ],
    "deck_crane": [
        "deck crane", "provision crane", "tender crane", "boat crane",
    ],
    "bilge_pump": [
        "bilge pump",
    ],
    "fuel_pump": [
        "fuel pump", "fuel transfer pump", "fuel day tank pump",
    ],
    "sea_water_pump": [
        "sea water pump", "seawater pump", "sea water cooling pump",
        "raw water pump", "cooling water pump",
    ],
    "freshwater_pump": [
        "freshwater pump", "fresh water pump", "domestic water pump",
        "water pressure pump",
    ],
    "windlass": [
        "windlass", "anchor windlass", "anchor winch",
    ],
    "bow_thruster": [
        "bow thruster", "tunnel thruster", "lateral thruster",
    ],
    "stern_thruster": [
        "stern thruster",
    ],
    "stabiliser": [
        "stabiliser", "stabilizer", "fin stabiliser", "gyrostabiliser",
        "seakeeper",
    ],
    "generator": [
        "generating set", "diesel generator",
    ],
    "main_engine": [
        "main engine", "propulsion engine", "main propulsion",
    ],
    "gearbox": [
        "gearbox", "gear box", "marine transmission", "reduction gear",
    ],
    "heat_exchanger": [
        "heat exchanger", "keel cooler",
    ],
    "air_compressor": [
        "air compressor", "starting air compressor", "control air compressor",
    ],
    "turbocharger": [
        "turbocharger", "turbo charger",
    ],
}

# Human-readable display labels
_COMPONENT_LABELS = {
    "anti_siphon_valve": "Anti-siphon valve",
    "watermaker": "Watermaker",
    "deckwash_pump": "Deck wash pump",
    "stern_drive": "Stern drive",
    "transom_plate": "Transom plate",
    "fire_pump": "Fire pump",
    "chiller": "Chiller",
    "sewage_treatment_plant": "Sewage treatment plant",
    "ows": "Oily water separator",
    "black_water_pump": "Black water pump",
    "hydraulic_pump": "Hydraulic pump",
    "deck_crane": "Deck crane",
    "bilge_pump": "Bilge pump",
    "fuel_pump": "Fuel pump",
    "sea_water_pump": "Sea water pump",
    "freshwater_pump": "Fresh water pump",
    "windlass": "Windlass",
    "bow_thruster": "Bow thruster",
    "stern_thruster": "Stern thruster",
    "stabiliser": "Stabiliser",
    "generator": "Generator",
    "main_engine": "Main engine",
    "gearbox": "Gearbox",
    "heat_exchanger": "Heat exchanger",
    "air_compressor": "Air compressor",
    "turbocharger": "Turbocharger",
}

_COMPONENT_SYSTEM = {
    "anti_siphon_valve": "plumbing",
    "watermaker": "fresh_water",
    "deckwash_pump": "deck_services",
    "stern_drive": "propulsion",
    "transom_plate": "propulsion",
    "fire_pump": "fire_fighting",
    "chiller": "hvac",
    "sewage_treatment_plant": "sewage",
    "ows": "bilge_oily_water",
    "black_water_pump": "sewage",
    "hydraulic_pump": "hydraulics",
    "deck_crane": "deck_machinery",
    "bilge_pump": "bilge_oily_water",
    "fuel_pump": "fuel",
    "sea_water_pump": "cooling",
    "freshwater_pump": "fresh_water",
    "windlass": "deck_machinery",
    "bow_thruster": "propulsion",
    "stern_thruster": "propulsion",
    "stabiliser": "stabilisation",
    "generator": "electrical",
    "main_engine": "propulsion",
    "gearbox": "propulsion",
    "heat_exchanger": "cooling",
    "air_compressor": "compressed_air",
    "turbocharger": "propulsion",
}

# Part number pattern: e.g. "DA226", "196350-04061", "NJ-1234/56"
_PART_NUMBER_RE = re.compile(
    r'''(?x)
    \b
    (
        [A-Z]{1,6}[0-9]{2,}[A-Z0-9-/]*   # letter-prefix: DA226, NJ-1234
        | [A-Z0-9]{2,}-[A-Z0-9]{3,}        # dashed: 196350-04061
    )
    \b''',
    re.IGNORECASE,
)

_OEM_BRANDS = [
    "yanmar", "mtu", "caterpillar", "danfoss", "nanni", "volvo penta",
    "kohler", "cummins", "detroit diesel", "mercury", "perkins",
    "john deere", "westerbeke", "jabsco", "vetus", "wartsila",
    "hydro electrique", "spectra", "watermaster", "typhoon", "selkirk",
    "racor", "parker", "groco", "whale", "rule", "johnson pump",
    "grundfos", "flowserve", "imo pump", "allweiler", "framo",
    "maxwell", "lewmar", "lofrans", "muir", "italwinch",
    "ultraflex", "sleipner", "sidepower", "wesmar",
    "dometic", "webasto", "eberspacher",
    "furuno", "garmin", "simrad", "raymarine",
    "seakeeper", "quantum",
    "zf marine", "twin disc", "scania", "man diesel",
    "northern lights", "onan", "rolls royce",
    "mercruiser", "bravo", "alpha drive",
    "hamworthy", "serck", "alfa laval",
]

_CONF_RANK = {"low": 0, "medium": 1, "high": 2}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _classify_component_type(text: str) -> Optional[str]:
    t = text.lower()
    for comp_type, keywords in _COMPONENT_KEYWORDS.items():
        if any(kw in t for kw in keywords):
            return comp_type
    return None


def _extract_part_number(text: str) -> Optional[str]:
    m = _PART_NUMBER_RE.search(text)
    return m.group(1).upper() if m else None


def _extract_make(text: str, supplier_name: str = "") -> Optional[str]:
    t = text.lower()
    for brand in _OEM_BRANDS:
        if brand in t:
            idx = t.index(brand)
            return text[idx: idx + len(brand)].title()
    if supplier_name:
        sup = supplier_name.strip().lower()
        for brand in _OEM_BRANDS:
            if brand in sup:
                return supplier_name.strip()
    return None


def _extract_model(text: str, part_number: str = "") -> Optional[str]:
    m = re.match(r'^([A-Z]{1,4}[0-9]{2,}[A-Z0-9-]*)', text.strip(), re.IGNORECASE)
    if m:
        candidate = m.group(1).upper()
        if candidate != (part_number or "").upper():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Extraction from documents
# ---------------------------------------------------------------------------

def extract_components_from_doc(doc_record: dict) -> list:
    """Scan line items of a doc_record and return a list of component dicts."""
    components = []
    supplier = (doc_record.get("supplier_name") or "").strip()
    doc_type = doc_record.get("doc_type", "unknown")
    doc_id = doc_record.get("document_id", "")
    line_items = doc_record.get("line_items") or []
    seen_types: set = set()

    for item in line_items:
        desc = (item.get("description") or "").strip()
        if not desc:
            continue
        comp_type = _classify_component_type(desc)
        if not comp_type or comp_type in seen_types:
            continue
        seen_types.add(comp_type)

        part_number = _extract_part_number(desc)
        make = _extract_make(desc, supplier)
        model = _extract_model(desc, part_number)
        label = _COMPONENT_LABELS.get(comp_type, comp_type.replace("_", " ").title())
        system = _COMPONENT_SYSTEM.get(comp_type, "general")
        confidence = (
            "high" if (part_number or (make and model))
            else "medium" if (make or model)
            else "low"
        )

        components.append({
            "component_type": comp_type,
            "component_name": label,
            "make": make,
            "model": model,
            "part_number": part_number,
            "system": system,
            "supplier": supplier or None,
            "source_doc_type": doc_type,
            "source_doc_id": doc_id,
            "last_seen_at": _now(),
            "confidence": confidence,
            "source_description": desc[:120],
        })
        logger.info(
            "Component extracted: type=%s make=%s model=%s pn=%s confidence=%s",
            comp_type, make, model, part_number, confidence,
        )

    return components


def extract_components_from_text(text: str, source_type: str = "user_message") -> list:
    """Extract a component record from a plain text string (e.g. a market check query)."""
    comp_type = _classify_component_type(text)
    if not comp_type:
        return []
    part_number = _extract_part_number(text)
    make = _extract_make(text)
    model = _extract_model(text, part_number)
    label = _COMPONENT_LABELS.get(comp_type, comp_type.replace("_", " ").title())
    system = _COMPONENT_SYSTEM.get(comp_type, "general")
    confidence = "medium" if (part_number or make or model) else "low"
    return [{
        "component_type": comp_type,
        "component_name": label,
        "make": make,
        "model": model,
        "part_number": part_number,
        "system": system,
        "supplier": None,
        "source_doc_type": source_type,
        "source_doc_id": None,
        "last_seen_at": _now(),
        "confidence": confidence,
        "source_description": text[:120],
    }]


# ---------------------------------------------------------------------------
# State integration
# ---------------------------------------------------------------------------

def merge_components(new_components: list, state: dict) -> dict:
    """
    Upsert extracted components into state["components"].
    Updates an existing record by component_type; appends if new.
    Keeps at most 20 records, most-recently-seen first.
    """
    existing = state.setdefault("components", [])
    by_type = {c["component_type"]: i for i, c in enumerate(existing)}

    for comp in new_components:
        ct = comp["component_type"]
        if ct in by_type:
            old = existing[by_type[ct]]
            if not old.get("part_number") and comp.get("part_number"):
                old["part_number"] = comp["part_number"]
            if not old.get("make") and comp.get("make"):
                old["make"] = comp["make"]
            if not old.get("model") and comp.get("model"):
                old["model"] = comp["model"]
            old["last_seen_at"] = comp["last_seen_at"]
            old["source_doc_id"] = comp["source_doc_id"]
            old["source_doc_type"] = comp["source_doc_type"]
            if _CONF_RANK.get(comp["confidence"], 0) > _CONF_RANK.get(old.get("confidence", "low"), 0):
                old["confidence"] = comp["confidence"]
        else:
            existing.append(comp)
            by_type[ct] = len(existing) - 1

    existing.sort(key=lambda c: c.get("last_seen_at", ""), reverse=True)
    state["components"] = existing[:20]
    return state


def get_recent_components(state: dict, limit: int = 5) -> list:
    components = state.get("components") or []
    return sorted(components, key=lambda c: c.get("last_seen_at", ""), reverse=True)[:limit]


def build_component_context(state: dict) -> str:
    """
    Return a short plain-text summary of recent known components for use
    in follow-up prompts. Empty string when nothing is known.
    """
    components = get_recent_components(state, limit=3)
    if not components:
        return ""
    lines = ["Known vessel components:"]
    for c in components:
        parts = [c["component_name"]]
        id_parts = []
        if c.get("make"):
            id_parts.append(c["make"])
        if c.get("model"):
            id_parts.append(c["model"])
        if id_parts:
            parts.append(f"({' '.join(id_parts)})")
        if c.get("part_number"):
            parts.append(f"P/N: {c['part_number']}")
        if c.get("system"):
            parts.append(f"[{c['system'].replace('_', ' ')}]")
        lines.append("• " + " ".join(parts))
    return "\n".join(lines)
