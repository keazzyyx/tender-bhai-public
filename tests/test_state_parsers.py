"""Karnataka / Telangana / Bihar parsers against the fixtures captured live on 2026-10-30."""

import json
from datetime import datetime

from tender_bhai.config import IST
from tender_bhai.scrapers.states import (
    kppp_detail_to_result,
    kppp_search_body,
    parse_bihar_list,
    parse_kppp_list,
    parse_ts_detail,
    parse_ts_rows,
    ts_list_params,
    ts_total,
)
from tests.conftest import FIXTURES, fixture


def test_kppp_list_and_body():
    records = json.loads(fixture("karnataka", "search_eproc_tenders_goods_page0.json"))
    rows = parse_kppp_list(records)
    assert len(rows) == 5
    first = rows[0]
    assert first.tender_id == "RDPR/2026-27/IND15368"
    assert first.title == "SUPPLY OF MATERIAL TO KUKKUT SHED IN NAGARAHAL"
    assert first.buyer_org == "Rural Development and Panchayat Raj Department"
    assert first.estimated_value_inr == 308552.86
    assert first.deadline == datetime(2026, 11, 16, 11, 0, tzinfo=IST)
    assert first.raw["nitId"] == 63804
    assert first.detail_url.endswith("/63804/goods-tender-full-view")
    body = kppp_search_body("cricket")
    assert body["category"] == "GOODS" and body["status"] == "PUBLISHED" and body["title"] == "cricket"
    assert kppp_search_body()["title"] is None


def test_kppp_detail():
    data = json.loads(fixture("karnataka", "goods_tender_full_view_nit63804.json"))
    result = kppp_detail_to_result(data)
    assert result.fields["emd_inr"] == 8000
    assert result.fields["tender_value_inr"] == 308552.86
    assert result.fields["deadline"] == datetime(2026, 11, 16, 11, 0, tzinfo=IST)
    assert result.fields["contact_name"] == "Praveen Patil"
    assert "Display board kuk0258 | qty 1.0 Nos" in result.text
    assert "Rural Development and Panchayat Raj Department" in result.text


def test_telangana_rows_and_params():
    data = json.loads(fixture("telangana", "TenderDetailsHomeJson_current_page0.json"))
    assert ts_total(data) == 1985
    rows = parse_ts_rows(data, "https://tender.telangana.gov.in", "telangana", "Telangana")
    assert len(rows) == 5
    first = rows[0]
    assert first.tender_id == "740206"
    assert first.buyer_org == "THE SINGARENI COLLIERIES COMPANY LIMITED"
    assert first.estimated_value_inr == 14_899_300_000
    assert first.deadline == datetime(2026, 11, 7, 15, 0, tzinfo=IST)
    assert first.published_at == datetime(2026, 10, 7, 12, 7, tzinfo=IST)
    assert first.raw["procurement_id"] == "673197" and first.raw["indent_id"] == "673207"
    assert first.detail_url.endswith("hdnProcurementID=673197")
    params = dict(ts_list_params(100, 100, 2))
    recorded = (FIXTURES / "telangana" / "TenderDetailsHomeJson_request_url.txt").read_text()
    for key in ("hdnType=current", "hdnEncryptNames=hdnEncryptNames", "iColumns=10", "mDataProp_9=9&bSortable_9=false"):
        assert key in recorded
    assert params["hdnType"] == "current" and params["iDisplayStart"] == "100" and params["sEcho"] == "2"
    assert params["bSortable_9"] == "false" and params["bSortable_0"] == "true"


def test_telangana_detail():
    result = parse_ts_detail(fixture("telangana", "ViewTender_677069.html"))
    assert result.fields["tender_value_inr"] == 12_600_000
    assert result.fields["emd_inr"] == 126_000
    assert result.fields["deadline"] == datetime(2026, 11, 16, 17, 0, tzinfo=IST)
    assert result.fields["department"] == "THE SINGARENI COLLIERIES COMPANY LIMITED"
    assert result.fields["contact_phone"] == "8332974875"
    assert result.fields["contact_email"] == "gm_naini@scclmines.com"
    assert "Tender ID: 744078" in result.text


def test_bihar_list_from_template_keys():
    records = [
        {
            "currenttenderid": 91,
            "currentOrgTenderId": "BSEIDC/2026/17",
            "currenttenderrefno": "NIT-17",
            "currentdescription": "Supply of sports kits to schools",
            "currentbidEndDate": "2026-11-20 15:00",
            "currentdeptid": 5,
        },
        {"currentdescription": "", "currenttenderrefno": "x"},
    ]
    rows = parse_bihar_list(records)
    assert len(rows) == 1
    assert rows[0].tender_id == "NIT-17"
    assert rows[0].deadline == datetime(2026, 11, 20, 15, 0, tzinfo=IST)
    assert rows[0].detail_url.endswith("tenderId=91")
    assert "openareaTenderList" in fixture("bihar", "openarea_tenderListingPage_shell.html")
