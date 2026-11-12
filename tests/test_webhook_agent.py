from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tender_bhai.agent.agent import TenderAgent, _history_to_messages
from tender_bhai.agent.tools import LAST_DIGEST_KEY, ToolExecutor
from tender_bhai.messaging.base import Messenger, SendResult
from tender_bhai.webhook import (
    Inbound,
    WebhookService,
    parse_meta_payload,
    parse_telegram_payload,
    verify_meta_signature,
)
from tests.conftest import FakeAnthropic, make_tender, response, text_block, tool_use_block


# --- fakes ------------------------------------------------------------------------------------------
class FakeDB:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.prefs: dict[str, Any] = {}
        self.tenders = {"11111111-1111-1111-1111-111111111111": make_tender()}
        self.calls: list[tuple[str, Any]] = []

    # messages
    def message_exists(self, wa_message_id: str) -> bool:
        return any(m["wa_message_id"] == wa_message_id for m in self.messages)

    def add_message(self, direction: str, phone: str, text: str, wa_message_id: str | None = None) -> None:
        self.messages.append(
            {
                "direction": direction,
                "phone": phone,
                "text": text,
                "wa_message_id": wa_message_id,
                "created_at": "2026-11-10T08:00:00+05:30",
            }
        )

    def recent_messages(self, phone: str, limit: int = 30) -> list[dict[str, Any]]:
        return [m for m in self.messages if m["phone"] == phone][-limit:]

    def last_inbound_at(self, phone: str):
        return None

    # preferences
    def get_preference(self, key: str, default: Any = None) -> Any:
        return self.prefs.get(key, default)

    def set_preference(self, key: str, value: Any) -> None:
        self.prefs[key] = value

    def delete_preference(self, key: str) -> None:
        self.prefs.pop(key, None)

    # tenders
    def get_tender(self, tender_id: str):
        return self.tenders.get(tender_id)

    def search_tenders(self, **kwargs: Any):
        self.calls.append(("search", kwargs))
        return list(self.tenders.values())

    def closing_soon(self, days: int, sent_only: bool = True):
        self.calls.append(("closing_soon", days))
        return list(self.tenders.values())

    def select(self, table: str, **params: Any):
        return []

    def recent_runs(self, since):
        return [{"status": "ok", "finished_at": "x", "errors": [{"portal": "gem", "error": "refused"}]}]

    def count_tenders(self, since, sent=None):
        return 7 if sent is None else 5


class RecordingMessenger(Messenger):
    name = "recording"
    has_session_window = True

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_text(self, to: str, text: str) -> SendResult:
        self.sent.append((to, text))
        return SendResult(ok=True, message_id=f"wamid.{len(self.sent)}")

    def send_document(self, to: str, url: str, filename: str, caption: str | None = None) -> SendResult:
        return SendResult(ok=True)


# --- signature / parsing ----------------------------------------------------------------------------
def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_meta_signature_valid_and_invalid():
    body = b'{"object":"whatsapp_business_account"}'
    assert verify_meta_signature("app-secret", body, sign("app-secret", body))
    assert not verify_meta_signature("app-secret", body, sign("other", body))
    assert not verify_meta_signature("app-secret", body, None)
    assert not verify_meta_signature("app-secret", body, "md5=abc")
    assert not verify_meta_signature("", body, sign("", body))


META_PAYLOAD = {
    "object": "whatsapp_business_account",
    "entry": [{"changes": [{"value": {
        "messages": [{"from": "91XXXXXXXXXX", "id": "wamid.ABC", "type": "text", "text": {"body": "details on 1"}}],
        "statuses": [],
    }}]}],
}  # fmt: skip


def test_parse_payloads():
    inbound = parse_meta_payload(META_PAYLOAD)
    assert inbound == [Inbound("whatsapp", "91XXXXXXXXXX", "details on 1", "wamid.ABC")]
    assert parse_meta_payload({"entry": [{"changes": [{"value": {"statuses": [{"id": "x"}]}}]}]}) == []
    tg = parse_telegram_payload({"message": {"message_id": 5, "chat": {"id": 777}, "text": " what closes this week "}})
    assert tg == [Inbound("telegram", "777", "what closes this week", "tg-5")]
    assert parse_telegram_payload({"message": {"chat": {"id": 1}, "photo": []}}) == []


# --- agent tool routing with a fake LLM ------------------------------------------------------------
def build_agent(db: FakeDB, responses: list[Any], settings) -> TenderAgent:
    return TenderAgent(db, client=FakeAnthropic(responses), cfg=settings)


def test_agent_routes_ordinal_to_get_tender(settings):
    db = FakeDB()
    db.prefs[LAST_DIGEST_KEY] = {"ids": ["11111111-1111-1111-1111-111111111111"]}
    agent = build_agent(
        db,
        [
            response([text_block("Let me check."), tool_use_block("get_tender", {"ref": "1"}, "t1")], "tool_use"),
            response([text_block("*Directorate of Sports, UP* — closes 14 Nov, 3 PM. https://etender.up.nic.in/...")]),
        ],
        settings,
    )
    reply = agent.reply("91XXXXXXXXXX", "details on 1")
    assert reply.startswith("*Directorate of Sports, UP*")
    calls = agent.client.messages.calls
    assert calls[0]["tools"][0]["name"] == "search_tenders"
    second_turn = calls[1]["messages"]
    assert second_turn[-2]["role"] == "assistant"
    assert second_turn[-2]["content"][1]["name"] == "get_tender"
    tool_result = json.loads(second_turn[-1]["content"][0]["content"])
    assert tool_result["portal_tender_id"] == "2026_UPSPORTS_1_1"
    assert tool_result["value"] == "₹25 lakh"


def test_agent_search_and_preference_tools(settings):
    db = FakeDB()
    agent = build_agent(
        db,
        [
            response(
                [
                    tool_use_block("search_tenders", {"query": "cricket", "state": "Delhi"}, "t1"),
                    tool_use_block("set_preference", {"key": "min_value", "value": "10 lakh"}, "t2"),
                    tool_use_block(
                        "set_preference", {"key": "ignore_states", "value": ["Assam", "Manipur"], "action": "add"}, "t3"
                    ),
                ],
                "tool_use",
            ),
            response([text_block("Found 1. Noted: only above ₹10 lakh, ignoring Assam and Manipur.")]),
        ],
        settings,
    )
    reply = agent.reply("91XXXXXXXXXX", "show delhi cricket tenders, only above 10 lakh, ignore assam and manipur")
    assert reply.startswith("Found 1")
    assert db.calls[0] == (
        "search",
        {"query": "cricket", "state": "Delhi", "min_value": None, "closing_before": None, "limit": 8},
    )
    assert db.prefs["min_value"] == 1_000_000
    assert db.prefs["ignore_states"] == ["Assam", "Manipur"]
    results = agent.client.messages.calls[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2", "t3"]


def test_agent_unknown_tool_and_bad_ordinal(settings):
    db = FakeDB()
    agent = build_agent(
        db,
        [
            response([tool_use_block("get_tender", {"ref": "9"}, "t1"), tool_use_block("nope", {}, "t2")], "tool_use"),
            response([text_block("I can't find number 9 in today's digest.")]),
        ],
        settings,
    )
    assert "can't find" in agent.reply("91XXXXXXXXXX", "details on 9")
    results = agent.client.messages.calls[1]["messages"][-1]["content"]
    assert "No tender found" in results[0]["content"]
    assert "unknown tool" in results[1]["content"]


def test_tool_executor_stats_and_closing_soon(settings):
    tools = ToolExecutor(FakeDB())
    stats = json.loads(tools.run("stats", {"period": "week"}))
    assert stats["tenders_found"] == 7 and stats["tenders_sent"] == 5
    assert stats["portal_failures"] == {"gem": 1}
    soon = json.loads(tools.run("closing_soon", {"days": 3}))
    assert soon["count"] == 1 and soon["tenders"][0]["closes"]
    assert "bad arguments" in tools.run("closing_soon", {"dayz": 3})


def test_cover_letter_tool_uses_second_llm_call(settings):
    db = FakeDB()
    agent = build_agent(
        db,
        [
            response(
                [
                    tool_use_block(
                        "draft_bid_cover_letter",
                        {"tender_id": "11111111-1111-1111-1111-111111111111", "company_facts": "Turnover 4 crore"},
                        "t1",
                    )
                ],
                "tool_use",
            ),
            response([text_block("To, The Purchase Officer ... [signatory]")]),  # letter writer call
            response([text_block("Here is your draft:\nTo, The Purchase Officer ...")]),
        ],
        settings,
    )
    interim: list[str] = []
    agent.send_interim = interim.append
    reply = agent.reply("91XXXXXXXXXX", "draft a cover letter for that")
    assert reply.startswith("Here is your draft")
    assert interim == ["One sec, checking…"]
    letter_call = agent.client.messages.calls[1]
    assert "Turnover 4 crore" in letter_call["messages"][0]["content"]


def test_history_merging():
    rows = [
        {"direction": "out", "text": "[template daily_tenders count=3]"},
        {"direction": "out", "text": "digest part"},
        {"direction": "in", "text": "ok"},
        {"direction": "in", "text": "details on 2"},
        {"direction": "out", "text": "Here you go"},
    ]
    assert _history_to_messages(rows) == [
        {"role": "user", "content": "ok\ndetails on 2"},
        {"role": "assistant", "content": "Here you go"},
    ]


# --- webhook service end-to-end with FastAPI test client ------------------------------------------------
@pytest.fixture
def wired(settings, monkeypatch):
    import api.webhook as api_module

    db = FakeDB()
    messenger = RecordingMessenger()
    agent = build_agent(db, [response([text_block("Reply from agent")])] * 5, settings)
    service = WebhookService(settings, db, messenger, agent)
    monkeypatch.setattr(api_module, "load_settings", lambda: settings)
    monkeypatch.setattr(api_module, "_service", lambda: service)
    return TestClient(api_module.app), db, messenger, service


def test_verify_handshake(wired):
    client, *_ = wired
    ok = client.get(
        "/api/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "12345"}
    )
    assert ok.status_code == 200 and ok.text == "12345"
    bad = client.get("/api/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"})
    assert bad.status_code == 403


def test_meta_post_valid_signature_answers(wired):
    client, db, messenger, _ = wired
    body = json.dumps(META_PAYLOAD).encode()
    res = client.post(
        "/api/webhook",
        content=body,
        headers={"X-Hub-Signature-256": sign("app-secret", body), "Content-Type": "application/json"},
    )
    assert res.status_code == 200
    assert res.json()["handled"] == "answered"
    assert messenger.sent == [("91XXXXXXXXXX", "Reply from agent")]
    assert [m["direction"] for m in db.messages] == ["in", "out"]
    # Meta retries the same message id -> deduplicated
    res2 = client.post("/api/webhook", content=body, headers={"X-Hub-Signature-256": sign("app-secret", body)})
    assert res2.json()["handled"] == "duplicate"
    assert len(messenger.sent) == 1


def test_meta_post_invalid_signature_rejected(wired):
    client, _, messenger, _ = wired
    body = json.dumps(META_PAYLOAD).encode()
    res = client.post("/api/webhook", content=body, headers={"X-Hub-Signature-256": sign("wrong", body)})
    assert res.status_code == 403
    assert client.post("/api/webhook", content=body).status_code == 403
    assert messenger.sent == []


def test_meta_unknown_sender_ignored(wired):
    client, _, messenger, _ = wired
    payload = json.loads(json.dumps(META_PAYLOAD))
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["from"] = "911234567890"
    body = json.dumps(payload).encode()
    res = client.post("/api/webhook", content=body, headers={"X-Hub-Signature-256": sign("app-secret", body)})
    assert res.json()["handled"] == "ignored"
    assert messenger.sent == []


def test_telegram_secret_path(wired):
    client, _, messenger, _ = wired
    payload = {"message": {"message_id": 1, "chat": {"id": 777}, "text": "what closes this week"}}
    assert client.post("/api/webhook/telegram/wrong", json=payload).status_code == 403
    res = client.post("/api/webhook/telegram/tg-secret", json=payload)
    assert res.status_code == 200 and res.json()["handled"] == "answered"
    assert messenger.sent[-1] == ("777", "Reply from agent")
    other_chat = {"message": {"message_id": 2, "chat": {"id": 1}, "text": "hi"}}
    assert client.post("/api/webhook/telegram/tg-secret", json=other_chat).json()["handled"] == "ignored"


def test_pending_digest_released_on_reply(wired):
    client, db, messenger, _ = wired
    db.prefs["pending_digest"] = {"to": "91XXXXXXXXXX", "parts": ["digest part 1", "digest part 2"], "count": 2}
    payload = json.loads(json.dumps(META_PAYLOAD))
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["text"]["body"] = "Yes"
    body = json.dumps(payload).encode()
    res = client.post("/api/webhook", content=body, headers={"X-Hub-Signature-256": sign("app-secret", body)})
    assert res.json()["handled"] == "digest"
    assert [t for _, t in messenger.sent] == ["digest part 1", "digest part 2"]
    assert "pending_digest" not in db.prefs
    # a real question releases the digest AND gets answered
    db.prefs["pending_digest"] = {"to": "91XXXXXXXXXX", "parts": ["digest again"], "count": 1}
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["id"] = "wamid.DEF"
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["text"]["body"] = "which of these is in Delhi?"
    body = json.dumps(payload).encode()
    res = client.post("/api/webhook", content=body, headers={"X-Hub-Signature-256": sign("app-secret", body)})
    assert res.json()["handled"] == "answered"
    assert [t for _, t in messenger.sent][-2:] == ["digest again", "Reply from agent"]


def test_health(wired):
    client, *_ = wired
    assert client.get("/api/health").json() == {"status": "ok"}
