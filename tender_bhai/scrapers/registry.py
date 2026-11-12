"""Every scraper the nightly run knows about."""

from __future__ import annotations

from tender_bhai.scrapers.base import BaseScraper, ScrapeContext
from tender_bhai.scrapers.cppp import CpppScraper
from tender_bhai.scrapers.gem import GemScraper
from tender_bhai.scrapers.ireps import IrepsScraper
from tender_bhai.scrapers.nic_states import nic_scrapers
from tender_bhai.scrapers.sai import SaiScraper
from tender_bhai.scrapers.states import state_scrapers


def all_scrapers(ctx: ScrapeContext, only: set[str] | None = None) -> list[BaseScraper]:
    scrapers: list[BaseScraper] = [GemScraper(ctx), CpppScraper(ctx), SaiScraper(ctx), IrepsScraper(ctx)]
    scrapers.extend(nic_scrapers(ctx))
    scrapers.extend(state_scrapers(ctx))
    if only:
        scrapers = [s for s in scrapers if s.name in only]
    return scrapers


def scraper_names(ctx: ScrapeContext) -> list[str]:
    return [s.name for s in all_scrapers(ctx)]
