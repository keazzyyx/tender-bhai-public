"""IREPS (Indian Railways e-procurement).

Live check 2026-10-30: every anonymous search entry point (``/epsn/anonymSearch.do``,
``?searchParam=showPageClosed``, ``HighValue.do``) now serves a "Guest" page that demands a
mobile number + SMS OTP + image CAPTCHA before any listing. There is no public listing to
scrape, so this scraper reports ``blocked`` with the reason instead of failing the run. If
IREPS reopens an anonymous listing, ``parse_results`` handles the classic results table.
"""

from __future__ import annotations

from tender_bhai.dates import parse_indian_datetime
from tender_bhai.models import Candidate
from tender_bhai.scrapers.base import BaseScraper, ScrapeResult, clean, soup_of

SEARCH_URL = "https://www.ireps.gov.in/epsn/anonymSearch.do?searchParam=showPageClosed&language=en"


def is_otp_gate(html: str) -> bool:
    lowered = html.lower()
    return "guestlogonform" in lowered or ("mobileno" in lowered and "otp" in lowered)


def parse_results(html: str) -> list[Candidate]:
    """Best-effort parser for a results table (tender no | title | railway | closing)."""
    soup = soup_of(html)
    out: list[Candidate] = []
    for table in soup.find_all("table"):
        header = clean(table.get_text(" ")).lower()
        if "tender" not in header or "clos" not in header:
            continue
        for row in table.find_all("tr"):
            cells = [clean(c.get_text(" ")) for c in row.find_all("td")]
            if len(cells) < 4 or not cells[0]:
                continue
            link = row.find("a", href=True)
            deadline = next((parse_indian_datetime(c) for c in cells if parse_indian_datetime(c)), None)
            out.append(
                Candidate(
                    portal="ireps",
                    tender_id=cells[0],
                    title=cells[1],
                    buyer_org=cells[2] if len(cells) > 2 else "Indian Railways",
                    portal_url=SEARCH_URL,
                    detail_url=link["href"] if link else None,
                    deadline=deadline,
                    listing_text=" | ".join(cells),
                )
            )
    return out


class IrepsScraper(BaseScraper):
    name = "ireps"
    base_url = SEARCH_URL

    async def scrape(self) -> ScrapeResult:
        result = ScrapeResult(portal=self.name)
        try:
            async with self.client() as client:
                html = await client.get_text(SEARCH_URL)
            result.pages_fetched = 1
            if is_otp_gate(html):
                result.status = "blocked"
                result.errors.append("IREPS anonymous search requires mobile OTP + CAPTCHA (no public listing)")
                return result
            result.candidates = self.keyword_filter(parse_results(html))[: self.ctx.max_candidates]
            if not result.candidates:
                result.errors.append("no results table recognised")
                result.status = "partial"
        except Exception as exc:
            result.fail(str(exc))
        return result
