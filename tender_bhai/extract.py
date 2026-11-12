"""LLM extraction + fit ranking via Anthropic tool use, validated with pydantic.

The model is forced to call ``record_tender`` whose input schema mirrors
:class:`tender_bhai.models.Extraction`. Invalid or missing output is retried once with
the validation error fed back; a second failure raises :class:`ExtractionError`.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from tender_bhai import obs
from tender_bhai.config import Settings, settings
from tender_bhai.models import Candidate, Extraction
from tender_bhai.money import format_inr

log = logging.getLogger(__name__)

MAX_INPUT_CHARS = 24000

COMPANY_PROFILE = (
    "M.J. International, Meerut (Uttar Pradesh) - manufacturer and exporter of cricket "
    "equipment (bats, balls, pads, gloves, kits, stumps, nets), sports clothing of every "
    "kind (uniforms, tracksuits, jerseys, t-shirts, shorts, school sports kits), caps and "
    "hats, and training/fitness equipment (cones, ladders, hurdles, bowling machines and "
    "balls, nets, gym and boxing gear, hockey and athletics items). They can supply pan-India "
    "and hold standard MSME/GST/ISO documentation."
)

SYSTEM_PROMPT = f"""You read Indian government tender notices and extract facts for a sports-goods supplier.

Supplier profile: {COMPANY_PROFILE}

Rules:
- Extract ONLY what the text states. Never invent values, dates, phone numbers or emails. Use null when absent.
- Amounts are in Indian rupees; convert lakh/crore to a plain number (12.5 lakh -> 1250000).
- Dates: return ISO 8601 with +05:30 (e.g. 2026-11-14T15:00+05:30). Assume IST.
- items[]: every distinct item with its quantity and key specs when given.
- fit: "strong" when the tender is mostly cricket gear, sports clothing/uniforms/kits, caps, or training/fitness equipment the supplier makes; "possible" when sports goods are part of a mixed lot or the category is adjacent (general sports equipment, PE material, school kits); "skip" when the tender is unrelated (works contracts, services, IT, furniture, medical, food, stationery, or a sports item they do not make such as electronic scoreboards or synthetic turf) or is only for a single tiny lot.
- fit_reason: one short line a busy exporter can read in two seconds.
- summary: two short lines - what is being bought and by whom, then the key condition (turnover, MSE exemption, sample, delivery place).
- state: the Indian state/UT of the buyer when derivable (from address, organisation, portal), else null.
"""

RECORD_TOOL: dict[str, Any] = {
    "name": "record_tender",
    "description": "Record the structured facts of one tender notice. Call this exactly once.",
    "input_schema": {
        "type": "object",
        "properties": {
            "buyer_org": {"type": "string", "description": "Buying organisation / office"},
            "department": {"type": ["string", "null"]},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "qty": {"type": ["string", "null"]},
                        "specs": {"type": ["string", "null"]},
                    },
                    "required": ["name"],
                },
            },
            "estimated_value_inr": {"type": ["number", "null"]},
            "emd_inr": {"type": ["number", "null"]},
            "deadline": {"type": ["string", "null"], "description": "Bid submission end, ISO 8601 IST"},
            "opening_date": {"type": ["string", "null"], "description": "Bid opening, ISO 8601 IST"},
            "state": {"type": ["string", "null"]},
            "location": {"type": ["string", "null"], "description": "City / delivery place"},
            "contact": {
                "type": "object",
                "properties": {
                    "name": {"type": ["string", "null"]},
                    "designation": {"type": ["string", "null"]},
                    "phone": {"type": ["string", "null"]},
                    "email": {"type": ["string", "null"]},
                },
            },
            "portal_url": {"type": ["string", "null"]},
            "document_url": {"type": ["string", "null"]},
            "eligibility": {
                "type": "object",
                "properties": {
                    "min_turnover": {"type": ["string", "null"]},
                    "msme_exempt": {"type": ["boolean", "null"]},
                    "past_performance": {"type": ["string", "null"]},
                    "other": {"type": ["string", "null"]},
                },
            },
            "fit": {"type": "string", "enum": ["strong", "possible", "skip"]},
            "fit_reason": {"type": "string"},
            "summary": {"type": "string"},
        },
        "required": ["buyer_org", "items", "fit", "fit_reason", "summary"],
    },
}


class ExtractionError(RuntimeError):
    pass


def build_user_prompt(candidate: Candidate, detail_text: str, pdf_text: str) -> str:
    known = {
        "portal": candidate.portal,
        "tender_id": candidate.tender_id,
        "title": candidate.title,
        "buyer_org": candidate.buyer_org or None,
        "state": candidate.state or None,
        "portal_url": candidate.detail_url or candidate.portal_url,
        "document_url": candidate.document_url,
        "listed_value": format_inr(candidate.estimated_value_inr) if candidate.estimated_value_inr else None,
        "listed_deadline": candidate.deadline.isoformat() if candidate.deadline else None,
    }
    budget = MAX_INPUT_CHARS
    listing = candidate.listing_text[: budget // 4]
    budget -= len(listing)
    detail = detail_text[: int(budget * 0.6)]
    budget -= len(detail)
    pdf = pdf_text[:budget]
    return (
        "Known listing fields (JSON):\n" + json.dumps(known, ensure_ascii=False) + "\n\n"
        "=== LISTING TEXT ===\n" + (listing or "(none)") + "\n\n"
        "=== DETAIL PAGE TEXT ===\n" + (detail or "(none)") + "\n\n"
        "=== TENDER DOCUMENT (PDF) TEXT ===\n" + (pdf or "(none)") + "\n\n"
        "Call record_tender with the extracted facts."
    )


def _tool_input(response: Any) -> dict[str, Any] | None:
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "record_tender":
            payload = getattr(block, "input", None)
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    return None
            return payload if isinstance(payload, dict) else None
    return None


class Extractor:
    def __init__(self, client: Any | None = None, cfg: Settings | None = None) -> None:
        self.cfg = cfg or settings()
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=self.cfg.anthropic_api_key or None, max_retries=3, timeout=120.0)
        self.client = client
        self.model = self.cfg.claude_model

    def _call(self, messages: list[dict[str, Any]]) -> Any:
        return self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=[RECORD_TOOL],
            tool_choice={"type": "tool", "name": "record_tender"},
            messages=messages,
        )

    @obs.span(kind="CHAIN", name="extract_rank")
    def extract(self, candidate: Candidate, detail_text: str = "", pdf_text: str = "") -> Extraction:
        prompt = build_user_prompt(candidate, detail_text, pdf_text)
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        last_error = "no record_tender call in response"
        for attempt in range(2):
            response = self._call(messages)
            payload = _tool_input(response)
            if payload is not None:
                try:
                    return Extraction.model_validate(payload)
                except ValidationError as exc:
                    last_error = str(exc)[:1500]
            if attempt == 0:
                log.info(
                    "extraction invalid for %s/%s, retrying: %s",
                    candidate.portal,
                    candidate.tender_id,
                    last_error[:200],
                )
                messages = [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": "I attempted record_tender but the input was rejected."},
                    {
                        "role": "user",
                        "content": (
                            "Your previous record_tender input was invalid:\n"
                            + last_error
                            + "\nCall record_tender again with valid JSON matching the schema exactly."
                        ),
                    },
                ]
        raise ExtractionError(f"{candidate.portal}/{candidate.tender_id}: {last_error}")
