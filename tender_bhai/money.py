"""Rupee amount parsing and formatting (lakh / crore aware)."""

from __future__ import annotations

import re

_NUM = r"(?P<num>\d[\d,]*(?:\.\d+)?)"
_AMOUNT_RE = re.compile(
    rf"(?:₹|rs\.?|inr|rupees)?\s*{_NUM}\s*(?P<unit>crores?|cr\.?|lakhs?|lacs?|lakh|lac|l\b|k\b|thousand|million|mn)?",
    re.IGNORECASE,
)
_UNIT_MULT = {
    "crore": 10_000_000, "crores": 10_000_000, "cr": 10_000_000, "cr.": 10_000_000,
    "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000, "l": 100_000,
    "k": 1_000, "thousand": 1_000, "million": 1_000_000, "mn": 1_000_000,
}  # fmt: skip


def parse_inr(text: str | float | int | None) -> float | None:
    """Parse "₹ 6,50,000", "Rs. 12.5 Lakh", "1.2 Cr", "6500000.00" into rupees.

    Returns None for blanks, "NA", "Refer document", or zero.
    """
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text) if text > 0 else None
    cleaned = text.strip()
    if not cleaned:
        return None
    lowered = cleaned.lower()
    if lowered in {"na", "n/a", "nil", "-", "--", "0", "0.00", "not applicable"}:
        return None
    match = _AMOUNT_RE.search(cleaned)
    if not match:
        return None
    number = float(match.group("num").replace(",", ""))
    unit = (match.group("unit") or "").lower()
    number *= _UNIT_MULT.get(unit, 1)
    return number if number > 0 else None


def format_inr(amount: float | None) -> str:
    """Format rupees Indian-style: ₹6.5 lakh, ₹1.2 crore, ₹45,000."""
    if amount is None or amount <= 0:
        return "value not stated"
    if amount >= 10_000_000:
        value = amount / 10_000_000
        return f"₹{value:.2f}".rstrip("0").rstrip(".") + " crore"
    if amount >= 100_000:
        value = amount / 100_000
        return f"₹{value:.2f}".rstrip("0").rstrip(".") + " lakh"
    return "₹" + indian_group(round(amount))


def indian_group(value: int) -> str:
    text = str(abs(value))
    if len(text) <= 3:
        grouped = text
    else:
        head, tail = text[:-3], text[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        grouped = ",".join(parts) + "," + tail
    return ("-" if value < 0 else "") + grouped
