"""The NIC state portals (same nicgep template, one scraper parameterised by host).

All verified 2026-10-30 on nicgep 1.09.24 except cgtenders.gov.in, whose hostname did not
resolve (NXDOMAIN) - it stays in the list so the run reports it instead of forgetting it.
"""

from __future__ import annotations

from dataclasses import dataclass

from tender_bhai.scrapers.base import ScrapeContext
from tender_bhai.scrapers.nicgep import NicGepScraper


@dataclass(frozen=True)
class Portal:
    name: str
    state: str
    host: str
    app_path: str = "/nicgep/app"

    @property
    def base_url(self) -> str:
        return f"https://{self.host}{self.app_path}"


NIC_PORTALS: tuple[Portal, ...] = (
    Portal("nic_up", "Uttar Pradesh", "etender.up.nic.in"),
    Portal("nic_delhi", "Delhi", "govtprocurement.delhi.gov.in"),
    Portal("nic_haryana", "Haryana", "etenders.hry.nic.in"),
    Portal("nic_punjab", "Punjab", "eproc.punjab.gov.in"),
    Portal("nic_maharashtra", "Maharashtra", "mahatenders.gov.in"),
    Portal("nic_rajasthan", "Rajasthan", "eproc.rajasthan.gov.in"),
    Portal("nic_mp", "Madhya Pradesh", "mptenders.gov.in"),
    Portal("nic_uttarakhand", "Uttarakhand", "uktenders.gov.in"),
    Portal("nic_wb", "West Bengal", "wbtenders.gov.in"),
    Portal("nic_tn", "Tamil Nadu", "tntenders.gov.in"),
    Portal("nic_odisha", "Odisha", "tendersodisha.gov.in"),
    Portal("nic_hp", "Himachal Pradesh", "hptenders.gov.in"),
    Portal("nic_jk", "Jammu and Kashmir", "jktenders.gov.in"),
    Portal("nic_assam", "Assam", "assamtenders.gov.in"),
    Portal("nic_jharkhand", "Jharkhand", "jharkhandtenders.gov.in"),
    Portal("nic_chhattisgarh", "Chhattisgarh", "cgtenders.gov.in"),
)


def make_nic_scraper(portal: Portal, ctx: ScrapeContext) -> NicGepScraper:
    scraper = NicGepScraper(ctx)
    scraper.name = portal.name
    scraper.state = portal.state
    scraper.base_url = portal.base_url
    return scraper


def nic_scrapers(ctx: ScrapeContext) -> list[NicGepScraper]:
    return [make_nic_scraper(p, ctx) for p in NIC_PORTALS]
