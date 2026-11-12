"""WhatsApp-markdown digest formatting and clean splitting at 4000 chars."""

from __future__ import annotations

from datetime import datetime

from tender_bhai.dates import format_deadline, ist_date_label, now_ist
from tender_bhai.models import Tender
from tender_bhai.money import format_inr

MAX_MESSAGE_CHARS = 4000
FIT_BADGE = {"strong": "🟢 strong", "possible": "🟡 possible", "skip": "⚪ skip"}
FOOTER = "Ask me anything — e.g. 'details on 2', 'what closes this week', 'show Delhi tenders'"


def format_items(tender: Tender, limit: int = 4) -> str:
    parts: list[str] = []
    for item in tender.items[:limit]:
        text = item.name
        if item.qty:
            text += f" × {item.qty}"
        parts.append(text)
    if len(tender.items) > limit:
        parts.append(f"+{len(tender.items) - limit} more")
    return "; ".join(parts) if parts else (tender.summary.split("\n")[0] if tender.summary else "items not listed")


def format_tender(tender: Tender, ordinal: int, reference: datetime | None = None) -> str:
    lines = [f"*{ordinal}. {tender.buyer_org}*"]
    if tender.department:
        lines[0] += f" ({tender.department})"
    lines.append(f"📦 {format_items(tender)}")
    value = format_inr(tender.estimated_value_inr)
    emd = f" | EMD {format_inr(tender.emd_inr)}" if tender.emd_inr else ""
    lines.append(f"💰 {value}{emd}")
    lines.append(f"⏰ Closes: {format_deadline(tender.deadline, reference)}")
    contact = tender.contact.line()
    if contact:
        lines.append(f"📞 {contact}")
    if tender.state or tender.location:
        where = ", ".join(p for p in (tender.location, tender.state) if p)
        lines.append(f"📍 {where}")
    lines.append(f"🔗 {tender.portal_url}")
    badge = FIT_BADGE.get(tender.fit, tender.fit)
    reason = f" — {tender.fit_reason}" if tender.fit_reason else ""
    lines.append(f"{badge}{reason}")
    return "\n".join(lines)


def format_reminder(tender: Tender, reference: datetime | None = None) -> str:
    return f"• *{tender.buyer_org}* — {format_items(tender, limit=2)} — closes {format_deadline(tender.deadline, reference)}\n  {tender.portal_url}"


def build_digest(
    tenders: list[Tender],
    closing_soon: list[Tender] | None = None,
    reference: datetime | None = None,
) -> str:
    reference = reference or now_ist()
    count = len(tenders)
    header = f"🏏 *Tender Bhai — {ist_date_label(reference.date())}*\n"
    if count == 0:
        header += "No new matching tenders today."
    else:
        header += f"{count} new tender{'s' if count != 1 else ''} worth a look:"
    sections = [header]
    for index, tender in enumerate(tenders, start=1):
        sections.append(format_tender(tender, index, reference))
    reminders = [t for t in (closing_soon or []) if t.id not in {x.id for x in tenders}]
    if reminders:
        body = "\n".join(format_reminder(t, reference) for t in reminders)
        sections.append(f"⏳ *Closing in next 3 days* (sent earlier)\n{body}")
    sections.append(f"_{FOOTER}_")
    return "\n\n".join(sections)


def split_message(text: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Split on blank lines, then lines, never mid-word unless a single word exceeds the limit."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = block if not current else current + "\n\n" + block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
            current = ""
        if len(block) <= limit:
            current = block
            continue
        for line in block.split("\n"):
            candidate = line if not current else current + "\n" + line
            if len(candidate) <= limit:
                current = candidate
                continue
            if current:
                parts.append(current)
                current = ""
            while len(line) > limit:
                cut = line.rfind(" ", 0, limit)
                cut = cut if cut > 0 else limit
                parts.append(line[:cut])
                line = line[cut:].lstrip()
            current = line
    if current:
        parts.append(current)
    total = len(parts)
    if total > 1:
        parts = [f"({i}/{total})\n{p}" if len(p) + 8 <= limit else p for i, p in enumerate(parts, start=1)]
    return parts
