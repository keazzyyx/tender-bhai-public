"""Meta WhatsApp Cloud API adapter (Graph API v21+)."""

from __future__ import annotations

import logging

import httpx

from tender_bhai.messaging.base import Messenger, SendResult

log = logging.getLogger(__name__)

TEMPLATE_NAME = "daily_tenders"
TEMPLATE_LANGUAGE = "en"


class WhatsAppMessenger(Messenger):
    name = "whatsapp"
    has_session_window = True

    def __init__(
        self,
        access_token: str,
        phone_number_id: str,
        api_version: str = "v21.0",
        timeout: float = 20.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
        self._client = client or httpx.Client(timeout=timeout)
        self._headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    def _post(self, payload: dict) -> SendResult:
        try:
            response = self._client.post(self.url, json=payload, headers=self._headers)
        except httpx.HTTPError as exc:
            log.error("whatsapp send failed: %s", exc)
            return SendResult(ok=False, error=str(exc))
        if response.status_code >= 400:
            log.error("whatsapp API %s: %s", response.status_code, response.text[:400])
            return SendResult(ok=False, error=f"{response.status_code}: {response.text[:400]}")
        data = response.json()
        messages = data.get("messages") or []
        message_id = messages[0].get("id") if messages else None
        return SendResult(ok=True, message_id=message_id)

    def send_text(self, to: str, text: str) -> SendResult:
        return self._post(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"preview_url": False, "body": text},
            }
        )

    def send_template(self, to: str, template: str = TEMPLATE_NAME, variables: list[str] | None = None) -> SendResult:
        parameters = [{"type": "text", "text": str(v)} for v in (variables or [])]
        components = [{"type": "body", "parameters": parameters}] if parameters else []
        return self._post(
            {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "template",
                "template": {
                    "name": template,
                    "language": {"code": TEMPLATE_LANGUAGE},
                    "components": components,
                },
            }
        )

    def send_document(self, to: str, url: str, filename: str, caption: str | None = None) -> SendResult:
        document: dict = {"link": url, "filename": filename}
        if caption:
            document["caption"] = caption[:1024]
        return self._post({"messaging_product": "whatsapp", "to": to, "type": "document", "document": document})

    def mark_read(self, message_id: str) -> SendResult:
        return self._post({"messaging_product": "whatsapp", "status": "read", "message_id": message_id})
