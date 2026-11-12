"""Thin Supabase (PostgREST) client over httpx. No ORM.

Every method maps to one REST call. Filters follow PostgREST syntax
(``column=eq.value``, ``or=(a.ilike.*x*,b.ilike.*x*)``).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

import httpx

from tender_bhai.dates import now_ist, to_iso
from tender_bhai.models import Tender

log = logging.getLogger(__name__)

_UNSAFE = re.compile(r"[,()\"'\\%*]")


def _clean(term: str) -> str:
    """Strip characters that have meaning in PostgREST filters."""
    return _UNSAFE.sub(" ", term or "").strip()


def _quote_in(values: list[str]) -> str:
    quoted = ",".join('"' + v.replace('"', "") + '"' for v in values)
    return f"in.({quoted})"


class DatabaseError(RuntimeError):
    pass


class Database:
    def __init__(self, url: str, service_key: str, timeout: float = 25.0) -> None:
        if not url or not service_key:
            raise DatabaseError("SUPABASE_URL and SUPABASE_SERVICE_KEY are required")
        self.base = url.rstrip("/") + "/rest/v1/"
        self._client = httpx.Client(
            timeout=timeout,
            headers={
                "apikey": service_key,
                "Authorization": f"Bearer {service_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

    def close(self) -> None:
        self._client.close()

    # -- low level ---------------------------------------------------------
    def _request(
        self,
        method: str,
        table: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        prefer: str | None = None,
    ) -> Any:
        headers = {"Prefer": prefer} if prefer else {}
        try:
            response = self._client.request(method, self.base + table, params=params, json=json, headers=headers)
        except httpx.HTTPError as exc:
            raise DatabaseError(f"{method} {table}: {exc}") from exc
        if response.status_code >= 400:
            raise DatabaseError(f"{method} {table} -> {response.status_code}: {response.text[:300]}")
        if not response.content:
            return []
        try:
            return response.json()
        except ValueError:
            return []

    def select(self, table: str, **params: Any) -> list[dict[str, Any]]:
        return list(self._request("GET", table, params=params))

    def insert(self, table: str, rows: list[dict[str, Any]] | dict[str, Any]) -> list[dict[str, Any]]:
        return list(self._request("POST", table, json=rows, prefer="return=representation"))

    def upsert(self, table: str, rows: list[dict[str, Any]] | dict[str, Any], on_conflict: str) -> list[dict[str, Any]]:
        return list(
            self._request(
                "POST",
                table,
                params={"on_conflict": on_conflict},
                json=rows,
                prefer="resolution=merge-duplicates,return=representation",
            )
        )

    def update(self, table: str, values: dict[str, Any], **filters: Any) -> list[dict[str, Any]]:
        return list(self._request("PATCH", table, params=filters, json=values, prefer="return=representation"))

    # -- tenders -------------------------------------------------------------
    def known_tender_keys(self, days: int = 120) -> tuple[set[tuple[str, str]], set[str]]:
        since = to_iso(now_ist() - timedelta(days=days))
        rows = self.select(
            "tenders",
            select="portal,tender_id,dedup_hash",
            first_seen_at=f"gte.{since}",
            limit=20000,
        )
        ids = {(r["portal"], r["tender_id"]) for r in rows}
        hashes = {r["dedup_hash"] for r in rows if r.get("dedup_hash")}
        return ids, hashes

    def save_tenders(self, tenders: list[Tender]) -> list[Tender]:
        if not tenders:
            return []
        rows = [t.to_row() for t in tenders]
        saved = self.upsert("tenders", rows, on_conflict="portal,tender_id")
        return [Tender.model_validate(r) for r in saved]

    def unsent_tenders(self, limit: int = 40) -> list[Tender]:
        now = to_iso(now_ist())
        rows = self.select(
            "tenders",
            select="*",
            sent_at="is.null",
            status="eq.new",
            **{"or": f"(deadline.is.null,deadline.gte.{now})"},
            order="first_seen_at.desc",
            limit=limit,
        )
        tenders = [Tender.model_validate(r) for r in rows]
        rank = {"strong": 0, "possible": 1, "skip": 2}
        tenders.sort(key=lambda t: (rank.get(t.fit, 1), t.deadline or datetime.max.replace(tzinfo=now_ist().tzinfo)))
        return tenders

    def mark_sent(self, ids: list[str], when: datetime | None = None) -> None:
        if not ids:
            return
        self.update(
            "tenders",
            {"sent_at": to_iso(when or now_ist()), "status": "sent"},
            id=_quote_in(ids),
        )

    def get_tender(self, tender_id: str) -> Tender | None:
        rows = self.select("tenders", select="*", id=f"eq.{_clean(tender_id)}", limit=1)
        return Tender.model_validate(rows[0]) if rows else None

    def closing_soon(self, days: int, sent_only: bool = True) -> list[Tender]:
        now = now_ist()
        params: dict[str, Any] = {
            "select": "*",
            "deadline": f"gte.{to_iso(now)}",
            "order": "deadline.asc",
            "limit": 30,
        }
        params["and"] = f"(deadline.lte.{to_iso(now + timedelta(days=days))})"
        if sent_only:
            params["sent_at"] = "not.is.null"
        rows = self.select("tenders", **params)
        return [Tender.model_validate(r) for r in rows]

    def search_tenders(
        self,
        query: str | None = None,
        state: str | None = None,
        min_value: float | None = None,
        closing_before: datetime | None = None,
        limit: int = 10,
        include_closed: bool = False,
    ) -> list[Tender]:
        params: dict[str, Any] = {"select": "*", "order": "deadline.asc.nullslast", "limit": limit}
        ands: list[str] = []
        if query:
            term = _clean(query)
            if term:
                like = f"*{term}*"
                ands.append(
                    f"or(title.ilike.{like},buyer_org.ilike.{like},items_text.ilike.{like},"
                    f"summary.ilike.{like},department.ilike.{like},location.ilike.{like})"
                )
        if state:
            term = _clean(state)
            ands.append(f"or(state.ilike.*{term}*,location.ilike.*{term}*,buyer_org.ilike.*{term}*)")
        if min_value:
            ands.append(f"estimated_value_inr.gte.{float(min_value)}")
        if closing_before is not None:
            ands.append(f"deadline.lte.{to_iso(closing_before)}")
        if not include_closed:
            ands.append(f"or(deadline.is.null,deadline.gte.{to_iso(now_ist())})")
        if ands:
            params["and"] = "(" + ",".join(ands) + ")"
        rows = self.select("tenders", **params)
        return [Tender.model_validate(r) for r in rows]

    def count_tenders(self, since: datetime, sent: bool | None = None) -> int:
        params: dict[str, Any] = {"select": "id", "first_seen_at": f"gte.{to_iso(since)}", "limit": 5000}
        if sent is True:
            params["sent_at"] = "not.is.null"
        elif sent is False:
            params["sent_at"] = "is.null"
        return len(self.select("tenders", **params))

    # -- runs ----------------------------------------------------------------
    def start_run(self, dry_run: bool = False) -> str:
        rows = self.insert(
            "runs",
            {
                "started_at": to_iso(now_ist()),
                "status": "running",
                "dry_run": dry_run,
                "portal_counts": {},
                "errors": [],
            },
        )
        return str(rows[0]["id"])

    def finish_run(self, run_id: str, values: dict[str, Any]) -> None:
        values = {**values, "finished_at": to_iso(now_ist())}
        self.update("runs", values, id=f"eq.{run_id}")

    def recent_runs(self, since: datetime) -> list[dict[str, Any]]:
        return self.select("runs", select="*", started_at=f"gte.{to_iso(since)}", order="started_at.desc", limit=50)

    # -- messages --------------------------------------------------------------
    def message_exists(self, wa_message_id: str) -> bool:
        if not wa_message_id:
            return False
        rows = self.select("messages", select="id", wa_message_id=f"eq.{_clean(wa_message_id)}", limit=1)
        return bool(rows)

    def add_message(self, direction: str, phone: str, text: str, wa_message_id: str | None = None) -> None:
        self.insert(
            "messages",
            {
                "direction": direction,
                "phone": phone,
                "text": text[:8000],
                "wa_message_id": wa_message_id,
                "created_at": to_iso(now_ist()),
            },
        )

    def recent_messages(self, phone: str, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.select(
            "messages",
            select="direction,text,created_at",
            phone=f"eq.{_clean(phone)}",
            order="created_at.desc",
            limit=limit,
        )
        return list(reversed(rows))

    def last_inbound_at(self, phone: str) -> datetime | None:
        rows = self.select(
            "messages",
            select="created_at",
            phone=f"eq.{_clean(phone)}",
            direction="eq.in",
            order="created_at.desc",
            limit=1,
        )
        if not rows:
            return None
        return datetime.fromisoformat(rows[0]["created_at"].replace("Z", "+00:00"))

    # -- preferences -----------------------------------------------------------
    def preference_rows(self) -> list[dict[str, Any]]:
        return self.select("preferences", select="key,value,updated_at")

    def get_preference(self, key: str, default: Any = None) -> Any:
        rows = self.select("preferences", select="value", key=f"eq.{_clean(key)}", limit=1)
        return rows[0]["value"] if rows else default

    def set_preference(self, key: str, value: Any) -> None:
        self.upsert(
            "preferences",
            {"key": key, "value": value, "updated_at": to_iso(now_ist())},
            on_conflict="key",
        )

    def delete_preference(self, key: str) -> None:
        self._request("DELETE", "preferences", params={"key": f"eq.{_clean(key)}"})
