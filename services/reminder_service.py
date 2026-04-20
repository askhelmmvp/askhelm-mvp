"""
Reminder service for AskHelm.

Storage:   STORAGE_DIR/reminders/reminders.json — a JSON list of reminder dicts.
Timezone:  Default Europe/London (override via REMINDER_TZ env var).
Scheduler: Single daemon thread polling every 60 s.

NOTE: The threading.Lock is process-local. Safe with Gunicorn --workers 1.
      For multi-worker deployments, replace with a file-advisory lock or a
      proper queue/database.
"""

import re
import json
import uuid
import logging
import threading
import calendar
import time as time_module
from datetime import date, time, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Optional, Tuple, List

import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

from config import STORAGE_DIR

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REMINDERS_FILE = STORAGE_DIR / "reminders" / "reminders.json"
DEFAULT_TZ_NAME = os.environ.get("REMINDER_TZ", "Europe/London")
DEFAULT_TZ = ZoneInfo(DEFAULT_TZ_NAME)
DEFAULT_REMINDER_HOUR = 9   # used when no time is given (e.g. "next week")
POLL_INTERVAL_SECONDS = 60

_TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
_TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
_TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER")

_file_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Persistent storage
# ---------------------------------------------------------------------------

def _load_raw() -> List[dict]:
    if not REMINDERS_FILE.exists():
        return []
    with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_raw(reminders: List[dict]) -> None:
    REMINDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(reminders, f, indent=2)


def load_reminders() -> List[dict]:
    with _file_lock:
        return _load_raw()


def save_reminder(reminder: dict) -> None:
    with _file_lock:
        existing = _load_raw()
        existing.append(reminder)
        _save_raw(existing)


# ---------------------------------------------------------------------------
# Date/time helpers
# ---------------------------------------------------------------------------

_MONTH_NAMES: dict = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

_WEEKDAY_NAMES: dict = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


def _now_local() -> datetime:
    return datetime.now(DEFAULT_TZ)


def _parse_time_str(s: str) -> Optional[time]:
    """Parse time from: 0800, 08:00, 8am, 14:30, 2pm, 8:30am."""
    s = s.strip().lower()
    # "HH:MM" with optional am/pm
    m = re.match(r'^(\d{1,2}):(\d{2})\s*(am|pm)?$', s)
    if m:
        h, mins, ampm = int(m.group(1)), int(m.group(2)), m.group(3)
        if ampm == "pm" and h < 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        if 0 <= h <= 23 and 0 <= mins <= 59:
            return time(h, mins)
    # "HHMM" 4-digit no colon
    m = re.match(r'^(\d{4})$', s)
    if m:
        h, mins = int(s[:2]), int(s[2:])
        if 0 <= h <= 23 and 0 <= mins <= 59:
            return time(h, mins)
    # "Ham" or "H:MMam/pm"
    m = re.match(r'^(\d{1,2})(?::(\d{2}))?\s*(am|pm)$', s)
    if m:
        h, mins, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if ampm == "pm" and h < 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        if 0 <= h <= 23 and 0 <= mins <= 59:
            return time(h, mins)
    return None


def _combine_local(d: date, t: time) -> datetime:
    """Combine date + time in DEFAULT_TZ, return a tz-aware datetime."""
    return datetime(d.year, d.month, d.day, t.hour, t.minute, tzinfo=DEFAULT_TZ)


def _next_weekday_date(weekday: int) -> date:
    """Next date (strictly after today) with the given weekday number (0=Monday)."""
    today = _now_local().date()
    days = (weekday - today.weekday()) % 7 or 7
    return today + timedelta(days=days)


def _add_one_month(d: date) -> date:
    month = d.month + 1
    year = d.year
    if month > 12:
        month = 1
        year += 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


# ---------------------------------------------------------------------------
# Core parser
# ---------------------------------------------------------------------------

_MONTH_PAT = "|".join(_MONTH_NAMES.keys())
_WEEKDAY_PAT = "|".join(_WEEKDAY_NAMES.keys())


def parse_datetime_and_text(s: str) -> Tuple[Optional[datetime], str]:
    """
    Parse a due-datetime from the start of s.
    Returns (due_datetime_utc_aware, reminder_text).
    Returns (None, s) when the date/time cannot be parsed.

    Supported forms:
      in N hours/minutes
      tomorrow [TIME]
      next Monday/Tue/.../week/month [TIME]
      DD Month [YYYY] [TIME]
    When TIME is absent, DEFAULT_REMINDER_HOUR is used (09:00).
    """
    s = s.strip()
    now = _now_local()

    # ── "in N hours / minutes" ────────────────────────────────────────────────
    m = re.match(
        r'^in\s+(\d+)\s+(hours?|hrs?|minutes?|mins?)\b\s*(.*)',
        s, re.IGNORECASE | re.DOTALL,
    )
    if m:
        n, unit, text = int(m.group(1)), m.group(2).lower(), m.group(3).strip()
        delta = timedelta(hours=n) if unit[:2] in ("ho", "hr") else timedelta(minutes=n)
        return now + delta, text

    # ── "tomorrow [TIME] ..." ─────────────────────────────────────────────────
    m = re.match(r'^tomorrow\b\s*(\S+)?\s*(.*)', s, re.IGNORECASE | re.DOTALL)
    if m:
        time_tok, rest = (m.group(1) or "").strip(), m.group(2).strip()
        t = _parse_time_str(time_tok) if time_tok else None
        if t is None:
            # token is not a time → treat as part of text
            t = time(DEFAULT_REMINDER_HOUR, 0)
            text = (time_tok + (" " + rest if rest else "")).strip()
        else:
            text = rest
        d = (now + timedelta(days=1)).date()
        return _combine_local(d, t), text

    # ── "next <weekday|week|month> [TIME] ..." ────────────────────────────────
    m = re.match(
        rf'^next\s+({_WEEKDAY_PAT}|week|month)\b\s*(\S+)?\s*(.*)',
        s, re.IGNORECASE | re.DOTALL,
    )
    if m:
        spec = m.group(1).lower()
        time_tok, rest = (m.group(2) or "").strip(), m.group(3).strip()
        t = _parse_time_str(time_tok) if time_tok else None
        if t is None:
            t = time(DEFAULT_REMINDER_HOUR, 0)
            text = (time_tok + (" " + rest if rest else "")).strip()
        else:
            text = rest
        if spec == "week":
            d = (now + timedelta(weeks=1)).date()
        elif spec == "month":
            d = _add_one_month(now.date())
        else:
            d = _next_weekday_date(_WEEKDAY_NAMES[spec])
        return _combine_local(d, t), text

    # ── "DD MonthName [YYYY] [TIME] ..." ─────────────────────────────────────
    m = re.match(
        rf'^(\d{{1,2}})\s+({_MONTH_PAT})\b\s*(\d{{4}})?\s*(\S+)?\s*(.*)',
        s, re.IGNORECASE | re.DOTALL,
    )
    if m:
        day = int(m.group(1))
        month = _MONTH_NAMES[m.group(2).lower()]
        year_str = m.group(3)
        if year_str and int(year_str) >= 1900:
            year = int(year_str)
            time_tok = (m.group(4) or "").strip()
            rest = m.group(5).strip()
        else:
            # "0830" matched the \d{4} group but is a time, not a year
            year = now.year
            time_tok = (year_str or m.group(4) or "").strip()
            rest = ((m.group(4) if year_str else m.group(5)) or "").strip()
            if year_str:
                rest = ((m.group(4) or "") + (" " + m.group(5) if m.group(5) else "")).strip()
        t = _parse_time_str(time_tok) if time_tok else None
        if t is None:
            t = time(DEFAULT_REMINDER_HOUR, 0)
            text = (time_tok + (" " + rest if rest else "")).strip()
        else:
            text = rest
        try:
            d = date(year, month, day)
            return _combine_local(d, t), text
        except ValueError:
            pass

    return None, s


# ---------------------------------------------------------------------------
# Command prefix stripping
# ---------------------------------------------------------------------------

# Ordered longest-first so "remind me to " is tried before "remind me "
_REMINDER_PREFIXES = [
    "!remind me to ",
    "!remind me ",
    "!remindme ",
    "remind me to ",
    "remind me ",
    "remindme ",
    "set a reminder to ",
    "set a reminder for ",
    "set a reminder ",
    "set reminder to ",
    "set reminder for ",
    "set reminder ",
    "!remindme",    # bare (no trailing space)
    "!remind me",
]


def strip_reminder_prefix(message: str) -> Optional[str]:
    """
    Strip the reminder command prefix from message.
    Returns the remainder (datetime + text), or None if not a reminder command.
    """
    lower = message.strip().lower()
    for prefix in _REMINDER_PREFIXES:
        if lower.startswith(prefix):
            return message.strip()[len(prefix):].strip()
    return None


def is_reminder_command(message: str) -> bool:
    return strip_reminder_prefix(message) is not None


# ---------------------------------------------------------------------------
# Reminder creation
# ---------------------------------------------------------------------------

def create_reminder(
    phone: str,
    due_at: datetime,
    text: str,
    tz_name: str = DEFAULT_TZ_NAME,
) -> dict:
    """Create, persist, and return a new reminder dict."""
    due_utc = due_at.astimezone(timezone.utc)
    reminder = {
        "reminder_id": str(uuid.uuid4()),
        "phone": phone,
        "text": text,
        "due_at": due_utc.isoformat(),
        "timezone": tz_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
    }
    save_reminder(reminder)
    logger.info(
        "Reminder created: id=%s phone=%s due=%s text=%r",
        reminder["reminder_id"], phone, reminder["due_at"], text,
    )
    return reminder


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_due_datetime(due_at: datetime, tz_name: str = DEFAULT_TZ_NAME) -> str:
    """Format a UTC-aware datetime as a human-readable string in the reminder timezone."""
    tz = ZoneInfo(tz_name)
    local = due_at.astimezone(tz)
    dow = local.strftime("%A")
    day = local.day
    month_name = local.strftime("%B")
    year = local.year
    hm = local.strftime("%H:%M")
    return f"{dow} {day} {month_name} {year} at {hm}"


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

def _send_reminder_message(phone: str, text: str) -> None:
    if not (_TWILIO_FROM_NUMBER and _TWILIO_ACCOUNT_SID and _TWILIO_AUTH_TOKEN):
        logger.warning("Reminder send skipped — Twilio credentials not configured")
        return
    from twilio.rest import Client as TwilioRestClient
    client = TwilioRestClient(_TWILIO_ACCOUNT_SID, _TWILIO_AUTH_TOKEN)
    body = f"⚓ AskHelm Reminder\n\n{text}"
    client.messages.create(from_=_TWILIO_FROM_NUMBER, to=phone, body=body)
    logger.info("Reminder sent: phone=%s body_length=%d", phone, len(body))


def process_due_reminders() -> int:
    """
    Find all pending reminders whose due_at has passed and send them.
    Updates status to 'sent' or 'failed' in place.
    Returns the number of reminders attempted.
    """
    now_utc = datetime.now(timezone.utc)
    with _file_lock:
        reminders = _load_raw()
        attempted = 0
        for r in reminders:
            if r.get("status") != "pending":
                continue
            try:
                due = datetime.fromisoformat(r["due_at"])
            except (ValueError, KeyError):
                continue
            if due > now_utc:
                continue
            attempted += 1
            try:
                _send_reminder_message(r["phone"], r["text"])
                r["status"] = "sent"
                logger.info(
                    "Reminder delivered: id=%s phone=%s text=%r",
                    r["reminder_id"], r["phone"], r["text"],
                )
            except Exception as exc:
                r["status"] = "failed"
                logger.exception(
                    "Reminder delivery failed: id=%s %s", r["reminder_id"], exc
                )
        if attempted:
            _save_raw(reminders)
    return attempted


# ---------------------------------------------------------------------------
# Background scheduler
# ---------------------------------------------------------------------------

_scheduler_thread: Optional[threading.Thread] = None


def _scheduler_loop() -> None:
    logger.info("Reminder scheduler started (poll_interval=%ds)", POLL_INTERVAL_SECONDS)
    while True:
        try:
            n = process_due_reminders()
            if n:
                logger.info("Scheduler: processed %d due reminder(s)", n)
        except Exception as exc:
            logger.exception("Reminder scheduler error: %s", exc)
        time_module.sleep(POLL_INTERVAL_SECONDS)


def start_reminder_scheduler() -> None:
    """Start the background reminder loop. Idempotent — safe to call multiple times."""
    global _scheduler_thread
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop,
        daemon=True,
        name="reminder-scheduler",
    )
    _scheduler_thread.start()
    logger.info("Reminder scheduler thread launched")
