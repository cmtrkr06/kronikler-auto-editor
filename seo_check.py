"""[S] SEO / HTML CHECK - pure code, no AI.

sanitize_html(): whitelist rebuild of the writer's HTML (Blogger renders the post title as
H1, so <h1> becomes <h2>; script/style/iframe and every attribute except a[href] are dropped).
check(): errors block the draft (banned clichés, links outside the source pack, text copied
from a source, claims citing unknown passages, no body); warnings go to the editor.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser
from typing import Iterable

from normalizer import normalize_title, turkish_lower

ALLOWED_TAGS = {"p", "h2", "h3", "h4", "ul", "ol", "li", "strong", "em", "b", "i", "a", "br",
                "blockquote", "table", "thead", "tbody", "tr", "th", "td", "code",
                "figure", "figcaption", "img"}
VOID_TAGS = {"br", "img"}
IMG_ATTRS = ("src", "alt", "width", "height", "loading")
DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "noscript", "form", "svg"}
# Zapyus' banned clichés (docs/BRIEF.md section 12, 2026-10-14) + Kronikler: verdicts the owner data can
# not carry ("kesin arızalı", "asla alınmaz") and rate language (the site: "arıza oranı değildir").
BANNED_PHRASES = ("devrim niteliğinde", "çağ atlıyor", "benzersiz deneyim", "ezber bozuyor",
                  "çığır açıyor", "teknoloji dünyasını salladı",
                  "sonuç olarak", "özetlemek gerekirse", "unutmayın ki", "kusursuz", "büyüleyici",
                  "günümüz dünyasında", "günümüzde teknolojinin", "hayatımızın vazgeçilmez",
                  "kesin arızalı", "kesinlikle arıza", "asla alınmaz", "sakın almayın", "uzak durun",
                  "kronik arızalı", "arıza oranı yüksek", "arıza oranı düşük", "arıza oranı %")
# Blog kendi testini yapmadı: "test ettik / sürdük" gibi birinci el deney iddiası uydurmadır.
FAKE_EXPERIENCE = re.compile(
    r"\b(test ettik|test ettiğimizde|test ettiğimiz|denedik|denediğimizde|denediğimiz|kendi aracımızda|"
    r"kendi aracımda|kendi testlerimizde|testlerimizde|ölçtüğümüzde|sürdük|sürdüğümüzde)\b")
TITLE_MAX = 70
DATA_NOTE = "veri notu"     # the blockquote that says what the numbers are (and are not)
ENDING_HEADINGS = ("sonuç", "değerlendirme", "özet")
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
COPY_WINDOW = 12            # identical consecutive words with a source passage = copied
SEO_TITLE_MAX = 60
META_MIN, META_MAX = 70, 150
MIN_WORDS = 300
DENSITY_MAX = 0.04          # one content word above 4% of all words looks like stuffing
STOPWORDS = {"ve", "ile", "bir", "bu", "için", "olarak", "daha", "veya", "gibi", "olan", "de", "da",
             "ise", "her", "ancak", "sonra", "kadar", "çok", "the", "and"}


# ------------------------------------------------------------- sanitizer
class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.removed: Counter = Counter()
        self._skip = 0
        self._open: list[str] = []

    def handle_starttag(self, tag, attrs):
        if self._skip or tag in DROP_WITH_CONTENT:
            if tag in DROP_WITH_CONTENT:
                self._skip += 1
                self.removed[tag] += 1
            return
        if tag == "h1":
            self.removed["h1->h2"] += 1
            tag = "h2"
        if tag not in ALLOWED_TAGS:
            self.removed[tag] += 1
            return
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if not href.startswith(("http://", "https://")):
                self.removed["a(bad href)"] += 1
                self._open.append("a-dropped")
                return
            self.out.append(f'<a href="{escape(href, quote=True)}">')
        elif tag == "img":
            a = dict(attrs)
            src = a.get("src") or ""
            if not src.startswith(("https://", "../assets/")):
                self.removed["img(bad src)"] += 1
                return
            kept = "".join(f' {k}="{escape(a[k] or "", quote=True)}"' for k in IMG_ATTRS if k in a)
            self.out.append(f"<img{kept}>")
        else:
            self.out.append(f"<{tag}>")
        if tag not in VOID_TAGS:
            self._open.append(tag)

    def handle_endtag(self, tag):
        if tag in DROP_WITH_CONTENT:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "h1":
            tag = "h2"
        if tag == "a" and self._open and self._open[-1] == "a-dropped":
            self._open.pop()
            return
        if tag in ALLOWED_TAGS and tag not in VOID_TAGS and tag in self._open:
            while self._open:
                t = self._open.pop()
                if t != "a-dropped":
                    self.out.append(f"</{t}>")
                if t == tag:
                    break

    def handle_data(self, data):
        if not self._skip:
            self.out.append(escape(data, quote=False))

    def close_all(self) -> str:
        while self._open:
            t = self._open.pop()
            if t != "a-dropped":
                self.out.append(f"</{t}>")
        return "".join(self.out)


def sanitize_html(html: str) -> tuple[str, dict[str, int]]:
    s = _Sanitizer()
    s.feed(html)
    s.close()
    return s.close_all().strip(), dict(s.removed)


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []
        self.headings: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.append(dict(attrs).get("href") or "")
        if tag in ("h1", "h2", "h3", "h4"):
            self.headings.append(tag)
        self.parts.append(" ")

    def handle_endtag(self, tag):
        self.parts.append(" ")

    def handle_data(self, data):
        self.parts.append(data)


def html_text(html: str) -> tuple[str, list[str], list[str]]:
    p = _Text()
    p.feed(html)
    return re.sub(r"\s+", " ", "".join(p.parts)).strip(), p.links, p.headings


# ------------------------------------------------------------- checks
def words(text: str) -> list[str]:
    return normalize_title(text).split()


def copied_spans(body_text: str, sources: Iterable[str], window: int = COPY_WINDOW) -> list[str]:
    """Body word windows that appear verbatim (after normalization) in a source."""
    shingles = set()
    for src in sources:
        w = words(src)
        shingles.update(" ".join(w[i:i + window]) for i in range(len(w) - window + 1))
    bw = words(body_text)
    found, i = [], 0
    while i <= len(bw) - window:
        span = " ".join(bw[i:i + window])
        if span in shingles:
            found.append(span)
            i += window  # report each copied stretch once
        else:
            i += 1
    return found


@dataclass
class SeoReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    word_count: int = 0
    removed_tags: dict[str, int] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.errors


class _Images(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.images: list[dict] = []

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            self.images.append(dict(attrs))


def check_images(html: str, allowed_prefixes: Iterable[str]) -> list[str]:
    """Errors for images in a decorated article: every <img> needs alt text and must be served
    from our own asset host (no hotlinking third-party files)."""
    p = _Images()
    p.feed(html)
    errors = []
    prefixes = tuple(allowed_prefixes)
    for n, img in enumerate(p.images, start=1):
        if not (img.get("alt") or "").strip():
            errors.append(f"görsel {n}: alt metin yok")
        if not (img.get("src") or "").startswith(prefixes):
            errors.append(f"görsel {n}: izinli barındırma dışında: {img.get('src')}")
    return errors


def heading_texts(html: str) -> list[str]:
    return [turkish_lower(re.sub(r"<[^>]+>", "", m)).strip(" :.")
            for m in re.findall(r"(?is)<h[2-4]>(.*?)</h[2-4]>", html)]


def number_tokens(text: str) -> set[str]:
    """Every number in a text, plus its parts: '1.498' -> {'1.498', '1', '498'}, '5,5' -> {'5,5', '5'}."""
    out: set[str] = set()
    for m in NUMBER_RE.findall(text or ""):
        out.add(m)
        out.update(x for x in re.split(r"[.,]", m) if x)
    return out


def ungrounded_numbers(text: str, source_texts: Iterable[str]) -> list[str]:
    """Numbers in the article that appear nowhere in the data pack: a computed rate ("%13"), a converted
    count ("42 sahip"), a rounded or invented figure. Every number must be the pack's own."""
    known: set[str] = set()
    for src in source_texts:
        known |= number_tokens(src)
    return sorted({m for m in NUMBER_RE.findall(text or "") if m not in known},
                  key=lambda x: (len(x), x))


def title_problems(title: str, source_texts: Iterable[str]) -> list[str]:
    """A headline may be catchy, never wrong: length, banned verdicts, every number from the pack."""
    errors = []
    if not title.strip():
        errors.append("başlık boş")
    if len(title) > TITLE_MAX:
        errors.append(f"başlık {len(title)} karakter (en çok {TITLE_MAX})")
    lowered = turkish_lower(title)
    errors += [f"başlıkta yasak ifade: '{p}'" for p in BANNED_PHRASES if p in lowered]
    stray = ungrounded_numbers(title, source_texts)
    if stray:
        errors.append("başlıkta veri paketinde olmayan sayı: " + ", ".join(stray))
    return errors


def chronicle_problems(html: str, title: str) -> list[str]:
    """Kronikler yapısı (docs/PLAN.md bölüm 4): başlıksız giriş, veri tablosu, 'Veri notu' kutusu."""
    errors = []
    if len(title) > TITLE_MAX:
        errors.append(f"başlık {len(title)} karakter (en çok {TITLE_MAX})")
    tables = re.findall(r"(?is)<table>.*?</table>", html)
    if not any(len(re.findall(r"(?i)<tr>", t)) >= 3 for t in tables):
        errors.append("en az iki veri satırı olan bir tablo yok (sahip verisi tabloyla verilmeli)")
    quotes = re.findall(r"(?is)<blockquote>.*?</blockquote>", html)
    if not any(DATA_NOTE in turkish_lower(html_text(q)[0]) for q in quotes):
        errors.append("blockquote içinde 'Veri notu' kutusu yok (sayıların gönüllü bildirim olduğu söylenmeli)")
    first_p = re.search(r"(?is)<p>(.*?)</p>", html)
    if not first_p or re.search(r"(?is)<h[2-4]>|<table>", html[:first_p.start()]):
        errors.append("yazı başlıksız bir giriş paragrafıyla başlamalı")
    return errors


def check(*, html: str, seo_title: str, meta_description: str, slug: str,
          allowed_urls: Iterable[str], source_texts: Iterable[str], claim_ids: list[list[str]],
          known_ids: set[str], removed_tags: dict[str, int] | None = None,
          kind: str = "", title: str = "") -> SeoReport:
    """`html` must already be sanitized. `claim_ids`: the passage ids each claim cites.
    `kind` "chronicle" adds the Kronikler structure rules and the number check (every number in the
    title and body must come from the data pack)."""
    report = SeoReport(removed_tags=dict(removed_tags or {}))
    text, links, headings = html_text(html)
    w = words(text)
    report.word_count = len(w)
    lowered = turkish_lower(text)  # str.casefold() turns "İ" into "i̇" and misses Turkish phrases

    if not w:
        report.errors.append("gövde boş")
    for phrase in BANNED_PHRASES:
        if phrase in lowered:
            report.errors.append(f"yasak klişe: '{phrase}'")
    for m in sorted({m.group(0) for m in FAKE_EXPERIENCE.finditer(lowered)}):
        report.errors.append(f"uydurma deneyim iddiası: '{m}' (blog kendi testini yapmadı)")
    for h in heading_texts(html):
        if h in ENDING_HEADINGS:
            report.errors.append(f"'{h}' başlığı açma; yazı verinin söylediği son somut noktayla bitsin")
    source_texts = list(source_texts)
    if kind == "chronicle":
        report.errors += chronicle_problems(html, title)
        stray = ungrounded_numbers(title + " " + text, source_texts)
        if stray:
            report.errors.append("veri paketinde olmayan sayı: " + ", ".join(stray[:10])
                                 + " (oran hesaplama, yuvarlama, yüzdeyi kişiye çevirme yok; yalnız paketteki sayılar)")
    allowed = set(allowed_urls)
    for link in links:
        if link not in allowed:
            report.errors.append(f"kaynak pakette olmayan link: {link}")
    for span in copied_spans(text, source_texts):
        report.errors.append(f"kaynaktan aynen alınmış: '{span}'")
    if not claim_ids:
        report.errors.append("iddia listesi boş: hiçbir bilgi kaynağa bağlanmamış")
    for n, ids in enumerate(claim_ids, start=1):
        if not ids:
            report.errors.append(f"iddia {n} kaynak göstermiyor")
        unknown = [i for i in ids if i not in known_ids]
        if unknown:
            report.errors.append(f"iddia {n} bilinmeyen parçaya dayanıyor: {', '.join(unknown)}")

    if "h2" not in headings:
        report.warnings.append("hiç H2 alt başlık yok")
    if report.removed_tags:
        report.warnings.append("temizlenen etiketler: " + ", ".join(f"{k}×{v}" for k, v in report.removed_tags.items()))
    if len(seo_title) > SEO_TITLE_MAX:
        report.warnings.append(f"SEO başlığı {len(seo_title)} karakter (> {SEO_TITLE_MAX})")
    if not META_MIN <= len(meta_description) <= META_MAX:
        report.warnings.append(f"meta açıklama {len(meta_description)} karakter ({META_MIN}-{META_MAX} olmalı)")
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug or ""):
        report.warnings.append(f"slug biçimi geçersiz: '{slug}'")
    if 0 < len(w) < MIN_WORDS:
        report.warnings.append(f"yazı kısa: {len(w)} kelime (< {MIN_WORDS})")
    content = [x for x in w if len(x) >= 4 and x not in STOPWORDS]
    if len(w) >= 100 and content:
        word, n = Counter(content).most_common(1)[0]
        if n / len(w) > DENSITY_MAX:
            report.warnings.append(f"'{word}' kelimesi çok sık: %{100 * n / len(w):.1f}")
    return report
