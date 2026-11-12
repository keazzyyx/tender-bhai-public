"""Parsers and a parameterised scraper for the NIC Tapestry template used by CPPP
(``/eprocure/app``) and the state portals (``/nicgep/app``).

Verified live structure (2026-10-30):
- Listing: ``table#table.list_table``; header ``tr.list_header`` with <td> cells; data rows
  ``tr.even`` / ``tr.odd`` with 6 cells: S.No | e-Published Date | Closing Date | Opening Date |
  Title+[Ref No][Tender ID] | Organisation Chain. The title cell has
  ``a[title="View Tender Information"]`` whose href is a session-bound Tapestry DirectLink.
- Pagination: links ``component=$TablePages.linkPage ... &sp=A<Page>,table&sp=<N>`` and
  ``$TablePages.linkFwd``; they only work inside the JSESSIONID session that served page 1
  (a stateless request returns a tiny "Stale Session" page).
- Detail: ``td.td_caption`` label followed by ``td.td_field`` value, inside ``table.tablebg``.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlencode, urljoin

from bs4 import Tag

from tender_bhai.dates import parse_indian_datetime
from tender_bhai.models import Candidate, DetailResult
from tender_bhai.money import parse_inr
from tender_bhai.scrapers.base import BaseScraper, PoliteClient, ScrapeResult, clean, looks_like_captcha, soup_of

log = logging.getLogger(__name__)

_BRACKET = re.compile(r"\[([^\]]*)\]")
_TENDER_ID = re.compile(r"\b(\d{4}_[A-Za-z0-9]+_\d+_\d+)\b")


def parse_listing(html: str, base_url: str, portal: str, state: str, listing_page: str) -> list[Candidate]:
    """Rows of the 6-column tender table on a CPPP/nicgep listing page."""
    soup = soup_of(html)
    table = soup.select_one("table#table")
    if table is None:
        return []
    candidates: list[Candidate] = []
    for row in table.select("tr"):
        classes = row.get("class") or []
        if "even" not in classes and "odd" not in classes:
            continue
        cells = row.find_all("td", recursive=False)
        if len(cells) < 6:
            continue
        link = cells[4].find("a")
        if link is None:
            continue
        title_text = clean(link.get_text(" "))
        title = title_text.strip("[]").strip() or title_text
        tail = clean(cells[4].get_text(" ")).replace(title_text, "", 1)
        brackets = _BRACKET.findall(tail)
        ref_no = brackets[0].strip() if brackets else ""
        id_match = _TENDER_ID.search(tail)
        tender_id = id_match.group(1) if id_match else (brackets[-1].strip() if brackets else "")
        if not tender_id:
            continue
        org_chain = clean(cells[5].get_text(" "))
        buyer = org_chain.split("||")[0].strip() if org_chain else ""
        href = link.get("href") or ""
        detail_url = urljoin(base_url, href) if href else None
        candidates.append(
            Candidate(
                portal=portal,
                tender_id=tender_id,
                title=title,
                buyer_org=buyer,
                state=state,
                portal_url=f"{base_url}?page={listing_page}&service=page",
                detail_url=detail_url,
                published_at=parse_indian_datetime(clean(cells[1].get_text())),
                deadline=parse_indian_datetime(clean(cells[2].get_text())),
                opening_date=parse_indian_datetime(clean(cells[3].get_text())),
                listing_text=f"{title}. Ref: {ref_no}. Tender ID: {tender_id}. Organisation: {org_chain}.",
                raw={"ref_no": ref_no, "org_chain": org_chain},
            )
        )
    return candidates


def next_page_href(html: str) -> str | None:
    """The ``$TablePages.linkFwd`` href (relative) or None on the last page."""
    soup = soup_of(html)
    link = soup.select_one("a[href*='TablePages.linkFwd']")
    return link.get("href") if link else None


def page_hrefs(html: str) -> dict[int, str]:
    soup = soup_of(html)
    pages: dict[int, str] = {}
    for link in soup.select("a[href*='TablePages.linkPage']"):
        href = link.get("href") or ""
        match = re.search(r"sp=(\d+)$", href)
        if match:
            pages[int(match.group(1))] = href
    return pages


def is_stale_session(html: str) -> bool:
    return "stale session" in html.lower() or "session has timed out" in html.lower()


def build_tab_post(html: str, submitname: str = "LinkSubmit_1") -> list[tuple[str, str]] | None:
    """Replay the ``form#ListTendersbyDate`` hidden inputs to switch the tab without CAPTCHA.

    ``LinkSubmit_0`` = closing within 7 days, ``LinkSubmit_1`` = closing within 14 days,
    ``tabByClosingToday`` = default view. Returns url-encodable (name, value) pairs or None.
    """
    soup = soup_of(html)
    form = soup.select_one("form#ListTendersbyDate")
    if form is None:
        return None
    pairs: list[tuple[str, str]] = []
    for field in form.find_all(["input", "select"]):
        name = field.get("name")
        if not name:
            continue
        if name == "submitmode":
            pairs.append((name, "submit"))
        elif name == "submitname":
            pairs.append((name, submitname))
        elif field.name == "select":
            selected = field.find("option", selected=True) or field.find("option")
            pairs.append((name, (selected.get("value") if selected else "") or "0"))
        else:
            pairs.append((name, field.get("value") or ""))
    names = {n for n, _ in pairs}
    if "submitmode" not in names:
        pairs.append(("submitmode", "submit"))
    if "submitname" not in names:
        pairs.append(("submitname", submitname))
    return pairs


def last_page_number(html: str) -> int:
    soup = soup_of(html)
    link = soup.select_one("a[href*='TablePages.linkLast']")
    if link is None:
        return len(page_hrefs(html)) + 1 if page_hrefs(html) else 1
    match = re.search(r"sp=(\d+)$", link.get("href") or "")
    return int(match.group(1)) if match else 1


def page_url(base_url: str, listing_page: str, number: int) -> str:
    return (
        f"{base_url}?component=%24TablePages.linkPage&page={listing_page}&service=direct&session=T"
        f"&sp=A{listing_page}%2Ctable&sp={number}"
    )


ORG_PATTERN = re.compile(
    r"sport|youth|yuva|khel|education|school|shiksha|vidyalaya|university|college|police|"
    r"home guard|paramilitary|defence|army|cantonment|navy|air force|cadet|ncc|municipal|nagar nigam|"
    r"tribal|welfare|social|women|child|jail|prison|sainik|bsf|crpf|itbp|ssb|cisf|assam rifles",
    re.IGNORECASE,
)


def parse_org_index(html: str) -> list[tuple[str, int, str]]:
    """(organisation, tender count, relative href) rows of the Tenders-by-Organisation index."""
    soup = soup_of(html)
    table = soup.select_one("table#table")
    if table is None:
        return []
    out: list[tuple[str, int, str]] = []
    for row in table.select("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 3:
            continue
        link = cells[2].find("a", href=True)
        if link is None:
            continue
        name = clean(cells[1].get_text(" "))
        try:
            count = int(clean(link.get_text()) or 0)
        except ValueError:
            count = 0
        if name:
            out.append((name, count, link["href"]))
    return out


def relevant_orgs(rows: list[tuple[str, int, str]], limit: int = 10) -> list[tuple[str, int, str]]:
    picked = [r for r in rows if ORG_PATTERN.search(r[0]) and r[1] > 0]
    picked.sort(key=lambda r: (0 if re.search(r"sport|youth|khel|yuva", r[0], re.I) else 1, -r[1]))
    return picked[:limit]


def parse_detail(html: str, base_url: str) -> dict[str, Any]:
    """label -> value for every ``td.td_caption`` / ``td.td_field`` pair, plus document links."""
    soup = soup_of(html)
    fields: dict[str, Any] = {}
    for caption in soup.select("td.td_caption"):
        value = caption.find_next_sibling("td")
        if not isinstance(value, Tag) or "td_field" not in (value.get("class") or []):
            continue
        label = clean(caption.get_text(" "))
        text = clean(value.get_text(" "))
        if label and label not in fields and value.find("table") is None:
            fields[label] = text
    documents: list[dict[str, str]] = []
    for link in soup.select("a[href*='docDownoad'], a[href*='docDownload'], a.blue_link[href]"):
        name = clean(link.get_text())
        if name and name.lower().endswith((".pdf", ".xls", ".xlsx", ".doc", ".docx", ".zip")):
            documents.append({"name": name, "url": urljoin(base_url, link.get("href") or "")})
    fields["_documents"] = documents
    return fields


def detail_to_result(fields: dict[str, Any], text: str) -> DetailResult:
    def pick(*labels: str) -> str | None:
        for label in labels:
            for key, value in fields.items():
                if key.lower().startswith(label.lower()):
                    return value
        return None

    structured = {
        "tender_id": pick("Tender ID"),
        "ref_no": pick("Tender Reference Number"),
        "title": pick("Title"),
        "org_chain": pick("Organisation Chain"),
        "tender_value_inr": parse_inr(pick("Tender Value")),
        "emd_inr": parse_inr(pick("EMD Amount")),
        "emd_exemption": pick("EMD Exemption Allowed"),
        "product_category": pick("Product Category"),
        "tender_category": pick("Tender Category"),
        "location": pick("Location"),
        "pincode": pick("Pincode"),
        "deadline": parse_indian_datetime(pick("Bid Submission End Date")),
        "opening_date": parse_indian_datetime(pick("Bid Opening Date")),
        "authority_name": pick("Name"),
        "authority_address": pick("Address"),
        "documents": fields.get("_documents", []),
    }
    doc_url = structured["documents"][0]["url"] if structured["documents"] else None
    return DetailResult(text=text, document_url=doc_url, fields=structured)


def detail_text(fields: dict[str, Any]) -> str:
    lines = [f"{k}: {v}" for k, v in fields.items() if not k.startswith("_") and v]
    docs = fields.get("_documents") or []
    if docs:
        lines.append("Documents: " + ", ".join(d["name"] for d in docs))
    return "\n".join(lines)


class NicGepScraper(BaseScraper):
    """Parameterised scraper for one Tapestry portal. Subclasses / factory set the attributes."""

    name = "nicgep"
    state = ""
    base_url = ""  # e.g. https://etender.up.nic.in/nicgep/app
    listing_pages: tuple[str, ...] = ("FrontEndListTendersbyDate",)

    def listing_url(self, page: str) -> str:
        return f"{self.base_url}?page={page}&service=page"

    #: which closing-window tab to open on ``FrontEndListTendersbyDate`` (14 days)
    tab_submit: str = "LinkSubmit_1"

    async def _collect(self, client: PoliteClient, page_name: str, result: ScrapeResult, seen: set[str]) -> None:
        url = self.listing_url(page_name)
        html = await client.get_text(url)
        result.pages_fetched += 1
        if page_name == "FrontEndListTendersbyDate" and self.tab_submit:
            form = build_tab_post(html, self.tab_submit)
            if form:
                await client.pause()
                response = await client.post(
                    self.base_url,
                    content=urlencode(form),
                    headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": url},
                )
                if response.status_code == 200 and parse_listing(
                    response.text, self.base_url, self.name, self.state, page_name
                ):
                    html = response.text
                    result.pages_fetched += 1
                else:
                    result.errors.append(f"{page_name}: tab switch failed ({response.status_code}), using default view")
        rows = parse_listing(html, self.base_url, self.name, self.state, page_name)
        if not rows:
            if is_stale_session(html):
                result.errors.append(f"{page_name}: stale session")
            elif looks_like_captcha(html):
                result.errors.append(f"{page_name}: CAPTCHA-gated listing, no rows")
            else:
                result.errors.append(f"{page_name}: no tender table found")
            return
        for row in rows:
            row.raw["source"] = "bydate"
        self._absorb(rows, result, seen)
        total_pages = min(last_page_number(html), self.ctx.max_pages)
        for number in range(2, total_pages + 1):
            if len(result.candidates) >= self.ctx.max_candidates * 4:
                return
            await client.pause()
            html = await client.get_text(page_url(self.base_url, page_name, number), headers={"Referer": url})
            result.pages_fetched += 1
            if is_stale_session(html):
                result.errors.append(f"{page_name}: session expired at page {number}")
                return
            rows = parse_listing(html, self.base_url, self.name, self.state, page_name)
            if not rows:
                return
            for row in rows:
                row.raw["source"] = "bydate"
            self._absorb(rows, result, seen)

    #: also drill into sports/education/police-type organisations (captcha-free index)
    org_drilldown: bool = True
    max_orgs: int = 10

    async def _collect_orgs(self, client: PoliteClient, result: ScrapeResult, seen: set[str]) -> None:
        index_url = self.listing_url("FrontEndTendersByOrganisation")
        html = await client.get_text(index_url)
        result.pages_fetched += 1
        orgs = relevant_orgs(parse_org_index(html), self.max_orgs)
        for name, _count, href in orgs:
            await client.pause()
            page = await client.get_text(urljoin(self.base_url, href), headers={"Referer": index_url})
            result.pages_fetched += 1
            if is_stale_session(page):
                result.errors.append("organisations: session expired")
                return
            rows = parse_listing(page, self.base_url, self.name, self.state, "FrontEndTendersByOrganisation")
            for row in rows:
                row.raw["organisation"] = name
                row.raw["source"] = "org"
            self._absorb(rows, result, seen)
            if len(result.candidates) >= self.ctx.max_candidates * 6:
                return

    @staticmethod
    def _absorb(rows: list[Candidate], result: ScrapeResult, seen: set[str]) -> None:
        for candidate in rows:
            if candidate.tender_id in seen:
                continue
            seen.add(candidate.tender_id)
            result.candidates.append(candidate)

    async def scrape(self) -> ScrapeResult:
        result = ScrapeResult(portal=self.name)
        seen: set[str] = set()
        try:
            async with self.client() as client:
                for page_name in self.listing_pages:
                    try:
                        await self._collect(client, page_name, result, seen)
                    except Exception as exc:
                        result.fail(f"{page_name}: {exc}")
                    await client.pause()
                if self.org_drilldown:
                    try:
                        await self._collect_orgs(client, result, seen)
                    except Exception as exc:
                        result.errors.append(f"organisations: {exc}")
        except Exception as exc:
            result.fail(str(exc))
        total = len(result.candidates)
        result.candidates = self.keyword_filter(result.candidates)[: self.ctx.max_candidates]
        log.info("%s: %d rows scanned, %d keyword matches", self.name, total, len(result.candidates))
        if result.errors and result.status == "ok":
            result.status = "partial"
        return result

    async def fetch_detail(self, candidate: Candidate) -> DetailResult:
        """Detail links are session-bound, so re-walk the listing that produced the row, then follow it."""
        if not candidate.detail_url:
            return DetailResult()
        source = candidate.raw.get("source", "bydate")
        async with self.client() as client:
            if source == "org":
                html = await self._reopen_org_listing(client, candidate.raw.get("organisation", ""))
                href = _find_row_href(html, candidate.tender_id) if html else None
            elif source == "home":
                html = await client.get_text(self.base_url)
                href = _find_row_href(html, candidate.tender_id, table_id="activeTenders")
            else:
                href = await self._find_in_by_date(client, candidate.tender_id)
            if href is None:
                return DetailResult(text="", fields={"status": "row not found on listing"})
            await client.pause()
            response = await client.get(urljoin(self.base_url, href))
        if response.status_code != 200:
            return DetailResult(text="", fields={"status": response.status_code})
        page = response.text
        if is_stale_session(page):
            return DetailResult(text="", fields={"status": "stale session"})
        if looks_like_captcha(page) and "td_caption" not in page:
            return DetailResult(text="", captcha_blocked=True)
        fields = parse_detail(page, self.base_url)
        return detail_to_result(fields, detail_text(fields))

    async def _find_in_by_date(self, client: PoliteClient, tender_id: str) -> str | None:
        listing_page = "FrontEndListTendersbyDate"
        url = self.listing_url(listing_page)
        html = await client.get_text(url)
        if self.tab_submit:
            form = build_tab_post(html, self.tab_submit)
            if form:
                await client.pause()
                response = await client.post(
                    self.base_url,
                    content=urlencode(form),
                    headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": url},
                )
                if response.status_code == 200 and "table" in response.text:
                    html = response.text
        href = _find_row_href(html, tender_id)
        total_pages = min(last_page_number(html), self.ctx.max_pages)
        page_no = 1
        while href is None and page_no < total_pages:
            page_no += 1
            await client.pause()
            html = await client.get_text(page_url(self.base_url, listing_page, page_no), headers={"Referer": url})
            if is_stale_session(html):
                return None
            href = _find_row_href(html, tender_id)
        return href

    async def _reopen_org_listing(self, client: PoliteClient, organisation: str) -> str | None:
        index_url = self.listing_url("FrontEndTendersByOrganisation")
        html = await client.get_text(index_url)
        for name, _count, href in parse_org_index(html):
            if name == organisation:
                await client.pause()
                return await client.get_text(urljoin(self.base_url, href), headers={"Referer": index_url})
        return None


def _find_row_href(html: str, tender_id: str, table_id: str = "table") -> str | None:
    soup = soup_of(html)
    for row in soup.select(f"table#{table_id} tr"):
        if tender_id in row.get_text():
            link = row.find("a", href=True)
            if link:
                return link["href"]
    return None
