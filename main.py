"""Kronikler Auto Editor - entry point.

    python main.py --check-ai     # verify the Gemini key and configured models (1 API call)
    python main.py --quota        # today's AI request ledger

    python main.py --plan         # read CarScore, save the snapshot, print candidate topics (no AI)
    python main.py --plan --snapshot data/carscore/2026-10-16.json   # same, from a saved snapshot
    python main.py --plan --show <key-prefix>                        # also print that topic's data pack
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Optional

from config import load_ai_settings, load_settings
from memory import MemoryStore, create_store


def print_quota(store: MemoryStore, out=None) -> None:
    from config import QuotaSettings
    from quota import QuotaLedger
    out = out or sys.stdout
    ledger = QuotaLedger(store)
    usage = ledger.usage()
    limits = QuotaSettings().daily_limits
    print(f"Günlük AI kotası (Pasifik günü {ledger.today()}):", file=out)
    if not usage:
        print("  bugün henüz istek yok", file=out)
    for model, e in sorted(usage.items()):
        cap = limits.get(model)
        steps = ", ".join(f"{k} {v}" for k, v in sorted(e["steps"].items()))
        flag = "  [SAĞLAYICI KOTASI DOLDU]" if e["exhausted"] else ""
        print(f"  {model}: {e['total']}" + (f" / {cap}" if cap else "") + f"  ({steps}){flag}", file=out)


def check_ai(out=None) -> int:
    """Verify the API key and that every configured model is usable. One API call."""
    from ai_client import AIError, create_ai_client
    out = out or sys.stdout
    settings = load_ai_settings()
    print(f"Modeller: yazar={settings.deep_model}, kontrol={settings.check_model}, "
          f"hızlı={settings.fast_model}", file=out)
    try:
        problems = create_ai_client(settings, os.environ.get("GEMINI_API_KEY")).verify_models()
    except AIError as exc:
        print(f"[HATA] AI doğrulaması yapılamadı - {exc}", file=out)
        return 1
    for p in problems:
        print(f"[HATA] {p}", file=out)
    if not problems:
        print("[OK] API anahtarı geçerli, yapılandırılmış modellerin hepsi kullanılabilir.", file=out)
    return 1 if problems else 0


def plan(snapshot_path: Optional[str] = None, show: Optional[str] = None, out=None) -> int:
    """Step 1 dry run: nothing is written to memory or Blogger, no AI call."""
    import json
    from carscore_source import (CarScoreSource, drift, latest_snapshot, print_summary, save_snapshot,
                                 snapshot_from_dict)
    from config import CarScoreSettings
    from fetcher import Fetcher
    from planner import candidates, print_plan
    out = out or sys.stdout
    s = CarScoreSettings()
    if snapshot_path:
        with open(snapshot_path, encoding="utf-8") as fh:
            snap = snapshot_from_dict(json.load(fh))
    else:
        snap = CarScoreSource(Fetcher(load_settings()), s).collect()
        day = snap.taken_at[:10]
        for subject, problems in drift(latest_snapshot(s.snapshot_dir, day), snap, s).items():
            snap.problems.setdefault(subject, []).extend(problems)
        print(f"Anlık görüntü: {save_snapshot(snap, s.snapshot_dir, day)}", file=out)
    print_summary(snap, out)
    cands = candidates(snap, create_store(load_settings()), s)
    print_plan(cands, out)
    if show:
        for c in cands:
            if c.pack and c.topic.key.startswith(show):
                print(f"\n== {c.topic.key} ==\n{c.pack.render()}", file=out)
    return 0 if snap.hub is not None else 1


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Kronikler Auto Editor")
    ap.add_argument("--check-ai", action="store_true",
                    help="only verify the Gemini key and configured models, then exit")
    ap.add_argument("--quota", action="store_true", help="show today's AI request ledger and exit")
    ap.add_argument("--plan", action="store_true", help="read CarScore and print candidate topics (no AI)")
    ap.add_argument("--snapshot", help="--plan from a saved snapshot instead of the live site")
    ap.add_argument("--show", metavar="KEY", help="--plan: print the data pack of topics starting with KEY")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    settings = load_settings()
    logging.basicConfig(level=logging.DEBUG if args.verbose else getattr(logging, settings.log_level, logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.check_ai:
        return check_ai()
    if args.quota:
        print_quota(create_store(settings))
        return 0
    if args.plan:
        return plan(args.snapshot, args.show)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
