from datetime import datetime

from tender_bhai.config import IST
from tender_bhai.dates import days_until, format_deadline, parse_indian_datetime, to_iso
from tender_bhai.money import format_inr, indian_group, parse_inr


def test_parse_dash_month_with_time():
    dt = parse_indian_datetime("14-Nov-2026 15:00")
    assert dt == datetime(2026, 11, 14, 15, 0, tzinfo=IST)


def test_parse_slash_date_defaults_to_end_of_day():
    dt = parse_indian_datetime("14/11/2026")
    assert dt == datetime(2026, 11, 14, 23, 59, tzinfo=IST)


def test_parse_dotted_date_with_pm():
    dt = parse_indian_datetime("14.11.2026 03:00 PM")
    assert dt == datetime(2026, 11, 14, 15, 0, tzinfo=IST)


def test_parse_am_pm_edge_cases():
    assert parse_indian_datetime("01-Jan-2027 12:00 AM").hour == 0
    assert parse_indian_datetime("01-Jan-2027 12:30 PM").hour == 12
    assert parse_indian_datetime("31-Oct-2026 09:00 AM").hour == 9


def test_parse_portal_formats():
    assert parse_indian_datetime("13 Nov 2026") == datetime(2026, 11, 13, 23, 59, tzinfo=IST)
    assert parse_indian_datetime("16-03-2023 15:00:00") == datetime(2023, 3, 16, 15, 0, tzinfo=IST)
    assert parse_indian_datetime("2026-11-14T15:00+05:30") == datetime(2026, 11, 14, 15, 0, tzinfo=IST)
    assert parse_indian_datetime("2026-11-14T09:30:00Z") == datetime(2026, 11, 14, 15, 0, tzinfo=IST)


def test_parse_garbage():
    assert parse_indian_datetime("NA") is None
    assert parse_indian_datetime("") is None
    assert parse_indian_datetime(None) is None
    assert parse_indian_datetime("31-Feb-2026") is None


def test_to_iso_and_days_until():
    dt = parse_indian_datetime("14-Nov-2026 15:00")
    assert to_iso(dt) == "2026-11-14T15:00+05:30"
    ref = datetime(2026, 11, 10, 8, 0, tzinfo=IST)
    assert days_until(dt, ref) == 4
    assert "4 days" in format_deadline(dt, ref)
    assert "today" in format_deadline(dt, datetime(2026, 11, 14, 8, 0, tzinfo=IST))
    assert "closed" in format_deadline(dt, datetime(2026, 11, 20, 8, 0, tzinfo=IST))
    assert format_deadline(None) == "deadline not stated"


def test_parse_inr_variants():
    assert parse_inr("₹ 6,50,000") == 650_000
    assert parse_inr("Rs. 12.5 Lakh") == 1_250_000
    assert parse_inr("1.2 Cr") == 12_000_000
    assert parse_inr("6500000.00") == 6_500_000
    assert parse_inr("8,80,612") == 880_612
    assert parse_inr("80 Lakh (s)") == 8_000_000
    assert parse_inr(2000000) == 2_000_000
    assert parse_inr("NA") is None
    assert parse_inr("0.00") is None
    assert parse_inr("") is None


def test_format_inr():
    assert format_inr(650_000) == "₹6.5 lakh"
    assert format_inr(12_000_000) == "₹1.2 crore"
    assert format_inr(45_000) == "₹45,000"
    assert format_inr(None) == "value not stated"
    assert indian_group(1234567) == "12,34,567"
    assert indian_group(999) == "999"
