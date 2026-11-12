"""State portals that do not use the NIC template: Karnataka, Telangana (+ Andhra Pradesh,
same vendor stack), Bihar and Gujarat.

Live status 2026-10-30 from a non-Indian IP:
- Karnataka KPPP: VERIFIED. Public JSON search + detail API, no CAPTCHA/cookies.
- Telangana: VERIFIED. DataTables JSON listing + detail page need only the JSESSIONID cookie.
- Andhra Pradesh: BLOCKED (TCP timeout). Same vendor product as Telangana, so the Telangana
  scraper is instantiated for AP and reports ``blocked``/``partial`` until a run from India
  confirms the structure.
- Bihar EPSV2Web: PARTIAL. Listing page + REST endpoint and request body derived from the
  live Angular controller (``getTenderList`` posts ``{orgId}``); response keys come from the
  page template (``currentOrgTenderId``, ``currentdescription``...). Not yet observed live.
- Gujarat: BLOCKED. ``www.nprocure.com`` holds 0 tenders and only speaks TLS 1.0/RC4; every
  department migrated to ``tender.nprocure.com`` which times out from abroad.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from tender_bhai.dates import parse_indian_datetime
from tender_bhai.keywords import SEARCH_QUERIES
from tender_bhai.models import Candidate, DetailResult
from tender_bhai.money import parse_inr
from tender_bhai.scrapers.base import BaseScraper, PoliteClient, ScrapeContext, ScrapeResult, clean, soup_of

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------------------------
# Karnataka (kppp.karnataka.gov.in)
# ---------------------------------------------------------------------------------------------
KPPP = "https://kppp.karnataka.gov.in"
KPPP_API = f"{KPPP}/supplier-registration-service/v1/api/portal-service"
KPPP_PAGE_SIZE = 50


def kppp_search_body(title: str | None = None) -> dict[str, Any]:
    """Body shape copied from the portal's ``constructSearchInputData()``."""
    return {
        "tenderNumber": None,
        "category": "GOODS",
        "status": "PUBLISHED",
        "deptId": None,
        "publishedFromDate": None,
        "publishedToDate": None,
        "tenderType": "OPEN",
        "title": title or None,
        "ecv": None,
        "location": None,
        "tenderClosureFromDate": None,
        "tenderClosureToDate": None,
    }


def parse_kppp_list(records: list[dict[str, Any]]) -> list[Candidate]:
    out: list[Candidate] = []
    for rec in records:
        number = str(rec.get("tenderNumber") or "").strip()
        nit_id = rec.get("nitId")
        if not number or nit_id is None:
            continue
        title = clean(rec.get("title"))
        dept = clean(rec.get("deptName"))
        location = clean(rec.get("locationName"))
        out.append(
            Candidate(
                portal="karnataka",
                tender_id=number,
                title=title or number,
                buyer_org=dept,
                state="Karnataka",
                portal_url=f"{KPPP}/#/portal/searchTender/live",
                detail_url=f"{KPPP_API}/{nit_id}/goods-tender-full-view",
                published_at=parse_indian_datetime(rec.get("publishedDate")),
                deadline=parse_indian_datetime(rec.get("tenderClosureDate")),
                estimated_value_inr=parse_inr(rec.get("ecv")),
                listing_text=f"{title}. {clean(rec.get('description'))}. Department: {dept}. Location: {location}.",
                raw={"nitId": nit_id, "id": rec.get("id"), "category": rec.get("category"), "location": location},
            )
        )
    return out


def kppp_detail_to_result(data: dict[str, Any]) -> DetailResult:
    nit = data.get("noticeInvitingTenderDTO") or {}
    schedule = data.get("tenderSchedule") or {}
    items = data.get("tenderGoodsItemsList") or []
    lines = [
        f"Tender: {schedule.get('tenderNumber')} - {schedule.get('title')}",
        f"Description: {schedule.get('description')}",
        f"Department: {schedule.get('deptName')} ({schedule.get('procEntityTypeText')})",
        f"Location: {schedule.get('locationName')}",
        f"Estimated value (ECV): {schedule.get('ecv')}",
        f"EMD: {nit.get('emd')}  Tender fee: {nit.get('tenderFee')}",
        f"Bid submission closes: {nit.get('tenderReceiptClose')}  Technical bid opens: {nit.get('technicalBidOpen')}",
        f"Published: {nit.get('publishedDate')}  Bid validity days: {nit.get('bidValidityPeriod')}",
        f"Contact: {nit.get('contactPerson')} {nit.get('mobileNumber') or ''}",
        f"Evaluation: {nit.get('evaluationTypeText')}",
    ]
    if items:
        lines.append("Items:")
        for item in items[:60]:
            lines.append(
                f"  - {item.get('itemName')} | qty {item.get('quantity')} {item.get('uomName') or ''} | {clean(item.get('specifications'))}"
            )
    for key in ("tenderEligibilityCriterionList", "tenderPreQualCriterionList", "tenderTechnicalCriterionList"):
        criteria = data.get(key) or []
        if criteria:
            lines.append(key.replace("tender", "").replace("List", "") + ":")
            for crit in criteria[:20]:
                lines.append("  - " + clean(" ".join(str(v) for v in crit.values() if isinstance(v, str)))[:300])
    fields = {
        "tender_value_inr": parse_inr(schedule.get("ecv")) if schedule.get("ecvtenderYn") else None,
        "emd_inr": parse_inr(nit.get("emd")),
        "deadline": parse_indian_datetime(nit.get("tenderReceiptClose")),
        "opening_date": parse_indian_datetime(nit.get("technicalBidOpen")),
        "contact_name": nit.get("contactPerson"),
        "contact_phone": nit.get("mobileNumber"),
        "location": schedule.get("locationName"),
    }
    return DetailResult(text="\n".join(lines), fields=fields)


class KarnatakaScraper(BaseScraper):
    name = "karnataka"
    state = "Karnataka"
    base_url = KPPP

    async def _page(self, client: PoliteClient, page: int, title: str | None) -> tuple[list[dict[str, Any]], int]:
        url = f"{KPPP_API}/search-eproc-tenders?page={page}&size={KPPP_PAGE_SIZE}&order-by-tender-publish=true"
        response = await client.post(url, json=kppp_search_body(title), headers={"Accept": "application/json"})
        if response.status_code != 200:
            raise RuntimeError(f"KPPP search returned {response.status_code}")
        total = int(response.headers.get("X-Total-Count") or 0)
        data = response.json()
        return (data if isinstance(data, list) else []), total

    async def scrape(self) -> ScrapeResult:
        result = ScrapeResult(portal=self.name)
        seen: set[str] = set()
        try:
            async with self.client() as client:
                # newest GOODS tenders, paginated
                page = 0
                while page < self.ctx.max_pages:
                    records, total = await self._page(client, page, None)
                    result.pages_fetched += 1
                    for cand in parse_kppp_list(records):
                        if cand.tender_id not in seen:
                            seen.add(cand.tender_id)
                            result.candidates.append(cand)
                    page += 1
                    if (page * KPPP_PAGE_SIZE) >= total or not records:
                        break
                    await client.pause()
                # keyword searches on the title field
                for keyword in SEARCH_QUERIES + self.ctx.extra_keywords:
                    await client.pause()
                    try:
                        records, _ = await self._page(client, 0, keyword)
                    except Exception as exc:
                        result.errors.append(f"title search '{keyword}': {exc}")
                        continue
                    result.pages_fetched += 1
                    for cand in parse_kppp_list(records):
                        if cand.tender_id not in seen:
                            seen.add(cand.tender_id)
                            result.candidates.append(cand)
        except Exception as exc:
            result.fail(str(exc))
        result.candidates = self.keyword_filter(result.candidates)[: self.ctx.max_candidates]
        if result.errors and result.status == "ok":
            result.status = "partial"
        return result

    async def fetch_detail(self, candidate: Candidate) -> DetailResult:
        nit_id = candidate.raw.get("nitId")
        if nit_id is None:
            return DetailResult()
        async with self.client() as client:
            response = await client.get(
                f"{KPPP_API}/{nit_id}/goods-tender-full-view", headers={"Accept": "application/json"}
            )
            if response.status_code != 200:
                return DetailResult(fields={"status": response.status_code})
            result = kppp_detail_to_result(response.json())
            await client.pause()
            files = await client.get(
                f"{KPPP_API}/{nit_id}/get-goods-tender-files", headers={"Accept": "application/json"}
            )
            if files.status_code == 200:
                names = [f.get("fileName") for f in files.json() if isinstance(f, dict) and f.get("fileName")]
                if names:
                    result.text += "\nDocuments: " + ", ".join(names)
        return result


# ---------------------------------------------------------------------------------------------
# Telangana (tender.telangana.gov.in) and Andhra Pradesh (same vendor product, unverified)
# ---------------------------------------------------------------------------------------------
TS_LIST_FIELDS: dict[str, str] = {
    "nTenderID": "", "nDepartmentID": "0", "subDeptId": "", "ddlDistrict": "", "ddlMandal": "", "biddingType": "",
    "sProcurementType": "", "mECVValue1": "", "mECVValue2": "", "dtBidClosingselect": "", "dtBidClosing1": "",
    "dtBidClosing2": "", "dtTenderOpening1": "", "dtTenderOpening2": "", "hdnSearch4": "", "hdnSearch": "",
    "hdncorrigendumsDetails": "", "hdncorrigendumsDetails1": "", "hdnnoSearch": "", "hdncorrigendumsDetails2": "",
    "hdnPreviousPage": "", "hdnIndentID": "", "hdnTenderCategory": "", "hdnProcurementID": "", "hdnType": "current",
    "hdnPreviousPge": "TenderDetailsHome.html", "hdnadvsearch": "", "hdnFromStatus": "",
    "typeOfWorkFromConsolidation": "", "popUPRequestParameter": "", "selectedCircleDivison": "",
    "selectedDepartmentID": "", "selectedProcurementType": "", "selectedTypeofWork": "", "aid": "",
    "hdnEncryptNames": "hdnEncryptNames", "hdnEncryptValues": "hdnEncryptValues",
}  # fmt: skip
TS_PAGE_SIZE = 100
_VIEW_BTN = re.compile(r"viewBtn\((\d+),(\d+),(\d+)\)")
_CSRF = re.compile(r'name="CSRFToken"[^>]*value="([^"]+)"')


def ts_list_params(start: int, length: int = TS_PAGE_SIZE, echo: int = 1) -> list[tuple[str, str]]:
    params: list[tuple[str, str]] = list(TS_LIST_FIELDS.items())
    params += [
        ("sEcho", str(echo)),
        ("iColumns", "10"),
        ("sColumns", ",,,,,,,,,"),
        ("iDisplayStart", str(start)),
        ("iDisplayLength", str(length)),
    ]
    for i in range(10):
        params += [(f"mDataProp_{i}", str(i)), (f"bSortable_{i}", "false" if i == 9 else "true")]
    params += [("iSortCol_0", "6"), ("sSortDir_0", "desc"), ("iSortingCols", "1")]
    return params


def parse_ts_rows(data: dict[str, Any], base: str, portal: str, state: str) -> list[Candidate]:
    out: list[Candidate] = []
    for row in data.get("aaData") or []:
        if not isinstance(row, list) or len(row) < 10:
            continue
        dept, tender_id, notice_no, category, work, ecv, published, _start, closing, action = (str(c) for c in row[:10])
        match = _VIEW_BTN.search(action)
        procurement_id = match.group(3) if match else None
        indent_id = match.group(1) if match else None
        if not tender_id.strip():
            continue
        out.append(
            Candidate(
                portal=portal,
                tender_id=tender_id.strip(),
                title=clean(work),
                buyer_org=clean(dept).split("-")[0].strip(),
                state=state,
                portal_url=f"{base}/TenderDetailsHome.html",
                detail_url=f"{base}/ViewTender.html?hdnProcurementID={procurement_id}" if procurement_id else None,
                published_at=parse_indian_datetime(published),
                deadline=parse_indian_datetime(closing),
                estimated_value_inr=parse_inr(ecv),
                listing_text=f"{clean(work)}. Notice {notice_no}. Category: {category}. Department: {clean(dept)}.",
                raw={
                    "procurement_id": procurement_id,
                    "indent_id": indent_id,
                    "notice_no": notice_no,
                    "category": category,
                },
            )
        )
    return out


def ts_total(data: dict[str, Any]) -> int:
    try:
        return int(str(data.get("iTotalDisplayRecords") or "0").replace(",", ""))
    except ValueError:
        return 0


def parse_ts_detail(html: str) -> DetailResult:
    soup = soup_of(html)
    fields: dict[str, Any] = {}
    pairs: list[str] = []
    for head in soup.select("td.tdlight span.TextHead"):
        cell = head.find_parent("td")
        value_cell = cell.find_next_sibling("td") if cell else None
        label = clean(head.get_text())
        value = clean(value_cell.get_text(" ")) if value_cell else ""
        if label and value:
            pairs.append(f"{label}: {value}")
            fields.setdefault(label, value)

    def by_id(*ids: str) -> str | None:
        for element_id in ids:
            element = soup.find(id=element_id)
            if element is not None and clean(element.get_text(" ")):
                return clean(element.get_text(" "))
        return None

    department = by_id("lblDepartmentNameValue")
    officer = by_id("VTMSBodyContent_lblOfficerInvitingBidsValue", "lblOfficerInvitingBidsValue")
    phone = by_id("VTMSBodyContent_lblContactDetailsValue", "lblContactDetailsValue")
    email = by_id("VTMSBodyContent_lblsEMailValue", "lblsEMailValue")
    emd = by_id("lblBidSecurityPayableValue")
    for label, value in (
        ("Department", department),
        ("Officer inviting bids", officer),
        ("Contact", phone),
        ("Email", email),
        ("EMD", emd),
    ):
        if value:
            pairs.append(f"{label}: {value}")
    structured = {
        "tender_value_inr": parse_inr(fields.get("Estimated Contract Value")),
        "emd_inr": parse_inr(emd),
        "deadline": parse_indian_datetime(fields.get("Bid Submission Closing Date")),
        "department": department,
        "contact_name": officer,
        "contact_phone": phone,
        "contact_email": email,
        "category": fields.get("Tender Category"),
    }
    return DetailResult(text="\n".join(pairs), fields=structured)


class TelanganaScraper(BaseScraper):
    """Telangana e-procurement (verified). Also instantiated for Andhra Pradesh (unverified)."""

    name = "telangana"
    state = "Telangana"
    base_url = "https://tender.telangana.gov.in"
    verified = True

    async def _open_session(self, client: PoliteClient) -> str | None:
        response = await client.get(self.base_url + "/")
        if response.status_code != 200:
            raise RuntimeError(f"home returned {response.status_code}")
        match = _CSRF.search(response.text)
        return match.group(1) if match else None

    async def scrape(self) -> ScrapeResult:
        result = ScrapeResult(portal=self.name)
        try:
            async with self.client() as client:
                token = await self._open_session(client)
                if token is None:
                    result.fail("home page has no CSRFToken field - portal structure differs from Telangana")
                    result.status = "blocked" if not self.verified else "failed"
                    return result
                start, echo = 0, 1
                while echo <= self.ctx.max_pages:
                    await client.pause()
                    response = await client.get(
                        f"{self.base_url}/TenderDetailsHomeJson.html?{urlencode(ts_list_params(start, TS_PAGE_SIZE, echo))}",
                        headers={
                            "X-Requested-With": "XMLHttpRequest",
                            "Referer": f"{self.base_url}/TenderDetailsHome.html",
                        },
                    )
                    if response.status_code != 200:
                        result.fail(f"TenderDetailsHomeJson returned {response.status_code}")
                        break
                    data = response.json()
                    result.pages_fetched += 1
                    rows = parse_ts_rows(data, self.base_url, self.name, self.state)
                    result.candidates.extend(rows)
                    start += TS_PAGE_SIZE
                    echo += 1
                    if not rows or start >= ts_total(data):
                        break
        except Exception as exc:
            result.fail(str(exc))
            if not self.verified:
                result.status = "blocked"
        result.candidates = self.keyword_filter(result.candidates)[: self.ctx.max_candidates]
        return result

    async def fetch_detail(self, candidate: Candidate) -> DetailResult:
        procurement_id = candidate.raw.get("procurement_id")
        if not procurement_id:
            return DetailResult()
        async with self.client() as client:
            token = await self._open_session(client)
            form = {
                "popUPRequestParameter": "popUPRequestParameter",
                "hdnPage": "login.html",
                "hdnProcurementID": str(procurement_id),
                "hdnPreviousPage": "login.html",
                "hdnImmediatePreviousPage": "login.html",
                "CSRFToken": token or "",
                "hdnEncryptNames": "hdnEncryptNames",
                "hdnEncryptValues": "hdnEncryptValues",
            }
            await client.pause()
            response = await client.post(
                f"{self.base_url}/ViewTender.html",
                content=urlencode(form),
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": f"{self.base_url}/TenderDetailsHome.html",
                },
            )
        if response.status_code != 200:
            return DetailResult(fields={"status": response.status_code})
        return parse_ts_detail(response.text)


class AndhraPradeshScraper(TelanganaScraper):
    name = "andhra_pradesh"
    state = "Andhra Pradesh"
    base_url = "https://tender.apeprocurement.gov.in"
    verified = False


# ---------------------------------------------------------------------------------------------
# Bihar (eproc2.bihar.gov.in/EPSV2Web)
# ---------------------------------------------------------------------------------------------
BIHAR = "https://eproc2.bihar.gov.in/EPSV2Web"
_ORG_ID = re.compile(r"orgId\s*=\s*'(\d+)'")


def parse_bihar_list(records: list[dict[str, Any]]) -> list[Candidate]:
    """Keys come from the listing page's Angular template bindings (``tender.current*``)."""
    out: list[Candidate] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        ref = str(
            rec.get("currenttenderrefno") or rec.get("currentOrgTenderId") or rec.get("currenttenderid") or ""
        ).strip()
        title = clean(rec.get("currentdescription"))
        if not ref or not title:
            continue
        tender_pk = rec.get("currenttenderid") or rec.get("tenderid")
        out.append(
            Candidate(
                portal="bihar",
                tender_id=ref,
                title=title,
                buyer_org=clean(rec.get("currentdeptname") or rec.get("deptname") or ""),
                state="Bihar",
                portal_url=f"{BIHAR}/openarea/tenderListingPage.action#latestTenders",
                detail_url=f"{BIHAR}/rest/tender/previewTenderByTenderId?tenderId={tender_pk}" if tender_pk else None,
                deadline=parse_indian_datetime(str(rec.get("currentbidEndDate") or "")),
                published_at=parse_indian_datetime(str(rec.get("currentbidStartDate") or "")),
                listing_text=f"{title}. Ref {ref}. Org tender id {rec.get('currentOrgTenderId')}.",
                raw={"tenderid": tender_pk, "deptid": rec.get("currentdeptid")},
            )
        )
    return out


class BiharScraper(BaseScraper):
    name = "bihar"
    state = "Bihar"
    base_url = BIHAR

    async def scrape(self) -> ScrapeResult:
        result = ScrapeResult(portal=self.name)
        try:
            async with self.client() as client:
                home = await client.get(f"{BIHAR}/")
                if home.status_code != 200:
                    raise RuntimeError(f"home returned {home.status_code}")
                await client.pause()
                listing = await client.post(
                    f"{BIHAR}/openarea/tenderListingPage.action",
                    content=urlencode({"Authorization": ""}),
                    headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": f"{BIHAR}/"},
                )
                result.pages_fetched += 1
                match = _ORG_ID.search(listing.text)
                body: dict[str, Any] = {"orgId": match.group(1)} if match else {}
                await client.pause()
                response = await client.post(
                    f"{BIHAR}/rest/openarea/getTenderList",
                    json=body,
                    headers={"Accept": "application/json", "Referer": f"{BIHAR}/openarea/tenderListingPage.action"},
                )
                result.pages_fetched += 1
                if response.status_code != 200:
                    result.fail(
                        f"getTenderList returned {response.status_code} (body {body}) - request shape unverified"
                    )
                    return result
                data = response.json()
                records = data if isinstance(data, list) else data.get("tenderList") or data.get("data") or []
                rows = parse_bihar_list(records)
                if records and not rows:
                    result.errors.append("getTenderList responded but item keys differ from the page template")
                    result.status = "partial"
                result.candidates = self.keyword_filter(rows)[: self.ctx.max_candidates]
        except Exception as exc:
            result.fail(str(exc))
        return result

    async def fetch_detail(self, candidate: Candidate) -> DetailResult:
        if not candidate.detail_url:
            return DetailResult()
        async with self.client() as client:
            await client.get(f"{BIHAR}/")
            await client.pause()
            response = await client.post(candidate.detail_url, json={}, headers={"Accept": "application/json"})
        if response.status_code != 200:
            return DetailResult(fields={"status": response.status_code})
        try:
            data = response.json()
        except ValueError:
            return DetailResult(text=clean(BeautifulSoup(response.text, "lxml").get_text(" "))[:20000])
        return DetailResult(text=_flatten_json(data))


def _flatten_json(data: Any, prefix: str = "", limit: int = 400) -> str:
    lines: list[str] = []

    def walk(node: Any, path: str) -> None:
        if len(lines) >= limit:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for index, value in enumerate(node[:50]):
                walk(value, f"{path}[{index}]")
        elif node not in (None, "", [], {}):
            lines.append(f"{path}: {node}")

    walk(data, prefix)
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------
# Gujarat (nprocure)
# ---------------------------------------------------------------------------------------------
NPROCURE_LIVE = "https://tender.nprocure.com/"


class NprocureScraper(BaseScraper):
    """Reports the reachability of tender.nprocure.com; no listing structure could be observed."""

    name = "gujarat_nprocure"
    state = "Gujarat"
    base_url = NPROCURE_LIVE

    async def scrape(self) -> ScrapeResult:
        result = ScrapeResult(portal=self.name)
        try:
            async with self.client(timeout=30.0, retries=1) as client:
                response = await client.get(NPROCURE_LIVE)
            result.pages_fetched = 1
            if response.status_code == 200:
                result.status = "partial"
                result.errors.append(
                    "tender.nprocure.com reachable (first time) - listing structure not yet mapped; save the page and extend NprocureScraper"
                )
            else:
                result.fail(f"tender.nprocure.com returned {response.status_code}")
        except Exception as exc:
            result.status = "blocked"
            result.errors.append(
                f"tender.nprocure.com unreachable from this network ({type(exc).__name__}); needs Indian egress"
            )
        return result


def state_scrapers(ctx: ScrapeContext) -> list[BaseScraper]:
    return [
        KarnatakaScraper(ctx),
        TelanganaScraper(ctx),
        AndhraPradeshScraper(ctx),
        BiharScraper(ctx),
        NprocureScraper(ctx),
    ]
