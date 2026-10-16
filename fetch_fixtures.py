"""Step 1, first check (docs/PLAN.md): can GitHub's runners read CarScore politely, and what does the
raw HTML look like? Saves the pages to fixtures/raw/ for the parser tests. Run by the
"CarScore ornek sayfalar" workflow; nothing here writes to Blogger or calls AI.

    python fetch_fixtures.py [out_dir]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from config import load_settings
from fetcher import Fetcher

BASE = "https://shiftandtrend.com"
# Pages seen on https://shiftandtrend.com/araclar/ on 2026-10-16 (hub, two fault pages, two vehicles).
PAGES = [
    f"{BASE}/araclar/",
    f"{BASE}/sanziman/dq200/",
    f"{BASE}/sanziman/dq381/",
    f"{BASE}/motor/1-5-etsi-150-hp/",
    f"{BASE}/arac/volkswagen-golf-mk8-facelift-1-5-etsi-116-hp-life-dsg-7-ileri/",
    f"{BASE}/arac/cupra-formentor-km7-1-5-tsi-150-hp-formentor-dsg/",
]
SITEMAP_RE = re.compile(r"^\s*sitemap:\s*(\S+)", re.I | re.M)


def file_name(url: str) -> str:
    path = urlsplit(url).path.strip("/") or "index"
    return re.sub(r"[^a-z0-9]+", "_", path.lower()).strip("_") + (".xml" if path.endswith(".xml") else ".html")


def main(argv: list[str]) -> int:
    out = Path(argv[1] if len(argv) > 1 else "fixtures/raw")
    out.mkdir(parents=True, exist_ok=True)
    fetcher = Fetcher(load_settings())
    report = []

    robots = fetcher.fetch(f"{BASE}/robots.txt")
    report.append({"url": robots.url, "outcome": robots.outcome.value, "status": robots.status_code})
    urls = list(PAGES)
    if robots.ok:
        (out / "robots.txt").write_bytes(robots.content)
        urls += SITEMAP_RE.findall(robots.content.decode("utf-8", "replace"))[:3]

    for url in urls:
        r = fetcher.fetch(url)
        row = {"url": url, "outcome": r.outcome.value, "status": r.status_code, "bytes": len(r.content),
               "detail": r.detail}
        if r.ok:
            name = file_name(url)
            (out / name).write_bytes(r.content)
            row["file"] = name
        report.append(row)
        print(f"{row['outcome']:>20}  {row['status']}  {url}")

    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = sum(1 for r in report[1:] if r["outcome"] == "ok")
    print(f"{ok}/{len(report) - 1} sayfa okundu")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
