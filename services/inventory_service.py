"""
Inventory ingestion: classify, extract, and normalise equipment lists
and stock inventories from Excel, CSV, PDF text, and images.
"""
import io
import re
import os
import csv
import json
import uuid
import base64
import hashlib
import logging
from typing import Optional
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv(dotenv_path=".env")
logger = logging.getLogger(__name__)
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# ---------------------------------------------------------------------------
# Classification signals
# ---------------------------------------------------------------------------

_EQUIPMENT_HEADING_KW = frozenset([
    "equipment list", "machinery list", "asset list", "asset register",
    "equipment register", "machinery register", "equipment inventory",
    "installed equipment", "onboard equipment", "vessel equipment",
    "main machinery",
])

_STOCK_HEADING_KW = frozenset([
    "stock list", "spare parts list", "spare parts inventory", "spares list",
    "inventory", "stores list", "parts list", "parts inventory",
    "stock inventory", "consumables", "bonded stores",
])

_EQUIPMENT_BODY_KW = frozenset([
    "serial number", "serial no", "s/n", "installed", "asset", "machinery",
    "make", "maker", "manufacturer", "model", "location", "system",
])

_STOCK_BODY_KW = frozenset([
    "part number", "p/n", "qty", "quantity", "bin", "onboard",
    "storage location", "spare", "consumable",
])


def classify_inventory_text(text: str) -> Optional[str]:
    """
    Return 'equipment_list', 'stock_inventory', 'spare_parts_inventory', or None.
    Returns None for technical manuals so they are not imported as inventory.
    Uses keyword signals — no Claude call.
    """
    from services.manual_service import is_technical_manual_text
    if is_technical_manual_text(text):
        logger.debug("inventory_classify: technical manual detected — skipping inventory classification")
        return None

    t = text.lower()

    # Heading-level signals — one match is enough.
    for kw in _STOCK_HEADING_KW:
        if kw in t:
            doc_type = "spare_parts_inventory" if "spare" in kw else "stock_inventory"
            logger.debug("inventory_classify: heading match kw=%r → %s", kw, doc_type)
            return doc_type

    for kw in _EQUIPMENT_HEADING_KW:
        if kw in t:
            logger.debug("inventory_classify: heading match kw=%r → equipment_list", kw)
            return "equipment_list"

    # Body signals — 2+ required.
    stock_hits = sum(1 for kw in _STOCK_BODY_KW if kw in t)
    equip_hits = sum(1 for kw in _EQUIPMENT_BODY_KW if kw in t)

    if stock_hits >= 2 and stock_hits >= equip_hits:
        return "stock_inventory"
    if equip_hits >= 2:
        return "equipment_list"

    return None


# ---------------------------------------------------------------------------
# Column header normalisation
# ---------------------------------------------------------------------------

_EQUIPMENT_COL_MAP = {
    # system / group
    "system": "system",
    "group": "system",
    "category": "system",
    # equipment name
    "equipment": "equipment_name",
    "equipment name": "equipment_name",
    "equipment_name": "equipment_name",
    "name": "equipment_name",
    "description": "equipment_name",
    "machinery": "equipment_name",
    "asset": "equipment_name",
    "item": "equipment_name",
    "unit": "equipment_name",        # "Unit" is often used as the item-name column
    # make / manufacturer
    "make": "make",
    "maker": "make",
    "brand": "make",
    "manufacturer": "make",
    "mfr": "make",
    # model / type
    "model": "model",
    "type": "model",
    "designation": "model",
    # serial number
    "serial": "serial_number",
    "serial number": "serial_number",
    "serial no": "serial_number",
    "serial no.": "serial_number",
    "s/n": "serial_number",
    "sn": "serial_number",
    "serial_number": "serial_number",
    # location
    "location": "location",
    "loc": "location",
    "position": "location",
    "installed at": "location",
    "area": "location",
    "room": "location",
    # specification (maps to notes — no separate schema field)
    "specification": "notes",
    "specifications": "notes",
    "spec": "notes",
    # serial number variants with special characters
    "serial #": "serial_number",
    # notes
    "notes": "notes",
    "remarks": "notes",
    "comment": "notes",
    "comments": "notes",
}

_STOCK_COL_MAP = {
    "part number": "part_number",
    "part no": "part_number",
    "part no.": "part_number",
    "p/n": "part_number",
    "pn": "part_number",
    "part_number": "part_number",
    "code": "part_number",
    "item code": "part_number",
    "part code": "part_number",
    "ref": "part_number",
    "reference": "part_number",
    "article no": "part_number",
    "article number": "part_number",
    "mat no": "part_number",
    "mat. no": "part_number",
    "material no": "part_number",
    "material number": "part_number",
    "description": "description",
    "desc": "description",
    "item": "description",
    "item description": "description",
    "item name": "description",
    "part name": "description",
    "name": "description",
    "material": "description",
    "material description": "description",
    "qty": "quantity_onboard",
    "quantity": "quantity_onboard",
    "qty onboard": "quantity_onboard",
    "qty on board": "quantity_onboard",
    "stock": "quantity_onboard",
    "on hand": "quantity_onboard",
    "in stock": "quantity_onboard",
    "onboard": "quantity_onboard",
    "stock qty": "quantity_onboard",
    "stock quantity": "quantity_onboard",
    "qty in stock": "quantity_onboard",
    "available qty": "quantity_onboard",
    "available": "quantity_onboard",
    "unit": "unit",
    "uom": "unit",
    "units": "unit",
    "location": "storage_location",
    "loc": "storage_location",
    "bin": "storage_location",
    "bin no": "storage_location",
    "bin number": "storage_location",
    "shelf": "storage_location",
    "shelf no": "storage_location",
    "storage location": "storage_location",
    "store": "storage_location",
    "storage": "storage_location",
    "storeroom": "storage_location",
    "storage_location": "storage_location",
    "equipment": "linked_equipment",
    "linked equipment": "linked_equipment",
    "linked_equipment": "linked_equipment",
    "fitted to": "linked_equipment",
    "for": "linked_equipment",
    "applicable to": "linked_equipment",
    "system": "linked_equipment",
    "make": "make",
    "manufacturer": "make",
    "brand": "make",
    "model": "model",
    "supplier": "supplier",
    "vendor": "supplier",
    "notes": "notes",
    "remarks": "notes",
    "comment": "notes",
    "comments": "notes",
}

# Fields unique to each type (used for voting)
_EQUIPMENT_ONLY_FIELDS = {"serial_number", "system"}
_STOCK_ONLY_FIELDS = {"part_number", "quantity_onboard", "storage_location", "linked_equipment"}


# ---------------------------------------------------------------------------
# Junk row filter
# ---------------------------------------------------------------------------

_JUNK_EQUIPMENT_NAMES = frozenset({
    "component tags", "component tag",
    "emergency stops", "emergency stop",
    "notes", "note",
    "remarks",
    "section", "sub-section", "subsection",
    "description", "item", "items",
    "equipment", "equipment name",
    "name",
    "yes", "no", "n/a", "na", "tbc", "tbd",
    "-", "–", "—",
})

# Matches date/timestamp values like "29.04.2026 05:48:37" or "2026-04-29"
_TIMESTAMP_RE = re.compile(
    r"^\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}"
    r"(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?$"
)


def is_junk_equipment_name(name: str) -> bool:
    """Return True if name is a junk row (section header, timestamp, placeholder)."""
    n = name.strip().lower()
    if not n:
        return True
    if n in _JUNK_EQUIPMENT_NAMES:
        return True
    if _TIMESTAMP_RE.match(name.strip()):
        return True
    return False


def _normalise_col(raw: str) -> str:
    return raw.strip().lower().replace("_", " ").replace("-", " ")


def _map_headers(raw_headers: list) -> dict:
    """
    Given a list of raw column header strings, return a dict mapping
    col_index → canonical_field_name for all recognised columns.
    """
    mapping = {}
    for i, h in enumerate(raw_headers):
        norm = _normalise_col(str(h))
        # Try stock map first (more specific for combined docs)
        if norm in _STOCK_COL_MAP:
            mapping[i] = _STOCK_COL_MAP[norm]
        elif norm in _EQUIPMENT_COL_MAP:
            mapping[i] = _EQUIPMENT_COL_MAP[norm]
    return mapping


def _classify_mapped_columns(col_mapping: dict) -> str:
    """Vote on table type from canonical field names."""
    fields = set(col_mapping.values())
    stock_votes = len(fields & _STOCK_ONLY_FIELDS)
    equip_votes = len(fields & _EQUIPMENT_ONLY_FIELDS)
    if "description" in fields and "part_number" in fields:
        return "stock"
    if stock_votes >= equip_votes and ("quantity_onboard" in fields or "part_number" in fields):
        return "stock"
    return "equipment"


def _parse_qty(val) -> Optional[float]:
    if val is None:
        return None
    s = str(val).strip()
    if not s or s in ("-", "N/A", "n/a", ""):
        return None
    # extract leading number from strings like "3 pcs" or "3.5L"
    m = re.match(r"^([\d]+(?:[.,]\d+)?)", s)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Tabular extraction (shared by Excel and CSV)
# ---------------------------------------------------------------------------

def _extract_stock_row(row: dict) -> dict:
    item = {
        "part_number": (row.get("part_number") or "").strip() or None,
        "description": (row.get("description") or row.get("equipment_name") or "").strip(),
        "quantity_onboard": _parse_qty(row.get("quantity_onboard")),
        "unit": (row.get("unit") or "").strip() or None,
        "storage_location": (row.get("storage_location") or row.get("location") or "").strip() or None,
        "linked_equipment": (row.get("linked_equipment") or row.get("system") or "").strip() or None,
        "make": (row.get("make") or "").strip() or None,
        "model": (row.get("model") or "").strip() or None,
        "supplier": (row.get("supplier") or "").strip() or None,
        "notes": (row.get("notes") or "").strip() or None,
    }
    return item if item["description"] or item["part_number"] else {}


def _extract_equipment_row(row: dict) -> dict:
    item = {
        "system": (row.get("system") or row.get("linked_equipment") or "").strip() or None,
        # "unit" shadows the stock UOM field when _map_headers resolves it from _STOCK_COL_MAP;
        # fall back to it here so CSVs that use "Unit" as the item-name column are not lost.
        "equipment_name": (
            row.get("equipment_name") or row.get("description") or row.get("unit") or ""
        ).strip() or None,
        "make": (row.get("make") or "").strip() or None,
        "model": (row.get("model") or "").strip() or None,
        "serial_number": (row.get("serial_number") or "").strip() or None,
        "location": (row.get("location") or row.get("storage_location") or "").strip() or None,
        "notes": (row.get("notes") or "").strip() or None,
    }
    return item if item["equipment_name"] or item["system"] else {}


def extract_inventory_from_tabular(headers: list, rows: list, confidence: float = 0.8) -> dict:
    """
    Convert a list of header strings and row dicts (keyed by header index)
    into a normalised inventory dict {equipment: [...], stock: [...]}.
    """
    col_mapping = _map_headers(headers)

    mapped_indices = set(col_mapping.keys())
    unmapped_columns = [
        str(headers[i]) for i in range(len(headers))
        if i not in mapped_indices and str(headers[i]).strip()
    ]

    if not col_mapping:
        logger.warning(
            "inventory tabular: no recognisable columns "
            "rows_detected=%d unmapped_columns=%r",
            len(rows), headers[:10],
        )
        return {"equipment": [], "stock": []}

    table_type = _classify_mapped_columns(col_mapping)
    logger.info(
        "inventory tabular: table_type=%s cols_recognised=%d rows_detected=%d "
        "unmapped_columns=%r",
        table_type, len(col_mapping), len(rows), unmapped_columns,
    )

    equipment_items = []
    stock_items = []
    junk_skipped = 0

    for raw_row in rows:
        # Map column indices to canonical field names
        mapped: dict = {}
        for col_idx, canon_field in col_mapping.items():
            if col_idx < len(raw_row):
                val = raw_row[col_idx]
                if val is not None and str(val).strip():
                    mapped[canon_field] = str(val).strip()

        if not mapped:
            continue

        if table_type == "stock":
            item = _extract_stock_row(mapped)
            if item:
                item["confidence"] = confidence
                stock_items.append(item)
        else:
            item = _extract_equipment_row(mapped)
            if item:
                name = item.get("equipment_name") or ""
                if name and is_junk_equipment_name(name):
                    junk_skipped += 1
                    logger.debug("inventory tabular: junk row skipped name=%r", name)
                    continue
                item["confidence"] = confidence
                equipment_items.append(item)

    rows_mapped = len(equipment_items) + len(stock_items)
    logger.info(
        "inventory tabular complete: table_type=%s rows_detected=%d rows_mapped=%d junk_skipped=%d",
        table_type, len(rows), rows_mapped, junk_skipped,
    )
    return {"equipment": equipment_items, "stock": stock_items, "skipped_rows": junk_skipped}


# ---------------------------------------------------------------------------
# Excel extraction
# ---------------------------------------------------------------------------

def extract_inventory_from_excel(file_path: str) -> dict:
    """Parse an Excel file into an inventory dict using pandas."""
    try:
        import pandas as pd
    except ImportError:
        logger.error("inventory: pandas not available")
        return {"equipment": [], "stock": []}

    all_equipment: list = []
    all_stock: list = []
    all_skipped: int = 0

    try:
        xl = pd.ExcelFile(file_path, engine="openpyxl")
    except Exception:
        try:
            xl = pd.ExcelFile(file_path, engine="xlrd")
        except Exception as exc:
            logger.warning("inventory: failed to open Excel file=%s: %s", file_path, exc)
            return {"equipment": [], "stock": []}

    for sheet_name in xl.sheet_names:
        try:
            df = xl.parse(sheet_name, header=0, dtype=str)
        except Exception as exc:
            logger.warning("inventory: failed to parse sheet=%r: %s", sheet_name, exc)
            continue

        df = df.dropna(how="all")
        if df.empty:
            continue

        headers = [str(h) for h in df.columns.tolist()]
        rows = [row.tolist() for _, row in df.iterrows()]

        result = extract_inventory_from_tabular(headers, rows, confidence=0.8)
        all_equipment.extend(result["equipment"])
        all_stock.extend(result["stock"])
        all_skipped += result.get("skipped_rows", 0)

    logger.info(
        "inventory excel: equipment=%d stock=%d skipped=%d file=%s",
        len(all_equipment), len(all_stock), all_skipped, os.path.basename(file_path),
    )
    return {"equipment": all_equipment, "stock": all_stock, "skipped_rows": all_skipped}


# ---------------------------------------------------------------------------
# CSV header detection helpers
# ---------------------------------------------------------------------------

# Tokens whose presence in a cell strongly suggests it is a header cell, not
# a data value.  Used by _find_header_row for scoring candidate rows.
_HEADER_SIGNAL_TOKENS = frozenset({
    "name", "description", "equipment", "machinery", "asset",
    "make", "maker", "manufacturer", "brand",
    "model", "type", "designation",
    "serial", "serial number", "serial no", "s/n", "sn",
    "location", "area", "room", "position",
    "system", "group", "category",
    "specification", "spec", "supplier", "vendor",
    "unit", "qty", "quantity", "notes", "remarks",
    "part number", "part no",
})


def _flatten_csv_header(raw: str) -> str:
    """
    Resolve a compound CSV header like 'Serial # / Type' or 'Make / Supplier'
    to the first slash-separated part that maps to a known column name.
    Non-compound headers are returned unchanged so _map_headers processes them
    normally.  If no part maps, the original string is returned.
    """
    if "/" not in raw:
        return raw
    for part in raw.split("/"):
        part_norm = _normalise_col(part)
        if part_norm in _STOCK_COL_MAP or part_norm in _EQUIPMENT_COL_MAP:
            return part.strip()
    return raw  # nothing mapped — pass through unchanged


def _find_header_row(all_rows: list, max_scan: int = 10) -> int:
    """
    Scan the first max_scan rows and return the index of the row most likely
    to be the header row.

    Each candidate row is scored by the number of its cells that resolve to a
    known column name (after compound-header flattening).  Direct map hits
    score 2; cells that contain a header-signal token score 1.  Blank rows and
    single-cell title rows (e.g. 'Components') score 0 and are skipped.

    Returns 0 if no row scores above the default of -1 (safe fallback: use
    the first row as header, which is the original behaviour).
    """
    best_idx = 0
    best_score = -1

    for idx, row in enumerate(all_rows[:max_scan]):
        non_empty = [str(c).strip() for c in row if str(c).strip()]
        if not non_empty:
            continue

        score = 0
        for cell in non_empty:
            flat_norm = _normalise_col(_flatten_csv_header(cell))
            if flat_norm in _STOCK_COL_MAP or flat_norm in _EQUIPMENT_COL_MAP:
                score += 2
            elif any(tok in _normalise_col(cell) for tok in _HEADER_SIGNAL_TOKENS):
                score += 1

        if score > best_score:
            best_score = score
            best_idx = idx

    return best_idx


# ---------------------------------------------------------------------------
# CSV extraction
# ---------------------------------------------------------------------------

# Encodings tried in preference order. cp1252 covers most Windows/Excel exports;
# latin-1 and iso-8859-1 are byte-transparent fallbacks that never raise on any
# single-byte file.
_CSV_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1", "iso-8859-1")


def extract_inventory_from_csv(file_path: str) -> dict:
    """Parse a CSV file into an inventory dict, auto-detecting the header row.

    Tries multiple encodings in preference order so Windows/Excel CSV exports
    (cp1252, latin-1) are handled without crashing.
    Returns {"encoding_error": True} if all encodings fail.
    """
    fname = os.path.basename(file_path)
    all_rows = None
    used_encoding = None

    for encoding in _CSV_ENCODINGS:
        try:
            with open(file_path, newline="", encoding=encoding) as f:
                sample = f.read(2048)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
            with open(file_path, newline="", encoding=encoding) as f:
                reader = csv.reader(f, dialect)
                all_rows = list(reader)
            used_encoding = encoding
            logger.info("inventory: csv_encoding_used=%s file=%s", encoding, fname)
            break
        except UnicodeDecodeError:
            logger.debug("inventory: csv_encoding_failed=%s file=%s", encoding, fname)
        except Exception as exc:
            logger.warning("inventory: CSV read failed encoding=%s file=%s: %s", encoding, fname, exc)

    if all_rows is None:
        logger.warning(
            "inventory: csv_encoding_not_supported — all encodings failed file=%s tried=%r",
            fname, list(_CSV_ENCODINGS),
        )
        return {"equipment": [], "stock": [], "encoding_error": True}

    if len(all_rows) < 2:
        return {"equipment": [], "stock": []}

    header_idx = _find_header_row(all_rows)
    if header_idx > 0:
        logger.info(
            "inventory csv: header_row_detected=%d skipped_title_rows=%d file=%s",
            header_idx, header_idx, fname,
        )

    # Flatten compound headers (e.g. 'Serial # / Type' → 'Serial #') so that
    # _map_headers can resolve them to canonical field names.
    headers = [_flatten_csv_header(h) for h in all_rows[header_idx]]
    rows = all_rows[header_idx + 1:]

    result = extract_inventory_from_tabular(headers, rows, confidence=0.8)
    logger.info(
        "inventory csv: equipment=%d stock=%d encoding=%s file=%s",
        len(result["equipment"]), len(result["stock"]), used_encoding, fname,
    )
    return result


# ---------------------------------------------------------------------------
# Claude extraction prompt (text and images)
# ---------------------------------------------------------------------------

_INVENTORY_EXTRACT_PROMPT = """\
You are extracting structured inventory data from a marine vessel document.
The document may be an equipment list, spare parts inventory, or stock list.
It may be a table, spreadsheet image, list, or unstructured text.

CRITICAL OUTPUT RULES:
- Start your response with { and end it with }
- Return ONLY a raw JSON object — no markdown, no code fences, no comments, no trailing text
- Do not add ANY text before or after the JSON object
- Ensure every string is properly escaped — no literal newlines inside string values
- Ensure all arrays and objects are properly closed — no truncation
- If you cannot extract structured data, return {"doc_type": null, "equipment": [], "stock": []} and nothing else

JSON schema to return:
{
  "doc_type": "equipment_list|stock_inventory|spare_parts_inventory|null",
  "equipment": [
    {
      "system": "string|null",
      "equipment_name": "string|null",
      "make": "string|null",
      "model": "string|null",
      "serial_number": "string|null",
      "location": "string|null",
      "notes": "string|null"
    }
  ],
  "stock": [
    {
      "part_number": "string|null",
      "description": "string",
      "quantity_onboard": "number|null",
      "unit": "string|null",
      "storage_location": "string|null",
      "linked_equipment": "string|null",
      "make": "string|null",
      "model": "string|null",
      "supplier": "string|null",
      "notes": "string|null"
    }
  ]
}

Extraction rules:
- equipment: machinery, systems, assets installed onboard (main engine, generator, OWS, etc.)
- stock: spare parts, consumables, stores held onboard (oil, filters, gaskets, etc.)
- If equipment list only: leave stock as empty array []
- If stock/spares list only: leave equipment as empty array []
- Some documents contain both
- Normalise headers: Qty → quantity_onboard, S/N → serial_number, Loc/Bin → storage_location
- quantity_onboard must be a number or null — never a string
- If quantity is a range, use the lower bound
- linked_equipment: the equipment a spare part belongs to, if stated
- Use null for unknown fields — do not invent values
- Shorten notes to one line maximum — no multi-line strings
"""

# Chunk size for large text documents — keeps per-call output within token budget
_TEXT_CHUNK_SIZE = 5000
_TOKENS_PER_CHUNK = 3000
_TOKENS_SINGLE_CALL = 8000


# ---------------------------------------------------------------------------
# JSON recovery
# ---------------------------------------------------------------------------

def _fix_json_strings(s: str) -> str:
    """Escape literal control characters that appear inside JSON string values."""
    result = []
    in_string = False
    escape_next = False
    for ch in s:
        if escape_next:
            result.append(ch)
            escape_next = False
        elif ch == "\\":
            result.append(ch)
            escape_next = True
        elif ch == '"':
            in_string = not in_string
            result.append(ch)
        elif in_string and ch == "\n":
            result.append("\\n")
        elif in_string and ch == "\r":
            result.append("\\r")
        elif in_string and ch == "\t":
            result.append("\\t")
        else:
            result.append(ch)
    return "".join(result)


def _recover_partial_arrays(raw: str) -> dict:
    """
    Extract complete JSON objects from truncated equipment/stock arrays.
    Used when the response was cut off before the closing brackets.
    """
    result: dict = {}
    for key in ("equipment", "stock"):
        m = re.search(r'"' + key + r'"\s*:\s*\[', raw)
        if not m:
            continue
        pos = m.end()
        depth = 0
        obj_start: Optional[int] = None
        items = []
        while pos < len(raw):
            ch = raw[pos]
            if ch == "{":
                if depth == 0:
                    obj_start = pos
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and obj_start is not None:
                    try:
                        item = json.loads(raw[obj_start : pos + 1])
                        items.append(item)
                    except json.JSONDecodeError:
                        pass
                    obj_start = None
            elif ch == "]" and depth == 0:
                break
            pos += 1
        result[key] = items
    return result


def _parse_json_safe(raw: str) -> tuple:
    """
    Multi-stage JSON parser with recovery.
    Returns (parsed_dict, parse_error: bool).
    parse_error=True means recovery was needed or all attempts failed.
    """
    if not raw:
        return {}, True

    # Stage 1: strip markdown fences
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?\s*```\s*$", "", text.rstrip())
        text = text.strip()

    # Stage 2: direct parse — the happy path
    try:
        return json.loads(text), False
    except json.JSONDecodeError:
        pass

    logger.warning(
        "inventory: direct JSON parse failed, attempting recovery. "
        "raw_len=%d preview=%r",
        len(raw), raw[:120],
    )

    # Stage 3: extract between first { and last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidate = text[start : end + 1]

        # Stage 3a: parse extracted substring
        try:
            return json.loads(candidate), True
        except json.JSONDecodeError:
            pass

        # Stage 3b: remove trailing commas before } or ]
        cleaned = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            return json.loads(cleaned), True
        except json.JSONDecodeError:
            pass

        # Stage 3c: escape unescaped control characters in string values
        fixed = _fix_json_strings(cleaned)
        try:
            return json.loads(fixed), True
        except json.JSONDecodeError:
            pass

    # Stage 4: partial recovery — extract complete objects from truncated arrays
    partial = _recover_partial_arrays(text)
    if partial.get("equipment") or partial.get("stock"):
        logger.warning(
            "inventory: partial recovery succeeded: equipment=%d stock=%d",
            len(partial.get("equipment", [])), len(partial.get("stock", [])),
        )
        return partial, True

    logger.error(
        "inventory: all JSON recovery attempts failed. raw_len=%d preview=%r",
        len(raw), raw[:300],
    )
    return {}, True


def _normalise_items(items: list, confidence: float) -> list:
    """Ensure each item is a dict and has a confidence score."""
    result = []
    for item in items:
        if isinstance(item, dict):
            item.setdefault("confidence", confidence)
            result.append(item)
    return result


# ---------------------------------------------------------------------------
# Text chunking for large PDFs
# ---------------------------------------------------------------------------

def _split_text_chunks(text: str, chunk_size: int = _TEXT_CHUNK_SIZE) -> list:
    """Split text into chunks at newline boundaries."""
    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= chunk_size:
            chunks.append(remaining)
            break
        # Break at the last newline within the chunk window
        break_at = remaining.rfind("\n", 0, chunk_size)
        if break_at < chunk_size // 2:
            break_at = chunk_size
        chunks.append(remaining[:break_at])
        remaining = remaining[break_at:].lstrip("\n")
    return [c for c in chunks if c.strip()]


def _call_claude_inventory(content_text: str, max_tokens: int) -> tuple:
    """Single Claude text call. Returns (result_dict, parse_error)."""
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        system=[{"type": "text", "text": _INVENTORY_EXTRACT_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content_text}],
        timeout=90.0,
    )
    raw = response.content[0].text if response.content else ""
    return _parse_json_safe(raw)


def extract_inventory_from_text(text: str) -> dict:
    """
    Extract inventory from plain text via Claude.
    Returns {"equipment": [...], "stock": [...], "parse_error": bool}.
    parse_error=True signals that JSON recovery was needed or some records
    may be missing — the caller should surface a partial-success warning.
    """
    if not text.strip():
        return {"equipment": [], "stock": [], "parse_error": False}

    any_parse_error = False
    all_equipment: list = []
    all_stock: list = []

    try:
        if len(text) <= _TEXT_CHUNK_SIZE:
            # Single call — raise max_tokens high enough to fit the full output
            result, parse_error = _call_claude_inventory(text, _TOKENS_SINGLE_CALL)
            any_parse_error = parse_error
            all_equipment = _normalise_items(result.get("equipment") or [], 0.7)
            all_stock = _normalise_items(result.get("stock") or [], 0.7)
            logger.info(
                "inventory text (single): doc_type=%r equipment=%d stock=%d parse_error=%s",
                result.get("doc_type"), len(all_equipment), len(all_stock), parse_error,
            )
        else:
            # Chunked path for large documents
            chunks = _split_text_chunks(text, _TEXT_CHUNK_SIZE)
            logger.info("inventory text (chunked): chunks=%d total_chars=%d", len(chunks), len(text))
            for idx, chunk in enumerate(chunks):
                try:
                    result, parse_error = _call_claude_inventory(chunk, _TOKENS_PER_CHUNK)
                    if parse_error:
                        logger.warning(
                            "inventory text: inventory_json_parse_failed=True chunk_index=%d/%d",
                            idx + 1, len(chunks),
                        )
                        any_parse_error = True
                    eq = _normalise_items(result.get("equipment") or [], 0.7)
                    st = _normalise_items(result.get("stock") or [], 0.7)
                    all_equipment.extend(eq)
                    all_stock.extend(st)
                    logger.info(
                        "inventory text: chunk_index=%d/%d equipment=%d stock=%d parse_error=%s",
                        idx + 1, len(chunks), len(eq), len(st), parse_error,
                    )
                except Exception as exc:
                    logger.warning(
                        "inventory text: chunk_index=%d/%d inventory_json_parse_failed=True skipped error=%s",
                        idx + 1, len(chunks), exc,
                    )
                    any_parse_error = True

    except Exception as exc:
        logger.exception("inventory text extraction failed: %s", exc)
        return {"equipment": [], "stock": [], "parse_error": True}

    partial_records_imported = len(all_equipment) + len(all_stock)
    logger.info(
        "inventory text complete: partial_records_imported=%d equipment=%d stock=%d any_parse_error=%s",
        partial_records_imported, len(all_equipment), len(all_stock), any_parse_error,
    )
    return {"equipment": all_equipment, "stock": all_stock, "parse_error": any_parse_error}


def extract_inventory_from_images(image_paths: list) -> dict:
    """
    Extract inventory from image files one page at a time to avoid JSON truncation.
    Returns {"equipment": [...], "stock": [...], "parse_error": bool}.
    """
    if not image_paths:
        return {"equipment": [], "stock": [], "parse_error": False}

    all_equipment: list = []
    all_stock: list = []
    any_parse_error = False

    for chunk_idx, path in enumerate(image_paths):
        media_type = "image/png" if path.lower().endswith(".png") else "image/jpeg"
        try:
            with open(path, "rb") as f:
                img_data = base64.b64encode(f.read()).decode()
        except Exception as exc:
            logger.warning(
                "inventory image: chunk_index=%d read_failed path=%s: %s",
                chunk_idx, path, exc,
            )
            any_parse_error = True
            continue

        content = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": img_data},
            },
            {
                "type": "text",
                "text": (
                    "Extract structured inventory data from this page. "
                    "Return only the raw JSON object — start with { and end with }. "
                    "No markdown, no code fences, no commentary."
                ),
            },
        ]

        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=_TOKENS_PER_CHUNK,
                system=[{"type": "text", "text": _INVENTORY_EXTRACT_PROMPT, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": content}],
                timeout=90.0,
            )
            raw = response.content[0].text if response.content else ""
            result, parse_error = _parse_json_safe(raw)
            if parse_error:
                logger.warning(
                    "inventory image: inventory_json_parse_failed=True chunk_index=%d",
                    chunk_idx,
                )
                any_parse_error = True
            eq = _normalise_items(result.get("equipment") or [], 0.65)
            st = _normalise_items(result.get("stock") or [], 0.65)
            all_equipment.extend(eq)
            all_stock.extend(st)
            logger.info(
                "inventory image: chunk_index=%d equipment=%d stock=%d parse_error=%s",
                chunk_idx, len(eq), len(st), parse_error,
            )
        except Exception as exc:
            logger.warning(
                "inventory image: chunk_index=%d inventory_json_parse_failed=True skipped error=%s",
                chunk_idx, exc,
            )
            any_parse_error = True

    partial_records_imported = len(all_equipment) + len(all_stock)
    logger.info(
        "inventory image complete: partial_records_imported=%d equipment=%d stock=%d "
        "any_parse_error=%s pages=%d",
        partial_records_imported, len(all_equipment), len(all_stock),
        any_parse_error, len(image_paths),
    )
    return {"equipment": all_equipment, "stock": all_stock, "parse_error": any_parse_error}


# ---------------------------------------------------------------------------
# doc_record builder (compatible with _dispatch_doc_record)
# ---------------------------------------------------------------------------

def make_inventory_doc_record(data: dict, doc_type: str, file_path: str) -> dict:
    fp = hashlib.md5(file_path.encode()).hexdigest()
    return {
        "document_id": str(uuid.uuid4()),
        "file_path": file_path,
        "doc_type": doc_type,
        "supplier_name": "",
        "document_number": "",
        "reference_number": "",
        "document_date": "",
        "currency": "",
        "total": None,
        "subtotal": None,
        "tax": None,
        "line_items": [],
        "exclusions": [],
        "assumptions": [],
        "fingerprint": fp,
        "billing_address": {},
        "delivery_address": {},
        "status": "new",
        "inventory_data": data,
    }


# ---------------------------------------------------------------------------
# WhatsApp response formatter
# ---------------------------------------------------------------------------

_INVENTORY_NEEDS_REVIEW = (
    "DECISION:\nINVENTORY EXTRACTION NEEDS REVIEW\n\n"
    "WHY:\nI could read the file but could not safely structure all equipment records.\n\n"
    "RECOMMENDED ACTIONS:\n"
    "• Try exporting the list as Excel or CSV\n"
    "• Or upload the equipment list in smaller sections"
)


def format_inventory_response(
    eq_added: int,
    eq_merged: int,
    st_added: int,
    st_merged: int,
    parse_error: bool = False,
    skipped_rows: int = 0,
    st_linked: int = 0,
    yacht_id: str = "H3",
) -> str:
    eq_total = eq_added + eq_merged
    st_total = st_added + st_merged
    total_records = eq_total + st_total

    # Total failure — no records extracted regardless of parse state
    if total_records == 0:
        return _INVENTORY_NEEDS_REVIEW

    skip_note = f" Skipped {skipped_rows} header/section rows." if skipped_rows > 0 else ""
    yid = yacht_id.upper()

    _eq_actions = (
        "ACTIONS:\n"
        "• Ask \"show equipment\"\n"
        "• Ask \"what equipment do we have from <make>?\"\n"
        "• Ask \"what is <model/serial>?\""
    )

    # Equipment-only import — use focused equipment messages
    if eq_total > 0 and st_total == 0:
        if parse_error:
            return (
                "DECISION:\nEQUIPMENT LIST PARTIALLY IMPORTED\n\n"
                f"WHY:\nI imported {eq_total} equipment records, "
                f"but some rows could not be structured safely.{skip_note}\n\n"
                "ACTIONS:\n"
                "• Upload Excel or CSV for better results\n"
                "• Or upload the list in smaller sections"
            )
        if eq_merged > 0:
            if eq_added > 0:
                why = (
                    f"Added {eq_added} new equipment records and updated {eq_merged} "
                    f"existing records.{skip_note}"
                )
            else:
                why = (
                    f"Updated {eq_merged} existing records — no new equipment added.{skip_note}"
                )
            return f"DECISION:\nEQUIPMENT LIST UPDATED\n\nWHY:\n{why}\n\n{_eq_actions}"
        why = f"Imported {eq_added} new equipment records into vessel memory.{skip_note}"
        return f"DECISION:\nEQUIPMENT LIST IMPORTED\n\nWHY:\n{why}\n\n{_eq_actions}"

    # Stock-only import
    if st_total > 0 and eq_total == 0:
        linked_note = f" {st_linked} linked to equipment." if st_linked > 0 else ""
        partial_note = (
            "\nSome records may be missing due to formatting issues in the source."
        ) if parse_error else ""
        return (
            "DECISION:\nSTOCK IMPORTED\n\n"
            f"WHY:\nImported {st_total} stock records for {yid}.{linked_note}{partial_note}\n\n"
            "ACTIONS:\n"
            "• Ask \"show stock\"\n"
            "• Ask \"do we have <part> onboard?\"\n"
            "• Ask \"show spares for <system>\""
        )

    # Mixed equipment + stock import
    parts = []
    if eq_total:
        parts.append(f"{eq_added} new + {eq_merged} updated equipment records")
    if st_total:
        linked_note = f" ({st_linked} linked to equipment)" if st_linked > 0 else ""
        parts.append(f"{st_added} new + {st_merged} updated stock items{linked_note}")
    summary = "; ".join(parts)
    partial_note = (
        "\nSome records may be missing due to formatting issues in the source."
    ) if parse_error else ""

    return (
        "DECISION:\nINVENTORY IMPORTED\n\n"
        f"WHY:\nImported {summary}.{partial_note}\n\n"
        "ACTIONS:\n"
        "• Ask \"do we have <part> onboard?\"\n"
        "• Ask \"show spares for <system>\"\n"
        "• Ask \"show equipment\" or \"show stock\""
    )
