"""Pipeline behaviour with fake scrapers: one portal failing never kills the run."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tender_bhai import pipeline
from tender_bhai.models import Candidate, DetailResult, Extraction
from tender_bhai.scrapers.base import BaseScraper, ScrapeContext, ScrapeResult
from tender_bhai.scrapers.registry import all_scrapers, scraper_names


def cand(portal: str, tid: str, title: str, value: float | None = None, state: str = "Uttar Pradesh") -> Candidate:
    return Candidate(
        portal=portal, tender_id=tid, title=title, buyer_org="Directorate of Sports", state=state,
        portal_url=f"https://{portal}.example/listing", listing_text=title, estimated_value_inr=value,
    )  # fmt: skip


class GoodScraper(BaseScraper):
    name = "good"

    async def scrape(self) -> ScrapeResult:
        return ScrapeResult(
            portal=self.name,
            candidates=[
                cand("good", "A", "Supply of cricket kits", 2_000_000),
                cand("good", "B", "Supply of cricket kits", 2_000_000),  # same title+buyer -> dedup
                cand("good", "C", "Tracksuits for school teams", 300_000),  # below floor after detail
                cand("good", "D", "Sports goods for Assam schools", None, state="Assam"),  # ignored state
                cand("good", "E", "Gym equipment", None),  # unknown value -> kept
            ],
            pages_fetched=1,
        )

    async def fetch_detail(self, candidate: Candidate) -> DetailResult:
        if candidate.tender_id == "E":
            return DetailResult(text="Tender Value in ₹ 9,00,000", fields={"tender_value_inr": 900_000})
        return DetailResult(text="detail")


class CrashingScraper(BaseScraper):
    name = "crash"

    async def scrape(self) -> ScrapeResult:
        raise RuntimeError("portal exploded")


class SlowScraper(BaseScraper):
    name = "slow"

    async def scrape(self) -> ScrapeResult:
        await asyncio.sleep(5)
        return ScrapeResult(portal=self.name)


class FakeExtractor:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def extract(self, candidate: Candidate, detail_text: str = "", pdf_text: str = "") -> Extraction:
        self.seen.append(candidate.tender_id)
        fit = "skip" if candidate.tender_id == "E" else "strong"
        return Extraction(
            buyer_org="Directorate of Sports", items=[{"name": candidate.title}], fit=fit,
            fit_reason="r", summary="s", estimated_value_inr=candidate.estimated_value_inr,
        )  # fmt: skip


class FakeDB:
    def __init__(self) -> None:
        self.saved: list[Any] = []

    def preference_rows(self) -> list[dict[str, Any]]:
        return [{"key": "ignore_states", "value": ["Assam"]}, {"key": "min_value", "value": 600000}]

    def known_tender_keys(self):
        return set(), set()

    def save_tenders(self, tenders):
        self.saved.extend(tenders)
        return tenders


@pytest.fixture
def fake_registry(monkeypatch, settings):
    def fake_all(ctx: ScrapeContext, only: set[str] | None = None):
        scrapers = [GoodScraper(ctx), CrashingScraper(ctx), SlowScraper(ctx)]
        return [s for s in scrapers if not only or s.name in only]

    monkeypatch.setattr(pipeline, "all_scrapers", fake_all)
    return settings


def test_nightly_survives_failures_and_filters(fake_registry):
    cfg = fake_registry.__class__(**{**fake_registry.__dict__, "portal_timeout_seconds": 1})
    db = FakeDB()
    extractor = FakeExtractor()
    report = asyncio.run(pipeline.nightly(cfg, db, extractor))
    assert report.portal_counts["good"]["status"] == "ok"
    assert report.portal_counts["crash"]["status"] == "failed"
    assert report.portal_counts["slow"]["status"] == "failed"
    assert any("timed out" in e["error"] for e in report.errors)
    assert any("exploded" in e["error"] for e in report.errors)
    assert report.status == "partial"
    assert report.candidates == 5
    assert report.after_dedup == 3  # B deduped by title+buyer, D dropped by ignore_states
    assert sorted(extractor.seen) == ["A", "E"]  # C skipped by value floor before the LLM
    assert report.skipped_value == 1
    assert report.skipped_fit == 1
    assert [t.tender_id for t in report.new_tenders] == ["A"]
    assert db.saved and db.saved[0].tender_id == "A"
    assert db.saved[0].estimated_value_inr == 2_000_000


def test_plausible_value_guard():
    assert pipeline.plausible_value(2_500_000, 50_000) == 2_500_000
    assert pipeline.plausible_value(1799, 26_250) is None  # per-unit "ECV" seen on KPPP
    assert pipeline.plausible_value(None, 100) is None
    assert pipeline.plausible_value(900_000, None) == 900_000
    candidate = cand("good", "Z", "Sports material", 1799)
    pipeline._apply_detail_fields(candidate, DetailResult(fields={"emd_inr": 26_250}))
    assert candidate.estimated_value_inr is None and candidate.emd_inr == 26_250


def test_nightly_dry_run_does_not_write(fake_registry):
    db = FakeDB()
    report = asyncio.run(pipeline.nightly(fake_registry, db, FakeExtractor(), dry_run=True, only_portals={"good"}))
    assert [t.tender_id for t in report.new_tenders] == ["A"]
    assert db.saved == []
    assert report.status == "ok"


def test_nightly_without_extractor_stops_after_dedup(fake_registry):
    report = asyncio.run(pipeline.nightly(fake_registry, None, None, only_portals={"good"}))
    assert report.after_dedup == 4  # no DB -> no preference filtering, in-run dedup only
    assert report.new_tenders == []


def test_real_registry_lists_every_portal(settings):
    ctx = ScrapeContext(settings=settings)
    names = scraper_names(ctx)
    for expected in ("gem", "cppp", "sai", "ireps", "nic_up", "nic_delhi", "nic_chhattisgarh"):
        assert expected in names
    assert len(names) == len(set(names))
    assert len(all_scrapers(ctx, {"sai"})) == 1
