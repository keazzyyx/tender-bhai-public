"""Common messenger interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SendResult:
    ok: bool
    message_id: str | None = None
    error: str | None = None


class MessengerError(RuntimeError):
    pass


class Messenger(ABC):
    """Every provider sends text and documents to one recipient id (phone or chat id)."""

    name: str = "base"
    #: True when the provider enforces a 24-hour customer-service window.
    has_session_window: bool = False

    @abstractmethod
    def send_text(self, to: str, text: str) -> SendResult: ...

    @abstractmethod
    def send_document(self, to: str, url: str, filename: str, caption: str | None = None) -> SendResult: ...

    def send_template(self, to: str, template: str, variables: list[str]) -> SendResult:
        """Only WhatsApp needs templates; other providers send plain text instead."""
        return self.send_text(to, " ".join(variables))

    def send_many(self, to: str, parts: list[str]) -> list[SendResult]:
        results: list[SendResult] = []
        for part in parts:
            result = self.send_text(to, part)
            results.append(result)
            if not result.ok:
                break
        return results
