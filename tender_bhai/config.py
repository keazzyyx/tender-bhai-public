"""Environment-driven configuration. Every value has a documented env var in .env.example."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_MIN_VALUE_INR = 600_000
DEFAULT_MODEL = "claude-sonnet-4-5"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str = ""
    claude_model: str = DEFAULT_MODEL
    supabase_url: str = ""
    supabase_service_key: str = ""
    neatlogs_api_key: str = ""
    messaging_provider: str = "whatsapp"
    meta_access_token: str = ""
    meta_phone_number_id: str = ""
    meta_app_secret: str = ""
    meta_verify_token: str = ""
    meta_api_version: str = "v21.0"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_webhook_secret: str = ""
    recipient_phone: str = "91XXXXXXXXXX"
    min_value_inr: int = DEFAULT_MIN_VALUE_INR
    portal_timeout_seconds: int = 600
    request_delay_seconds: float = 1.5
    max_pages_per_portal: int = 40
    max_candidates_per_portal: int = 60
    verify_tls: bool = False
    env_tags: list[str] = field(default_factory=lambda: ["prod"])

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_key)

    @property
    def anthropic_configured(self) -> bool:
        return bool(self.anthropic_api_key)


def load_settings(dotenv_path: Path | None = None) -> Settings:
    """Read settings from the environment (and a .env file if present)."""
    load_dotenv(dotenv_path, override=False)
    tags = [t.strip() for t in os.getenv("NEATLOGS_TAGS", "prod").split(",") if t.strip()]
    return Settings(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        claude_model=os.getenv("CLAUDE_MODEL", DEFAULT_MODEL),
        supabase_url=os.getenv("SUPABASE_URL", "").rstrip("/"),
        supabase_service_key=os.getenv("SUPABASE_SERVICE_KEY", ""),
        neatlogs_api_key=os.getenv("NEATLOGS_API_KEY", ""),
        messaging_provider=os.getenv("MESSAGING_PROVIDER", "whatsapp").strip().lower(),
        meta_access_token=os.getenv("META_ACCESS_TOKEN", ""),
        meta_phone_number_id=os.getenv("META_PHONE_NUMBER_ID", ""),
        meta_app_secret=os.getenv("META_APP_SECRET", ""),
        meta_verify_token=os.getenv("META_VERIFY_TOKEN", ""),
        meta_api_version=os.getenv("META_API_VERSION", "v21.0"),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        telegram_webhook_secret=os.getenv("TELEGRAM_WEBHOOK_SECRET", ""),
        recipient_phone=os.getenv("RECIPIENT_PHONE", "91XXXXXXXXXX"),
        min_value_inr=_env_int("MIN_VALUE_INR", DEFAULT_MIN_VALUE_INR),
        portal_timeout_seconds=_env_int("PORTAL_TIMEOUT_SECONDS", 600),
        request_delay_seconds=float(os.getenv("REQUEST_DELAY_SECONDS", "1.5")),
        max_pages_per_portal=_env_int("MAX_PAGES_PER_PORTAL", 40),
        max_candidates_per_portal=_env_int("MAX_CANDIDATES_PER_PORTAL", 60),
        verify_tls=_env_bool("VERIFY_TLS", False),
        env_tags=tags,
    )


@lru_cache(maxsize=1)
def settings() -> Settings:
    return load_settings()
