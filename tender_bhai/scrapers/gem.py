"""GeM (bidplus.gem.gov.in) scraper.

Structure verified from the portal's own page JavaScript (``loadBids()``):
POST ``/all-bids-data`` with form fields ``payload`` (JSON) and ``csrf_bd_gem_nk`` (token
embedded in the ``/all-bids`` HTML). The response is Solr-shaped:
``response.response.{numFound,start,docs}``, 10 docs per page. Each bid's document is a
PDF at ``/showbidDocument/<b_id>``.

NOTE: *.gem.gov.in refuses TCP connections from non-Indian IPs. Set ``SCRAPER_PROXY_URL``
to an Indian HTTP(S) proxy when running from GitHub Actions or abroad.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from tender_bhai.config import IST
from tender_bhai.dates import parse_indian_datetime
from tender_bhai.keywords import SEARCH_QUERIES
from tender_bhai.models import Candidate, DetailResult
from tender_bhai.money import parse_inr
from tender_bhai.pdf import pdf_to_text
from tender_bhai.scrapers.base import BaseScraper, PoliteClient, ScrapeResult

log = logging.getLogger(__name__)

BASE = "https://bidplus.gem.gov.in"
LISTING_URL = f"{BASE}/all-bids"
DATA_URL = f"{BASE}/all-bids-data"
PAGE_SIZE = 10
_TOKEN_RE = re.compile(r"csrf_bd_gem_nk['\"]?\s*[:=]\s*['\"]([0-9a-f]{32})['\"]")


def build_payload(keyword: str, page: int | None = None) -> dict[str, str]:
    postdata: dict[str, Any] = {}
    if page and page > 1:
        postdata["page"] = page
    postdata["param"] = {"searchBid": keyword, "searchType": "fullText"}
    postdata["filter"] = {
        "bidStatusType": "ongoing_bids",
        "byType": "all",
        "highBidValue": "",
        "byEndDate": {"from": "", "to": ""},
        "sort": "Bid-End-Date-Oldest",
    }
    return {"payload": json.dumps(postdata, separators=(",", ":"))}


def extract_csrf_token(html: str) -> str | None:
    match = _TOKEN_RE.search(html)
    return match.group(1) if match else None


def _gem_date(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / (1000 if value > 1e11 else 1), tz=IST)
    return parse_indian_datetime(str(value))


def document_url(doc: dict[str, Any]) -> str:
    bid_id = doc.get("b_id")
    bid_type = int(doc.get("b_bid_type") or 1)
    if bid_type == 5:
        return f"{BASE}/showdirectradocumentPdf/{bid_id}"
    if bid_type == 2:
        return f"{BASE}/showradocumentPdf/{bid_id}"
    return f"{BASE}/showbidDocument/{bid_id}"


def parse_docs(docs: list[dict[str, Any]]) -> list[Candidate]:
    candidates: list[Candidate] = []
    for doc in docs:
        bid_number = str(doc.get("b_bid_number") or "").strip()
        bid_id = doc.get("b_id")
        if not bid_number or bid_id is None:
            continue
        categories = doc.get("b_category_name") or []
        if isinstance(categories, str):
            categories = [categories]
        title = ", ".join(str(c) for c in categories) or bid_number
        if doc.get("bbt_title"):
            title += f" ({doc['bbt_title']})"
        ministry = str(doc.get("ba_official_details_minName") or "").replace("_", " ").strip()
        department = str(doc.get("ba_official_details_deptName") or "").strip()
        buyer = " - ".join(p for p in (ministry, department) if p) or "GeM buyer"
        quantity = doc.get("b_total_quantity")
        listing = f"Bid {bid_number}. Items: {title}. Quantity: {quantity}. Buyer: {buyer}."
        candidates.append(
            Candidate(
                portal="gem",
                tender_id=bid_number,
                title=title,
                buyer_org=buyer,
                state="",
                portal_url=f"{BASE}/showbidDocument/{bid_id}",
                detail_url=document_url(doc),
                document_url=document_url(doc),
                published_at=_gem_date(doc.get("final_start_date_sort")),
                deadline=_gem_date(doc.get("final_end_date_sort")),
                listing_text=listing,
                raw={"b_id": bid_id, "quantity": quantity, "bid_type": doc.get("b_bid_type")},
            )
        )
    return candidates


_CID = re.compile(r"\(cid:\d+\)")
_ASCII_TAIL = re.compile(r"([A-Za-z][A-Za-z0-9 ,.()&/-]*?)\s*$")
_SAME_LINE_LABELS = {
    "ministry": "Ministry/State Name",
    "department": "Department Name",
    "organisation": "Organisation Name",
    "office": "Office Name",
}


def _clean_pdf_text(text: str) -> str:
    return _CID.sub("", text)


def _same_line_value(text: str, label: str) -> str | None:
    """Last ASCII run on the label's line (pdfplumber and pdftotext both keep value on that line)."""
    match = re.search(re.escape(label) + r"[^\n]*", text)
    if not match:
        return None
    line = match.group(0)[len(label) :]
    tail = _ASCII_TAIL.search(line)
    value = tail.group(1).strip(" /:-") if tail else ""
    return value or None


def parse_gem_pdf_text(text: str) -> dict[str, Any]:
    """Pull the headline fields from a GeM bid document's text (pdfplumber or pdftotext output)."""
    text = _clean_pdf_text(text)
    fields: dict[str, Any] = {}
    value = re.search(r"Estimated Bid Value[^\n0-9]*([0-9][0-9,]*(?:\.\d+)?)", text)
    if value:
        fields["estimated_value_inr"] = parse_inr(value.group(1))
    end = re.search(r"Bid End Date/Time[^\n0-9]*(\d{2}-\d{2}-\d{4}\s+\d{2}:\d{2}:\d{2})", text)
    if end:
        fields["deadline"] = parse_indian_datetime(end.group(1))
    opening = re.search(r"Bid Opening Date/Time[^0-9]*(\d{2}-\d{2}-\d{4}\s+\d{2}:\d{2}:\d{2})", text)
    if opening:
        fields["opening_date"] = parse_indian_datetime(opening.group(1))
    for key, label in _SAME_LINE_LABELS.items():
        found = _same_line_value(text, label)
        if found:
            fields[key] = found
    quantity = re.search(r"Total Quantity[^\n0-9]*([0-9][0-9,]*)", text)
    if quantity:
        fields["quantity"] = quantity.group(1)
    turnover = re.search(r"Minimum Average Annual Turnover.{0,160}?([0-9][0-9.,]*\s*(?:Lakh|Crore)[^\n]*)", text, re.S)
    if turnover:
        fields["min_turnover"] = turnover.group(1).strip()
    mse = re.search(r"MSE Exemption for Years Of.{0,200}?\b(Yes|No)\b", text, re.S)
    if mse:
        fields["msme_exempt"] = mse.group(1) == "Yes"
    emd_section = re.search(r"EMD Detail(.{0,400})", text, re.S)
    if emd_section:
        block = emd_section.group(1)
        required = re.search(r"Required[^\nA-Za-z]*(Yes|No)", block)
        fields["emd_required"] = required.group(1) == "Yes" if required else None
        amount = re.search(r"(?:EMD Amount|Amount)[^\n0-9]*([0-9][0-9,]*)", block)
        if amount and fields.get("emd_required"):
            fields["emd_inr"] = parse_inr(amount.group(1))
    bid_number = re.search(r"(GEM/20\d\d/[BR]/\d+)", text)
    if bid_number:
        fields["bid_number"] = bid_number.group(1)
    return fields


class GemScraper(BaseScraper):
    name = "gem"
    state = ""
    base_url = BASE

    async def _session(self, client: PoliteClient) -> str:
        html = await client.get_text(LISTING_URL)
        token = extract_csrf_token(html)
        if not token:
            raise RuntimeError("GeM: csrf_bd_gem_nk token not found on /all-bids")
        return token

    async def _search(
        self, client: PoliteClient, token: str, keyword: str, page: int
    ) -> tuple[list[dict[str, Any]], int]:
        form = build_payload(keyword, page)
        form["csrf_bd_gem_nk"] = token
        response = await client.post(
            DATA_URL,
            data=form,
            headers={
                "Referer": LISTING_URL,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*; q=0.01",
            },
        )
        if response.status_code != 200:
            raise RuntimeError(f"GeM: /all-bids-data returned {response.status_code}")
        data = response.json()
        if data.get("code") not in (200, "200", None):
            raise RuntimeError(f"GeM: API error {data.get('message') or data.get('code')}")
        inner = (data.get("response") or {}).get("response") or {}
        return list(inner.get("docs") or []), int(inner.get("numFound") or 0)

    async def scrape(self) -> ScrapeResult:
        result = ScrapeResult(portal=self.name)
        seen: set[str] = set()
        try:
            async with self.client(timeout=45.0) as client:
                token = await self._session(client)
                for keyword in SEARCH_QUERIES + self.ctx.extra_keywords:
                    page = 1
                    while page <= self.ctx.max_pages:
                        await client.pause()
                        try:
                            docs, total = await self._search(client, token, keyword, page)
                        except Exception as exc:
                            result.fail(f"{keyword} p{page}: {exc}")
                            break
                        result.pages_fetched += 1
                        for candidate in parse_docs(docs):
                            if candidate.tender_id in seen:
                                continue
                            seen.add(candidate.tender_id)
                            result.candidates.append(candidate)
                        if page * PAGE_SIZE >= total or not docs:
                            break
                        page += 1
                    if len(result.candidates) >= self.ctx.max_candidates:
                        break
        except Exception as exc:
            result.fail(str(exc))
            if "Connection refused" in str(exc) or "ConnectError" in type(exc).__name__:
                result.status = "blocked"
        result.candidates = self.keyword_filter(result.candidates)[: self.ctx.max_candidates]
        return result

    async def fetch_detail(self, candidate: Candidate) -> DetailResult:
        url = candidate.document_url or candidate.detail_url
        if not url:
            return DetailResult()
        async with self.client(timeout=60.0) as client:
            response = await client.get(url, headers={"Referer": LISTING_URL})
        if response.status_code != 200 or not response.content.startswith(b"%PDF"):
            return DetailResult(text="", document_url=url, fields={"status": response.status_code})
        text = pdf_to_text(response.content)
        fields = parse_gem_pdf_text(text)
        return DetailResult(text=text, pdf_bytes=response.content, document_url=url, fields=fields)
