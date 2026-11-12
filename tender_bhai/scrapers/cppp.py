"""CPPP - Central Public Procurement Portal (eprocure.gov.in).

Live check 2026-10-30: the "Latest Active Tenders" and "Tenders by Classification" pages are
image-CAPTCHA search forms, so we use the CAPTCHA-free listings instead:
``FrontEndListTendersbyDate`` (closing-soon view, paginated) and the home page's
``table#activeTenders`` (10 newest). Detail pages render without CAPTCHA.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from tender_bhai.dates import parse_indian_datetime
from tender_bhai.models import Candidate
from tender_bhai.scrapers.base import ScrapeResult, clean, soup_of
from tender_bhai.scrapers.nicgep import NicGepScraper

HOME_URL = "https://eprocure.gov.in/eprocure/app"


def parse_home_active(html: str) -> list[Candidate]:
    """Rows of the home page ``table#activeTenders`` (title link | ref no | closing | opening)."""
    soup = soup_of(html)
    table = soup.select_one("table#activeTenders")
    if table is None:
        return []
    out: list[Candidate] = []
    for row in table.select("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 4:
            continue
        link = cells[0].find("a")
        if link is None:
            continue
        title = re.sub(r"^\d+\.\s*", "", clean(link.get_text(" ")))
        ref_no = clean(cells[1].get_text())
        if not title or not ref_no:
            continue
        out.append(
            Candidate(
                portal="cppp",
                tender_id=ref_no,
                title=title,
                buyer_org="",
                state="",
                portal_url=HOME_URL,
                detail_url=urljoin(HOME_URL, link.get("href") or ""),
                deadline=parse_indian_datetime(clean(cells[2].get_text())),
                opening_date=parse_indian_datetime(clean(cells[3].get_text())),
                listing_text=f"{title}. Ref: {ref_no}.",
                raw={"ref_no": ref_no, "source": "home"},
            )
        )
    return out


class CpppScraper(NicGepScraper):
    name = "cppp"
    state = ""
    base_url = HOME_URL
    listing_pages = ("FrontEndListTendersbyDate",)

    async def scrape(self) -> ScrapeResult:
        result = await super().scrape()
        try:
            async with self.client() as client:
                html = await client.get_text(HOME_URL)
                result.pages_fetched += 1
                seen = {c.tender_id for c in result.candidates}
                extra = [c for c in self.keyword_filter(parse_home_active(html)) if c.tender_id not in seen]
                result.candidates.extend(extra)
        except Exception as exc:
            result.errors.append(f"home: {exc}")
            if result.status == "ok":
                result.status = "partial"
        return result
