"""Sports Authority of India tenders page (sportsauthorityofindia.nic.in/sai_new/tenders).

Verified 2026-10-30: one server-rendered ``table#site_datatable`` with columns
S.No. | Tender No. & Description | Corrigendum | Opening Date | Closing Date | Details (PDF).
No pagination (client-side DataTables); the PDF is the only detail.
"""

from __future__ import annotations

import hashlib
import re

from tender_bhai.dates import parse_indian_datetime
from tender_bhai.models import Candidate, DetailResult
from tender_bhai.pdf import pdf_to_text
from tender_bhai.scrapers.base import BaseScraper, ScrapeResult, clean, soup_of

TENDERS_URL = "https://sportsauthorityofindia.nic.in/sai_new/tenders"
_UUID = re.compile(r"/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.pdf", re.I)
_STATE_WORDS = (
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat", "Haryana",
    "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur",
    "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana",
    "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal", "Delhi", "Jammu", "Kashmir", "Ladakh", "Chandigarh",
    "Puducherry",
)  # fmt: skip


def _guess_state(text: str) -> str:
    for word in _STATE_WORDS:
        if word.lower() in text.lower():
            return word
    return ""


def parse_sai_tenders(html: str) -> list[Candidate]:
    soup = soup_of(html)
    table = soup.select_one("table#site_datatable")
    if table is None:
        return []
    out: list[Candidate] = []
    for row in table.select("tbody tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 6:
            continue
        title = clean(cells[1].get_text(" "))
        view = cells[5].find("a", href=True)
        pdf_url = view["href"] if view else None
        if not title or not pdf_url:
            continue
        uuid_match = _UUID.search(pdf_url)
        tender_id = uuid_match.group(1) if uuid_match else hashlib.sha1(pdf_url.encode()).hexdigest()[:16]
        corrigenda = [a["href"] for a in cells[2].find_all("a", href=True)]
        out.append(
            Candidate(
                portal="sai",
                tender_id=tender_id,
                title=title,
                buyer_org="Sports Authority of India",
                state=_guess_state(title),
                portal_url=TENDERS_URL,
                detail_url=pdf_url,
                document_url=pdf_url,
                opening_date=parse_indian_datetime(clean(cells[3].get_text())),
                deadline=parse_indian_datetime(clean(cells[4].get_text())),
                listing_text=title,
                raw={"corrigenda": corrigenda},
            )
        )
    return out


class SaiScraper(BaseScraper):
    name = "sai"
    state = ""
    base_url = TENDERS_URL

    async def scrape(self) -> ScrapeResult:
        result = ScrapeResult(portal=self.name)
        try:
            async with self.client() as client:
                html = await client.get_text(TENDERS_URL)
            result.pages_fetched = 1
            rows = parse_sai_tenders(html)
            if not rows:
                result.fail("no table#site_datatable rows found")
            result.candidates = self.keyword_filter(rows)[: self.ctx.max_candidates]
        except Exception as exc:
            result.fail(str(exc))
        return result

    async def fetch_detail(self, candidate: Candidate) -> DetailResult:
        if not candidate.document_url:
            return DetailResult()
        async with self.client(timeout=60.0) as client:
            data = await client.get_bytes(candidate.document_url)
        text = pdf_to_text(data)
        return DetailResult(text=text, pdf_bytes=data, document_url=candidate.document_url)
