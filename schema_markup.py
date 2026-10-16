"""[Ş] SCHEMA - BlogPosting JSON-LD for every LIVE post, pure code, no AI.

    python schema_markup.py --apply             # BlogPosting on every LIVE post
    python schema_markup.py --apply --dry-run   # what would change, nothing is written
    python schema_markup.py --show <post_id>    # the JSON-LD that post would get

Copied from zapyus-auto-editor (e193136) without the review/star part. Built AFTER publishing, from
the Blogger API: `published` and `updated` are exact, author name/url come from the Blogger profile
unless SchemaSettings overrides them, images are the first https images of the post. The only change
to the post is our <script type="application/ld+json"> block (update_live_content pins `published`).
No stars: CarScore ratings are collected on shiftandtrend.com, marking them up here would be another
site's votes (Google review-snippet rules). Checked against Google's article docs 2026-10-15.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from html import escape
from pathlib import Path
from typing import Optional

from config import SchemaSettings

log = logging.getLogger("kronikler.schema")

ARTICLE_ID = "#kronikler-article"
LD_RE = re.compile(r'<script\b[^>]*type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script\s*>',
                   re.S | re.I)
IMG_RE = re.compile(r'<img\b[^>]*\bsrc\s*=\s*["\'](https://[^"\']+)["\']', re.I)
# Rating markup would claim stars for this page. CarScore's owner ratings are collected on
# shiftandtrend.com, not here, so a Kronikler post never carries them (plan section 6).
RATING_MARKUP_RE = re.compile(r'aggregateRating|reviewRating|ratingValue|itemprop\s*=\s*["\']?rating', re.I)


# ------------------------------------------------------------------ helpers
def script(data: dict) -> str:
    """One JSON-LD block. `</` is escaped so text can never close the script tag early."""
    body = json.dumps(data, ensure_ascii=False, indent=1).replace("</", "<\\/")
    return f'<script type="application/ld+json">\n{body}\n</script>'


def ld_blocks(html: str) -> list[tuple[str, Optional[dict]]]:
    """(raw block, parsed JSON or None) for every JSON-LD script in the HTML."""
    out = []
    for m in LD_RE.finditer(html or ""):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            data = None
        out.append((m.group(0), data))
    return out


def _ours(raw: str, marker: str) -> bool:
    return marker in raw


def strip_ours(html: str, markers: tuple = (ARTICLE_ID,)) -> str:
    """The HTML without our JSON-LD blocks (found by their @id marker, even if Blogger mangled the JSON)."""
    out = html or ""
    for raw, _ in ld_blocks(out):
        if any(_ours(raw, m) for m in markers):
            out = out.replace(raw, "")
    return out.rstrip()


def first_images(html: str, limit: int) -> list[str]:
    seen: list[str] = []
    for url in IMG_RE.findall(html or ""):
        if url not in seen:
            seen.append(url)
        if len(seen) >= limit:
            break
    return seen


# ------------------------------------------------------------------ BlogPosting (every live post)
def author_of(post: dict, s: SchemaSettings = SchemaSettings()) -> dict:
    a = post.get("author") or {}
    name = (s.author_name or a.get("displayName") or "").strip()
    url = (s.author_url or a.get("url") or "").strip()
    if not name:
        return {"@type": "Organization", "name": s.site_name, "url": s.site_url}
    out = {"@type": "Person", "name": name[:99]}
    if url.startswith("http"):
        out["url"] = url
    return out


def article_jsonld(post: dict, date_modified: str, s: SchemaSettings = SchemaSettings()) -> dict:
    url = post.get("url", "")
    data: dict = {"@context": "https://schema.org", "@type": "BlogPosting", "@id": url + ARTICLE_ID,
                  "mainEntityOfPage": {"@type": "WebPage", "@id": url},
                  "headline": (post.get("title") or "").strip(),
                  "author": [author_of(post, s)],
                  "publisher": {"@type": "Organization", "name": s.site_name, "url": s.site_url}}
    pics = first_images(post.get("content", ""), s.max_images)
    for extra in post.get("images") or []:
        u = (extra or {}).get("url", "")
        if u.startswith("https://") and u not in pics and len(pics) < s.max_images:
            pics.append(u)
    if pics:
        data["image"] = pics
    if post.get("published"):
        data["datePublished"] = post["published"]
    if date_modified or post.get("published"):
        data["dateModified"] = date_modified or post["published"]
    return data


def desired_content(post: dict, date_modified: str, s: SchemaSettings = SchemaSettings()) -> str:
    """The post's content with our BlogPosting block rebuilt from the API data (everything else unchanged)."""
    return strip_ours(post.get("content", "")) + "\n" + script(article_jsonld(post, date_modified, s))


# ------------------------------------------------------------------ records + apply
def load_records(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_records(path: str, records: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(records, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def plan(post: dict, rec: Optional[dict], s: SchemaSettings = SchemaSettings()) -> tuple[str, str]:
    """(action, date_modified). action: 'skip' (nothing changed since our last patch) or 'write'.
    Our own patch moves Blogger's `updated`; that value is recorded and not taken as a real edit."""
    updated = post.get("updated", "")
    if rec and rec.get("applied_updated") and updated == rec["applied_updated"]:
        return "skip", rec.get("date_modified", "")
    return "write", updated or post.get("published", "")


def apply(client, records: dict, s: SchemaSettings = SchemaSettings(), dry_run: bool = False) -> dict:
    """Bring every LIVE post's JSON-LD up to date. Returns counts {written, skipped, same, failed}."""
    counts = {"written": 0, "skipped": 0, "same": 0, "failed": 0}
    for post in client.live_posts():
        pid = str(post.get("id", ""))
        action, modified = plan(post, records.get(pid), s)
        if action == "skip":
            counts["skipped"] += 1
            continue
        new = desired_content(post, modified, s)
        if new == post.get("content", ""):
            records[pid] = {"applied_updated": post.get("updated", ""), "date_modified": modified}
            counts["same"] += 1
            continue
        if dry_run:
            print(f"[DENEME] {pid}: JSON-LD yazılacak ({post.get('title', '')})")
            counts["written"] += 1
            continue
        try:
            resp = client.update_live_content(pid, new) or {}
        except Exception as exc:  # BloggerError and transport errors: one post never stops the rest
            print(f"[HATA] {pid}: {exc}")
            counts["failed"] += 1
            continue
        records[pid] = {"applied_updated": resp.get("updated") or post.get("updated", ""),
                        "date_modified": modified}
        print(f"[ŞEMA] {pid}: BlogPosting yazıldı ({post.get('title', '')})")
        counts["written"] += 1
    return counts


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="JSON-LD'yi yayındaki yazılara yaz")
    ap.add_argument("--dry-run", action="store_true", help="--apply ile: yalnız göster, yazma")
    ap.add_argument("--if-enabled", action="store_true",
                    help="SchemaSettings.auto_apply kapalıysa hiçbir şey yapma (gunluk.sh için)")
    ap.add_argument("--show", metavar="POST_ID", help="bir yazının alacağı JSON-LD'yi bas")
    args = ap.parse_args(argv)
    s = SchemaSettings()
    if args.if_enabled and not s.auto_apply:
        print("[ŞEMA] otomatik uygulama kapalı (SchemaSettings.auto_apply=False)")
        return 0
    if not (args.apply or args.show):
        ap.print_help()
        return 2
    from blogger import BloggerError, GoogleBloggerClient
    try:
        client = GoogleBloggerClient(os.environ.get("BLOGGER_ID", ""))
    except BloggerError as exc:
        print(f"[HATA] Blogger yetkisi: {exc}")
        return 1
    records = load_records(s.record_path)
    if args.show:
        post = client.get_post(args.show)
        _, modified = plan(post, records.get(args.show), s)
        for raw, data in ld_blocks(desired_content(post, modified, s)):
            if _ours(raw, ARTICLE_ID):
                print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    try:
        counts = apply(client, records, s, dry_run=args.dry_run)
    except BloggerError as exc:
        print(f"[HATA] {exc}")
        return 1
    if not args.dry_run:
        save_records(s.record_path, records)
    print(f"[ŞEMA] yazılan {counts['written']}, değişmeyen {counts['skipped'] + counts['same']}, "
          f"hata {counts['failed']}")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
