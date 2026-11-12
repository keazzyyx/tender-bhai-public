"""Messaging adapters: WhatsApp (Meta Cloud API) and Telegram (testing)."""

from tender_bhai.messaging.base import Messenger, SendResult
from tender_bhai.messaging.factory import get_messenger

__all__ = ["Messenger", "SendResult", "get_messenger"]
