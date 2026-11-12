from __future__ import annotations

import json

import httpx
import pytest
import respx

from tender_bhai.dates import now_ist
from tender_bhai.db import Database, DatabaseError
from tender_bhai.messaging.telegram import TelegramMessenger
from tender_bhai.messaging.whatsapp import WhatsAppMessenger
from tests.conftest import make_tender

BASE = "https://example.supabase.co/rest/v1/"


@pytest.fixture
def db() -> Database:
    return Database("https://example.supabase.co", "service-key")


@respx.mock
def test_save_tenders_upserts_with_merge(db):
    route = respx.post(BASE + "tenders").mock(
        return_value=httpx.Response(201, json=[{**make_tender().to_row(), "id": "abc"}])
    )
    saved = db.save_tenders([make_tender(id=None)])
    assert saved[0].id == "abc"
    request = route.calls[0].request
    assert request.url.params["on_conflict"] == "portal,tender_id"
    assert "merge-duplicates" in request.headers["Prefer"]
    assert request.headers["apikey"] == "service-key"
    assert request.headers["Authorization"] == "Bearer service-key"


@respx.mock
def test_search_filters_are_postgrest_syntax(db):
    route = respx.get(BASE + "tenders").mock(return_value=httpx.Response(200, json=[]))
    db.search_tenders(query="cricket, (bats)", state="Delhi", min_value=1_000_000)
    params = route.calls[0].request.url.params
    assert params["order"] == "deadline.asc.nullslast"
    combined = params["and"]
    assert "title.ilike.*cricket   bats*" in combined
    assert "state.ilike.*Delhi*" in combined
    assert "estimated_value_inr.gte.1000000.0" in combined
    assert "deadline.is.null" in combined


@respx.mock
def test_known_keys_and_mark_sent(db):
    respx.get(BASE + "tenders").mock(
        return_value=httpx.Response(200, json=[{"portal": "gem", "tender_id": "G1", "dedup_hash": "h1"}])
    )
    ids, hashes = db.known_tender_keys()
    assert ids == {("gem", "G1")} and hashes == {"h1"}
    patch = respx.patch(BASE + "tenders").mock(return_value=httpx.Response(200, json=[]))
    db.mark_sent(["a", "b"])
    assert patch.calls[0].request.url.params["id"] == 'in.("a","b")'
    assert json.loads(patch.calls[0].request.content)["status"] == "sent"


@respx.mock
def test_messages_and_preferences(db):
    respx.get(BASE + "messages").mock(
        return_value=httpx.Response(200, json=[{"created_at": "2026-11-10T02:30:00+00:00"}])
    )
    assert db.last_inbound_at("91XXXXXXXXXX").year == 2026
    post = respx.post(BASE + "preferences").mock(return_value=httpx.Response(201, json=[]))
    db.set_preference("min_value", 1_000_000)
    assert post.calls[0].request.url.params["on_conflict"] == "key"
    respx.get(BASE + "preferences").mock(return_value=httpx.Response(200, json=[{"value": [1]}]))
    assert db.get_preference("ignore_states") == [1]


@respx.mock
def test_errors_surface_as_database_error(db):
    respx.get(BASE + "runs").mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(DatabaseError):
        db.recent_runs(now_ist())


def test_database_requires_config():
    with pytest.raises(DatabaseError):
        Database("", "")


@respx.mock
def test_whatsapp_payloads():
    url = "https://graph.facebook.com/v21.0/12345/messages"
    route = respx.post(url).mock(return_value=httpx.Response(200, json={"messages": [{"id": "wamid.1"}]}))
    wa = WhatsAppMessenger("tok", "12345")
    assert wa.send_text("91XXXXXXXXXX", "hello").message_id == "wamid.1"
    body = json.loads(route.calls[0].request.read())
    assert body["type"] == "text" and body["text"]["body"] == "hello"
    assert route.calls[0].request.headers["Authorization"] == "Bearer tok"
    wa.send_template("91XXXXXXXXXX", "daily_tenders", ["4"])
    tpl = json.loads(route.calls[1].request.read())
    assert tpl["template"]["name"] == "daily_tenders"
    assert tpl["template"]["language"] == {"code": "en"}
    assert tpl["template"]["components"][0]["parameters"] == [{"type": "text", "text": "4"}]
    wa.send_document("91XXXXXXXXXX", "https://x/y.pdf", "y.pdf", caption="NIT")
    assert '"document"' in route.calls[2].request.read().decode()
    respx.post(url).mock(return_value=httpx.Response(400, json={"error": {"message": "bad"}}))
    assert wa.send_text("91XXXXXXXXXX", "x").ok is False


@respx.mock
def test_telegram_payloads_and_fallback():
    url = "https://api.telegram.org/bottg/sendMessage"
    route = respx.post(url).mock(
        side_effect=[
            httpx.Response(400, json={"ok": False, "description": "Bad Request: can't parse entities"}),
            httpx.Response(200, json={"ok": True, "result": {"message_id": 9}}),
        ]
    )
    tg = TelegramMessenger("tg")
    result = tg.send_text("777", "*hi* _there_")
    assert result.ok and result.message_id == "9"
    first, second = (c.request.read().decode() for c in route.calls)
    assert "<b>hi</b>" in first and "parse_mode" in first
    assert "parse_mode" not in second
