"""Telegram Bot API adapter - same interface, used until the Meta account is approved."""

from __future__ import annotations

import html
import logging
import re

import httpx

from tender_bhai.messaging.base import Messenger, SendResult

log = logging.getLogger(__name__)

_BOLD = re.compile(r"\*(?=\S)(.+?)(?<=\S)\*")
_ITALIC = re.compile(r"(?<![\w*])_(?=\S)(.+?)(?<=\S)_(?![\w*])")
_URL = re.compile(r"https?://[^\s<>\"]+")


def whatsapp_to_telegram_html(text: str) -> str:
    """Convert WhatsApp markdown (*bold*, _italic_) to Telegram HTML, escaping everything else."""
    escaped = html.escape(text, quote=False)
    escaped = _BOLD.sub(r"<b>\1</b>", escaped)
    escaped = _ITALIC.sub(r"<i>\1</i>", escaped)
    return escaped


class TelegramMessenger(Messenger):
    name = "telegram"
    has_session_window = False

    def __init__(self, bot_token: str, timeout: float = 20.0, client: httpx.Client | None = None) -> None:
        self.base = f"https://api.telegram.org/bot{bot_token}"
        self._client = client or httpx.Client(timeout=timeout)

    def _post(self, method: str, payload: dict) -> SendResult:
        try:
            response = self._client.post(f"{self.base}/{method}", json=payload)
        except httpx.HTTPError as exc:
            log.error("telegram send failed: %s", exc)
            return SendResult(ok=False, error=str(exc))
        data = response.json() if response.content else {}
        if response.status_code >= 400 or not data.get("ok"):
            log.error("telegram API %s: %s", response.status_code, response.text[:400])
            return SendResult(ok=False, error=f"{response.status_code}: {response.text[:400]}")
        message_id = data.get("result", {}).get("message_id")
        return SendResult(ok=True, message_id=str(message_id) if message_id is not None else None)

    def send_text(self, to: str, text: str) -> SendResult:
        result = self._post(
            "sendMessage",
            {
                "chat_id": to,
                "text": whatsapp_to_telegram_html(text),
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )
        if not result.ok and result.error and "parse" in result.error.lower():
            # Fall back to plain text if our HTML conversion upset Telegram.
            result = self._post("sendMessage", {"chat_id": to, "text": text, "disable_web_page_preview": True})
        return result

    def send_document(self, to: str, url: str, filename: str, caption: str | None = None) -> SendResult:
        payload: dict = {"chat_id": to, "document": url}
        if caption:
            payload["caption"] = caption[:1024]
        return self._post("sendDocument", payload)
