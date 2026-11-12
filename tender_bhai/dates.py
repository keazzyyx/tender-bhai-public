"""Indian tender date parsing. All results are timezone-aware in IST."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from tender_bhai.config import IST

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip

_DATE_RE = re.compile(
    r"(?P<d>\d{1,2})[\s\-/.]+(?P<m>[A-Za-z]{3,9}|\d{1,2})[\s\-/.,]+(?P<y>\d{4}|\d{2})"
    r"(?:[\sT,]+(?P<h>\d{1,2})[:.](?P<mi>\d{2})(?:[:.](?P<s>\d{2}))?\s*(?P<ap>[APap]\.?[Mm]\.?)?(?:\s*(?:hrs|hours|IST))?)?"
)
_ISO_RE = re.compile(
    r"(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})(?:[T ](?P<h>\d{2}):(?P<mi>\d{2})(?::(?P<s>\d{2}))?(?:\.\d+)?(?P<tz>Z|[+-]\d{2}:?\d{2})?)?"
)


def _month(token: str) -> int | None:
    if token.isdigit():
        value = int(token)
        return value if 1 <= value <= 12 else None
    return _MONTHS.get(token.lower()[:4]) or _MONTHS.get(token.lower()[:3])


def parse_indian_datetime(text: str | None) -> datetime | None:
    """Parse "14-Nov-2026 15:00", "14/11/2026", "14.11.2026 03:00 PM", ISO strings.

    Dates without a time default to 23:59 IST (end of the business day).
    Returns None when nothing parseable is found.
    """
    if not text:
        return None
    text = text.strip()
    if not text or text.lower() in {"na", "n/a", "nil", "-", "--"}:
        return None

    iso = _ISO_RE.search(text)
    if iso:
        try:
            parsed = datetime.fromisoformat(iso.group(0).replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=IST)
            return parsed.astimezone(IST)

    match = _DATE_RE.search(text)
    if not match:
        return None
    day = int(match.group("d"))
    month = _month(match.group("m"))
    year = int(match.group("y"))
    if year < 100:
        year += 2000
    if month is None:
        return None
    hour, minute, second = 23, 59, 0
    if match.group("h"):
        hour = int(match.group("h"))
        minute = int(match.group("mi"))
        second = int(match.group("s") or 0)
        ampm = (match.group("ap") or "").replace(".", "").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=IST)
    except ValueError:
        return None


def to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=IST)
    return value.astimezone(IST).isoformat(timespec="minutes")


def now_ist() -> datetime:
    return datetime.now(tz=IST)


def days_until(deadline: datetime | None, reference: datetime | None = None) -> int | None:
    """Whole days from now (IST) until the deadline, negative when passed."""
    if deadline is None:
        return None
    reference = reference or now_ist()
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=IST)
    delta = deadline.astimezone(IST).date() - reference.astimezone(IST).date()
    return delta.days


def format_deadline(deadline: datetime | None, reference: datetime | None = None) -> str:
    if deadline is None:
        return "deadline not stated"
    remaining = days_until(deadline, reference)
    stamp = deadline.astimezone(IST).strftime("%d %b %Y, %I:%M %p").replace(" 0", " ")
    if remaining is None:
        return stamp
    if remaining < 0:
        return f"{stamp} (closed)"
    if remaining == 0:
        return f"{stamp} (today!)"
    if remaining == 1:
        return f"{stamp} (1 day)"
    return f"{stamp} ({remaining} days)"


def ist_date_label(day: date | None = None) -> str:
    day = day or now_ist().date()
    return day.strftime("%a, %d %b %Y")


def window_start(period: str, reference: datetime | None = None) -> datetime:
    reference = reference or now_ist()
    period = period.lower().strip()
    if period in {"today", "day"}:
        return reference.replace(hour=0, minute=0, second=0, microsecond=0)
    if period in {"week", "7d", "this week"}:
        return reference - timedelta(days=7)
    if period in {"month", "30d", "this month"}:
        return reference - timedelta(days=30)
    return reference - timedelta(days=1)
