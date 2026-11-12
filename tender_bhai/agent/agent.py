"""Claude-powered chat loop with tool use and Supabase-backed memory."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

from tender_bhai import obs
from tender_bhai.agent.prompts import SYSTEM_PROMPT
from tender_bhai.agent.tools import SLOW_TOOLS, TOOL_DEFINITIONS, ToolExecutor
from tender_bhai.config import Settings, settings
from tender_bhai.db import Database
from tender_bhai.extract import COMPANY_PROFILE
from tender_bhai.models import Tender

log = logging.getLogger(__name__)

MAX_ROUNDS = 6
MEMORY_MESSAGES = 30
SLOW_AFTER_SECONDS = 4.0
INTERIM_TEXT = "One sec, checking…"


def _history_to_messages(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert stored messages to alternating user/assistant turns (merging same-role runs)."""
    messages: list[dict[str, Any]] = []
    for row in rows:
        role = "user" if row.get("direction") == "in" else "assistant"
        text = (row.get("text") or "").strip()
        if not text or text.startswith("[template "):
            continue
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"] += "\n" + text
        else:
            messages.append({"role": role, "content": text})
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages


class TenderAgent:
    def __init__(
        self,
        db: Database,
        client: Any | None = None,
        cfg: Settings | None = None,
        send_interim: Callable[[str], None] | None = None,
    ) -> None:
        self.cfg = cfg or settings()
        self.db = db
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=self.cfg.anthropic_api_key or None, max_retries=2, timeout=60.0)
        self.client = client
        self.model = self.cfg.claude_model
        self.send_interim = send_interim
        self.tools = ToolExecutor(db, letter_writer=self.write_cover_letter)

    # -- public -----------------------------------------------------------------------
    def reply(self, phone: str, text: str) -> str:
        with obs.trace("chat", session_id=phone):
            return self._reply_traced(phone, text)

    def _reply_traced(self, phone: str, text: str) -> str:
        runner = obs.span(kind="AGENT", name="chat_turn", role="Tender Bhai", session_id=phone)(self._run)
        return runner(phone, text)

    # -- core loop -------------------------------------------------------------------
    def _run(self, phone: str, text: str) -> str:
        history = _history_to_messages(self.db.recent_messages(phone, MEMORY_MESSAGES))
        if history and history[-1]["role"] == "user" and history[-1]["content"].endswith(text):
            messages = history
        else:
            messages = [*history, {"role": "user", "content": text}]
        started = time.monotonic()
        interim_sent = False
        final_text = ""
        for _round in range(MAX_ROUNDS):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1500,
                system=SYSTEM_PROMPT,
                tools=TOOL_DEFINITIONS,
                tool_choice={"type": "auto"},
                messages=messages,
            )
            blocks = list(getattr(response, "content", []) or [])
            tool_uses = [b for b in blocks if getattr(b, "type", None) == "tool_use"]
            texts = [getattr(b, "text", "") for b in blocks if getattr(b, "type", None) == "text"]
            if texts:
                final_text = "\n".join(t for t in texts if t).strip() or final_text
            if not tool_uses or getattr(response, "stop_reason", None) != "tool_use":
                break
            if self.send_interim and not interim_sent:
                slow_tool = any(getattr(t, "name", "") in SLOW_TOOLS for t in tool_uses)
                if slow_tool or time.monotonic() - started > SLOW_AFTER_SECONDS:
                    self.send_interim(INTERIM_TEXT)
                    interim_sent = True
            messages.append({"role": "assistant", "content": _serialise_blocks(blocks)})
            results = []
            for use in tool_uses:
                args = getattr(use, "input", {}) or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                output = self.tools.run(getattr(use, "name", ""), dict(args))
                results.append({"type": "tool_result", "tool_use_id": getattr(use, "id", ""), "content": output})
            messages.append({"role": "user", "content": results})
        return final_text or "Sorry, I could not work that out. Could you rephrase?"

    # -- secondary LLM task ----------------------------------------------------------
    def write_cover_letter(self, tender: Tender, company_facts: str = "") -> str:
        facts = company_facts.strip() or "No extra facts given - leave placeholders in [square brackets]."
        details = {
            "buyer": tender.buyer_org,
            "department": tender.department,
            "title": tender.title,
            "tender_id": tender.tender_id,
            "items": [i.model_dump(exclude_none=True) for i in tender.items],
            "value": tender.estimated_value_inr,
            "emd": tender.emd_inr,
            "deadline": tender.deadline.isoformat() if tender.deadline else None,
            "location": tender.location,
            "eligibility": tender.eligibility.model_dump(exclude_none=True),
            "contact": tender.contact.model_dump(exclude_none=True),
        }
        prompt = (
            f"Company: {COMPANY_PROFILE}\nExtra company facts: {facts}\n\n"
            f"Tender (JSON): {json.dumps(details, ensure_ascii=False, default=str)}\n\n"
            "Write a formal one-page bid cover letter addressed to the tender inviting authority, "
            "in Indian business English: subject line with tender id, reference to the NIT, a short "
            "company introduction, confirmation of compliance with specifications, EMD/eligibility "
            "statement, list of enclosed documents, and a courteous close with signatory placeholders. "
            "Use only the facts above; put anything unknown in [square brackets]. Plain text, no markdown."
        )
        response = self.client.messages.create(
            model=self.model, max_tokens=1800, messages=[{"role": "user", "content": prompt}]
        )
        return "\n".join(
            getattr(b, "text", "") for b in getattr(response, "content", []) if getattr(b, "type", None) == "text"
        ).strip()


def _serialise_blocks(blocks: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for block in blocks:
        kind = getattr(block, "type", None)
        if kind == "text" and getattr(block, "text", ""):
            out.append({"type": "text", "text": block.text})
        elif kind == "tool_use":
            out.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
    return out
