"""Pydantic models shared by scrapers, extraction, storage and the chat agent."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tender_bhai.dates import parse_indian_datetime
from tender_bhai.money import parse_inr

Fit = Literal["strong", "possible", "skip"]


class Candidate(BaseModel):
    """A tender as seen on a listing page, before any LLM work."""

    model_config = ConfigDict(extra="forbid")

    portal: str
    tender_id: str
    title: str
    buyer_org: str = ""
    state: str = ""
    portal_url: str
    detail_url: str | None = None
    document_url: str | None = None
    published_at: datetime | None = None
    deadline: datetime | None = None
    opening_date: datetime | None = None
    estimated_value_inr: float | None = None
    emd_inr: float | None = None
    listing_text: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tender_id", "title", mode="before")
    @classmethod
    def _strip(cls, value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class DetailResult(BaseModel):
    """What a scraper could fetch for one candidate beyond the listing."""

    text: str = ""
    document_url: str | None = None
    pdf_bytes: bytes | None = None
    captcha_blocked: bool = False
    fields: dict[str, Any] = Field(default_factory=dict)


class Item(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    qty: str | None = None
    specs: str | None = None

    @field_validator("qty", "specs", mode="before")
    @classmethod
    def _to_str(cls, value: Any) -> str | None:
        if value is None or value == "":
            return None
        return str(value)


class Contact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    designation: str | None = None
    phone: str | None = None
    email: str | None = None

    def line(self) -> str:
        parts = [p for p in (self.name, self.designation, self.phone, self.email) if p]
        return ", ".join(parts)


class Eligibility(BaseModel):
    model_config = ConfigDict(extra="ignore")

    min_turnover: str | None = None
    msme_exempt: bool | None = None
    past_performance: str | None = None
    other: str | None = None


class Extraction(BaseModel):
    """Structured output the LLM must produce for one tender."""

    model_config = ConfigDict(extra="ignore")

    buyer_org: str
    department: str | None = None
    items: list[Item] = Field(default_factory=list)
    estimated_value_inr: float | None = None
    emd_inr: float | None = None
    deadline: datetime | None = None
    opening_date: datetime | None = None
    state: str | None = None
    location: str | None = None
    contact: Contact = Field(default_factory=Contact)
    portal_url: str | None = None
    document_url: str | None = None
    eligibility: Eligibility = Field(default_factory=Eligibility)
    fit: Fit
    fit_reason: str
    summary: str

    @field_validator("estimated_value_inr", "emd_inr", mode="before")
    @classmethod
    def _money(cls, value: Any) -> float | None:
        return parse_inr(value)

    @field_validator("deadline", "opening_date", mode="before")
    @classmethod
    def _dates(cls, value: Any) -> datetime | None:
        if value is None or isinstance(value, datetime):
            return value
        return parse_indian_datetime(str(value))

    @field_validator("fit", mode="before")
    @classmethod
    def _fit(cls, value: Any) -> str:
        return str(value or "").strip().lower()


class Tender(BaseModel):
    """A row of the ``tenders`` table."""

    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    portal: str
    tender_id: str
    dedup_hash: str
    title: str
    buyer_org: str
    department: str | None = None
    items: list[Item] = Field(default_factory=list)
    items_text: str = ""
    estimated_value_inr: float | None = None
    emd_inr: float | None = None
    deadline: datetime | None = None
    opening_date: datetime | None = None
    state: str | None = None
    location: str | None = None
    contact: Contact = Field(default_factory=Contact)
    portal_url: str
    document_url: str | None = None
    eligibility: Eligibility = Field(default_factory=Eligibility)
    fit: Fit = "possible"
    fit_reason: str = ""
    summary: str = ""
    raw_text: str = ""
    first_seen_at: datetime | None = None
    sent_at: datetime | None = None
    status: str = "new"

    @field_validator("deadline", "opening_date", "first_seen_at", "sent_at", mode="before")
    @classmethod
    def _dates(cls, value: Any) -> datetime | None:
        if value is None or isinstance(value, datetime):
            return value
        return parse_indian_datetime(str(value))

    def to_row(self) -> dict[str, Any]:
        row = self.model_dump(mode="json", exclude={"id"} if self.id is None else set())
        row["items_text"] = "; ".join(" ".join(p for p in (i.name, i.qty, i.specs) if p) for i in self.items)
        return row

    @classmethod
    def from_parts(cls, candidate: Candidate, extraction: Extraction, raw_text: str) -> Tender:
        buyer = extraction.buyer_org or candidate.buyer_org or "Unknown buyer"
        return cls(
            portal=candidate.portal,
            tender_id=candidate.tender_id,
            dedup_hash=dedup_hash(candidate.title, buyer),
            title=candidate.title,
            buyer_org=buyer,
            department=extraction.department,
            items=extraction.items,
            estimated_value_inr=extraction.estimated_value_inr or candidate.estimated_value_inr,
            emd_inr=extraction.emd_inr or candidate.emd_inr,
            deadline=extraction.deadline or candidate.deadline,
            opening_date=extraction.opening_date or candidate.opening_date,
            state=extraction.state or candidate.state or None,
            location=extraction.location,
            contact=extraction.contact,
            portal_url=candidate.detail_url or candidate.portal_url,
            document_url=extraction.document_url or candidate.document_url,
            eligibility=extraction.eligibility,
            fit=extraction.fit,
            fit_reason=extraction.fit_reason,
            summary=extraction.summary,
            raw_text=raw_text[:20000],
        )


_NORMALISE = re.compile(r"[^a-z0-9]+")


def normalise(text: str) -> str:
    return _NORMALISE.sub(" ", (text or "").lower()).strip()


def dedup_hash(title: str, buyer: str) -> str:
    """Stable hash of normalised title + buyer used for cross-portal dedup."""
    key = normalise(title) + "|" + normalise(buyer)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


class Preference(BaseModel):
    key: str
    value: Any
    updated_at: datetime | None = None


class Preferences(BaseModel):
    """Preferences learned from chat that feed the nightly run."""

    min_value: int = 600_000
    ignore_states: list[str] = Field(default_factory=list)
    extra_keywords: list[str] = Field(default_factory=list)
    ignore_keywords: list[str] = Field(default_factory=list)
    only_states: list[str] = Field(default_factory=list)

    @classmethod
    def from_rows(cls, rows: list[dict[str, Any]], default_min_value: int = 600_000) -> Preferences:
        data: dict[str, Any] = {"min_value": default_min_value}
        for row in rows:
            data[row["key"]] = row.get("value")
        known = {k: v for k, v in data.items() if k in cls.model_fields and v is not None}
        return cls.model_validate(known)
