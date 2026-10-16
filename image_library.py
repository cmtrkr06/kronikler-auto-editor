"""[G] IMAGE LIBRARY - the editor's own photos, from the "Görsel arşivi" draft on the blog.

Google Photos albums cannot be read by an app since 2025-03-31 (Library API: only app-uploaded items),
so the editor puts the photos he allows into one DRAFT post on Otomobil Kronikleri, titled
"Görsel arşivi" (user decision 2026-10-16). That draft is never published; this module reads its
<img> tags through the Blogger API we already have, and keeps a small library in data/images.json:
url (largest size), alt text, file name and, once, a label from a vision model ("which car is this?").
Matching a topic to a photo is plain text matching on those fields; no label, no guess.

    python image_library.py            # refresh the library and label new photos (fast model)
    python image_library.py --list     # print the library
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from config import ImageSettings
from normalizer import normalize_title

log = logging.getLogger("kronikler.images")
# Blogger image URLs carry a size segment: /s320/, /s1600/, /w640-h360/, /w640-h360-rw/, /s16000-rw/
SIZE_RE = re.compile(r"/(s\d+|w\d+(?:-h\d+)?)(-[a-z0-9-]+)?/(?=[^/]+$)")


@dataclass
class LibraryImage:
    url: str                 # largest size we use (SIZE_RE segment -> settings.size)
    alt: str = ""
    file: str = ""
    label: dict = field(default_factory=dict)   # vision: {arac, marka, model, guven, gorulen}

    def text(self) -> str:
        """Everything that can be matched against a topic, normalized (ŠKODA == skoda)."""
        parts = [self.alt, self.file.rsplit(".", 1)[0].replace("_", " ").replace("-", " ")]
        if self.label.get("arac") and self.label.get("guven", 0) >= ImageSettings().min_label_confidence:
            parts += [self.label.get("marka", ""), self.label.get("model", "")]
        return normalize_title(" ".join(parts))


def large(url: str, size: str = "s1600") -> str:
    return SIZE_RE.sub(f"/{size}/", url, count=1)


def is_blogger_image(url: str) -> bool:
    host = urlsplit(url).netloc
    return host == "blogger.googleusercontent.com" or host.endswith(".bp.blogspot.com")


def parse_archive(html: str, settings: ImageSettings = ImageSettings()) -> list[LibraryImage]:
    """Every Blogger-hosted <img> of the archive draft, once, at the large size."""
    out: dict[str, LibraryImage] = {}
    for img in BeautifulSoup(html or "", "html.parser").find_all("img"):
        src = img.get("src") or ""
        parent = img.find_parent("a")
        href = parent.get("href", "") if parent else ""
        url = href if is_blogger_image(href) else src            # Blogger links the original size
        if not is_blogger_image(url):
            continue
        url = large(url, settings.size)
        if url in out:
            continue
        name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
        out[url] = LibraryImage(url, (img.get("alt") or img.get("title") or "").strip(), name)
    return list(out.values())


def load(path: str) -> list[LibraryImage]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return [LibraryImage(**x) for x in data.get("images", [])]
    except (OSError, ValueError, TypeError):
        return []


def save(path: str, images: list[LibraryImage]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"images": [asdict(i) for i in images]}, ensure_ascii=False, indent=1), encoding="utf-8")


def merge(old: list[LibraryImage], new: list[LibraryImage]) -> list[LibraryImage]:
    """The draft decides which photos exist; labels already paid for are kept."""
    labels = {i.url: i.label for i in old}
    for i in new:
        i.label = labels.get(i.url, {})
    return new


# ------------------------------------------------------------------ vision label (once per photo)
class CarLabel(BaseModel):
    arac: bool = Field(description="Fotoğrafın ana konusu bir otomobil mi")
    marka: str = Field(description="Görünen aracın markası (ör. Volkswagen, Škoda, CUPRA, SEAT, Audi); emin değilsen boş")
    model: str = Field(description="Model adı (ör. Golf, Octavia, Leon, Formentor); emin değilsen boş")
    guven: int = Field(ge=0, le=100, description="Marka ve modelden ne kadar eminsin (0-100)")
    gorulen: str = Field(description="Fotoğrafta ne var, kısa Türkçe")


LABEL_SYSTEM = ("Bir otomobil blogunun görsel editörüsün. Fotoğraftaki aracın marka ve modelini, ancak logo, ızgara, "
                "far, gövde çizgisi gibi görünür ipuçlarından eminsen yaz. Tahmin etme: emin değilsen marka/model boş, "
                "güven düşük olsun. Araç yoksa arac=false.")


def label_new(images: list[LibraryImage], ai, fetch: Callable, settings: ImageSettings = ImageSettings()) -> int:
    """Label at most `max_labels_per_run` unlabeled photos. A fetch or AI failure leaves the photo
    unlabeled (tried again next run); its alt text and file name still match."""
    from ai_client import AIError
    done = 0
    for img in images:
        if img.label or done >= settings.max_labels_per_run:
            continue
        r = fetch(large(img.url, settings.vision_size))
        if not r.ok:
            log.warning("görsel indirilemedi (%s): %s", r.outcome.value, img.url)
            continue
        mime = "image/png" if r.content[:4] == b"\x89PNG" else "image/webp" if r.content[8:12] == b"WEBP" else "image/jpeg"
        try:
            lab = ai.structured(settings.vision_tier, f"Dosya adı: {img.file}\nAlt metin: {img.alt or '-'}",
                                CarLabel, system=LABEL_SYSTEM, step="image_label", images=[(r.content, mime)])
        except AIError as exc:
            log.warning("görsel etiketlenemedi: %s", exc)
            break                                   # quota/busy: the rest waits for the next run
        img.label = lab.model_dump()
        done += 1
    return done


# ------------------------------------------------------------------ matching
def score(img: LibraryImage, terms: Iterable[str]) -> int:
    text = f" {img.text()} "
    return sum(1 for t in terms if t and f" {normalize_title(t)} " in text)


def best_match(images: list[LibraryImage], must: Iterable[str], nice: Iterable[str] = (),
               exclude: Iterable[str] = ()) -> Optional[LibraryImage]:
    """A photo that names at least one `must` term (model names: Golf, Passat...), ranked by how many
    `must` + `nice` (brand, generation) terms it carries. No match -> None, never a random photo."""
    must, nice, skip = [m for m in must if m], [n for n in nice if n], set(exclude)
    ranked = []
    for i, img in enumerate(images):
        if img.url in skip:
            continue
        hit = score(img, must)
        if hit:
            ranked.append((hit * 10 + score(img, nice), -i, img))
    ranked.sort(reverse=True)
    return ranked[0][2] if ranked else None


# ------------------------------------------------------------------ CLI
def refresh(client, store_path: str, ai=None, fetch=None, settings: ImageSettings = ImageSettings()) -> list[LibraryImage]:
    post = client.find_draft(settings.archive_title)
    if post is None:
        raise SystemExit(f"Blogger'da '{settings.archive_title}' başlıklı taslak bulunamadı")
    images = merge(load(store_path), parse_archive(post.get("content", ""), settings))
    if ai is not None and fetch is not None:
        label_new(images, ai, fetch, settings)
    save(store_path, images)
    return images


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true", help="yalnız kayıtlı kütüphaneyi yaz")
    ap.add_argument("--no-label", action="store_true", help="görsel model kullanma")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    s = ImageSettings()
    if not args.list:
        from blogger import BloggerError, GoogleBloggerClient
        from config import load_ai_settings, load_settings
        from memory import create_store
        try:
            client = GoogleBloggerClient(os.environ.get("BLOGGER_ID", ""))
        except BloggerError as exc:
            print(f"[HATA] Blogger: {exc}")
            return 1
        ai = fetch = None
        store = create_store(load_settings())
        if not args.no_label and os.environ.get("GEMINI_API_KEY"):
            from ai_client import create_ai_client
            from fetcher import Fetcher
            from quota import QuotaLedger
            ai = create_ai_client(load_ai_settings(), os.environ["GEMINI_API_KEY"], ledger=QuotaLedger(store))
            fetch = Fetcher(load_settings()).fetch
        refresh(client, s.store_path, ai, fetch, s)
        store.flush()
    images = load(s.store_path)
    print(f"Görsel arşivi: {len(images)} resim, {sum(1 for i in images if i.label)} etiketli")
    for i in images:
        lab = i.label
        tag = (f"{lab.get('marka', '')} {lab.get('model', '')} (%{lab.get('guven', 0)})".strip()
               if lab.get("arac") else ("araç yok" if lab else "etiketsiz"))
        print(f"  - {i.file[:50]:50} alt='{i.alt[:30]}' · {tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
