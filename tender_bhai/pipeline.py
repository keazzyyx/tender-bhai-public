"""Nightly pipeline: scrape -> keyword filter -> dedup -> detail+PDF -> LLM extract -> store."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from tender_bhai import obs
from tender_bhai.config import Settings
from tender_bhai.db import Database
from tender_bhai.dedup import dedup_candidates, filter_known, passes_value_floor
from tender_bhai.extract import ExtractionError, Extractor
from tender_bhai.keywords import ALL_KEYWORDS
from tender_bhai.models import Candidate, DetailResult, Preferences, Tender
from tender_bhai.pdf import pdf_to_text
from tender_bhai.scrapers.base import BaseScraper, ScrapeContext, ScrapeResult
from tender_bhai.scrapers.registry import all_scrapers

log = logging.getLogger(__name__)

CONCURRENT_PORTALS = 4


@dataclass
class RunReport:
    portal_counts: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: list[dict[str, str]] = field(default_factory=list)
    candidates: int = 0
    after_dedup: int = 0
    extracted: int = 0
    skipped_fit: int = 0
    skipped_value: int = 0
    new_tenders: list[Tender] = field(default_factory=list)

    @property
    def status(self) -> str:
        failed = [p for p, c in self.portal_counts.items() if c.get("status") in {"failed", "blocked"}]
        if not self.portal_counts:
            return "failed"
        if len(failed) == len(self.portal_counts):
            return "failed"
        return "partial" if failed or self.errors else "ok"

    def as_row(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "portal_counts": self.portal_counts,
            "candidates": self.candidates,
            "extracted": self.extracted,
            "new_tenders": len(self.new_tenders),
            "errors": self.errors,
        }


def build_context(cfg: Settings, prefs: Preferences) -> ScrapeContext:
    return ScrapeContext(
        settings=cfg,
        keywords=ALL_KEYWORDS,
        extra_keywords=prefs.extra_keywords,
        ignore_keywords=prefs.ignore_keywords,
        max_pages=cfg.max_pages_per_portal,
        max_candidates=cfg.max_candidates_per_portal,
        delay_seconds=cfg.request_delay_seconds,
        proxy_url=os.getenv("SCRAPER_PROXY_URL") or None,
    )


async def run_scraper(scraper: BaseScraper, timeout: float) -> ScrapeResult:
    traced = obs.span(kind="TOOL", name=f"scrape_{scraper.name}", tool_name=scraper.name)(scraper.scrape)
    try:
        return await asyncio.wait_for(traced(), timeout=timeout)
    except TimeoutError:
        result = ScrapeResult(portal=scraper.name)
        result.fail(f"timed out after {int(timeout)}s")
        return result
    except Exception as exc:  # one portal must never kill the run
        log.exception("%s crashed", scraper.name)
        result = ScrapeResult(portal=scraper.name)
        result.fail(f"{type(exc).__name__}: {exc}")
        return result


async def scrape_all(
    scrapers: list[BaseScraper], timeout: float, report: RunReport
) -> list[tuple[BaseScraper, Candidate]]:
    semaphore = asyncio.Semaphore(CONCURRENT_PORTALS)

    async def guarded(scraper: BaseScraper) -> tuple[BaseScraper, ScrapeResult]:
        async with semaphore:
            log.info("scraping %s", scraper.name)
            return scraper, await run_scraper(scraper, timeout)

    pairs = await asyncio.gather(*(guarded(s) for s in scrapers))
    found: list[tuple[BaseScraper, Candidate]] = []
    for scraper, result in pairs:
        report.portal_counts[scraper.name] = {
            "status": result.status,
            "candidates": len(result.candidates),
            "pages": result.pages_fetched,
            "errors": len(result.errors),
        }
        for error in result.errors:
            report.errors.append({"portal": scraper.name, "error": error})
        found.extend((scraper, c) for c in result.candidates)
        log.info(
            "%s: %s, %d candidates, %d errors", scraper.name, result.status, len(result.candidates), len(result.errors)
        )
    return found


def _state_blocked(candidate: Candidate, prefs: Preferences) -> bool:
    state = (candidate.state or "").lower()
    if prefs.only_states and state and not any(s.lower() in state for s in prefs.only_states):
        return True
    return bool(state and any(s.lower() in state for s in prefs.ignore_states))


async def enrich(scraper: BaseScraper, candidate: Candidate) -> DetailResult:
    try:
        return await asyncio.wait_for(scraper.fetch_detail(candidate), timeout=120)
    except Exception as exc:
        log.info("detail fetch failed for %s/%s: %s", candidate.portal, candidate.tender_id, exc)
        return DetailResult()


def plausible_value(value: float | None, emd: float | None) -> float | None:
    """Portals sometimes expose a per-unit or placeholder "ECV"; EMD is normally 1-5% of the value,
    so a value below 10x the EMD is not a tender total and must not trigger the value floor."""
    if value is None or value <= 0:
        return None
    if emd and value < 10 * emd:
        return None
    return float(value)


def _apply_detail_fields(candidate: Candidate, detail: DetailResult) -> None:
    fields = detail.fields or {}
    emd = fields.get("emd_inr")
    if emd and not candidate.emd_inr:
        candidate.emd_inr = float(emd)
    value = plausible_value(fields.get("tender_value_inr") or fields.get("estimated_value_inr"), candidate.emd_inr)
    if value and not candidate.estimated_value_inr:
        candidate.estimated_value_inr = value
    elif candidate.estimated_value_inr and plausible_value(candidate.estimated_value_inr, candidate.emd_inr) is None:
        candidate.estimated_value_inr = None
    if fields.get("deadline") and not candidate.deadline:
        candidate.deadline = fields["deadline"]
    if detail.document_url and not candidate.document_url:
        candidate.document_url = detail.document_url


@obs.span(kind="WORKFLOW", name="nightly_run")
async def nightly(
    cfg: Settings,
    db: Database | None,
    extractor: Extractor | None,
    *,
    dry_run: bool = False,
    only_portals: set[str] | None = None,
    max_extract: int = 80,
) -> RunReport:
    report = RunReport()
    prefs = Preferences(min_value=cfg.min_value_inr)
    if db is not None:
        try:
            prefs = Preferences.from_rows(db.preference_rows(), cfg.min_value_inr)
        except Exception as exc:
            log.warning("could not load preferences, using defaults: %s", exc)
    ctx = build_context(cfg, prefs)
    scrapers = all_scrapers(ctx, only_portals)
    found = await scrape_all(scrapers, cfg.portal_timeout_seconds, report)
    report.candidates = len(found)

    by_scraper = {s.name: s for s, _ in found}
    candidates = dedup_candidates(c for _, c in found)
    candidates = [c for c in candidates if not _state_blocked(c, prefs)]
    if db is not None:
        try:
            known_ids, known_hashes = db.known_tender_keys()
            candidates = filter_known(candidates, known_ids, known_hashes)
        except Exception as exc:
            report.errors.append({"portal": "db", "error": f"dedup lookup failed: {exc}"})
    report.after_dedup = len(candidates)
    log.info("%d candidates after dedup (%d raw)", len(candidates), report.candidates)

    if extractor is None:
        log.warning("no extractor configured - stopping after dedup")
        return report

    for candidate in candidates[:max_extract]:
        scraper = by_scraper[candidate.portal]
        detail = await enrich(scraper, candidate)
        _apply_detail_fields(candidate, detail)
        if not passes_value_floor(candidate.estimated_value_inr, prefs.min_value):
            report.skipped_value += 1
            continue
        pdf_text = pdf_to_text(detail.pdf_bytes) if detail.pdf_bytes else ""
        try:
            extraction = await asyncio.to_thread(extractor.extract, candidate, detail.text, pdf_text)
        except ExtractionError as exc:
            report.errors.append({"portal": candidate.portal, "error": f"extract {candidate.tender_id}: {exc}"[:300]})
            continue
        except Exception as exc:
            report.errors.append(
                {
                    "portal": candidate.portal,
                    "error": f"extract {candidate.tender_id}: {type(exc).__name__}: {exc}"[:300],
                }
            )
            continue
        report.extracted += 1
        if extraction.fit == "skip":
            report.skipped_fit += 1
            continue
        if not passes_value_floor(extraction.estimated_value_inr or candidate.estimated_value_inr, prefs.min_value):
            report.skipped_value += 1
            continue
        raw_text = "\n\n".join(p for p in (candidate.listing_text, detail.text, pdf_text[:6000]) if p)
        report.new_tenders.append(Tender.from_parts(candidate, extraction, raw_text))

    if db is not None and not dry_run and report.new_tenders:
        saved = db.save_tenders(report.new_tenders)
        if saved:
            report.new_tenders = saved
    return report
