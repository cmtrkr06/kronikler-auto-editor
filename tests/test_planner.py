from carscore_source import CarScoreSource, snapshot_from_dict
from config import CarScoreSettings
from memory import InMemoryStore
from models import TopicStatus
from planner import candidates, compare_key, fault_key, print_plan
from test_carscore_source import FakeFetcher, FORMENTOR, GOLF


def snap(**over):
    return CarScoreSource(FakeFetcher(), CarScoreSettings(**over)).collect()


def by_key(cands):
    return {c.topic.key: c for c in cands}


def test_fault_gate_and_pack():
    c = by_key(candidates(snap()))
    dq200, dq381 = c[fault_key("DQ200")], c[fault_key("DQ381")]
    assert dq200.ok and dq200.action == "new" and dq200.topic.sample == 84
    assert not dq381.ok and "15 bildirim < eşik 30" in dq381.reason
    text = dq200.pack.render()
    assert "84 bildirimden 11 tanesinde" in text and "arıza oranı değildir" in text.lower()
    assert "[K1]" in text and "https://shiftandtrend.com/sanziman/dq200/" in text
    # engine units: their vehicle pages are not in the fixtures -> never a candidate
    assert not c[fault_key("1.5 eTSI 150 HP")].ok


def test_compare_needs_both_vehicles_and_enough_opinions():
    cands = [c for c in candidates(snap()) if c.topic.type.value == "karsilastirma"]
    # only Golf and Formentor pages exist in the fixtures, and they are in different classes
    assert cands and not any(c.ok for c in cands)
    golf_leon = next(c for c in cands if GOLF in c.topic.subject_urls and "Leon" in c.topic.title)
    assert "veri yok" in golf_leon.reason


def test_compare_pack_marks_fault_data_as_unit_wide():
    s = snap()
    s.hub.leads[1].body = "Hatchback"                  # put Formentor next to Golf for this test
    c = next(x for x in candidates(s) if set(x.topic.subject_urls) == {GOLF, FORMENTOR})
    assert c.ok and c.topic.sample == 31
    text = c.pack.render()
    assert "DQ200 kullanan TÜM araçların" in text and "yüzdeler kişi sayısına çevrilemez" in text


def test_drafted_topic_is_not_repeated_but_updated_after_growth():
    store = InMemoryStore()
    first = by_key(candidates(snap()))[fault_key("DQ200")]
    first.topic.status = TopicStatus.DRAFTED
    store.save_topic(first.topic)
    again = by_key(candidates(snap(), store))[fault_key("DQ200")]
    assert not again.ok and "zaten yazıldı" in again.reason
    first.topic.sample, first.topic.data_hash = 60, "old"       # it was written when there were 60 reports
    grown = by_key(candidates(snap(), store))[fault_key("DQ200")]
    assert grown.ok and grown.action == "update"


def test_rejected_topic_never_comes_back():
    store = InMemoryStore()
    t = by_key(candidates(snap()))[fault_key("DQ200")].topic
    t.status = TopicStatus.REJECTED
    store.save_topic(t)
    assert not by_key(candidates(snap(), store))[fault_key("DQ200")].ok


def test_problem_subject_is_never_a_candidate():
    s = snap()
    s.problems["DQ200"] = ["DQ200: bildirim 90 -> 84 (geriledi)"]
    c = by_key(candidates(s))[fault_key("DQ200")]
    assert not c.ok and "geriledi" in c.reason


def test_keys_are_stable_and_order_independent():
    assert fault_key("1.5 eTSI 150 HP") == "ariza-1-5-etsi-150-hp"
    assert compare_key("a", "b", "X Y") == compare_key("b", "a", "X Y")


def test_plan_runs_from_a_saved_snapshot(tmp_path, capsys, monkeypatch):
    import json
    import main
    path = tmp_path / "s.json"
    path.write_text(json.dumps(snap().to_dict(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("KRONIKLER_MEMORY_BACKEND", "memory")
    assert main.plan(str(path), show="ariza-dq200") == 0
    out = capsys.readouterr().out
    assert "[YENİ] ariza: DQ200" in out and "== ariza-dq200 ==" in out and "[ELENDİ]" in out
    print_plan([])
