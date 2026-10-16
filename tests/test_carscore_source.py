"""Parser tests on real CarScore pages saved 2026-10-16 (tests/fixtures/, fetched from GitHub Actions)."""
from pathlib import Path

import pytest

from carscore_source import (CarScoreSource, ParseError, Snapshot, drift, latest_snapshot, parse_hub,
                             parse_unit, parse_vehicle, save_snapshot, snapshot_from_dict, to_dec, to_int)
from config import CarScoreSettings
from models import FetchOutcome, FetchResult

FX = Path(__file__).parent / "fixtures"
BASE = "https://shiftandtrend.com"
GOLF = f"{BASE}/arac/volkswagen-golf-mk8-facelift-1-5-etsi-116-hp-life-dsg-7-ileri/"
FORMENTOR = f"{BASE}/arac/cupra-formentor-km7-1-5-tsi-150-hp-formentor-dsg/"
FILES = {
    f"{BASE}/araclar/": "araclar.html",
    f"{BASE}/sanziman/dq200/": "sanziman_dq200.html",
    f"{BASE}/sanziman/dq381/": "sanziman_dq381.html",
    f"{BASE}/motor/1-5-etsi-150-hp/": "motor_1_5_etsi_150_hp.html",
    GOLF: "arac_volkswagen_golf_mk8_facelift_1_5_etsi_116_hp_life_dsg_7_ileri.html",
    FORMENTOR: "arac_cupra_formentor_km7_1_5_tsi_150_hp_formentor_dsg.html",
}


def page(name: str) -> str:
    return (FX / name).read_text(encoding="utf-8")


# ---- helpers
def test_number_helpers():
    assert to_int("1.498") == 1498 and to_int(" %84 ") == 84 and to_int("84 ve 11") is None
    assert to_dec("5,5 L/100km") == 5.5 and to_dec("4,7") == 4.7 and to_dec("") is None


# ---- hub
def test_hub_spotlights_and_leaders():
    hub = parse_hub(page("araclar.html"), f"{BASE}/araclar/")
    assert hub.stats == {"araç": 181, "nesil": 103, "değerlendirme": 329, "sahip anketi": 491}
    units = [(s.unit, s.label, s.with_issue, s.reports) for s in hub.spotlights]
    assert units == [("DQ200", "DSG arızası", 11, 84), ("1.5 eTSI 150 HP", "48V jeneratör", 2, 50),
                     ("1.5 eTSI 116 HP", "48V jeneratör", 1, 23), ("DQ381", "DSG arızası", 1, 15)]
    assert hub.spotlights[0].url == f"{BASE}/sanziman/dq200/"
    golf = next(x for x in hub.leads if x.url == GOLF)
    assert (golf.body, golf.opinions, golf.carscore) == ("Hatchback", 72, 82)
    assert {x.body for x in hub.leads} == {"SUV", "Hatchback", "Sedan"} and len(hub.leads) == 9


# ---- gearbox / engine pages
def test_gearbox_fault_section_is_complete_and_consistent():
    u = parse_unit(page("sanziman_dq200.html"), f"{BASE}/sanziman/dq200/")
    f = u.fault
    assert (u.kind, u.code, f.unit, f.reports, f.with_issue, f.verified_owners) == ("Şanzıman", "DQ200", "DQ200", 84, 11, 5)
    assert [(o.label, o.count) for o in f.outcomes][:2] == [("Hayır, sorun yaşamadım", 67), ("Mekatronik onarıldı / değişti", 2)]
    km = next(x for x in f.facts if x.question == "Kaç kilometrede oldu?")
    assert (km.answer, km.base, km.stat) == ("80 – 100 Bin", 8, "ortanca")
    assert "arıza oranı değildir" in f.caveat
    assert f.problems() == []
    assert len(u.vehicles) == 12 and "138 araçta" in u.vehicle_note


def test_engine_page_has_no_fault_section():
    u = parse_unit(page("motor_1_5_etsi_150_hp.html"), f"{BASE}/motor/1-5-etsi-150-hp/")
    assert u.kind == "Motor" and u.fault is None and u.vehicles


# ---- vehicle pages
def test_vehicle_page_numbers():
    v = parse_vehicle(page(FILES[GOLF]), GOLF)
    assert (v.make, v.carscore, v.opinions, v.rating, v.rating_count, v.repurchase_pct) == \
        ("Volkswagen Golf", 82, 72, 4.7, 31, 84)
    assert v.consumption == {"Ortalama": 5.5, "En düşük": 4.3, "En yüksek": 6.5, "Kayıt": 27}
    assert v.complaints[0].label == "Trim Sesi" and v.complaints[0].count == 58
    assert v.specs["Şanzıman"] == "DQ200" and v.specs["Kasa Tipi"].startswith("Hatchback")
    assert [(f.unit, f.with_issue, f.reports) for f in v.faults] == [("1.5 eTSI 116 HP", 1, 23), ("DQ200", 11, 84)]
    assert v.problems() == []


def test_second_vehicle_page_parses_the_same_way():
    v = parse_vehicle(page(FILES[FORMENTOR]), FORMENTOR)
    assert (v.carscore, v.opinions, v.specs["Şanzıman"]) == (79, 31, "DQ381") and v.problems() == []


# ---- a changed layout must fail loudly, never produce a number
def test_missing_required_field_is_a_parse_error():
    html = page("sanziman_dq200.html").replace("carscore-fault__headline", "renamed-headline")
    with pytest.raises(ParseError, match="arıza başlığı"):
        parse_unit(html, "u")
    with pytest.raises(ParseError, match="CarScore|hüküm"):
        parse_vehicle(page(FILES[GOLF]).replace("carscore-verdict__number", "x"), GOLF)


def test_inconsistent_fault_numbers_are_problems():
    html = page("sanziman_dq200.html").replace("<strong>11</strong> tanesinde", "<strong>12</strong> tanesinde", 1)
    problems = parse_unit(html, "u").fault.problems()
    assert any("arıza seçenekleri toplamı 11, başlıkta 12" in p for p in problems)


def test_renamed_no_issue_label_is_a_problem_not_a_fault():
    html = page("sanziman_dq381.html").replace("Hayır, sorun yaşamadım", "Sorun yok")
    assert any("sorun yaşamadım" in p for p in parse_unit(html, "u").fault.problems())


def test_single_choice_percentages_must_add_up():
    v = parse_vehicle(page(FILES[GOLF]), GOLF)
    v.stats["Bu aracı tekrar alır mıydın?"][0].count = 60
    assert any("tekrar alır" in p for p in v.problems())


# ---- collector
class FakeFetcher:
    def __init__(self, overrides=None):
        self.calls, self.overrides = [], overrides or {}

    def fetch(self, url):
        self.calls.append(url)
        if url in self.overrides:
            return self.overrides[url]
        name = FILES.get(url)
        if name is None:
            return FetchResult(url, FetchOutcome.NOT_FOUND, 404)
        return FetchResult(url, FetchOutcome.OK, 200, page(name).encode("utf-8"), 1)


def collect(**over):
    fetcher = FakeFetcher(over.pop("overrides", None))
    return CarScoreSource(fetcher, CarScoreSettings(**over)).collect(), fetcher


def test_collect_reads_faults_and_leaders_and_cross_checks():
    snap, fetcher = collect()
    assert set(snap.faults) == {"DQ200", "DQ381"}           # 1.5 eTSI pages: vehicles not in fixtures
    assert snap.ok("DQ200") and snap.ok("DQ381") and snap.ok(GOLF)
    assert snap.vehicles[GOLF].opinions == 72
    assert any("1-5-etsi-116-hp" in e for e in snap.errors)  # missing page is reported, not invented
    assert len(fetcher.calls) == len(set(fetcher.calls))      # no page fetched twice


def test_hub_and_unit_page_must_agree():
    hub = page("araclar.html").replace("<strong>11</strong> / 84", "<strong>10</strong> / 84")
    snap, _ = collect(overrides={f"{BASE}/araclar/": FetchResult("", FetchOutcome.OK, 200, hub.encode(), 1)})
    assert any("ana sayfada 10/84" in p for p in snap.problems["DQ200"]) and not snap.ok("DQ200")


def test_vehicle_page_limit_is_respected():
    snap, fetcher = collect(max_vehicle_pages=1)
    assert len(snap.vehicles) == 1 and any("sınırı" in e for e in snap.errors)


def test_blocked_hub_stops_without_data():
    snap, _ = collect(overrides={f"{BASE}/araclar/": FetchResult("", FetchOutcome.FORBIDDEN, 403)})
    assert snap.hub is None and snap.faults == {} and snap.errors


# ---- snapshot + drift
def test_snapshot_roundtrip_and_drift(tmp_path):
    snap, _ = collect()
    save_snapshot(snap, str(tmp_path), "2026-10-15")
    back = latest_snapshot(str(tmp_path), "2026-10-16")
    assert back.faults["DQ200"].reports == 84 and back.vehicles[GOLF].opinions == 72
    assert drift(back, snap) == {}
    lower = snapshot_from_dict(snap.to_dict())
    lower.faults["DQ200"].reports = 80
    assert "DQ200" in drift(back, lower)
    assert latest_snapshot(str(tmp_path), "2026-10-15") is None   # only days BEFORE today count


# ---- variants seen on the live site 2026-10-16 (second fixture batch)
LEON = f"{BASE}/arac/cupra-leon-leon-1-5-etsi-150-hp-standart-dsg/"
TERRAMAR = f"{BASE}/arac/cupra-terramar-1-nesil-1-5-etsi-150-hp-supreme-dsg-7-ileri/"
SEAT = f"{BASE}/arac/seat-leon-mk4-1-5-e-hybrid-204-hp-fr-dsg-6-ileri/"
ID7 = f"{BASE}/arac/volkswagen-id-7-1-nesil-app-550-286-hp-pro-s/"


def test_below_threshold_survey_is_counts_not_percentages():
    v = parse_vehicle(page("arac_cupra_leon_leon_1_5_etsi_150_hp_standart_dsg.html"), LEON)
    q = "Seni en çok rahatsız eden ne oldu?"
    assert q in v.low_data and all(o.unit == "adet" for o in v.stats[q])
    assert not v.survey_ready() and v.problems() == []      # a format, not an inconsistency


def test_fault_section_still_collecting_is_pending_not_an_error():
    v = parse_vehicle(page("arac_cupra_terramar_1_nesil_1_5_etsi_150_hp_supreme_dsg_7_ileri.html"), TERRAMAR)
    assert v.pending_faults == [{"title": "48V Jeneratör (Alternatör)", "collected": 7, "threshold": 12}]
    assert [f.unit for f in v.faults] == ["DQ381"]
    seat = parse_vehicle(page("arac_seat_leon_mk4_1_5_e_hybrid_204_hp_fr_dsg_6_ileri.html"), SEAT)
    assert seat.faults == [] and seat.pending_faults[0]["threshold"] == 10


def test_participants_may_be_fewer_than_opinions():
    v = parse_vehicle(page("arac_volkswagen_id_7_1_nesil_app_550_286_hp_pro_s.html"), ID7)
    assert (v.opinions, v.participants) == (18, 17) and v.problems() == [] and v.survey_ready()


def test_saved_snapshot_plans_exactly_like_the_live_one(tmp_path):
    from planner import candidates
    live, _ = collect()
    save_snapshot(live, str(tmp_path), "2026-10-16")
    back = latest_snapshot(str(tmp_path), "2026-10-17")
    assert back.units.keys() == live.units.keys() and back.units[f"{BASE}/sanziman/dq200/"].vehicles
    a = {c.topic.key: c.pack.render() for c in candidates(live) if c.pack}
    b = {c.topic.key: c.pack.render() for c in candidates(back) if c.pack}
    assert a == b and a
