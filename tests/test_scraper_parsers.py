"""Parser unit tests against real pages saved during the build (tests/fixtures)."""

from datetime import datetime

from tender_bhai.config import IST
from tender_bhai.pdf import pdf_to_text
from tender_bhai.scrapers.base import looks_like_captcha, visible_text
from tender_bhai.scrapers.cppp import parse_home_active
from tender_bhai.scrapers.gem import build_payload, document_url, extract_csrf_token, parse_docs, parse_gem_pdf_text
from tender_bhai.scrapers.ireps import is_otp_gate, parse_results
from tender_bhai.scrapers.nicgep import (
    build_tab_post,
    detail_to_result,
    is_stale_session,
    last_page_number,
    next_page_href,
    page_url,
    parse_detail,
    parse_listing,
    parse_org_index,
    relevant_orgs,
)
from tender_bhai.scrapers.sai import parse_sai_tenders
from tests.conftest import fixture, fixture_bytes

UP = "https://etender.up.nic.in/nicgep/app"
CPPP = "https://eprocure.gov.in/eprocure/app"


# --- NIC / CPPP shared template -------------------------------------------------------------
def test_nic_by_date_listing_rows():
    rows = parse_listing(
        fixture("nic", "tenders_by_date_up.html"), UP, "nic_up", "Uttar Pradesh", "FrontEndListTendersbyDate"
    )
    assert len(rows) == 8
    first = rows[0]
    assert first.tender_id == "2026_UPPRD_1194378_19"
    assert first.title.startswith("Gram sabha Sirauli")
    assert first.buyer_org == "Panchayati Raj Department"
    assert first.raw["ref_no"] == "578 / Z P M / 2026-27 Date 07-10-2026"
    assert first.deadline == datetime(2026, 10, 31, 10, 0, tzinfo=IST)
    assert first.opening_date == datetime(2026, 11, 2, 11, 0, tzinfo=IST)
    assert first.published_at == datetime(2026, 10, 9, 16, 0, tzinfo=IST)
    assert first.detail_url.startswith(UP + "?component=%24DirectLink&page=FrontEndListTendersbyDate")
    assert first.state == "Uttar Pradesh"


def test_nic_pagination_helpers():
    html = fixture("nic", "tenders_by_date_up.html")
    assert last_page_number(html) == 107
    assert next_page_href(html).endswith("sp=AFrontEndListTendersbyDate%2Ctable&sp=2")
    assert page_url(UP, "FrontEndListTendersbyDate", 3).endswith("&sp=AFrontEndListTendersbyDate%2Ctable&sp=3")
    page2 = parse_listing(
        fixture("nic", "tenders_by_date_up_page2.html"), UP, "nic_up", "UP", "FrontEndListTendersbyDate"
    )
    assert page2[0].tender_id == "2026_UPPRD_1200308_12"
    assert {r.tender_id for r in page2}.isdisjoint(
        {r.tender_id for r in parse_listing(html, UP, "nic_up", "UP", "FrontEndListTendersbyDate")}
    )


def test_nic_14_day_tab_post_and_result():
    html = fixture("nic", "tenders_by_date_up.html")
    form = dict(build_tab_post(html, "LinkSubmit_1"))
    assert form["submitmode"] == "submit"
    assert form["submitname"] == "LinkSubmit_1"
    assert form["component"] == "ListTendersbyDate"
    assert form["page"] == "FrontEndListTendersbyDate"
    assert form["tokenSecret"]
    fourteen = fixture("nic", "tenders_by_date_up_14days.html")
    assert last_page_number(fourteen) == 948
    assert len(parse_listing(fourteen, UP, "nic_up", "UP", "FrontEndListTendersbyDate")) == 8


def test_nic_second_portal_same_structure():
    rows = parse_listing(
        fixture("nic", "tenders_by_date_uktenders.gov.in.html"),
        "https://uktenders.gov.in/nicgep/app",
        "nic_uttarakhand",
        "Uttarakhand",
        "FrontEndListTendersbyDate",
    )
    assert len(rows) == 8
    assert rows[0].tender_id == "2026_USCM1_101582_1"
    assert rows[0].detail_url.startswith("https://uktenders.gov.in/nicgep/app?component=")


def test_nic_latest_active_is_captcha_form():
    html = fixture("nic", "latest_active_tenders_up.html")
    assert parse_listing(html, UP, "nic_up", "UP", "FrontEndLatestActiveTenders") == []
    assert looks_like_captcha(html)
    assert looks_like_captcha(fixture("nic", "captcha_page.html"))
    assert is_stale_session(fixture("nic", "stale_session_page.html"))
    assert not is_stale_session(html)


def test_nic_org_index_and_drilldown():
    rows = parse_org_index(fixture("nic", "tenders_by_organisation_up.html"))
    assert rows[0][0] == "Agra Development Authority"
    assert rows[0][1] == 3
    assert "FrontEndTendersByOrganisation" in rows[0][2]
    picked = relevant_orgs(rows)
    assert [p[0] for p in picked] == ["Allahabad Municipal Corporation"]
    listing = parse_listing(
        fixture("nic", "tenders_by_organisation_up_listing.html"), UP, "nic_up", "UP", "FrontEndTendersByOrganisation"
    )
    assert len(listing) == 3
    assert listing[0].tender_id == "2026_ADAA_1199184_7"
    assert listing[0].deadline == datetime(2026, 11, 17, 15, 0, tzinfo=IST)


def test_nic_detail_fields():
    fields = parse_detail(fixture("nic", "tender_detail.html"), UP)
    result = detail_to_result(fields, "")
    f = result.fields
    assert f["tender_id"] == "2026_UPPRD_1194378_19"
    assert f["tender_value_inr"] == 4_128_800
    assert f["emd_inr"] == 82_580
    assert f["product_category"] == "Civil Works"
    assert f["location"] == "Maharajganj"
    assert f["pincode"] == "273303"
    assert f["deadline"] == datetime(2026, 10, 31, 10, 0, tzinfo=IST)
    assert f["opening_date"] == datetime(2026, 11, 2, 11, 0, tzinfo=IST)
    assert f["authority_name"].startswith("Apar Mukhya Adhikari")
    assert f["documents"][0]["name"] == "Tendernotice_1.pdf"
    assert result.document_url.startswith(UP)


def test_cppp_by_date_listing_and_detail():
    rows = parse_listing(fixture("cppp", "latest_active_tenders.html"), CPPP, "cppp", "", "FrontEndListTendersbyDate")
    assert len(rows) == 8
    assert rows[0].tender_id == "2026_SAI_929224_1"
    assert rows[0].buyer_org == "Sports Authority of India"
    assert rows[0].title == "Procurement of Colnago TT1 Non Consumable Cycling Equipment"
    assert last_page_number(fixture("cppp", "latest_active_tenders.html")) == 10
    page2 = parse_listing(
        fixture("cppp", "latest_active_tenders_page2.html"), CPPP, "cppp", "", "FrontEndListTendersbyDate"
    )
    assert page2 and page2[0].tender_id != rows[0].tender_id
    f = detail_to_result(parse_detail(fixture("cppp", "tender_detail.html"), CPPP), "").fields
    assert f["tender_id"] == "2026_ASI_932538_1"
    assert f["tender_value_inr"] == 880_612
    assert f["emd_inr"] == 22_015
    assert f["deadline"] == datetime(2026, 11, 9, 15, 0, tzinfo=IST)
    assert f["authority_name"] == "SA, ASI, Rajkot"


def test_cppp_captcha_pages_and_org_list():
    assert parse_listing(fixture("cppp", "latest_active_tenders_captcha_form.html"), CPPP, "cppp", "", "x") == []
    assert looks_like_captcha(fixture("cppp", "tenders_by_classification.html"))
    assert is_stale_session(fixture("cppp", "stale_session.html"))
    sai_rows = parse_listing(
        fixture("cppp", "tenders_by_organisation_sai.html"), CPPP, "cppp", "", "FrontEndTendersByOrganisation"
    )
    assert len(sai_rows) == 8
    assert all(r.buyer_org == "Sports Authority of India" for r in sai_rows)
    orgs = parse_org_index(fixture("cppp", "tenders_by_organisation.html"))
    assert orgs[0][:2] == ("Aligarh Muslim University", 7)


def test_cppp_home_table_parser():
    html = """<table id="activeTenders"><tr class="even" id="informal_0"><td width="30%"><a class="link2"
    href="/eprocure/app?component=%24DirectLink&amp;page=Home&amp;service=direct&amp;session=T&amp;sp=Sj5ff" id="DirectLink">1. SUPPLY OF SPORTS GOODS FOR KV SCHOOLS</a></td>
    <td width="20%">3C/1(28)/RJT/2026-27/C</td><td width="25%">09-Nov-2026 03:00 PM</td><td width="25%">10-Nov-2026 03:00 PM</td></tr></table>"""
    rows = parse_home_active(html)
    assert len(rows) == 1
    assert rows[0].title == "SUPPLY OF SPORTS GOODS FOR KV SCHOOLS"
    assert rows[0].tender_id == "3C/1(28)/RJT/2026-27/C"
    assert rows[0].deadline == datetime(2026, 11, 9, 15, 0, tzinfo=IST)
    assert rows[0].detail_url.startswith("https://eprocure.gov.in/eprocure/app?component=")


# --- SAI ----------------------------------------------------------------------------------------
def test_sai_tenders_table():
    rows = parse_sai_tenders(fixture("sai", "tenders.html"))
    assert len(rows) == 8
    first = rows[0]
    assert first.tender_id == "ea585618-2ce7-467f-b4b0-2f4cbf90513d"
    assert first.buyer_org == "Sports Authority of India"
    assert first.state == "Madhya Pradesh"
    assert first.document_url.endswith(".pdf")
    assert first.deadline == datetime(2026, 11, 13, 23, 59, tzinfo=IST)
    assert first.opening_date == datetime(2026, 11, 14, 23, 59, tzinfo=IST)
    archive = parse_sai_tenders(fixture("sai", "tenders_archive.html"))
    assert len(archive) == 8


# --- IREPS --------------------------------------------------------------------------------------
def test_ireps_gate_detected():
    assert is_otp_gate(fixture("ireps", "search_form.html"))
    assert parse_results(fixture("ireps", "search_form.html")) == []


# --- GeM --------------------------------------------------------------------------------------------
def test_gem_payload_and_token():
    payload = build_payload("cricket")
    assert '"searchBid":"cricket"' in payload["payload"]
    assert '"page"' not in payload["payload"]
    assert '"page":2' in build_payload("sports goods", 2)["payload"]
    assert (
        extract_csrf_token("data: {'payload': x, 'csrf_bd_gem_nk': 'c0640faa2eb9c3e7e407425e6d9e92d1'}")
        == "c0640faa2eb9c3e7e407425e6d9e92d1"
    )
    assert extract_csrf_token(fixture("gem", "all_bids_listing.html")) is None  # archived shell has scripts stripped
    assert 'id="searchBid"' in fixture("gem", "all_bids_listing.html")


def test_gem_docs_parser():
    docs = [
        {
            "b_id": 4476236, "b_bid_number": "GEM/2026/B/1234567", "b_bid_type": 1,
            "b_category_name": ["Cricket Bat", "Cricket Ball"], "b_total_quantity": 500,
            "ba_official_details_minName": "MINISTRY_OF_YOUTH_AFFAIRS_AND_SPORTS",
            "ba_official_details_deptName": "Sports Authority of India, Lucknow",
            "final_start_date_sort": "2026-10-20T10:00:00.000Z", "final_end_date_sort": "2026-11-10T15:00:00.000Z",
        },
        {"b_id": 1, "b_bid_number": "GEM/2026/R/1", "b_bid_type": 2, "b_category_name": "Tracksuit", "final_end_date_sort": 1794000000000},
        {"b_id": None, "b_bid_number": ""},
    ]  # fmt: skip
    rows = parse_docs(docs)
    assert len(rows) == 2
    assert rows[0].tender_id == "GEM/2026/B/1234567"
    assert rows[0].title == "Cricket Bat, Cricket Ball"
    assert rows[0].buyer_org == "MINISTRY OF YOUTH AFFAIRS AND SPORTS - Sports Authority of India, Lucknow"
    assert rows[0].deadline == datetime(2026, 11, 10, 20, 30, tzinfo=IST)
    assert rows[0].document_url == "https://bidplus.gem.gov.in/showbidDocument/4476236"
    assert document_url(docs[1]) == "https://bidplus.gem.gov.in/showradocumentPdf/1"
    assert rows[1].deadline.year == 2026


def test_gem_pdf_text_fields_from_pdftotext_and_pdfplumber():
    expected = {
        "estimated_value_inr": 2_000_000,
        "deadline": datetime(2023, 3, 16, 15, 0, tzinfo=IST),
        "opening_date": datetime(2023, 3, 16, 15, 30, tzinfo=IST),
        "ministry": "Ministry Of Finance",
        "department": "Department Of Financial Services",
        "organisation": "Regional Rural Banks (rrb)",
        "office": "Aryavart Bank",
        "min_turnover": "80 Lakh (s)",
        "msme_exempt": True,
        "emd_required": False,
        "bid_number": "GEM/2023/B/3178963",
    }
    for text in (fixture("gem", "bid_detail_4476236.txt"), pdf_to_text(fixture_bytes("gem", "bid_detail_4476236.pdf"))):
        fields = parse_gem_pdf_text(text)
        for key, value in expected.items():
            assert fields[key] == value, key


def test_pdf_helper_rejects_non_pdf():
    assert pdf_to_text(b"not a pdf") == ""
    assert pdf_to_text(None) == ""
    assert len(pdf_to_text(fixture_bytes("gem", "bid_detail_4476236.pdf"))) > 5000


def test_visible_text_strips_scripts():
    assert (
        visible_text(
            "<html><head><script>x</script></head><body><p>Hello</p> <style>a{}</style><p>World</p></body></html>"
        )
        == "Hello\nWorld"
    )
