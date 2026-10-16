"""Write one Kronikler article as a local preview (step 2: nothing goes to Blogger).

    python write.py --dry-run                         # best candidate of today's CarScore data
    python write.py --dry-run --topic ariza-dq200     # one topic (key prefix)
    python write.py --dry-run --snapshot data/carscore/2026-10-16.json

Writes data/drafts/<key>.html (preview with SEO/fact-check status and every claim next to the data it
rests on) and <key>.json (the draft, for step 3's Blogger send). One article per run: the free tier's
daily writer quota is small and the plan is 3 articles a week.
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
               outcome: Optional[ChronicleOutcome] = None) -> Path:
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
        "writer_model": r.model,
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


def dry_run(topic_prefix: str = "", snapshot_path: Optional[str] = None, pipeline=None, store=None,
            ws: WriterSettings = WriterSettings()) -> int:
    """0 = a preview was written (ready or not), 1 = nothing to write or the writer was unreachable."""
    from ai_client import AIError, create_ai_client
    from planner import candidates, pick
    from quota import QuotaLedger
    store = store if store is not None else create_store(load_settings())
    snap = load_snapshot(snapshot_path)
    chosen = pick(candidates(snap, store), topic_prefix)
    if chosen is None:
        print(f"[YOK] yazılabilir konu yok{' (' + topic_prefix + ')' if topic_prefix else ''}: "
              "python main.py --plan ile nedenlerine bak")
        return 1
    topic, pack = chosen.topic, chosen.pack
    print(f"[KONU] {topic.title} ({chosen.reason})")
    try:
        if pipeline is None:
            ai = create_ai_client(load_ai_settings(), os.environ.get("GEMINI_API_KEY"), ledger=QuotaLedger(store))
            pipeline = ChroniclePipeline(Writer(ai, ws), FactChecker(ai, FactCheckSettings()), FactCheckSettings())
        outcome = pipeline.run(topic, pack, pending_draft(topic, pack, ws))
    except AIError as exc:
        print(f"[HATA] yazar çalışmadı: {exc}")
        return 1
    finally:
        store.flush()
    path = save_draft(topic, pack, outcome.result, ws.draft_dir, outcome)
    state = "HAZIR" if outcome.ready else ("FACT-CHECK BEKLİYOR" if outcome.pending_check else "HAZIR DEĞİL")
    print(f"[{state}] {outcome.result.draft.title} · {outcome.result.seo.word_count} kelime · {path}")
    if not outcome.ready:
        print(f"   neden: {outcome.reason}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="yerel önizleme yaz (Blogger'a gönderme)")
    ap.add_argument("--topic", default="", help="konu anahtarı başı (örn. ariza-dq200)")
    ap.add_argument("--snapshot", help="canlı site yerine kayıtlı anlık görüntü")
    args = ap.parse_args(argv)
    logging.basicConfig(level=getattr(logging, load_settings().log_level, logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if not args.dry_run:
        print("Adım 2'de yalnız --dry-run var; Blogger'a gönderim Adım 3'te.")
        return 2
    return dry_run(args.topic, args.snapshot)


if __name__ == "__main__":
    sys.exit(main())
