from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tender_bhai.config import Settings
from tender_bhai.models import Contact, Item, Tender

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(*parts: str) -> str:
    return (FIXTURES.joinpath(*parts)).read_text(encoding="utf-8")


def fixture_bytes(*parts: str) -> bytes:
    return (FIXTURES.joinpath(*parts)).read_bytes()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        anthropic_api_key="test-key",
        supabase_url="https://example.supabase.co",
        supabase_service_key="service-key",
        meta_access_token="meta-token",
        meta_phone_number_id="12345",
        meta_app_secret="app-secret",
        meta_verify_token="verify-me",
        telegram_bot_token="tg-token",
        telegram_chat_id="777",
        telegram_webhook_secret="tg-secret",
        recipient_phone="91XXXXXXXXXX",
    )


def make_tender(**overrides: Any) -> Tender:
    base: dict[str, Any] = {
        "id": "11111111-1111-1111-1111-111111111111",
        "portal": "nic_up",
        "tender_id": "2026_UPSPORTS_1_1",
        "dedup_hash": "abc",
        "title": "Supply of cricket kits",
        "buyer_org": "Directorate of Sports, UP",
        "department": "Sports",
        "items": [Item(name="Cricket bat", qty="500"), Item(name="Leather ball", qty="2000", specs="4-piece")],
        "estimated_value_inr": 2_500_000,
        "emd_inr": 50_000,
        "deadline": "2026-11-14T15:00+05:30",
        "state": "Uttar Pradesh",
        "location": "Lucknow",
        "contact": Contact(name="Sri Verma", designation="Purchase Officer", phone="0522-1234567"),
        "portal_url": "https://etender.up.nic.in/nicgep/app?page=FrontEndListTendersbyDate&service=page",
        "fit": "strong",
        "fit_reason": "Cricket kits are the core product line",
        "summary": "500 bats and 2000 balls for state academies.\nMSE exemption on turnover.",
    }
    base.update(overrides)
    return Tender.model_validate(base)


class Block(SimpleNamespace):
    """Duck-typed stand-in for Anthropic content blocks."""


def text_block(text: str) -> Block:
    return Block(type="text", text=text)


def tool_use_block(name: str, input: dict[str, Any], id: str = "toolu_1") -> Block:
    return Block(type="tool_use", name=name, input=input, id=id)


def response(blocks: list[Block], stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(content=blocks, stop_reason=stop_reason)


class FakeMessages:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("fake LLM ran out of scripted responses")
        return self.responses.pop(0)


class FakeAnthropic:
    def __init__(self, responses: list[Any]) -> None:
        self.messages = FakeMessages(responses)


def load_json(path: str) -> Any:
    return json.loads(Path(path).read_text())
