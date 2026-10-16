"""[Ğ] VISUALS - photos and data graphics for an article. Pure code, no AI: every number drawn here
comes from the structured CarScore data the article was written from (planner.Candidate.data).

- Photo: the editor's own photo from the "Görsel arşivi" library (image_library.best_match), else a
  CarScore hero photo that carries an author + license credit (the credit goes under the photo).
  No match -> no photo and an editor note; never a random picture.
- Cover: the photo with a dark band carrying the headline number (e.g. "11 / 84"), as HTML/CSS over the
  <img> - the photo file is not modified, the first <img> stays the post thumbnail.
- Data graphics as HTML/CSS (no image file, no hosting, crisp on phones, numbers readable by search):
  fault outcome bars, comparison card. Self-contained colors, so they read the same in the blog's
  light and dark themes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import escape
from typing import Optional

from config import ImageSettings
from image_library import LibraryImage, best_match

BRANDS = ("Volkswagen", "Škoda", "Skoda", "SEAT", "CUPRA", "Audi")
INK, MUTED, LINE, ACCENT, OK, UNKNOWN = "#111418", "#5f6670", "#d9dde2", "#c8102e", "#8a939d", "#c9ced4"
FONT = "font-family:inherit;"


def upper_tr(text: str) -> str:
    """Turkish capitals ('i' -> 'İ'); CSS text-transform depends on the page's lang attribute."""
    return text.replace("i", "İ").replace("ı", "I").upper()


def tr(x) -> str:
    return f"{x:g}".replace(".", ",") if isinstance(x, float) else str(x)


def models_of(titles: list[str]) -> list[tuple[str, str]]:
    """'Volkswagen Passat B8 Facelift' -> ('Volkswagen', 'Passat'); 'CUPRA Leon' -> ('CUPRA', 'Leon')."""
    out = []
    for t in titles:
        words = t.split()
        if len(words) >= 2 and words[0] in BRANDS and (words[0], words[1]) not in out:
            out.append((words[0], words[1]))
    return out


@dataclass
class Photo:
    src: str
    alt: str
    source: str                         # "arşiv" | "carscore"
    credit_html: str = ""               # required for CarScore photos
    reason: str = ""


@dataclass
class VisualReport:
    photo: Optional[Photo] = None
    notes: list[str] = field(default_factory=list)   # for the editor (no photo found, ...)


def credit_line(photo: dict) -> str:
    lic = escape(photo.get("license", ""))
    if photo.get("license_url", "").startswith("https://"):
        lic = f'<a href="{escape(photo["license_url"], quote=True)}" rel="nofollow noopener">{lic}</a>'
    src = (f' · <a href="{escape(photo["source_url"], quote=True)}" rel="nofollow noopener">Kaynak</a>'
           if photo.get("source_url", "").startswith("https://") else "")
    return f"Fotoğraf: {escape(photo.get('author', ''))} · {lic}{src}"


def carscore_photo_ok(photo: dict, settings: ImageSettings = ImageSettings()) -> bool:
    return (bool(photo.get("src", "").startswith(settings.allowed_prefixes[1])) and bool(photo.get("author"))
            and any(photo.get("license", "").upper().startswith(l.upper()) for l in settings.carscore_licenses))


def pick_photo(models: list[tuple[str, str]], library: list[LibraryImage], carscore_photos: list[dict],
               settings: ImageSettings = ImageSettings()) -> tuple[Optional[Photo], list[str]]:
    """Own photo first (must show one of `models`), then a credited CarScore photo of one of those cars."""
    notes = []
    hit = best_match(library, [m for _, m in models], [b for b, _ in models])
    if hit is not None:
        label = hit.label if hit.label.get("arac") else {}
        alt = hit.alt or " ".join(x for x in (label.get("marka"), label.get("model")) if x) or models[0][1]
        return Photo(hit.url, alt, "arşiv", reason=f"arşivden: {hit.file}"), notes
    notes.append("Görsel arşivinde bu araçlara uyan resim yok (" + ", ".join(m for _, m in models[:6]) + ")")
    for p in carscore_photos:
        if carscore_photo_ok(p, settings):
            return Photo(p["src"], p.get("alt", ""), "carscore", credit_line(p), "CarScore fotoğrafı (kredili)"), notes
    notes.append("Kredili CarScore fotoğrafı da yok: yazı resimsiz; Blogger'da elle resim ekleyebilirsin")
    return None, notes


# ------------------------------------------------------------------ HTML pieces
def cover(photo: Photo, kicker: str, big: str, caption: str) -> str:
    credit = (f'<p style="{FONT}font-size:12px;color:{MUTED};margin:6px 0 0">{photo.credit_html}</p>'
              if photo.credit_html else "")
    return (f'<figure style="margin:0 0 24px;padding:0">'
            f'<div style="position:relative;overflow:hidden;background:{INK};border-radius:4px">'
            f'<img src="{escape(photo.src, quote=True)}" alt="{escape(photo.alt, quote=True)}" loading="eager" '
            f'style="display:block;width:100%;height:auto;aspect-ratio:16/9;object-fit:cover">'
            f'<div style="position:absolute;left:0;right:0;bottom:0;padding:28px 20px 16px;'
            f'background:linear-gradient(rgba(0,0,0,0),rgba(0,0,0,.82));color:#fff;{FONT}">'
            f'<div style="font-size:12px;letter-spacing:.14em;opacity:.85">{escape(upper_tr(kicker))}</div>'
            f'<div style="font-size:clamp(30px,7vw,52px);font-weight:800;line-height:1.05;margin:4px 0">{escape(big)}</div>'
            f'<div style="font-size:15px;opacity:.95">{escape(caption)}</div></div></div>{credit}</figure>')


def fault_bars(data: dict) -> str:
    reports = max(1, int(data["reports"]))
    rows = []
    for o in data["outcomes"]:
        label, count = o["label"], int(o["count"])
        color = OK if label in data.get("no_issue_labels", ()) else UNKNOWN if label in data.get("unknown_labels", ()) else ACCENT
        width = max(1.5, 100 * count / reports)
        rows.append(f'<div style="margin:10px 0">'
                    f'<div style="display:flex;justify-content:space-between;gap:12px;font-size:14px">'
                    f'<span>{escape(label)}</span><strong>{count}</strong></div>'
                    f'<div style="height:10px;background:#eef0f2;border-radius:2px;margin-top:4px">'
                    f'<div style="height:10px;width:{width:.1f}%;background:{color};border-radius:2px"></div></div></div>')
    return (f'<div style="{FONT}border:1px solid {LINE};border-left:4px solid {INK};background:#fff;color:{INK};'
            f'padding:16px 18px;margin:24px 0;border-radius:2px">'
            f'<div style="font-size:12px;letter-spacing:.12em;color:{MUTED}">SAHİP BİLDİRİMLERİ</div>'
            f'<div style="font-size:18px;font-weight:700;margin:2px 0 6px">{escape(data["unit"])}: {reports} bildirim, '
            f'{int(data["with_issue"])} arıza</div>{"".join(rows)}'
            f'<div style="font-size:12px;color:{MUTED};margin-top:10px">Kaynak: CarScore · gönüllü bildirimdir, arıza oranı değildir</div></div>')


def _cell(v: Optional[str]) -> str:
    return escape(v) if v else "–"


def compare_card(a: dict, b: dict) -> str:
    rows = [("CarScore", lambda v: f'{v["carscore"]}/100'),
            ("Görüş sayısı", lambda v: str(v["opinions"])),
            ("Kullanıcı puanı", lambda v: f'{tr(v["rating"])}/5' if v.get("rating") is not None else ""),
            ("Tekrar alırdım", lambda v: f'%{v["repurchase_pct"]}' if v.get("repurchase_pct") is not None else ""),
            ("Gerçek tüketim", lambda v: f'{tr(v["consumption"])} L/100km' if v.get("consumption") is not None else ""),
            ("En çok övülen", lambda v: v.get("top_strength") or ""),
            ("En çok şikâyet", lambda v: v.get("top_complaint") or "")]
    head = "".join(f'<th style="text-align:left;padding:10px 8px;font-size:15px;border-bottom:2px solid {INK}">'
                   f'{escape(v["name"])}</th>' for v in (a, b))
    body = "".join(f'<tr><td style="padding:8px;color:{MUTED};font-size:13px;border-bottom:1px solid {LINE}">{label}</td>'
                   + "".join(f'<td style="padding:8px;font-size:14px;border-bottom:1px solid {LINE}">{_cell(fn(v))}</td>'
                             for v in (a, b)) + "</tr>"
                   for label, fn in rows)
    return (f'<div style="{FONT}background:#fff;color:{INK};border:1px solid {LINE};padding:12px;margin:24px 0;'
            f'border-radius:2px;overflow-x:auto"><table style="width:100%;border-collapse:collapse;min-width:320px">'
            f'<thead><tr><th style="border-bottom:2px solid {INK}"></th>{head}</tr></thead><tbody>{body}</tbody></table>'
            f'<div style="font-size:12px;color:{MUTED};margin-top:8px">Kaynak: CarScore sahip anketi · '
            f'yüzdeler ankete katılanların payıdır</div></div>')


def after_intro(body: str, block: str) -> str:
    m = re.search(r"(?is)</p>", body)
    return body[:m.end()] + block + body[m.end():] if m else block + body


# ------------------------------------------------------------------ article
def decorate(kind: str, data: dict, html: str, library: list[LibraryImage],
             settings: ImageSettings = ImageSettings()) -> tuple[str, VisualReport]:
    """`html`: the writer's sanitized body + data source box. Returns the final article HTML."""
    report = VisualReport()
    if kind == "ariza":
        models = models_of(data.get("models", []))
        photo, report.notes = pick_photo(models, library, data.get("photos", []), settings) if models else (None, ["model listesi yok"])
        chart = fault_bars(data)
        top = (cover(photo, f"Arıza kroniği · {data['unit']}", f"{data['with_issue']} / {data['reports']}",
                     f"{data['unit']} bildiriminde {data['label']}") if photo else "")
    else:
        a, b = data["a"], data["b"]
        models = models_of([a["name"], b["name"]])
        photo, report.notes = pick_photo(models[:1], library, [a.get("photo") or {}], settings) if models else (None, ["model yok"])
        chart = compare_card(a, b)
        top = (cover(photo, "Karşılaştırma · sahip verisi", f'{a["carscore"]} – {b["carscore"]}',
                     f'CarScore: {a["short"]} / {b["short"]}') if photo else "")
    report.photo = photo
    return top + after_intro(html, chart), report
