"""Webhook handling shared by the Vercel function and the tests.

Meta: GET verify handshake + POST with X-Hub-Signature-256 HMAC.
Telegram: POST on a secret path. Both return 200 quickly and do the work inline.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from dataclasses import dataclass
from typing import Any

from tender_bhai import obs
from tender_bhai.agent.agent import TenderAgent
from tender_bhai.config import Settings
from tender_bhai.db import Database
from tender_bhai.delivery import release_pending_digest
from tender_bhai.messaging.base import Messenger

log = logging.getLogger(__name__)

_ACK = re.compile(r"^\W*(yes|ok|okay|sure|send|show|go|haan|ha|hi|hello|hey|thanks|thank you|👍|🙏)\W*$", re.IGNORECASE)


def verify_meta_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256=") :])


@dataclass(frozen=True)
class Inbound:
    provider: str
    sender: str
    text: str
    message_id: str | None


def parse_meta_payload(payload: dict[str, Any]) -> list[Inbound]:
    inbound: list[Inbound] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for message in value.get("messages") or []:
                sender = str(message.get("from") or "")
                kind = message.get("type")
                text = ""
                if kind == "text":
                    text = (message.get("text") or {}).get("body") or ""
                elif kind == "button":
                    text = (message.get("button") or {}).get("text") or ""
                elif kind == "interactive":
                    interactive = message.get("interactive") or {}
                    text = (interactive.get("button_reply") or interactive.get("list_reply") or {}).get("title") or ""
                if sender and text:
                    inbound.append(Inbound("whatsapp", sender, text.strip(), message.get("id")))
    return inbound


def parse_telegram_payload(payload: dict[str, Any]) -> list[Inbound]:
    message = payload.get("message") or payload.get("edited_message") or {}
    text = (message.get("text") or "").strip()
    chat_id = (message.get("chat") or {}).get("id")
    if not text or chat_id is None:
        return []
    return [Inbound("telegram", str(chat_id), text, f"tg-{message.get('message_id')}")]


class WebhookService:
    """Provider-agnostic inbound handling: dedup, store, release digest, answer."""

    def __init__(self, cfg: Settings, db: Database, messenger: Messenger, agent: TenderAgent | None = None) -> None:
        self.cfg = cfg
        self.db = db
        self.messenger = messenger
        self.agent = agent or TenderAgent(db, cfg=cfg, send_interim=None)

    def allowed_sender(self, inbound: Inbound) -> bool:
        if inbound.provider == "telegram":
            return bool(self.cfg.telegram_chat_id) and inbound.sender == str(self.cfg.telegram_chat_id)
        return inbound.sender == self.cfg.recipient_phone

    def handle(self, inbound: Inbound) -> str:
        if not self.allowed_sender(inbound):
            log.warning("ignoring message from unknown sender %s", inbound.sender)
            return "ignored"
        if inbound.message_id and self.db.message_exists(inbound.message_id):
            return "duplicate"
        self.db.add_message("in", inbound.sender, inbound.text, inbound.message_id)
        released = release_pending_digest(self.messenger, self.db, inbound.sender)
        if released and _ACK.match(inbound.text):
            return "digest"
        self.agent.send_interim = lambda text: self.messenger.send_text(inbound.sender, text)
        answer = self.agent.reply(inbound.sender, inbound.text)
        for part in _split(answer):
            result = self.messenger.send_text(inbound.sender, part)
            if result.ok:
                self.db.add_message("out", inbound.sender, part, result.message_id)
        obs.flush()
        return "answered"


def _split(text: str) -> list[str]:
    from tender_bhai.digest import split_message

    return split_message(text)
