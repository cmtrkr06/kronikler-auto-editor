"""Write Kronikler articles: local preview, Blogger DRAFT, and the scheduled run.

    python write.py --dry-run                         # best candidate of today's CarScore data, preview only
    python write.py --dry-run --topic ariza-dq200     # one topic (key prefix)
    python write.py --dry-run --snapshot data/carscore/2026-10-16.json
    python write.py --send ariza-dq200                # a saved READY draft -> Blogger DRAFT (with photo + graphics)
    python write.py --daily                           # scheduled run: weekly cap, write, check, send, schema

data/drafts/<key>.html is the preview (SEO/fact-check status, every claim next to its data); <key>.json is
the draft with the structured data the graphics are drawn from. One article per run: the free tier's daily
quota is shared with Zapyus and the plan is 3 articles a week. Nothing is ever published automatically.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from html import escape
from pathlib import Path
from typing import Optional

from chronicle_pipeline import ChronicleOutcome, ChroniclePipeline
from config import CarScoreSettings, FactCheckSettings, WriterSettings, load_ai_settings, load_settings
from data_pack import ResearchPack
from fact_checker import FactChecker, FactCheckResult
from memory import create_store
from models import ChronicleTopic
from writer import Draft, WriteResult, Writer, evaluate

log = logging.getLogger("kronikler.write")

PREVIEW_CSS = """body{font:16px/1.6 system-ui,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;color:#222}
.meta{background:#f5f5f2;border:1px solid #ddd;padding:1rem;border-radius:8px;font-size:14px}
.meta b{display:inline-block;min-width:150px}.err{color:#b00020}.warn{color:#8a6100}.ok{color:#0a6b2d}
article{border-top:3px solid #222;margin-top:1.5rem;padding-top:.5rem}
article table{border-collapse:collapse}article td,article th{border:1px solid #ccc;padding:4px 8px}
blockquote{border-left:4px solid #222;margin:1rem 0;padding:.25rem 1rem;background:#fafafa}
table.claims{border-collapse:collapse;font-size:13px;width:100%}table.claims td{border:1px solid #ddd;padding:4px;vertical-align:top}"""


def render_fact_check(fc: Optional[FactCheckResult]) -> str:
    if fc is None:
        return "<p><b>Fact-check</b> yapılmadı</p>"
    items = "".join(f"<li class=err>KRİTİK: {escape(e)}</li>" for e in fc.critical_errors)
    items += "".join(f"<li class=warn>Desteklenmiyor: {escape(e)}</li>" for e in fc.unsupported_claims)
    items += "".join(f"<li class=warn>Uyarı: {escape(e)}</li>" for e in fc.warnings)
    state = "<span class=ok>geçti</span>" if fc.passed else "<span class=err>GEÇMEDİ</span>"
    return f"<p><b>Fact-check</b> {state} ({escape(fc.model)}): {escape(fc.summary)}</p><ul>{items}</ul>"


def render_preview(topic: ChronicleTopic, pack: ResearchPack, r: WriteResult,
                   outcome: Optional[ChronicleOutcome] = None) -> str:
    d = r.draft
    passage = {p.id: p for p in pack.passages}
    rows = []
    for c in d.claims:
        ids = [k.strip().strip("[]").upper() for k in c.kaynaklar]
        src = "<br>".join(f"<b>{escape(i)}</b>: {escape(passage[i].text[:200])}…" if i in passage
                          else f"<b class=err>{escape(i)}: pakette yok</b>" for i in ids)
        rows.append(f"<tr><td>{escape(c.iddia)}</td><td>{src}</td></tr>")
    issues = "".join(f"<li class=err>HATA: {escape(e)}</li>" for e in r.seo.errors)
    issues += "".join(f"<li class=warn>Uyarı: {escape(w)}</li>" for w in r.seo.warnings)
    if outcome is None:
        state = "yalnız yazıldı"
    elif outcome.ready:
        state = "<span class=ok>HAZIR (SEO + fact-check geçti)</span>"
    else:
        state = f"<span class=err>HAZIR DEĞİL</span>: {escape(outcome.reason)}"
    fc = outcome.fact_check if outcome else None
    return f"""<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Önizleme: {escape(d.title)}</title><style>{PREVIEW_CSS}</style></head><body>
<div class="meta">
<b>Durum</b> {state} · {r.seo.word_count} kelime · düzeltme turu {r.revisions}<br>
<b>Konu</b> {escape(topic.title)} ({escape(topic.type.value)}, örneklem {topic.sample})<br>
<b>SEO başlığı</b> {escape(d.seo_title)} ({len(d.seo_title)})<br>
<b>Arama açıklaması</b> {escape(d.meta_description)} ({len(d.meta_description)})<br>
<b>Slug</b> {escape(r.slug)}<br>
<b>Etiketler</b> {escape(", ".join(r.labels))}<br>
<b>İç link önerisi</b> {escape("; ".join(d.internal_link_ideas) or "-")}<br>
<b>Görsel sorgusu</b> {escape(d.image_query)}<br>
<b>Yazan model</b> {escape(r.model or "-")} · <b>Veri</b> {len(pack.passages)} parça, {pack.total_chars} karakter, {escape(pack.created_at)}
<ul>{issues or "<li>SEO sorunu yok</li>"}</ul>
{render_fact_check(fc)}</div>
<article><h1>{escape(d.title)}</h1>
{r.html}</article>
<h2>İddialar ve dayandıkları veri parçaları</h2>
<table class="claims">{"".join(rows)}</table></body></html>"""


def save_draft(topic: ChronicleTopic, pack: ResearchPack, r: WriteResult, directory: str,
               outcome: Optional[ChronicleOutcome] = None, data: Optional[dict] = None) -> Path:
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    previous = out / f"{topic.key}.json"
    blogger_info = None   # a rewrite must never forget the Blogger draft it belongs to (no duplicates)
    if previous.exists():
        try:
            blogger_info = json.loads(previous.read_text(encoding="utf-8")).get("blogger")
        except json.JSONDecodeError:
            blogger_info = None
    previous.write_text(json.dumps({
        "blogger": blogger_info, "topic": topic.to_dict(), "pack": pack.to_dict(),
        "topic_key": topic.key, "draft": r.draft.model_dump(), "html": r.html, "slug": r.slug,
        "labels": r.labels, "source_urls": r.source_urls, "revisions": r.revisions, "history": r.history,
        "writer_model": r.model, "title_options": r.title_options, "data": data or {},
        "seo": {"errors": r.seo.errors, "warnings": r.seo.warnings, "word_count": r.seo.word_count},
        "ready": outcome.ready if outcome else None,
        "pending_check": outcome.pending_check if outcome else False,
        "fact_check": outcome.fact_check.to_dict() if outcome and outcome.fact_check else None,
        "fact_check_rounds": outcome.rounds if outcome else [],
        "body": r.body,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    path = out / f"{topic.key}.html"
    path.write_text(render_preview(topic, pack, r, outcome), encoding="utf-8")
    return path


def pending_draft(topic: ChronicleTopic, pack: ResearchPack, ws: WriterSettings) -> Optional[WriteResult]:
    """A saved draft whose fact check never ran (checker was unreachable): check it, don't rewrite it."""
    path = Path(ws.draft_dir) / f"{topic.key}.json"
    if not path.exists():
        return None
    saved = json.loads(path.read_text(encoding="utf-8"))
    if not saved.get("pending_check"):
        return None
    result = evaluate(topic, pack, Draft.model_validate(saved["draft"]), ws)
    result.model = saved.get("writer_model", "")
    return result


def load_snapshot(path: Optional[str]):
    from carscore_source import CarScoreSource, drift, latest_snapshot, save_snapshot, snapshot_from_dict
    from fetcher import Fetcher
    s = CarScoreSettings()
    if path:
        with open(path, encoding="utf-8") as fh:
            return snapshot_from_dict(json.load(fh))
    snap = CarScoreSource(Fetcher(load_settings()), s).collect()
    day = snap.taken_at[:10]
    for subject, problems in drift(latest_snapshot(s.snapshot_dir, day), snap, s).items():
        snap.problems.setdefault(subject, []).extend(problems)
    save_snapshot(snap, s.snapshot_dir, day)
    return snap


def write_one(topic_prefix: str = "", snapshot_path: Optional[str] = None, pipeline=None, store=None,
              ws: WriterSettings = WriterSettings()):
    """Pick today's topic, write + check it, save the draft. Returns (candidate, outcome, path), or None
    when there is nothing to write. Raises AIError when the writer is unreachable."""
    from ai_client import create_ai_client
    from planner import candidates, pick
    from quota import QuotaLedger
    store = store if store is not None else create_store(load_settings())
    snap = load_snapshot(snapshot_path)
    chosen = pick(candidates(snap, store), topic_prefix)
    if chosen is None:
        print(f"[YOK] yazılabilir konu yok{' (' + topic_prefix + ')' if topic_prefix else ''}: "
              "python main.py --plan ile nedenlerine bak")
        return None
    topic, pack = chosen.topic, chosen.pack
    print(f"[KONU] {topic.title} ({chosen.reason})")
    try:
        if pipeline is None:
            ai = create_ai_client(load_ai_settings(), os.environ.get("GEMINI_API_KEY"), ledger=QuotaLedger(store))
            pipeline = ChroniclePipeline(Writer(ai, ws), FactChecker(ai, FactCheckSettings()), FactCheckSettings())
        outcome = pipeline.run(topic, pack, pending_draft(topic, pack, ws))
    finally:
        store.flush()
    path = save_draft(topic, pack, outcome.result, ws.draft_dir, outcome, chosen.data)
    state = "HAZIR" if outcome.ready else ("FACT-CHECK BEKLİYOR" if outcome.pending_check else "HAZIR DEĞİL")
    print(f"[{state}] {outcome.result.draft.title} · {outcome.result.seo.word_count} kelime · {path}")
    if not outcome.ready:
        print(f"   neden: {outcome.reason}")
    return chosen, outcome, path


def dry_run(topic_prefix: str = "", snapshot_path: Optional[str] = None, pipeline=None, store=None,
            ws: WriterSettings = WriterSettings()) -> int:
    """0 = a preview was written (ready or not), 1 = nothing to write or the writer was unreachable."""
    from ai_client import AIError
    try:
        done = write_one(topic_prefix, snapshot_path, pipeline, store, ws)
    except AIError as exc:
        print(f"[HATA] yazar çalışmadı: {exc}")
        return 1
    return 0 if done else 1


def rewrite(key_prefix: str, writer=None, checker=None, store=None, ws: WriterSettings = WriterSettings()) -> int:
    """A saved draft that failed the fact-check: ONE rewrite with the saved findings fed back, then one
    check on another model (2 requests; the same as the pipeline's own revision round)."""
    from ai_client import AIError, create_ai_client
    from quota import QuotaLedger
    paths = sorted(Path(ws.draft_dir).glob(f"{key_prefix}*.json"))
    if len(paths) != 1:
        print(f"[HATA] '{key_prefix}' için {len(paths)} taslak var (tek olmalı)")
        return 1
    saved = json.loads(paths[0].read_text(encoding="utf-8"))
    fc = saved.get("fact_check") or {}
    feedback = ([f"DOĞRULUK HATASI, düzelt: {e}" for e in fc.get("critical_errors", [])]
                + [f"KAYNAKTA YOK, çıkar ya da düzelt: {e}" for e in fc.get("unsupported_claims", [])]
                + list((saved.get("seo") or {}).get("errors", [])))
    if not feedback:
        print("[ATLA] yeniden yazdıracak bulgu yok; --recheck kullan")
        return 1
    topic, pack = ChronicleTopic.from_dict(saved["topic"]), ResearchPack.from_dict(saved["pack"])
    store = store if store is not None else create_store(load_settings())
    try:
        if writer is None or checker is None:
            ai = create_ai_client(load_ai_settings(), os.environ.get("GEMINI_API_KEY"), ledger=QuotaLedger(store))
            writer, checker = writer or Writer(ai, ws), checker or FactChecker(ai, FactCheckSettings())
        result = writer.write(topic, pack, feedback=feedback)
    except AIError as exc:
        store.flush()
        print(f"[HATA] yeniden yazım çalışmadı: {exc}")
        return 1
    check, check_error = None, ""
    if result.seo.passed:
        try:
            check = checker.check(result.draft.title, result.body, pack, result.model)
        except AIError as exc:            # keep the new text: the next run checks it, never rewrites it
            check_error = str(exc)
    store.flush()
    outcome = ChronicleOutcome(result, check, [check.to_dict()] if check else [], check_error=check_error)
    save_draft(topic, pack, result, ws.draft_dir, outcome, saved.get("data"))
    print(f"[{'HAZIR' if outcome.ready else 'HAZIR DEĞİL'}] {result.draft.title}" + ("" if outcome.ready else f": {outcome.reason}"))
    return 0 if outcome.ready else 1


def writer_budget_left(store) -> bool:
    """Is any model of the writer chain AND one of the check chain still under today's cap? (Pacific day,
    resets 10:00 TR). The shared key's caps keep Zapyus' day safe; an empty budget is a skip, not an error."""
    from config import AISettings, QuotaSettings
    from quota import QuotaLedger
    ai, ledger = AISettings(), QuotaLedger(store, QuotaSettings())
    chains = dict(ai.fallback_models)
    deep = [ai.deep_model, *chains.get("deep", ())]
    check = [ai.check_model, *chains.get("check", ())]
    free = lambda models, step: [m for m in models if ledger.check(m, step) is None]
    w = free(deep, "writer")
    return bool(w) and any(m != w[0] for m in free(check, "fact_check"))


def recheck(key_prefix: str, checker=None, store=None, ws: WriterSettings = WriterSettings()) -> int:
    """A saved draft that is not READY: code checks again (rules may have been fixed) and, if SEO now
    passes, ONE fact-check request on another model than the writer's. The writer is not called."""
    from ai_client import AIError, create_ai_client
    from quota import QuotaLedger
    paths = sorted(Path(ws.draft_dir).glob(f"{key_prefix}*.json"))
    if len(paths) != 1:
        print(f"[HATA] '{key_prefix}' için {len(paths)} taslak var (tek olmalı)")
        return 1
    saved = json.loads(paths[0].read_text(encoding="utf-8"))
    if saved.get("ready"):
        print("[ZATEN HAZIR] yeniden kontrol gerekmiyor")
        return 0
    topic, pack = ChronicleTopic.from_dict(saved["topic"]), ResearchPack.from_dict(saved["pack"])
    result = evaluate(topic, pack, Draft.model_validate(saved["draft"]), ws)
    result.model = saved.get("writer_model", "")
    if not result.seo.passed:
        print("[HAZIR DEĞİL] SEO: " + "; ".join(result.seo.errors))
        return 1
    store = store if store is not None else create_store(load_settings())
    try:
        if checker is None:
            ai = create_ai_client(load_ai_settings(), os.environ.get("GEMINI_API_KEY"), ledger=QuotaLedger(store))
            checker = FactChecker(ai, FactCheckSettings())
        fc = checker.check(result.draft.title, result.body, pack, result.model)
    except AIError as exc:
        print(f"[HATA] fact-check çalışmadı: {exc}")
        return 1
    finally:
        store.flush()
    outcome = ChronicleOutcome(result, fc, [fc.to_dict()])
    save_draft(topic, pack, result, ws.draft_dir, outcome, saved.get("data"))
    print(f"[{'HAZIR' if outcome.ready else 'HAZIR DEĞİL'}] {result.draft.title}" + ("" if outcome.ready else f": {outcome.reason}"))
    return 0 if outcome.ready else 1


# ------------------------------------------------------------------ Blogger
EDITOR_NOTE_MARK = "kronikler-editor-note"


def editor_note(saved: dict, notes: list[str]) -> str:
    """Red box at the top of the Blogger DRAFT: what the API cannot set (search description, permalink)
    and the choices left to the editor. It must be deleted before publishing - it says so."""
    d = saved["draft"]
    items = [f"<b>Arama açıklaması</b> (Blogger › Arama açıklaması): {escape(d['meta_description'])}",
             f"<b>Kalıcı bağlantı önerisi</b> (Blogger › Kalıcı bağlantı › Özel): {escape(saved['slug'])}"]
    if saved.get("title_options"):
        items.append("<b>Diğer başlık seçenekleri</b>: " + " · ".join(escape(t) for t in saved["title_options"]))
    items += [escape(n) for n in notes]
    return (f'<div class="{EDITOR_NOTE_MARK}" style="border:2px solid #c8102e;background:#fff5f5;color:#111;'
            f'padding:12px 14px;margin:0 0 20px;font-size:14px;line-height:1.5">'
            f'<strong style="color:#c8102e">EDİTÖR NOTU · Yayınlamadan önce bu kutuyu sil.</strong><br>'
            + "<br>".join(items) + "</div>")


def final_html(saved: dict, library, with_note: bool = True, extra_notes=()) -> tuple[str, list[str]]:
    from visuals import decorate
    kind = saved["topic"]["type"]
    html, report = decorate(kind, saved.get("data") or {}, saved["html"], library)
    notes = list(extra_notes) + list(report.notes)
    if report.photo is not None:
        notes.append(f"Görsel: {report.photo.reason}")
    return (editor_note(saved, notes) if with_note else "") + html, notes


def send(key_prefix: str, client, store, library=None, ws: WriterSettings = WriterSettings(),
         with_findings: bool = False) -> int:
    """A saved READY draft -> Blogger DRAFT (new) or update of our unedited Blogger draft.
    `with_findings` (editor's choice, 2026-10-16): a draft that passed every code check but not the
    fact-check may still go to Blogger as a DRAFT, its findings listed in the red editor note to fix
    before publishing. A draft that failed the code checks (numbers, structure) never goes."""
    from blogger import send_draft, update_draft
    from image_library import load as load_library
    from config import ImageSettings
    paths = sorted(Path(ws.draft_dir).glob(f"{key_prefix}*.json"))
    if len(paths) != 1:
        print(f"[HATA] '{key_prefix}' için {len(paths)} taslak var (tek olmalı)")
        return 1
    path = paths[0]
    saved = json.loads(path.read_text(encoding="utf-8"))
    topic = ChronicleTopic.from_dict(saved["topic"])
    library = library if library is not None else load_library(ImageSettings().store_path)
    findings = []
    if not saved.get("ready"):
        fc = saved.get("fact_check") or {}
        code_ok = not (saved.get("seo") or {}).get("errors")
        if not (with_findings and code_ok and fc):
            print(f"[GÖNDERİLMEDİ] {saved['draft']['title']}: taslak HAZIR değil"
                  + ("" if code_ok else " (kod kontrolleri geçmedi)"))
            return 1
        findings = ["DÜZELT (fact-check): " + e for e in fc.get("critical_errors", []) + fc.get("unsupported_claims", [])]
    html, notes = final_html(saved, library, extra_notes=findings)
    outgoing = dict(saved, html=html, ready=True)
    if (saved.get("blogger") or {}).get("post_id"):
        r = update_draft(topic, outgoing, client, store)
    else:
        r = send_draft(topic, outgoing, client, store)
    if not r.ok:
        print(f"[GÖNDERİLMEDİ] {saved['draft']['title']}: {r.message}")
        return 1
    saved["blogger"] = {"post_id": r.post_id, "sent_at": (r.post or {}).get("updated") or "",
                        "updated_at": (r.post or {}).get("updated") or ""}
    path.write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
    store.flush()
    print(f"[TASLAK] Blogger'a gitti: {saved['draft']['title']} (id {r.post_id})")
    for n in notes:
        print(f"   not: {n}")
    return 0


def our_post_ids(ws: WriterSettings = WriterSettings()) -> set[str]:
    ids = set()
    for p in Path(ws.draft_dir).glob("*.json"):
        try:
            pid = (json.loads(p.read_text(encoding="utf-8")).get("blogger") or {}).get("post_id")
        except (OSError, ValueError):
            continue
        if pid:
            ids.add(str(pid))
    return ids


def week_count(store, today) -> int:
    week = today.isocalendar()[:2]
    from datetime import date
    return sum(1 for d in (store.get_meta("sent_days") or []) if date.fromisoformat(d).isocalendar()[:2] == week)


def daily(client=None, store=None, pipeline=None, ws: WriterSettings = WriterSettings(), today=None,
          snapshot_path: Optional[str] = None, ai_for_labels=None, fetch=None) -> int:
    """Scheduled run (Mon/Wed/Fri evening). 0 = done (a draft went out, or nothing worth writing),
    1 = something needs attention (writer/check/Blogger error) - the Actions run turns red."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from ai_client import AIError
    from config import ImageSettings
    import image_library
    store = store if store is not None else create_store(load_settings())
    today = today or datetime.now(ZoneInfo("Europe/Istanbul")).date()
    sent_days = store.get_meta("sent_days") or []
    if today.isoformat() in sent_days:
        print("[ATLA] bugün zaten bir taslak gitti")
        return 0
    if week_count(store, today) >= ws.weekly_cap:
        print(f"[ATLA] bu hafta {ws.weekly_cap} taslak gitti (haftalık tavan)")
        return 0
    if pipeline is None and not writer_budget_left(store):
        print("[ATLA] bugünün Gemini kotası dolu (ortak anahtar, Zapyus'a yer bırakılıyor); sıfırlanma 10:00 TR")
        return 0
    try:
        done = write_one("", snapshot_path, pipeline, store, ws)
    except AIError as exc:
        print(f"[HATA] yazar çalışmadı: {exc}")
        return 1
    if not done:
        return 0                                   # "nothing worth writing" is a valid result
    chosen, outcome, _ = done
    if not outcome.ready:
        return 1
    library = []
    if client is not None:
        try:
            library = image_library.refresh(client, ImageSettings().store_path, ai_for_labels, fetch)
        except SystemExit as exc:                  # no archive draft: photos fall back to CarScore
            print(f"[UYARI] {exc}")
            library = image_library.load(ImageSettings().store_path)
    code = send(chosen.topic.key, client, store, library, ws)
    if code == 0:
        store.set_meta("sent_days", (sent_days + [today.isoformat()])[-30:])
        store.flush()
    return code


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="yerel önizleme yaz (Blogger'a gönderme)")
    ap.add_argument("--topic", default="", help="konu anahtarı başı (örn. ariza-dq200)")
    ap.add_argument("--snapshot", help="canlı site yerine kayıtlı anlık görüntü")
    ap.add_argument("--send", metavar="KONU", help="kayıtlı HAZIR taslağı Blogger'a TASLAK olarak gönder")
    ap.add_argument("--daily", action="store_true", help="zamanlanmış çalıştırma (haftalık tavan, yaz, gönder)")
    ap.add_argument("--recheck", metavar="KONU", help="kayıtlı taslağı yazarı çağırmadan yeniden kontrol et (1 fact-check)")
    ap.add_argument("--with-findings", action="store_true",
                    help="--send ile: fact-check bulgusu olan taslağı, bulgular editör notunda olarak gönder")
    ap.add_argument("--rewrite", metavar="KONU", help="fact-check'te kalan taslağı bulgularla bir kez yeniden yaz (2 istek)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=getattr(logging, load_settings().log_level, logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.dry_run:
        return dry_run(args.topic, args.snapshot)
    if args.recheck:
        return recheck(args.recheck)
    if args.rewrite:
        return rewrite(args.rewrite)
    if not (args.send or args.daily):
        ap.print_help()
        return 2
    from blogger import BloggerError, GoogleBloggerClient
    try:
        client = GoogleBloggerClient(os.environ.get("BLOGGER_ID", ""))
    except BloggerError as exc:
        print(f"[HATA] Blogger: {exc}")
        return 1
    store = create_store(load_settings())
    if args.send:
        return send(args.send, client, store, with_findings=args.with_findings)
    ai = fetch = None
    if os.environ.get("GEMINI_API_KEY"):
        from ai_client import create_ai_client
        from fetcher import Fetcher
        from quota import QuotaLedger
        ai = create_ai_client(load_ai_settings(), os.environ["GEMINI_API_KEY"], ledger=QuotaLedger(store))
        fetch = Fetcher(load_settings()).fetch
    code = daily(client, store, snapshot_path=args.snapshot, ai_for_labels=ai, fetch=fetch)
    from config import SchemaSettings
    import schema_markup
    s = SchemaSettings()
    if s.auto_apply:
        records = schema_markup.load_records(s.record_path)
        counts = schema_markup.apply(client, records, s, only_ids=our_post_ids())
        schema_markup.save_records(s.record_path, records)
        print(f"[ŞEMA] yazılan {counts['written']}, değişmeyen {counts['skipped'] + counts['same']}, hata {counts['failed']}")
    store.flush()
    return code


if __name__ == "__main__":
    sys.exit(main())
