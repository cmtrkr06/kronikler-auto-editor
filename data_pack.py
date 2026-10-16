"""[V] DATA PACK - the only material an article may be written from.

Zapyus built this pack from fetched web pages (researcher.py); Kronikler builds it from CarScore
data. Same shape, so the writer and the fact-checker work unchanged: every passage has an id
([K1], [K2]...), the writer cites them per claim, the fact-checker checks against the same pack.
The builders (CarScore snapshot -> passages) come in step 1 (docs/PLAN.md).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Passage:
    id: str              # "K1", "K2"... unique inside one pack
    reference_id: str    # e.g. "carscore:sanziman/dq200"
    url: str             # the CarScore page the numbers come from
    title: str
    text: str


@dataclass
class ResearchPack:
    subject_key: str
    subject_title: str
    passages: list[Passage] = field(default_factory=list)
    missing: list[dict] = field(default_factory=list)  # {reference_id, url, reason}
    created_at: str = ""

    @property
    def total_chars(self) -> int:
        return sum(len(p.text) for p in self.passages)

    @property
    def usable(self) -> bool:
        return bool(self.passages)

    def render(self) -> str:
        """The block given to the writer and the fact-checker."""
        return "\n\n".join(f"[{p.id}] {p.title} ({p.url})\n{p.text}" for p in self.passages)

    def urls(self) -> list[str]:
        return list(dict.fromkeys(p.url for p in self.passages))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ResearchPack":
        return cls(d["subject_key"], d["subject_title"], [Passage(**p) for p in d.get("passages", [])],
                   list(d.get("missing", [])), d.get("created_at", ""))


# ------------------------------------------------------------------ builders (CarScore -> passages)
# Inputs are carscore_source dataclasses (duck-typed here, so this module has no parsing code).
NOT_A_RATE = ("Bu sayılar gönüllü sahip bildirimidir, temsili bir örneklem değildir; sorun yaşayanların "
              "bildirme olasılığı daha yüksektir. Arıza ORANI olarak değil, bildirim ADEDİ olarak okunmalıdır.")
PERCENT_NOTE = ("Yüzdeler, aracın sayfasındaki sahip anketine katılanlar arasında o seçeneği işaretleyenlerin "
                "payıdır. Sayfa soru başına yanıt sayısını vermez; yüzdeler kişi sayısına çevrilemez. Birden çok "
                "seçenek işaretlenebilen sorularda yüzdelerin toplamı 100'ü geçer.")


class _Builder:
    def __init__(self, key: str, title: str) -> None:
        self.pack = ResearchPack(key, title)

    def add(self, ref: str, url: str, title: str, lines: list[str]) -> None:
        body = "\n".join(x for x in lines if x)
        if body:
            self.pack.passages.append(Passage(f"K{len(self.pack.passages) + 1}", ref, url, title, body))


def _fault_lines(f) -> list[str]:
    lines = [f"{f.unit} kullanan tüm araçlardan {f.reports} bildirimden {f.with_issue} tanesinde arıza bildirildi"
             f" (sorun: {f.title})."]
    if f.verified_owners is not None:
        lines.append(f"Bunlardan {f.verified_owners} tanesi doğrulanmış sahip.")
    lines.append("Seçeneklere göre bildirimler: " + "; ".join(f"{o.label}: {o.count}" for o in f.outcomes) + ".")
    for x in f.facts:
        lines.append(f"{x.question} {x.answer} ({x.base} bildirimin {'ortancası' if x.stat == 'ortanca' else 'en sık cevabı'}).")
    return lines


def fault_pack(key: str, title: str, spot, fault, unit=None) -> ResearchPack:
    """`spot`: hub Spotlight, `fault`: FaultReport, `unit`: UnitPage (gearbox/engine page) if read."""
    b = _Builder(key, title)
    ref = "carscore:" + fault.unit
    b.add(ref, fault.source_url, f"CarScore sahip bildirimleri: {fault.unit} {spot.label}", _fault_lines(fault))
    b.add(ref, fault.source_url, "CarScore notu: bildirim adedi, arıza oranı değil", [fault.caveat, NOT_A_RATE])
    if fault.intro:
        b.add(ref, fault.source_url, f"CarScore: {fault.title}", [fault.intro])
    if unit is not None:
        b.add("carscore:" + unit.code, unit.url, f"CarScore teknik künye: {unit.code} ({unit.kind})",
              [unit.meta] + [f"{k}: {v}" for k, v in unit.specs.items()] + ([unit.about] if unit.about else []))
        summary = [f"{k}: {v}" for k, v in unit.summary.items()]
        if unit.top_complaint:
            summary.append(f"{unit.code} kullanan araçların sahip anketi - {unit.top_complaint}.")
        b.add("carscore:" + unit.code, unit.url, f"CarScore özeti: {unit.code} kullanan araçlar", summary)
        if unit.vehicles:
            names = [f"{c.title} {c.config}".strip() + (f" (CarScore {c.carscore})" if c.carscore is not None else "")
                     for c in unit.vehicles]
            b.add("carscore:" + unit.code, unit.url, f"{unit.code} kullanan araçlardan bazıları",
                  ["; ".join(names) + ".", unit.vehicle_note])
    return b.pack


def _vehicle_lines(v) -> list[list[str]]:
    head = [f"{v.name}: CarScore {v.carscore}/100 ({v.verdict}), {v.opinions} kişinin görüşüne göre."]
    if v.rating is not None:
        head.append(f"Kullanıcı puanı {v.rating:g}/5" + (f", {v.rating_count} değerlendirme." if v.rating_count else "."))
    if v.repurchase_pct is not None:
        head.append(f"Ankete katılan sahiplerin %{v.repurchase_pct}'i aracı tekrar alırdım diyor.")
    if v.sources:
        head.append("Puanın kaynakları: " + "; ".join(f"{k} {val}/100" for k, val in v.sources.items()) + ".")
    if v.meters:
        head.append("Alt puanlar (0-100): " + "; ".join(f"{k} {val}" for k, val in v.meters.items()) + ".")
    survey = []
    if v.strengths:
        survey.append("Sahiplerin en çok övdüğü yönler: " + "; ".join(f"{o.label} %{o.count}" for o in v.strengths) + ".")
    if v.complaints:
        survey.append("Sahiplerin en çok şikâyet ettikleri: " + "; ".join(f"{o.label} %{o.count}" for o in v.complaints) + ".")
    c = v.consumption
    if c.get("Ortalama") is not None:
        survey.append(f"Sahiplerin bildirdiği gerçek tüketim: ortalama {c['Ortalama']:g} L/100km, en düşük "
                      f"{c.get('En düşük', 0):g}, en yüksek {c.get('En yüksek', 0):g}"
                      + (f" ({int(c['Kayıt'])} kayıt)." if c.get("Kayıt") else "."))
    for q, opts in v.stats.items():
        survey.append(f"{q} " + "; ".join(f"{o.label} %{o.count}" for o in opts) + ".")
    specs = [f"{k}: {val}" for k, val in v.specs.items()]
    return [head, survey, specs]


def compare_pack(key: str, title: str, a, b_) -> ResearchPack:
    """Two Vehicles. Fault sections on a vehicle page are per ENGINE/GEARBOX across every vehicle that
    uses it, not per vehicle: the passage says so, the writer must not attribute them to one model."""
    b = _Builder(key, title)
    for v in (a, b_):
        head, survey, specs = _vehicle_lines(v)
        ref = "carscore:" + v.url
        b.add(ref, v.url, f"CarScore özeti: {v.name}", head)
        b.add(ref, v.url, f"CarScore sahip anketi: {v.name}", survey + [PERCENT_NOTE] if survey else [])
        b.add(ref, v.url, f"CarScore teknik özellikler: {v.name}", specs)
        for f in v.faults:
            b.add(ref, v.url, f"CarScore sahip bildirimleri: {f.unit} (bu aracın {('şanzımanı' if f.unit == v.specs.get('Şanzıman') else 'motoru')})",
                  [f"Bu bölüm yalnız {v.name} için değil, {f.unit} kullanan TÜM araçların bildirimleridir."]
                  + _fault_lines(f) + [NOT_A_RATE])
    return b.pack
