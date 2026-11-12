"""Digest delivery with WhatsApp's 24-hour customer-service window.

If the recipient wrote to us within the last 24 h we can send free-form text, so the
digest goes out directly. Otherwise we send the approved ``daily_tenders`` template
with the tender count, park the digest in ``preferences`` under ``pending_digest`` and
release it from the webhook as soon as the recipient replies.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Protocol

from tender_bhai.dates import now_ist, to_iso
from tender_bhai.messaging.base import Messenger, SendResult

log = logging.getLogger(__name__)

PENDING_KEY = "pending_digest"
WINDOW = timedelta(hours=24)


class DeliveryStore(Protocol):
    def last_inbound_at(self, phone: str) -> datetime | None: ...
    def get_preference(self, key: str, default: Any = None) -> Any: ...
    def set_preference(self, key: str, value: Any) -> None: ...
    def delete_preference(self, key: str) -> None: ...
    def add_message(self, direction: str, phone: str, text: str, wa_message_id: str | None = None) -> None: ...


def within_window(last_inbound: datetime | None, now: datetime | None = None, window: timedelta = WINDOW) -> bool:
    if last_inbound is None:
        return False
    now = now or now_ist()
    if last_inbound.tzinfo is None:
        last_inbound = last_inbound.replace(tzinfo=now.tzinfo)
    return now - last_inbound < window


def _record(store: DeliveryStore, to: str, parts: list[str], results: list[SendResult]) -> None:
    for part, result in zip(parts, results, strict=False):
        if result.ok:
            store.add_message("out", to, part, result.message_id)


def deliver_digest(
    messenger: Messenger,
    store: DeliveryStore,
    to: str,
    parts: list[str],
    count: int,
    now: datetime | None = None,
) -> str:
    """Send the digest parts respecting the window. Returns 'sent', 'template' or 'failed'."""
    now = now or now_ist()
    if messenger.has_session_window and not within_window(store.last_inbound_at(to), now):
        result = messenger.send_template(to, "daily_tenders", [str(count)])
        if not result.ok:
            log.error("template send failed: %s", result.error)
            return "failed"
        store.add_message("out", to, f"[template daily_tenders count={count}]", result.message_id)
        store.set_preference(PENDING_KEY, {"to": to, "parts": parts, "count": count, "created_at": to_iso(now)})
        return "template"
    results = messenger.send_many(to, parts)
    _record(store, to, parts, results)
    return "sent" if all(r.ok for r in results) and results else "failed"


def release_pending_digest(messenger: Messenger, store: DeliveryStore, to: str) -> bool:
    """Called from the webhook on every inbound message. Returns True if a digest was sent."""
    pending = store.get_preference(PENDING_KEY)
    if not pending or pending.get("to") != to:
        return False
    parts = list(pending.get("parts") or [])
    results = messenger.send_many(to, parts)
    _record(store, to, parts, results)
    if results and all(r.ok for r in results):
        store.delete_preference(PENDING_KEY)
        return True
    return False
