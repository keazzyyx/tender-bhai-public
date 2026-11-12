from tender_bhai.dedup import dedup_candidates, filter_known, passes_value_floor
from tender_bhai.keywords import matched_keywords, matches
from tender_bhai.models import Candidate, dedup_hash


def cand(portal: str, tid: str, title: str, buyer: str = "Dept of Sports") -> Candidate:
    return Candidate(portal=portal, tender_id=tid, title=title, buyer_org=buyer, portal_url="https://x")


def test_keywords_match_core_products():
    assert "cricket" in matched_keywords("Supply of Cricket Bats and Balls")
    assert matches("Procurement of track suits and jerseys for state team")
    assert matches("Purchase of sports material for government schools")
    assert matches("Bowling machine with bowling machine balls")
    assert matches("Physical Education equipment for Kendriya Vidyalaya")


def test_weak_words_need_sport_context():
    assert not matches("Supply of mosquito net for hospital wards")
    assert not matches("Traffic cone and road barriers")
    assert not matches("Uniform for security guards")
    assert matches("Practice net for cricket academy")
    assert matches("Sports uniform for school teams")


def test_negative_patterns_and_ignore_keywords():
    assert not matches("Capacitor bank 100 kVAR")
    assert not matches("Mattress for hostel")
    assert not matches("Cricket kits", ignore_keywords=["cricket"])
    assert matches("Football ground maintenance", extra_keywords=["football"])


def test_dedup_within_run_and_against_db():
    a = cand("nic_up", "1", "Supply of Cricket Kits")
    b = cand("nic_up", "1", "Supply of Cricket Kits (repeat)")
    c = cand("cppp", "99", "supply  of CRICKET kits!")  # same normalised title+buyer
    d = cand("gem", "GEM/1", "Tracksuits")
    unique = dedup_candidates([a, b, c, d])
    assert [x.tender_id for x in unique] == ["1", "GEM/1"]
    fresh = filter_known(unique, known_ids={("gem", "GEM/1")}, known_hashes=set())
    assert [x.tender_id for x in fresh] == ["1"]
    fresh = filter_known(unique, known_ids=set(), known_hashes={dedup_hash("Supply of Cricket Kits", "Dept of Sports")})
    assert [x.tender_id for x in fresh] == ["GEM/1"]


def test_value_floor_keeps_unknown():
    assert passes_value_floor(None, 600_000)
    assert passes_value_floor(600_000, 600_000)
    assert not passes_value_floor(599_999, 600_000)
