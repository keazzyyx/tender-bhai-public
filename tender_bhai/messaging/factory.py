"""Pick the messenger from MESSAGING_PROVIDER and resolve the recipient id."""

from __future__ import annotations

from tender_bhai.config import Settings, settings
from tender_bhai.messaging.base import Messenger, MessengerError
from tender_bhai.messaging.telegram import TelegramMessenger
from tender_bhai.messaging.whatsapp import WhatsAppMessenger


def get_messenger(cfg: Settings | None = None) -> Messenger:
    cfg = cfg or settings()
    provider = cfg.messaging_provider
    if provider == "whatsapp":
        if not (cfg.meta_access_token and cfg.meta_phone_number_id):
            raise MessengerError("META_ACCESS_TOKEN and META_PHONE_NUMBER_ID are required for WhatsApp")
        return WhatsAppMessenger(cfg.meta_access_token, cfg.meta_phone_number_id, cfg.meta_api_version)
    if provider == "telegram":
        if not cfg.telegram_bot_token:
            raise MessengerError("TELEGRAM_BOT_TOKEN is required for Telegram")
        return TelegramMessenger(cfg.telegram_bot_token)
    raise MessengerError(f"Unknown MESSAGING_PROVIDER={provider!r} (use 'whatsapp' or 'telegram')")


def recipient_for(cfg: Settings | None = None) -> str:
    cfg = cfg or settings()
    if cfg.messaging_provider == "telegram":
        if not cfg.telegram_chat_id:
            raise MessengerError("TELEGRAM_CHAT_ID is required for Telegram")
        return cfg.telegram_chat_id
    return cfg.recipient_phone
