"""Writer, pipeline and write.py --dry-run with a fake Gemini backend and the real DQ200 data pack."""
import json

import pytest

from ai_client import AIClient
from chronicle_pipeline import ChronicleOutcome, ChroniclePipeline
from config import AISettings, FactCheckSettings, WriterSettings
from fact_checker import FactChecker, FactCheckOutput, Finding
from fakes import FakeBackend, HttpError
from planner import candidates, fault_key, pick
from writer import Draft, Writer, build_prompt, evaluate, source_box
from test_planner import snap

CAND = next(c for c in candidates(snap()) if c.topic.key == fault_key("DQ200"))
TOPIC, PACK = CAND.topic, CAND.pack
FILLER = " ".join(["Sahiplerin anlattıkları bu şanzımanın kullanım biçimine göre farklı sonuçlar verdiğini gösteriyor."] * 45)
BODY = ("<p>DQ200 kullanan araçlardan gelen 84 bildirimin 11'inde arıza bildirildi.</p>"
        "<h2>Sahipler ne bildirdi</h2><table><thead><tr><th>Durum</th><th>Bildirim</th></tr></thead><tbody>"
        "<tr><td>Sorun yaşamadım</td><td>67</td></tr><tr><td>Kavrama değişti</td><td>3</td></tr></tbody></table>"
        f"<h2>Kilometre ve masraf</h2><p>Arızalar 8 bildirimin ortancasına göre 80 – 100 bin km'de. {FILLER}</p>"
        "<blockquote><p><strong>Veri notu:</strong> Bunlar gönüllü bildirimlerdir, arıza oranı değildir.</p></blockquote>")


def draft(**kw):
    d = {"title": "DQ200 Kroniği: 84 Bildirimin 11'inde Arıza", "seo_title": "DQ200 Arızası: Sahiplerin 84 Bildirimi",
         "meta_description": "DQ200 DSG şanzımanı için CarScore'da sahiplerin bildirdiği 84 kaydın ne söylediğine bakıyoruz.",
         "slug": "DQ200 Kroniği", "tags": ["DQ200", "DSG", "Volkswagen", "Fazla"],
         "html_body": BODY, "image_query": "Volkswagen DSG gearbox",
         "claims": [{"iddia": "84 bildirimin 11'inde arıza", "kaynaklar": ["[K1]"]},
                    {"iddia": "gönüllü bildirim", "kaynaklar": ["k2"]}]}
    d.update(kw)
    return json.dumps(d, ensure_ascii=False)


def finding(text="%13 arıza", durum="desteklenmiyor", onem="kritik"):
    return Finding(alinti=text, durum=durum, onem=onem, aciklama="pakette oran yok", parcalar=["K1"])


CLEAN = FactCheckOutput(findings=[], ozet="temiz").model_dump_json()
DIRTY = FactCheckOutput(findings=[finding()], ozet="oran var").model_dump_json()


def ai_with(*responses):
    backend = FakeBackend(generate=list(responses))
    return AIClient(AISettings(), backend, sleep=lambda s: None), backend


def pipeline(*responses, **fc):
    ai, backend = ai_with(*responses)
    s = FactCheckSettings(**fc)
    return ChroniclePipeline(Writer(ai, WriterSettings()), FactChecker(ai, s), s), backend, ai


# ---- writer
def test_prompt_has_pack_date_and_feedback():
    p = build_prompt(TOPIC, PACK, feedback=["veri paketinde olmayan sayı: 13"])
    assert "[K1] CarScore sahip bildirimleri: DQ200" in p and "VERİ TARİHİ: 20" in p
    assert "REDDEDİLDİ" in p and "olmayan sayı: 13" in p


def test_good_draft_gets_labels_and_code_built_source_box():
    ai, backend = ai_with(draft())
    r = Writer(ai, WriterSettings()).write(TOPIC, PACK)
    assert r.seo.passed, r.seo.errors
    assert backend.prompts[0][0] == AISettings().deep_model and "ARIZA KRONİĞİ" in backend.prompts[0][2]
    assert r.slug == "dq200-kronigi" and r.labels == ["Arıza Kronikleri", "DQ200", "DSG", "Volkswagen"]
    assert r.source_urls == ["https://shiftandtrend.com/sanziman/dq200/"]
    assert "<h2>Veri kaynağı</h2>" in r.html and "<h2>Veri kaynağı</h2>" not in r.body


def test_computed_rate_is_rewritten_once_then_given_up():
    bad = draft(html_body=BODY.replace("11'inde arıza bildirildi", "%13'ünde arıza bildirildi"))
    ai, backend = ai_with(bad, bad)
    r = Writer(ai, WriterSettings()).write(TOPIC, PACK)
    assert not r.seo.passed and r.revisions == 1 and any("olmayan sayı: 13" in e for e in r.seo.errors)
    assert "olmayan sayı: 13" in backend.prompts[1][1]          # the rewrite was told why


def test_source_box_dates_the_data():
    box, urls = source_box(PACK)
    assert PACK.created_at[:4] in box and urls and "CarScore" in box


# ---- pipeline
def test_ready_on_first_round_with_two_models():
    p, backend, _ = pipeline(draft(), CLEAN)
    o = p.run(TOPIC, PACK)
    assert o.ready and len(o.rounds) == 1
    assert [m for m, _, _ in backend.prompts] == [AISettings().deep_model, AISettings().check_model]
    assert "VERİ PAKETİ:" in backend.prompts[1][1] and "Veri kaynağı" not in backend.prompts[1][1].split("VERİ PAKETİ:")[0]


def test_fact_check_finding_triggers_one_rewrite():
    p, backend, ai = pipeline(draft(), DIRTY, draft(), CLEAN)
    o = p.run(TOPIC, PACK)
    assert o.ready and len(o.rounds) == 2 and "%13 arıza" in backend.prompts[2][1]


def test_fails_after_max_revisions():
    p, _, _ = pipeline(draft(), DIRTY, draft(), DIRTY, max_revisions=1)
    o = p.run(TOPIC, PACK)
    assert not o.ready and "%13" in o.reason


def test_checker_unreachable_keeps_the_draft_pending():
    p, _, _ = pipeline(draft(), HttpError(503), HttpError(503), HttpError(503), HttpError(503), HttpError(503))
    o = p.run(TOPIC, PACK)
    assert o.pending_check and not o.ready and o.result.seo.passed


# ---- write.py --dry-run
def test_dry_run_writes_preview_and_json(tmp_path, monkeypatch, capsys):
    import write
    from memory import InMemoryStore
    path = tmp_path / "snap.json"
    path.write_text(json.dumps(snap().to_dict(), ensure_ascii=False), encoding="utf-8")
    ws = WriterSettings(draft_dir=str(tmp_path / "drafts"))
    p, _, _ = pipeline(draft(), CLEAN)
    assert write.dry_run("", str(path), pipeline=p, store=InMemoryStore(), ws=ws) == 0
    out = capsys.readouterr().out
    assert "[HAZIR]" in out and "DQ200" in out
    saved = json.loads((tmp_path / "drafts" / f"{TOPIC.key}.json").read_text(encoding="utf-8"))
    assert saved["ready"] is True and saved["pack"]["passages"] and saved["topic"]["type"] == "ariza"
    html = (tmp_path / "drafts" / f"{TOPIC.key}.html").read_text(encoding="utf-8")
    assert "HAZIR (SEO + fact-check geçti)" in html and "[K1]" not in html and "K1" in html


def test_pending_draft_is_checked_not_rewritten(tmp_path):
    import write
    ws = WriterSettings(draft_dir=str(tmp_path))
    r = evaluate(TOPIC, PACK, Draft.model_validate_json(draft()), ws)
    r.model = AISettings().deep_model
    write.save_draft(TOPIC, PACK, r, str(tmp_path), ChronicleOutcome(r, None, [], check_error="503"))
    existing = write.pending_draft(TOPIC, PACK, ws)
    p, backend, ai = pipeline(CLEAN)
    assert p.run(TOPIC, PACK, existing).ready and ai.stats.calls == 1
    assert backend.prompts[0][0] == AISettings().check_model


def test_pick_prefers_fault_chronicles_and_respects_prefix():
    cands = candidates(snap())
    assert pick(cands).topic.key == fault_key("DQ200")
    assert pick(cands, "yok-boyle") is None


def test_pack_states_what_the_site_leaves_open_and_lists_no_scores():
    text = PACK.render()
    assert "84 bildirimin mi yoksa 11 arızalı bildirimin mi" in text
    assert "toplamı 84 bildirimdir" in text
    models = next(p for p in PACK.passages if "modellerden bazıları" in p.title).text
    assert "CarScore 86" not in models and "Volkswagen Passat B8 Facelift" in models


def test_prompts_carry_the_arithmetic_rule():
    from fact_checker import SYSTEM as CHECK
    from writer import COMMON
    assert "geri kalan" in COMMON and "geri kalan 73" in CHECK


def test_a_bad_main_title_is_replaced_by_a_passing_alternative():
    from writer import choose_title
    d = Draft.model_validate_json(draft(title="DQ200: Sahiplerin %13'ü Pişman",
                                        title_alternatives=["DQ200'de Arıza Kaçıncı Kilometrede?",
                                                            "DQ200 Sahipleri Anlattı: 84 Bildirimin 11'inde Arıza"]))
    chosen, others = choose_title(d, [p.text for p in PACK.passages])
    assert chosen.title == "DQ200'de Arıza Kaçıncı Kilometrede?" and others == ["DQ200 Sahipleri Anlattı: 84 Bildirimin 11'inde Arıza"]
    r = evaluate(TOPIC, PACK, d, WriterSettings())
    assert r.draft.title.startswith("DQ200'de") and r.title_options and r.seo.passed, r.seo.errors


def test_all_titles_bad_keeps_the_error():
    d = Draft.model_validate_json(draft(title="DQ200 alınmaz mı? %13", title_alternatives=["Uzak durun: DQ200"]))
    r = evaluate(TOPIC, PACK, d, WriterSettings())
    assert not r.seo.passed and any("olmayan sayı" in e for e in r.seo.errors)
