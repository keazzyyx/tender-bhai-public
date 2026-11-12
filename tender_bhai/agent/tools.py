"""Tool definitions and implementations for the chat agent."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from tender_bhai.dates import format_deadline, now_ist, parse_indian_datetime, to_iso, window_start
from tender_bhai.db import Database
from tender_bhai.models import Tender
from tender_bhai.money import format_inr, parse_inr

log = logging.getLogger(__name__)

LAST_DIGEST_KEY = "last_digest_ordinals"
PREFERENCE_KEYS = {"min_value", "ignore_states", "only_states", "extra_keywords", "ignore_keywords"}

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "search_tenders",
        "description": "Search stored tenders by free text, state, minimum value and closing date. Returns open tenders only.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free text: item, buyer, keyword. Optional."},
                "state": {"type": "string", "description": "Indian state/UT or city. Optional."},
                "min_value": {"type": "number", "description": "Minimum estimated value in rupees. Optional."},
                "closing_before": {
                    "type": "string",
                    "description": "ISO date; only tenders closing on/before it. Optional.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
        },
    },
    {
        "name": "get_tender",
        "description": "Full details of one tender by its database id, portal tender id, or its ordinal (1, 2, 3...) in the latest digest.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "Database id, portal tender id, or digest ordinal like '2'"}
            },
            "required": ["ref"],
        },
    },
    {
        "name": "closing_soon",
        "description": "Tenders (already sent or new) closing within the next N days, soonest first.",
        "input_schema": {
            "type": "object",
            "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 60}},
            "required": ["days"],
        },
    },
    {
        "name": "set_preference",
        "description": (
            "Persist a preference for the nightly search. Keys: min_value (rupees), ignore_states (list),"
            " only_states (list), extra_keywords (list), ignore_keywords (list). action: set | add | remove."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "enum": sorted(PREFERENCE_KEYS)},
                "value": {"description": "Number for min_value; string or list of strings for list keys."},
                "action": {"type": "string", "enum": ["set", "add", "remove"]},
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "stats",
        "description": "Counts of tenders found/sent and run health for a period: today, week or month.",
        "input_schema": {
            "type": "object",
            "properties": {"period": {"type": "string", "enum": ["today", "week", "month"]}},
            "required": ["period"],
        },
    },
    {
        "name": "draft_bid_cover_letter",
        "description": "Draft a formal bid cover letter for one tender using the company facts provided.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tender_id": {"type": "string", "description": "Database id, portal id or digest ordinal"},
                "company_facts": {
                    "type": "string",
                    "description": "Extra facts: turnover, certifications, past supplies, signatory",
                },
            },
            "required": ["tender_id"],
        },
    },
]

SLOW_TOOLS = {"draft_bid_cover_letter"}


def tender_brief(tender: Tender, ordinal: int | None = None) -> dict[str, Any]:
    prefix = f"{ordinal}. " if ordinal else ""
    return {
        "id": tender.id,
        "label": f"{prefix}{tender.buyer_org}",
        "title": tender.title,
        "items": [i.name + (f" x {i.qty}" if i.qty else "") for i in tender.items[:5]],
        "value": format_inr(tender.estimated_value_inr),
        "emd": format_inr(tender.emd_inr) if tender.emd_inr else None,
        "closes": format_deadline(tender.deadline),
        "state": tender.state,
        "fit": tender.fit,
        "fit_reason": tender.fit_reason,
        "link": tender.portal_url,
    }


def tender_full(tender: Tender) -> dict[str, Any]:
    data = tender_brief(tender)
    data.update(
        {
            "portal": tender.portal,
            "portal_tender_id": tender.tender_id,
            "department": tender.department,
            "items_detail": [i.model_dump() for i in tender.items],
            "opening_date": format_deadline(tender.opening_date) if tender.opening_date else None,
            "location": tender.location,
            "contact": tender.contact.model_dump(exclude_none=True),
            "eligibility": tender.eligibility.model_dump(exclude_none=True),
            "document_url": tender.document_url,
            "summary": tender.summary,
            "status": tender.status,
            "first_seen": to_iso(tender.first_seen_at),
        }
    )
    return data


class ToolExecutor:
    def __init__(self, db: Database, letter_writer: Any | None = None) -> None:
        self.db = db
        self.letter_writer = letter_writer

    # -- dispatch -----------------------------------------------------------------
    def run(self, name: str, args: dict[str, Any]) -> str:
        handler = getattr(self, f"tool_{name}", None)
        if handler is None:
            return json.dumps({"error": f"unknown tool {name}"})
        try:
            result = handler(**args)
        except TypeError as exc:
            result = {"error": f"bad arguments: {exc}"}
        except Exception as exc:  # the agent must keep talking even if a tool breaks
            log.exception("tool %s failed", name)
            result = {"error": f"{type(exc).__name__}: {exc}"}
        return json.dumps(result, ensure_ascii=False, default=str)

    # -- helpers -------------------------------------------------------------------
    def resolve(self, ref: str) -> Tender | None:
        ref = (ref or "").strip()
        if not ref:
            return None
        ordinal = re.fullmatch(r"#?(\d{1,2})", ref)
        if ordinal:
            mapping = self.db.get_preference(LAST_DIGEST_KEY) or {}
            ids = mapping.get("ids") or []
            index = int(ordinal.group(1)) - 1
            if 0 <= index < len(ids):
                return self.db.get_tender(ids[index])
            return None
        if re.fullmatch(r"[0-9a-fA-F-]{36}", ref):
            return self.db.get_tender(ref)
        rows = self.db.select("tenders", select="*", tender_id=f"eq.{ref}", limit=1)
        return Tender.model_validate(rows[0]) if rows else None

    # -- tools ---------------------------------------------------------------------
    def tool_search_tenders(
        self,
        query: str | None = None,
        state: str | None = None,
        min_value: float | None = None,
        closing_before: str | None = None,
        limit: int = 8,
    ) -> dict[str, Any]:
        before = parse_indian_datetime(closing_before) if closing_before else None
        tenders = self.db.search_tenders(
            query=query, state=state, min_value=min_value, closing_before=before, limit=limit
        )
        return {"count": len(tenders), "tenders": [tender_brief(t) for t in tenders]}

    def tool_get_tender(self, ref: str) -> dict[str, Any]:
        tender = self.resolve(ref)
        if tender is None:
            return {"error": f"No tender found for '{ref}'. Ask him to check the number in the latest digest."}
        return tender_full(tender)

    def tool_closing_soon(self, days: int = 7) -> dict[str, Any]:
        tenders = self.db.closing_soon(days, sent_only=False)
        return {"days": days, "count": len(tenders), "tenders": [tender_brief(t) for t in tenders]}

    def tool_set_preference(self, key: str, value: Any, action: str = "set") -> dict[str, Any]:
        if key not in PREFERENCE_KEYS:
            return {"error": f"unknown preference {key}"}
        if key == "min_value":
            amount = parse_inr(value) if not isinstance(value, (int, float)) else float(value)
            if not amount:
                return {"error": f"could not understand amount {value!r}"}
            self.db.set_preference(key, int(amount))
            return {"ok": True, "key": key, "value": int(amount), "display": format_inr(amount)}
        incoming = value if isinstance(value, list) else [value]
        incoming = [str(v).strip() for v in incoming if str(v).strip()]
        current = self.db.get_preference(key) or []
        if not isinstance(current, list):
            current = []
        if action == "add":
            merged = current + [v for v in incoming if v.lower() not in {c.lower() for c in current}]
        elif action == "remove":
            drop = {v.lower() for v in incoming}
            merged = [c for c in current if c.lower() not in drop]
        else:
            merged = incoming
        self.db.set_preference(key, merged)
        return {"ok": True, "key": key, "value": merged}

    def tool_stats(self, period: str = "week") -> dict[str, Any]:
        since = window_start(period)
        runs = self.db.recent_runs(since)
        failed_portals: dict[str, int] = {}
        for run in runs:
            for err in run.get("errors") or []:
                portal = err.get("portal", "?") if isinstance(err, dict) else "?"
                failed_portals[portal] = failed_portals.get(portal, 0) + 1
        return {
            "period": period,
            "since": to_iso(since),
            "tenders_found": self.db.count_tenders(since),
            "tenders_sent": self.db.count_tenders(since, sent=True),
            "runs": len(runs),
            "last_run": runs[0].get("finished_at") if runs else None,
            "last_run_status": runs[0].get("status") if runs else None,
            "portal_failures": failed_portals,
        }

    def tool_draft_bid_cover_letter(self, tender_id: str, company_facts: str = "") -> dict[str, Any]:
        tender = self.resolve(tender_id)
        if tender is None:
            return {"error": f"No tender found for '{tender_id}'"}
        if self.letter_writer is None:
            return {"error": "letter drafting is not configured"}
        letter = self.letter_writer(tender, company_facts)
        return {"tender": tender_brief(tender), "letter": letter}


def days_from_now(days: int) -> datetime:
    return now_ist() + timedelta(days=days)
