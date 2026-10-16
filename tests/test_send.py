"""Visuals, Blogger send and the scheduled run, with fakes (no network, no AI)."""
import json
from datetime import date

import pytest

from image_library import LibraryImage
from memory import InMemoryStore
from models import TopicStatus
from planner import candidates, fault_key
from test_planner import snap
from test_writer import CLEAN, PACK, TOPIC, draft, pipeline
from visuals import compare_card, decorate, fault_bars, models_of, pick_photo
from config import WriterSettings

DQ200 = next(c for c in candidates(snap()) if c.topic.key == fault_key("DQ200"))
B = "https://blogger.googleusercontent.com/img/b/X/s1600/"


# ---- visuals
def test_models_and_fault_bars_come_from_data():
    assert models_of(["Volkswagen Passat B8 Facelift", "CUPRA Leon", "Volkswagen Passat Variant"]) == \
        [("Volkswagen", "Passat"), ("CUPRA", "Leon")]
    html = fault_bars(DQ200.data)
    assert "DQ200: 84 bildirim, 11 arıza" in html and ">67</strong>" in html and "arıza oranı değildir" in html


def test_own_photo_first_then_credited_carscore_photo_then_none():
    own = [LibraryImage(B + "a.jpg", "Kırmızı Passat", "a.jpg")]
    photo, notes = pick_photo([("Volkswagen", "Passat")], own, [])
    assert photo.source == "arşiv" and photo.src == B + "a.jpg" and not notes
    credited = {"src": "https://shiftandtrend.com/wp-content/uploads/g.jpg", "alt": "Golf", "author": "Yoshi Lye",
                "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/", "source_url": ""}
    photo, notes = pick_photo([("Volkswagen", "Golf")], own, [credited])
    assert photo.source == "carscore" and "Yoshi Lye" in photo.credit_html and notes
    uncredited = dict(credited, author="")
    photo, notes = pick_photo([("Volkswagen", "Golf")], own, [uncredited])
    assert photo is None and any("resimsiz" in n for n in notes)


def test_decorate_puts_cover_first_and_chart_after_intro():
    html, rep = decorate("ariza", DQ200.data, "<p>Giriş.</p><h2>A</h2><p>b</p>", [])
    assert html.startswith("<figure") and rep.photo.source == "carscore"
    assert html.index("<p>Giriş.</p>") < html.index("SAHİP BİLDİRİMLERİ") < html.index("<h2>A</h2>")
    assert "11 / 84" in html and "ARIZA KRONİĞİ" in html


def test_compare_card_shows_both_cars():
    a = {"name": "Golf", "carscore": 82, "opinions": 72, "rating": 4.7, "repurchase_pct": 84, "consumption": 5.5,
         "top_strength": "Konfor %71", "top_complaint": "Trim Sesi %58"}
    b = dict(a, name="Leon", rating=None, consumption=None)
    html = compare_card(a, b)
    assert "4,7/5" in html and "5,5 L/100km" in html and html.count("–") == 2


# ---- send
class FakeClient:
    def __init__(self):
        self.inserted = []

    def insert_draft(self, title, content, labels):
        self.inserted.append((title, content, labels))
        return {"id": "777", "status": "DRAFT", "updated": "2026-10-16T21:20:00+03:00"}

    def existing_titles(self):
        return []

    def find_draft(self, title):
        return {"id": "1", "content": f'<img src="{B}passat.jpg" alt="Passat">'}

    def live_posts(self):
        return []

    def get_post(self, post_id):
        return {"id": post_id, "status": "LIVE", "updated": "2026-10-17T10:00:00+03:00"}   # editor published it


def write_ready(tmp_path, monkeypatch):
    import write
    path = tmp_path / "snap.json"
    path.write_text(json.dumps(snap().to_dict(), ensure_ascii=False), encoding="utf-8")
    ws = WriterSettings(draft_dir=str(tmp_path / "drafts"))
    monkeypatch.chdir(tmp_path)
    return write, str(path), ws


def test_send_builds_final_html_with_note_photo_and_chart(tmp_path, monkeypatch, capsys):
    write, snap_path, ws = write_ready(tmp_path, monkeypatch)
    p, _, _ = pipeline(draft(), CLEAN)
    store = InMemoryStore()
    assert write.dry_run("", snap_path, pipeline=p, store=store, ws=ws) == 0
    client = FakeClient()
    library = [LibraryImage(B + "passat.jpg", "Passat", "passat.jpg")]
    assert write.send("ariza-dq200", client, store, library, ws) == 0
    title, html, labels = client.inserted[0]
    assert "EDİTÖR NOTU" in html and "Arama açıklaması" in html and B + "passat.jpg" in html
    assert html.index("EDİTÖR NOTU") < html.index("<figure") < html.index("SAHİP BİLDİRİMLERİ")
    assert store.get_topic("ariza-dq200").status == TopicStatus.DRAFTED
    saved = json.loads((tmp_path / "drafts" / "ariza-dq200.json").read_text(encoding="utf-8"))
    assert saved["blogger"]["post_id"] == "777"
    assert write.send("ariza-dq200", client, store, library, ws) == 1      # published: never touched, never twice
    assert len(client.inserted) == 1


def test_not_ready_draft_is_never_sent(tmp_path, monkeypatch):
    write, snap_path, ws = write_ready(tmp_path, monkeypatch)
    bad = draft(html_body="<p>çığır açıyor</p>")
    p, _, _ = pipeline(bad, bad)
    write.dry_run("", snap_path, pipeline=p, store=InMemoryStore(), ws=ws)
    client = FakeClient()
    assert write.send("ariza-dq200", client, InMemoryStore(), [], ws) == 1 and client.inserted == []


# ---- daily
def test_daily_sends_once_a_day_and_three_a_week(tmp_path, monkeypatch):
    write, snap_path, ws = write_ready(tmp_path, monkeypatch)
    store, client = InMemoryStore(), FakeClient()
    p, _, _ = pipeline(draft(), CLEAN)
    monday = date(2026, 10, 19)
    assert write.daily(client, store, p, ws, today=monday, snapshot_path=snap_path) == 0
    assert len(client.inserted) == 1 and store.get_meta("sent_days") == ["2026-10-19"]
    assert write.daily(client, store, p, ws, today=monday, snapshot_path=snap_path) == 0   # same day: skip
    assert len(client.inserted) == 1
    store.set_meta("sent_days", ["2026-10-19", "2026-10-21", "2026-10-22"])
    assert write.daily(client, store, p, ws, today=date(2026, 10, 23), snapshot_path=snap_path) == 0
    assert len(client.inserted) == 1                                                       # weekly cap


def test_daily_with_nothing_to_write_is_not_an_error(tmp_path, monkeypatch):
    write, snap_path, ws = write_ready(tmp_path, monkeypatch)
    store = InMemoryStore()
    for c in candidates(snap()):
        c.topic.status = TopicStatus.REJECTED
        store.save_topic(c.topic)
    assert write.daily(FakeClient(), store, None, ws, today=date(2026, 10, 19), snapshot_path=snap_path) == 0


def test_schema_only_touches_our_posts():
    import schema_markup as sm

    class Client:
        def __init__(self):
            self.updated = []

        def live_posts(self):
            return [{"id": "1", "title": "Eski yazı", "url": "u1", "content": "<p>a</p>", "published": "x", "updated": "y"},
                    {"id": "777", "title": "Bizim", "url": "u2", "content": "<p>b</p>", "published": "x", "updated": "y"}]

        def update_live_content(self, pid, content):
            self.updated.append(pid)
            return {"updated": "z"}
    c = Client()
    sm.apply(c, {}, only_ids={"777"})
    assert c.updated == ["777"]


def test_model_name_list_is_not_a_copy():
    from writer import Draft, evaluate
    names = next(p for p in PACK.passages if p.title.endswith("modellerden bazıları")).text.split(".")[0]
    body = draft(html_body=json.loads(draft())["html_body"].replace("</blockquote>", f"</blockquote><p>{names}.</p>"))
    r = evaluate(TOPIC, PACK, Draft.model_validate_json(body), WriterSettings())
    assert not any("aynen alınmış" in e for e in r.seo.errors), r.seo.errors


def test_recheck_runs_only_the_fact_check(tmp_path, monkeypatch):
    import write
    from ai_client import AIClient
    from config import AISettings
    from fact_checker import FactChecker
    from fakes import FakeBackend
    write_mod, snap_path, ws = write_ready(tmp_path, monkeypatch)
    bad = draft(html_body=json.loads(draft())["html_body"] + "<p>çığır açıyor</p>")
    p, _, _ = pipeline(bad, bad)
    write.dry_run("", snap_path, pipeline=p, store=InMemoryStore(), ws=ws)
    path = tmp_path / "drafts" / "ariza-dq200.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["ready"] is False
    saved["draft"]["html_body"] = json.loads(draft())["html_body"]          # e.g. a rule was fixed
    path.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    backend = FakeBackend(generate=[CLEAN])
    checker = FactChecker(AIClient(AISettings(), backend, sleep=lambda s: None))
    assert write.recheck("ariza-dq200", checker, InMemoryStore(), ws) == 0
    assert len(backend.prompts) == 1 and backend.prompts[0][0] == AISettings().check_model
    assert json.loads(path.read_text(encoding="utf-8"))["ready"] is True


def test_rewrite_feeds_the_saved_findings_back_once(tmp_path, monkeypatch):
    import write
    from ai_client import AIClient
    from config import AISettings
    from fact_checker import FactChecker
    from fakes import FakeBackend
    from test_writer import DIRTY
    from writer import Writer
    write_mod, snap_path, ws = write_ready(tmp_path, monkeypatch)
    p, _, _ = pipeline(draft(), DIRTY, draft(), DIRTY)          # failed after its own revision round
    write.dry_run("", snap_path, pipeline=p, store=InMemoryStore(), ws=ws)
    backend = FakeBackend(generate=[draft(), CLEAN])
    ai = AIClient(AISettings(), backend, sleep=lambda s: None)
    assert write.rewrite("ariza-dq200", Writer(ai, ws), FactChecker(ai), InMemoryStore(), ws) == 0
    assert len(backend.prompts) == 2 and "%13 arıza" in backend.prompts[0][1]
    assert backend.prompts[1][0] != backend.prompts[0][0]                       # checked on another model


def test_checker_prompt_accepts_facts_stated_with_their_base():
    from fact_checker import SYSTEM
    assert "Tabanı yanında açıkça yazılmışsa" in SYSTEM and "bulgu YAZMA" in SYSTEM


def test_rewrite_keeps_the_new_text_when_the_check_cannot_run(tmp_path, monkeypatch):
    import write
    from ai_client import AIClient
    from config import AISettings
    from fact_checker import FactChecker
    from fakes import FakeBackend, HttpError
    from test_writer import DIRTY
    from writer import Writer
    write_mod, snap_path, ws = write_ready(tmp_path, monkeypatch)
    p, _, _ = pipeline(draft(), DIRTY, draft(), DIRTY)
    write.dry_run("", snap_path, pipeline=p, store=InMemoryStore(), ws=ws)
    backend = FakeBackend(generate=[draft(title="Yeni Başlık: 84 Bildirim")] + [HttpError(503)] * 6)
    ai = AIClient(AISettings(), backend, sleep=lambda s: None)
    assert write.rewrite("ariza-dq200", Writer(ai, ws), FactChecker(ai), InMemoryStore(), ws) == 1
    saved = json.loads((tmp_path / "drafts" / "ariza-dq200.json").read_text(encoding="utf-8"))
    assert saved["pending_check"] is True and saved["draft"]["title"] == "Yeni Başlık: 84 Bildirim"


def test_daily_skips_quietly_when_todays_budget_is_spent(tmp_path, monkeypatch):
    import write
    from config import AISettings, QuotaSettings
    from quota import QuotaLedger
    write_mod, snap_path, ws = write_ready(tmp_path, monkeypatch)
    store = InMemoryStore()
    ledger = QuotaLedger(store)
    s = AISettings()
    for m in {s.deep_model, s.check_model, *[x for _, c in s.fallback_models for x in c]}:
        for _ in range(QuotaSettings().daily_limits[m]):
            ledger.record(m, "writer")
    assert write.writer_budget_left(store) is False
    client = FakeClient()
    assert write.daily(client, store, None, ws, today=date(2026, 10, 19), snapshot_path=snap_path) == 0
    assert client.inserted == []
    assert write.writer_budget_left(InMemoryStore()) is True


def test_failed_fact_check_goes_only_on_request_with_findings_in_the_note(tmp_path, monkeypatch):
    import write
    from test_writer import DIRTY
    write_mod, snap_path, ws = write_ready(tmp_path, monkeypatch)
    p, _, _ = pipeline(draft(), DIRTY, draft(), DIRTY)
    write.dry_run("", snap_path, pipeline=p, store=InMemoryStore(), ws=ws)
    client = FakeClient()
    assert write.send("ariza-dq200", client, InMemoryStore(), [], ws) == 1 and client.inserted == []
    assert write.send("ariza-dq200", client, InMemoryStore(), [], ws, with_findings=True) == 0
    html = client.inserted[0][1]
    assert "DÜZELT (fact-check)" in html and "%13 arıza" in html.split("</div>")[0]


def test_code_check_failure_never_goes_even_with_findings(tmp_path, monkeypatch):
    import write
    write_mod, snap_path, ws = write_ready(tmp_path, monkeypatch)
    bad = draft(html_body="<p>çığır açıyor</p>")
    p, _, _ = pipeline(bad, bad)
    write.dry_run("", snap_path, pipeline=p, store=InMemoryStore(), ws=ws)
    client = FakeClient()
    assert write.send("ariza-dq200", client, InMemoryStore(), [], ws, with_findings=True) == 1 and client.inserted == []
