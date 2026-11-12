from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from tender_bhai.config import IST
from tender_bhai.delivery import PENDING_KEY, deliver_digest, release_pending_digest, within_window
from tender_bhai.digest import FOOTER, build_digest, format_tender, split_message
from tender_bhai.messaging.base import Messenger, SendResult
from tender_bhai.messaging.telegram import whatsapp_to_telegram_html
from tests.conftest import make_tender

REF = datetime(2026, 11, 10, 8, 30, tzinfo=IST)


def test_format_tender_block():
    text = format_tender(make_tender(), 1, REF)
    assert text.startswith("*1. Directorate of Sports, UP* (Sports)")
    assert "Cricket bat × 500; Leather ball × 2000" in text
    assert "₹25 lakh | EMD ₹50,000" in text
    assert "Closes: 14 Nov 2026, 3:00 PM (4 days)" in text
    assert "📞 Sri Verma, Purchase Officer, 0522-1234567" in text
    assert "🔗 https://etender.up.nic.in" in text
    assert "🟢 strong — Cricket kits are the core product line" in text


def test_build_digest_header_reminders_footer():
    new = [
        make_tender(),
        make_tender(
            id="2",
            fit="possible",
            estimated_value_inr=None,
            emd_inr=None,
            buyer_org="KVS Delhi",
            deadline="2026-11-12T10:00+05:30",
        ),
    ]
    reminder = make_tender(id="3", buyer_org="Punjab Police", deadline="2026-11-11T17:00+05:30")
    digest = build_digest(new, [reminder, new[0]], REF)
    assert digest.startswith("🏏 *Tender Bhai — Tue, 10 Nov 2026*\n2 new tenders worth a look:")
    assert "*2. KVS Delhi*" in digest
    assert "value not stated" in digest
    assert "🟡 possible" in digest
    assert "⏳ *Closing in next 3 days* (sent earlier)" in digest
    assert "Punjab Police" in digest
    assert digest.count("Directorate of Sports, UP") == 1  # not repeated in reminders
    assert digest.rstrip().endswith(f"_{FOOTER}_")
    assert "No new matching tenders today." in build_digest([], [], REF)


def test_split_message_respects_limit_and_blocks():
    blocks = [f"*{i}. Buyer {i}*\n" + ("line of detail " * 20) for i in range(40)]
    text = "\n\n".join(blocks)
    parts = split_message(text, limit=1000)
    assert len(parts) > 1
    assert all(len(p) <= 1000 for p in parts)
    assert "".join(p.split("\n", 1)[1] if p.startswith("(") else p for p in parts).replace("\n", "") == text.replace(
        "\n", ""
    )
    assert parts[0].startswith(f"(1/{len(parts)})")
    for part in parts:
        body = part.split("\n", 1)[1]
        assert not body.startswith("\n")
        assert body.startswith("*")  # every part begins at a block boundary
    assert split_message("short") == ["short"]


def test_split_message_handles_oversized_line():
    long_line = "word " * 3000
    parts = split_message(long_line.strip(), limit=4000)
    assert all(len(p) <= 4000 for p in parts)
    assert len(parts) >= 4


def test_whatsapp_to_telegram_html():
    assert whatsapp_to_telegram_html("*Bold* and _it_ <x> & y") == "<b>Bold</b> and <i>it</i> &lt;x&gt; &amp; y"
    assert whatsapp_to_telegram_html("https://a.b/c_d_e") == "https://a.b/c_d_e"


class FakeMessenger(Messenger):
    name = "fake"

    def __init__(self, window: bool, fail: bool = False) -> None:
        self.has_session_window = window
        self.fail = fail
        self.texts: list[str] = []
        self.templates: list[tuple[str, list[str]]] = []

    def send_text(self, to: str, text: str) -> SendResult:
        if self.fail:
            return SendResult(ok=False, error="boom")
        self.texts.append(text)
        return SendResult(ok=True, message_id=f"m{len(self.texts)}")

    def send_document(self, to: str, url: str, filename: str, caption: str | None = None) -> SendResult:
        return SendResult(ok=True, message_id="doc")

    def send_template(self, to: str, template: str, variables: list[str]) -> SendResult:
        self.templates.append((template, variables))
        return SendResult(ok=True, message_id="tpl")


class FakeStore:
    def __init__(self, last_inbound: datetime | None) -> None:
        self._last = last_inbound
        self.prefs: dict[str, Any] = {}
        self.messages: list[tuple[str, str, str]] = []

    def last_inbound_at(self, phone: str) -> datetime | None:
        return self._last

    def get_preference(self, key: str, default: Any = None) -> Any:
        return self.prefs.get(key, default)

    def set_preference(self, key: str, value: Any) -> None:
        self.prefs[key] = value

    def delete_preference(self, key: str) -> None:
        self.prefs.pop(key, None)

    def add_message(self, direction: str, phone: str, text: str, wa_message_id: str | None = None) -> None:
        self.messages.append((direction, phone, text))


def test_within_window():
    now = REF
    assert within_window(now - timedelta(hours=23), now)
    assert not within_window(now - timedelta(hours=25), now)
    assert not within_window(None, now)


def test_deliver_inside_window_sends_directly():
    messenger = FakeMessenger(window=True)
    store = FakeStore(REF - timedelta(hours=2))
    assert deliver_digest(messenger, store, "91XXXXXXXXXX", ["part 1", "part 2"], 2, REF) == "sent"
    assert messenger.texts == ["part 1", "part 2"]
    assert not messenger.templates
    assert PENDING_KEY not in store.prefs
    assert [m[0] for m in store.messages] == ["out", "out"]


def test_deliver_outside_window_sends_template_and_parks_digest():
    messenger = FakeMessenger(window=True)
    store = FakeStore(REF - timedelta(hours=30))
    assert deliver_digest(messenger, store, "91XXXXXXXXXX", ["part 1"], 3, REF) == "template"
    assert messenger.templates == [("daily_tenders", ["3"])]
    assert messenger.texts == []
    assert store.prefs[PENDING_KEY]["parts"] == ["part 1"]
    # he replies -> digest released and cleared
    assert release_pending_digest(messenger, store, "91XXXXXXXXXX") is True
    assert messenger.texts == ["part 1"]
    assert PENDING_KEY not in store.prefs
    assert release_pending_digest(messenger, store, "91XXXXXXXXXX") is False


def test_deliver_without_window_provider_ignores_24h_rule():
    messenger = FakeMessenger(window=False)
    store = FakeStore(None)
    assert deliver_digest(messenger, store, "777", ["hello"], 1, REF) == "sent"
    assert messenger.templates == []


def test_deliver_failure_reported():
    messenger = FakeMessenger(window=False, fail=True)
    assert deliver_digest(messenger, FakeStore(None), "777", ["x"], 1, REF) == "failed"
