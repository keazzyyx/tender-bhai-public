from __future__ import annotations

import pytest

from tender_bhai.extract import RECORD_TOOL, ExtractionError, Extractor, build_user_prompt
from tender_bhai.models import Candidate, Tender
from tests.conftest import FakeAnthropic, response, text_block, tool_use_block

VALID = {
    "buyer_org": "Directorate of Sports, Uttar Pradesh",
    "department": "Sports",
    "items": [
        {"name": "Cricket bat (English willow)", "qty": "500", "specs": "Grade 2"},
        {"name": "Leather ball", "qty": 2000},
    ],
    "estimated_value_inr": "25,00,000",
    "emd_inr": 50000,
    "deadline": "14-Nov-2026 15:00",
    "opening_date": "15/11/2026",
    "state": "Uttar Pradesh",
    "location": "Lucknow",
    "contact": {"name": "Sri Verma", "designation": "Purchase Officer", "phone": "0522-1234567", "email": None},
    "portal_url": "https://etender.up.nic.in/nicgep/app",
    "document_url": None,
    "eligibility": {
        "min_turnover": "Rs 50 lakh",
        "msme_exempt": True,
        "past_performance": None,
        "other": "Sample required",
    },
    "fit": "Strong",
    "fit_reason": "Core cricket products",
    "summary": "500 bats, 2000 balls for state academies.\nMSE turnover exemption available.",
}


def candidate() -> Candidate:
    return Candidate(
        portal="nic_up", tender_id="2026_UPS_1_1", title="Supply of cricket kits", buyer_org="Dir. of Sports",
        state="Uttar Pradesh", portal_url="https://etender.up.nic.in/nicgep/app", listing_text="Supply of cricket kits",
    )  # fmt: skip


def test_extract_valid_first_try(settings):
    client = FakeAnthropic([response([tool_use_block("record_tender", VALID)], "tool_use")])
    extractor = Extractor(client=client, cfg=settings)
    result = extractor.extract(candidate(), "detail text", "pdf text")
    assert result.fit == "strong"
    assert result.estimated_value_inr == 2_500_000
    assert result.deadline.isoformat() == "2026-11-14T15:00:00+05:30"
    assert result.opening_date.day == 15
    assert result.items[1].qty == "2000"
    assert result.contact.phone == "0522-1234567"
    assert result.eligibility.msme_exempt is True
    call = client.messages.calls[0]
    assert call["model"] == "claude-sonnet-4-5"
    assert call["tool_choice"] == {"type": "tool", "name": "record_tender"}
    assert call["tools"][0]["name"] == RECORD_TOOL["name"]
    assert "=== TENDER DOCUMENT (PDF) TEXT ===\npdf text" in call["messages"][0]["content"]


def test_extract_invalid_then_retry_succeeds(settings):
    bad = {**VALID, "fit": "maybe", "summary": None}  # fails enum + type validation
    client = FakeAnthropic(
        [
            response([tool_use_block("record_tender", bad)], "tool_use"),
            response([tool_use_block("record_tender", VALID)], "tool_use"),
        ]
    )
    result = Extractor(client=client, cfg=settings).extract(candidate())
    assert result.fit == "strong"
    assert len(client.messages.calls) == 2
    retry_messages = client.messages.calls[1]["messages"]
    assert retry_messages[-1]["role"] == "user"
    assert "invalid" in retry_messages[-1]["content"].lower()


def test_extract_text_instead_of_tool_then_retry(settings):
    client = FakeAnthropic(
        [
            response([text_block("{not json")], "end_turn"),
            response([tool_use_block("record_tender", VALID)], "tool_use"),
        ]
    )
    assert Extractor(client=client, cfg=settings).extract(candidate()).buyer_org.startswith("Directorate")


def test_extract_fails_after_second_invalid(settings):
    bad = {"buyer_org": "X"}  # missing required fields
    client = FakeAnthropic(
        [response([tool_use_block("record_tender", bad)], "tool_use"), response([text_block("sorry")], "end_turn")]
    )
    with pytest.raises(ExtractionError):
        Extractor(client=client, cfg=settings).extract(candidate())
    assert len(client.messages.calls) == 2


def test_tool_input_as_json_string_is_parsed(settings):
    import json

    client = FakeAnthropic([response([tool_use_block("record_tender", json.dumps(VALID))], "tool_use")])
    assert Extractor(client=client, cfg=settings).extract(candidate()).fit == "strong"


def test_prompt_budget_and_tender_from_parts():
    cand = candidate()
    prompt = build_user_prompt(cand, "d" * 50000, "p" * 50000)
    assert len(prompt) < 26000
    from tender_bhai.models import Extraction

    extraction = Extraction.model_validate(VALID)
    tender = Tender.from_parts(cand, extraction, "raw")
    assert tender.buyer_org == "Directorate of Sports, Uttar Pradesh"
    assert tender.dedup_hash
    row = tender.to_row()
    assert "id" not in row
    assert row["items_text"].startswith("Cricket bat (English willow) 500 Grade 2")
    assert row["deadline"] == "2026-11-14T15:00:00+05:30"
