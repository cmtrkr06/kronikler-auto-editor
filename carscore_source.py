"""[C] CARSCORE SOURCE - reads CarScore's public pages and turns them into checked data.

Pages (markup checked 2026-10-16, examples in tests/fixtures/):
  /araclar/            hub: "Sahiplerin bildirdiği kronik sorunlar" (fault units) + class leaders
  /sanziman/<kod>/     gearbox page: full fault section (outcomes, km, cost, payer...)
  /motor/<kod>/        engine page: NO fault section; the engine's fault section is on the pages of
                       the vehicles that use it ("<motor> kullanan tüm araçlardan ...")
  /arac/<slug>/        vehicle page: CarScore, owner survey, fault sections of its engine/gearbox

The biggest risk of reading HTML is a SILENT wrong number. So:
- every required field is read with an explicit selector; missing -> ParseError, never a default;
- every page's numbers are checked against each other (problems()); a page with problems is not
  used for writing;
- numbers shown on two pages (hub card vs unit page, unit page vs vehicle page) must agree.

    python carscore_source.py            # read the site, print a summary, write data/carscore/<day>.json
"""
from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag

from config import CarScoreSettings

log = logging.getLogger("kronikler.carscore")
TR = ZoneInfo("Europe/Istanbul")


class ParseError(Exception):
    """A required field is missing: the page layout changed, the parser must be updated."""

    def __init__(self, url: str, what: str) -> None:
        super().__init__(f"{url}: '{what}' bulunamadı (sayfa yapısı değişmiş olabilir; ayrıştırıcı güncellenmeli)")
        self.url, self.what = url, what


# ------------------------------------------------------------------ text helpers
_WS = re.compile(r"\s+")


def text(el: Optional[Tag]) -> str:
    return _WS.sub(" ", el.get_text(" ", strip=True)).strip() if el is not None else ""


def to_int(raw: str) -> Optional[int]:
    """'1.498' -> 1498, '%84' -> 84, ' 72 ' -> 72. None if there is no single integer."""
    m = re.fullmatch(r"%?\s*(\d{1,3}(?:\.\d{3})+|\d+)", (raw or "").strip())
    return int(m.group(1).replace(".", "")) if m else None


def to_dec(raw: str) -> Optional[float]:
    """Turkish decimal: '5,5' -> 5.5, '4,7' -> 4.7, '82' -> 82.0."""
    m = re.search(r"(\d+(?:,\d+)?)", raw or "")
    return float(m.group(1).replace(",", ".")) if m else None


def need(el: Optional[Tag], url: str, what: str) -> Tag:
    if el is None:
        raise ParseError(url, what)
    return el


def need_int(el: Optional[Tag], url: str, what: str) -> int:
    value = to_int(text(need(el, url, what)))
    if value is None:
        raise ParseError(url, what + " (sayı)")
    return value


def soup(html: bytes | str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def main_of(page: BeautifulSoup, url: str) -> Tag:
    return need(page.select_one("main#carscore-main"), url, "main#carscore-main")


# ------------------------------------------------------------------ data
@dataclass
class Outcome:
    label: str
    count: int
    unit: str = "%"      # "%" (share of participants) or "adet" (raw count: the site shows counts while a
                         # question is below its own threshold, "yüzdeler 3 katılımcıdan sonra gösterilir")


@dataclass
class FaultFact:
    question: str        # "Kaç kilometrede oldu?"
    answer: str          # "80 – 100 Bin"
    base: int            # number of reports behind the answer
    stat: str            # "ortanca" | "en sık"


@dataclass
class FaultReport:
    unit: str                        # "DQ200", "1.5 eTSI 116 HP" (as the headline names it)
    title: str                       # "DSG Şanzıman (Mekatronik ve Kavrama)"
    intro: str
    reports: int                     # all reports for the unit, across every vehicle using it
    with_issue: int
    verified_owners: Optional[int]   # "Bunlardan N tanesi doğrulanmış sahip"; not on every section
    outcomes: list[Outcome]
    facts: list[FaultFact]
    caveat: str                      # the site's own "not a failure rate" note
    source_url: str

    def problems(self, s: CarScoreSettings = CarScoreSettings()) -> list[str]:
        p = []
        if not 0 <= self.with_issue <= self.reports:
            p.append(f"{self.unit}: arızalı {self.with_issue} > bildirim {self.reports}")
        total = sum(o.count for o in self.outcomes)
        if total != self.reports:
            p.append(f"{self.unit}: seçeneklerin toplamı {total}, bildirim {self.reports}")
        ok_labels = set(s.no_issue_labels) | set(s.unknown_labels)
        issues = sum(o.count for o in self.outcomes if o.label not in ok_labels)
        if issues != self.with_issue:
            p.append(f"{self.unit}: arıza seçenekleri toplamı {issues}, başlıkta {self.with_issue}")
        if not any(o.label in s.no_issue_labels for o in self.outcomes):
            p.append(f"{self.unit}: 'sorun yaşamadım' seçeneği yok (etiket değişmiş olabilir)")
        if self.verified_owners is not None and self.verified_owners > self.reports:
            p.append(f"{self.unit}: doğrulanmış sahip {self.verified_owners} > bildirim {self.reports}")
        for f in self.facts:
            if f.base > self.reports:
                p.append(f"{self.unit}: '{f.question}' tabanı {f.base} > bildirim {self.reports}")
        if not self.caveat:
            p.append(f"{self.unit}: 'arıza oranı değildir' notu yok")
        return p


@dataclass
class Spotlight:
    """One card of the hub's chronic-issue list."""
    unit: str
    label: str           # "DSG arızası", "48V jeneratör"
    url: str
    with_issue: int
    reports: int
    lines: list[str]


@dataclass
class Lead:
    """A class leader on the hub (candidate vehicle for comparisons)."""
    body: str            # "SUV", "Hatchback", "Sedan"
    url: str
    name: str
    config: str
    specs: str
    opinions: int
    carscore: int


@dataclass
class Hub:
    url: str
    stats: dict[str, int]
    spotlights: list[Spotlight]
    leads: list[Lead]


@dataclass
class UnitCard:
    url: str
    title: str
    config: str
    carscore: Optional[int]


@dataclass
class UnitPage:
    url: str
    kind: str                        # "Şanzıman" | "Motor"
    code: str                        # h1: "DQ200", "1.5 eTSI 150 HP"
    meta: str
    specs: dict[str, str]
    about: str
    fault: Optional[FaultReport]
    summary: dict[str, str]          # "Ortalama CarScore": "71 (18 araç üzerinden)" ...
    top_complaint: str
    vehicles: list[UnitCard]
    vehicle_note: str


@dataclass
class Vehicle:
    url: str
    make: str                        # "Volkswagen Golf"
    trim: str                        # "Mk8 Facelift 1.5 eTSI 116 HP Life DSG 7 İleri"
    carscore: int
    verdict: str                     # "Sahipleri çok memnun"
    opinions: int                    # "72 kişinin görüşüne göre"
    rating: Optional[float]
    rating_count: Optional[int]
    repurchase_pct: Optional[int]
    meters: dict[str, int]           # owner + technical sub-scores
    sources: dict[str, int]          # {"teknik veri": 55, "kullanıcı görüşü": 85}
    strengths: list[Outcome]         # label + percent
    complaints: list[Outcome]
    specs: dict[str, str]
    consumption: dict[str, float]    # Ortalama/En düşük/En yüksek (L/100km), Kayıt
    participants: Optional[int]
    stats: dict[str, list[Outcome]]  # survey question -> [(label, percent or count)]
    faults: list[FaultReport]
    low_data: list[str] = field(default_factory=list)       # questions under the site's own data threshold
    pending_faults: list[dict] = field(default_factory=list)  # fault sections still collecting (no numbers)

    @property
    def name(self) -> str:
        return f"{self.make} {self.trim}"

    def survey_ready(self, s: CarScoreSettings = CarScoreSettings()) -> bool:
        """The owner survey is published as percentages (above the site's threshold) for every question
        a comparison needs. Below it the verdict's strengths/complaints are percentages of 1-2 people."""
        return all(q in self.stats and q not in self.low_data for q in s.compare_required_questions)

    def problems(self, s: CarScoreSettings = CarScoreSettings()) -> list[str]:
        p = []
        if not 0 <= self.carscore <= 100:
            p.append(f"{self.name}: CarScore {self.carscore}")
        if self.rating is not None and not 1 <= self.rating <= 5:
            p.append(f"{self.name}: kullanıcı puanı {self.rating}")
        for label, value in [("tekrar alırdım", self.repurchase_pct), *self.meters.items(), *self.sources.items()]:
            if value is not None and not 0 <= value <= 100:
                p.append(f"{self.name}: {label} {value}")
        for group in (self.strengths, self.complaints, *self.stats.values()):
            for o in group:
                if o.unit == "%" and not 0 <= o.count <= 100:
                    p.append(f"{self.name}: %{o.count} ({o.label})")
        c = self.consumption
        if c and not (c.get("En düşük", 0) <= c.get("Ortalama", 0) <= c.get("En yüksek", 1e9)):
            p.append(f"{self.name}: tüketim en düşük/ortalama/en yüksek sırası bozuk {c}")
        if c.get("Kayıt") and self.participants and c["Kayıt"] > self.participants:
            p.append(f"{self.name}: tüketim kaydı {c['Kayıt']} > katılımcı {self.participants}")
        for q in s.single_choice_questions:
            opts = self.stats.get(q, [])
            if q in self.low_data or not opts or any(o.unit != "%" for o in opts):
                continue                                  # counts, not shares: nothing to add up
            if abs(sum(o.count for o in opts) - 100) > s.rounding_slack:
                p.append(f"{self.name}: '{q}' yüzdeleri toplamı {sum(o.count for o in self.stats[q])}")
        if self.participants is not None and self.participants > self.opinions:
            p.append(f"{self.name}: katılımcı {self.participants} > görüş {self.opinions}")
        for f in self.faults:
            p += f.problems(s)
        return p


# ------------------------------------------------------------------ parsers
def pending_fault(section: Tag, url: str) -> Optional[dict]:
    """A fault section still below the site's publishing threshold: '7 bildirim toplandı. 12 bildirime
    ulaşıldığında dağılım burada yayınlanacak.' No numbers to write about, but not a layout change."""
    progress = section.select_one(".carscore-fault__progress-text")
    if progress is None or section.select_one(".carscore-fault__headline") is not None:
        return None
    nums = [to_int(text(x)) for x in progress.select("strong")]
    if len(nums) != 2 or None in nums:
        raise ParseError(url, "toplanan bildirim / yayın eşiği")
    title_el = section.select_one(".carscore-fault__title")
    icon = title_el.select_one(".carscore-fault__icon") if title_el else None
    title = text(title_el).replace(text(icon), "", 1).strip() if icon else text(title_el)
    return {"title": title, "collected": nums[0], "threshold": nums[1]}


def parse_fault(section: Tag, url: str) -> FaultReport:
    headline = need(section.select_one(".carscore-fault__headline"), url, "arıza başlığı")
    nums = headline.select("strong")
    if len(nums) != 2:
        raise ParseError(url, "arıza başlığında iki sayı")
    m = re.match(r"\s*(.+?)\s+kullanan tüm araçlardan", text(headline))
    if not m:
        raise ParseError(url, "arıza başlığında birim adı ('... kullanan tüm araçlardan')")
    owners = section.select_one(".carscore-fault__owners strong")
    outcomes = []
    for li in section.select(".carscore-fault__bar"):
        label = text(need(li.select_one(".carscore-fault__bar-label"), url, "seçenek etiketi"))
        outcomes.append(Outcome(label, need_int(li.select_one(".carscore-fault__bar-value"), url, f"'{label}' sayısı")))
    if not outcomes:
        raise ParseError(url, "arıza seçenekleri (carscore-fault__bar)")
    facts = []
    for div in section.select(".carscore-fault__fact"):
        for icon in div.select('[aria-hidden="true"]'):
            icon.decompose()                       # emoji icons are decoration, not part of the question
        q = text(need(div.select_one(".carscore-fault__fact-label"), url, "soru"))
        a = text(need(div.select_one(".carscore-fault__fact-value strong"), url, f"'{q}' cevabı"))
        note = text(need(div.select_one(".carscore-fault__fact-note"), url, f"'{q}' notu"))
        nm = re.match(r"(\d+) bildirimin (ortancası|en sık cevabı)", note)
        if not nm:
            raise ParseError(url, f"'{q}' notunda 'N bildirimin ortancası/en sık cevabı'")
        facts.append(FaultFact(q, a, int(nm.group(1)), "ortanca" if nm.group(2) == "ortancası" else "en sık"))
    title_el = need(section.select_one(".carscore-fault__title"), url, "arıza bölümü başlığı")
    icon = title_el.select_one(".carscore-fault__icon")
    title = text(title_el).replace(text(icon), "", 1).strip() if icon else text(title_el)
    caveat = section.select_one("details.carscore-caveat")
    return FaultReport(
        unit=m.group(1), title=title, intro=text(section.select_one(".carscore-fault__intro")),
        reports=need_int(nums[0], url, "bildirim sayısı"), with_issue=need_int(nums[1], url, "arızalı bildirim"),
        verified_owners=to_int(text(owners)) if owners else None, outcomes=outcomes, facts=facts,
        caveat=" ".join(filter(None, [text(caveat.select_one("summary")), text(caveat.select_one("p"))])) if caveat else "",
        source_url=url)


def parse_hub(html: bytes | str, url: str) -> Hub:
    main = main_of(soup(html), url)
    stats = {}
    for li in main.select(".carscore-appbar__stat"):
        stats[text(li.select_one(".carscore-appbar__label"))] = need_int(li.select_one(".carscore-appbar__value"), url, "istatistik")
    spot = need(main.select_one("section.carscore-spotlight"), url, "kronik sorunlar bölümü")
    spotlights = []
    for card in spot.select("a.carscore-spotlight__card"):
        fig = need(card.select_one(".carscore-spotlight__figure"), url, "kronik sorun sayısı")
        fm = re.fullmatch(r"(\d+) / (\d+) bildirimde arıza", text(fig))
        if not fm:
            raise ParseError(url, "kronik sorun kartında 'N / M bildirimde arıza'")
        spotlights.append(Spotlight(
            unit=text(need(card.select_one(".carscore-spotlight__unit"), url, "birim")),
            label=text(need(card.select_one(".carscore-spotlight__label"), url, "sorun etiketi")),
            url=need(card, url, "kart").get("href", ""), with_issue=int(fm.group(1)), reports=int(fm.group(2)),
            lines=[text(x) for x in card.select(".carscore-spotlight__line")]))
    leads = []
    for cls in main.select(".carscore-shelf--classes .carscore-shelf__class"):
        body = text(need(cls.select_one(".carscore-shelf__class-title a"), url, "sınıf adı")).replace("→", "").strip()
        for lead in cls.select("li.carscore-lead"):
            reason = text(lead.select_one(".carscore-lead__reason"))
            om = re.search(r"(\d+) görüş", reason)
            leads.append(Lead(
                body=body, url=need(lead.select_one("a.carscore-lead__link"), url, "lider bağlantısı").get("href", ""),
                name=text(need(lead.select_one(".carscore-lead__name"), url, "lider adı")),
                config=text(lead.select_one(".carscore-lead__config")), specs=text(lead.select_one(".carscore-lead__specs")),
                opinions=int(om.group(1)) if om else 0,
                carscore=need_int(lead.select_one(".carscore-score-badge__value"), url, "lider CarScore")))
    return Hub(url, stats, spotlights, leads)


def parse_unit(html: bytes | str, url: str) -> UnitPage:
    main = main_of(soup(html), url)
    specs = {text(d.select_one("dt")): text(d.select_one("dd")) for d in main.select("section.carscore-panel > dl.carscore-specs > .carscore-spec")}
    fault_el = main.select_one("section.carscore-fault")
    summary = {}
    for st in main.select(".carscore-axis__stat"):
        summary[text(st.select_one(".carscore-axis__label"))] = " · ".join(
            filter(None, [text(st.select_one(".carscore-axis__value")), text(st.select_one(".carscore-axis__note"))]))
    cards = []
    for art in main.select("article.carscore-card"):
        link = art.select_one("a.carscore-card__link")
        if link is None:
            continue
        cards.append(UnitCard(link.get("href", ""), text(art.select_one(".carscore-card__title")),
                              text(art.select_one(".carscore-card__config")),
                              to_int(text(art.select_one(".carscore-score-badge__value")))))
    return UnitPage(
        url=url, kind=text(need(main.select_one(".carscore-hero__kicker"), url, "sayfa türü")),
        code=text(need(main.select_one("h1.carscore-hero__title"), url, "başlık")),
        meta=text(main.select_one(".carscore-hero__meta")), specs=specs,
        about=text(main.select_one("section.carscore-prose")).removeprefix("Bu Şanzıman Hakkında").removeprefix("Bu Motor Hakkında").strip(),
        fault=parse_fault(fault_el, url) if fault_el and not pending_fault(fault_el, url) else None, summary=summary,
        top_complaint=text(main.select_one(".carscore-axis__signal")).lstrip("⚠ ").strip(),
        vehicles=cards, vehicle_note=text(main.select_one("p.carscore-note")))


def _bars(ul: Optional[Tag], url: str, what: str) -> list[Outcome]:
    out = []
    for li in (ul.select("li") if ul else []):
        label = text(li.select_one(".carscore-bar__label, span:not([class]), span"))
        raw = text(li.select_one(".carscore-bar__value, em"))
        value = to_int(raw)
        if value is None:
            raise ParseError(url, f"{what}: '{label}' değeri")
        out.append(Outcome(label, value, "%" if raw.startswith("%") else "adet"))
    return out


def parse_vehicle(html: bytes | str, url: str) -> Vehicle:
    main = main_of(soup(html), url)
    verdict = need(main.select_one("section.carscore-verdict"), url, "hüküm bölümü (carscore-verdict)")
    meta = text(need(verdict.select_one(".carscore-verdict__meta"), url, "görüş sayısı"))
    om = re.match(r"(\d+) kişinin görüşüne göre", meta)
    if not om:
        raise ParseError(url, "'N kişinin görüşüne göre'")
    repurchase = None
    for fact in verdict.select(".carscore-fact"):
        if "tekrar alırdım" in text(fact.select_one(".carscore-fact__label")):
            repurchase = to_int(text(fact.select_one(".carscore-fact__value")))
    meters = {}
    for m in verdict.select(".carscore-meter"):
        label_el = m.select_one(".carscore-meter__label")
        note = label_el.select_one(".carscore-meter__note") if label_el else None
        label = text(label_el).replace(text(note), "").strip() if note else text(label_el)
        meters[label] = need_int(m.select_one(".carscore-meter__value"), url, f"'{label}' puanı")
    sources = {}
    for s in verdict.select(".carscore-verdict__source"):
        value_el = need(s.select_one(".carscore-verdict__source-value"), url, "kaynak puanı")
        max_el = value_el.select_one(".carscore-verdict__source-max")
        raw = text(value_el).replace(text(max_el), "").strip() if max_el else text(value_el)
        sources[text(s.select_one(".carscore-verdict__source-label"))] = to_int(raw)
    plus = verdict.select_one(".carscore-verdict__list--plus")
    minus = verdict.select_one(".carscore-verdict__list--minus")
    rating_el = main.select_one(".carscore-scorecard__rating .carscore-scorecard__value")
    rating_ctx = text(main.select_one(".carscore-scorecard__rating .carscore-scorecard__context"))
    rc = re.match(r"(\d+) değerlendirme", rating_ctx)
    specs = {}
    for d in main.select("dl.carscore-specs > .carscore-spec"):
        specs[text(d.select_one("dt"))] = text(d.select_one("dd"))
    community = main.select_one("section.carscore-community")
    participants, stats, consumption, low_data = None, {}, {}, []
    if community is not None:
        pm = re.search(r"(\d+) katılımcı", text(community.select_one(".carscore-section-title")))
        participants = int(pm.group(1)) if pm else None
        for art in community.select("article.carscore-stat"):
            q = text(art.select_one(".carscore-stat__title"))
            if art.select_one(".carscore-stat__notice") is not None:
                low_data.append(q)
            nums = art.select_one("dl.carscore-stat__numbers")
            if nums is not None:
                for d in nums.select("div"):
                    label = text(d.select_one("dt"))
                    value = to_int(text(d.select_one("dd"))) if label == "Kayıt" else to_dec(text(d.select_one("dd")))
                    if value is None:
                        raise ParseError(url, f"'{q}' / {label}")
                    consumption[label] = value
                continue
            stats[q] = [o for ul in art.select("ul.carscore-bars") for o in _bars(ul, url, q)]
    faults, pending = [], []
    for sec in main.select("section.carscore-fault"):
        waiting = pending_fault(sec, url)
        if waiting is not None:
            pending.append(waiting)
        else:
            faults.append(parse_fault(sec, url))
    hero = need(main.select_one("h1.carscore-hero__title"), url, "araç başlığı")
    return Vehicle(
        url=url, make=text(need(hero.select_one(".carscore-hero__make"), url, "marka/model")),
        trim=text(need(hero.select_one(".carscore-hero__trim"), url, "versiyon")),
        carscore=need_int(verdict.select_one(".carscore-verdict__number"), url, "CarScore"),
        verdict=text(verdict.select_one(".carscore-verdict__label")), opinions=int(om.group(1)),
        rating=to_dec(text(rating_el)) if rating_el else None, rating_count=int(rc.group(1)) if rc else None,
        repurchase_pct=repurchase, meters=meters, sources=sources,
        strengths=_bars(plus, url, "güçlü yönler"), complaints=_bars(minus, url, "şikâyetler"),
        specs=specs, consumption=consumption, participants=participants, stats=stats,
        faults=faults, low_data=low_data, pending_faults=pending)


# ------------------------------------------------------------------ cross checks
def spotlight_matches(spot: Spotlight, fault: FaultReport) -> list[str]:
    """The hub card and the unit's fault section must show the same numbers."""
    p = []
    if (spot.reports, spot.with_issue) != (fault.reports, fault.with_issue):
        p.append(f"{spot.unit}: ana sayfada {spot.with_issue}/{spot.reports}, ayrıntıda "
                 f"{fault.with_issue}/{fault.reports}")
    return p


def same_fault(a: FaultReport, b: FaultReport) -> list[str]:
    """The same unit's fault section on two pages (e.g. gearbox page vs vehicle page)."""
    if (a.reports, a.with_issue, [asdict(o) for o in a.outcomes]) != (b.reports, b.with_issue, [asdict(o) for o in b.outcomes]):
        return [f"{a.unit}: {a.source_url} ile {b.source_url} farklı sayı gösteriyor"]
    return []


# ------------------------------------------------------------------ snapshot
@dataclass
class Snapshot:
    taken_at: str
    hub: Optional[Hub] = None
    faults: dict[str, FaultReport] = field(default_factory=dict)      # unit -> report
    units: dict[str, UnitPage] = field(default_factory=dict)          # url -> page
    vehicles: dict[str, Vehicle] = field(default_factory=dict)        # url -> vehicle
    problems: dict[str, list[str]] = field(default_factory=dict)      # subject (unit/url) -> problems
    errors: list[str] = field(default_factory=list)                   # fetch/parse errors

    def ok(self, subject: str) -> bool:
        return not self.problems.get(subject)

    def to_dict(self) -> dict:
        return asdict(self)


def _fault_from_dict(d: dict) -> FaultReport:
    return FaultReport(**{**d, "outcomes": [Outcome(**o) for o in d["outcomes"]],
                          "facts": [FaultFact(**f) for f in d["facts"]]})


def snapshot_from_dict(d: dict) -> Snapshot:
    """Only what drift checks and data packs need: faults and vehicles."""
    snap = Snapshot(d.get("taken_at", ""))
    snap.faults = {k: _fault_from_dict(v) for k, v in d.get("faults", {}).items()}
    for k, v in d.get("vehicles", {}).items():
        snap.vehicles[k] = Vehicle(**{**v, "strengths": [Outcome(**o) for o in v["strengths"]],
                                      "complaints": [Outcome(**o) for o in v["complaints"]],
                                      "stats": {q: [Outcome(**o) for o in os_] for q, os_ in v["stats"].items()},
                                      "faults": [_fault_from_dict(f) for f in v["faults"]]})
    if d.get("hub"):
        h = d["hub"]
        snap.hub = Hub(h["url"], h["stats"], [Spotlight(**s) for s in h["spotlights"]], [Lead(**x) for x in h["leads"]])
    snap.problems = {k: list(v) for k, v in d.get("problems", {}).items()}
    snap.errors = list(d.get("errors", []))
    return snap


def drift(previous: Optional[Snapshot], current: Snapshot, s: CarScoreSettings = CarScoreSettings()) -> dict[str, list[str]]:
    """Report counts only grow. A drop means the source (or our parser) changed: that subject waits."""
    out: dict[str, list[str]] = {}
    if previous is None:
        return out
    for unit, now in current.faults.items():
        before = previous.faults.get(unit)
        if before and before.reports - now.reports > s.max_report_drop:
            out.setdefault(unit, []).append(f"{unit}: bildirim {before.reports} -> {now.reports} (geriledi)")
    for url, now in current.vehicles.items():
        before = previous.vehicles.get(url)
        if before and before.opinions - now.opinions > s.max_report_drop:
            out.setdefault(url, []).append(f"{now.name}: görüş {before.opinions} -> {now.opinions} (geriledi)")
    return out


def latest_snapshot(directory: str, before_day: str) -> Optional[Snapshot]:
    files = sorted(p for p in Path(directory).glob("*.json") if p.stem < before_day)
    if not files:
        return None
    try:
        return snapshot_from_dict(json.loads(files[-1].read_text(encoding="utf-8")))
    except (ValueError, KeyError, TypeError) as exc:
        log.warning("önceki anlık görüntü okunamadı (%s): %s", files[-1], exc)
        return None


# ------------------------------------------------------------------ collector
class CarScoreSource:
    def __init__(self, fetcher, settings: CarScoreSettings = CarScoreSettings(),
                 now=lambda: datetime.now(TR)) -> None:
        self.fetcher, self.s, self._now = fetcher, settings, now

    def _get(self, url: str, snap: Snapshot) -> Optional[bytes]:
        r = self.fetcher.fetch(url)
        if not r.ok:
            snap.errors.append(f"{url}: {r.outcome.value} {r.status_code or ''} {r.detail}".strip())
            return None
        return r.content

    def _vehicle(self, url: str, snap: Snapshot) -> Optional[Vehicle]:
        if url in snap.vehicles:
            return snap.vehicles[url]
        if len(snap.vehicles) >= self.s.max_vehicle_pages:
            snap.errors.append(f"{url}: araç sayfası sınırı ({self.s.max_vehicle_pages}) doldu, okunmadı")
            return None
        html = self._get(url, snap)
        if html is None:
            return None
        try:
            v = parse_vehicle(html, url)
        except ParseError as exc:
            snap.errors.append(str(exc))
            return None
        snap.vehicles[url] = v
        snap.problems[url] = v.problems(self.s)
        return v

    def collect(self) -> Snapshot:
        snap = Snapshot(self._now().isoformat(timespec="seconds"))
        hub_url = self.s.base_url + self.s.hub_path
        html = self._get(hub_url, snap)
        if html is None:
            return snap
        try:
            snap.hub = parse_hub(html, hub_url)
        except ParseError as exc:
            snap.errors.append(str(exc))
            return snap

        for spot in snap.hub.spotlights:
            page_html = self._get(spot.url, snap)
            if page_html is None:
                continue
            try:
                unit = parse_unit(page_html, spot.url)
            except ParseError as exc:
                snap.errors.append(str(exc))
                continue
            snap.units[spot.url] = unit
            fault = unit.fault
            if fault is None:
                # engine pages carry no fault section: read it from the first vehicle that uses the engine
                for card in unit.vehicles[:1]:
                    v = self._vehicle(card.url, snap)
                    fault = next((f for f in (v.faults if v else []) if f.unit == spot.unit), None)
            if fault is None:
                snap.problems[spot.unit] = [f"{spot.unit}: arıza ayrıntısı hiçbir sayfada bulunamadı"]
                continue
            snap.faults[spot.unit] = fault
            snap.problems[spot.unit] = fault.problems(self.s) + spotlight_matches(spot, fault)

        for lead in snap.hub.leads:
            self._vehicle(lead.url, snap)

        # a unit's fault section on a vehicle page must equal the one we keep for the unit
        for v in snap.vehicles.values():
            for f in v.faults:
                kept = snap.faults.get(f.unit)
                if kept is not None and kept is not f:
                    diff = same_fault(kept, f)
                    if diff:
                        snap.problems.setdefault(f.unit, []).extend(diff)
        return snap


def save_snapshot(snap: Snapshot, directory: str, day: str) -> Path:
    path = Path(directory) / f"{day}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def print_summary(snap: Snapshot, out=None) -> None:
    out = out or sys.stdout
    print(f"CarScore anlık görüntü {snap.taken_at}", file=out)
    if snap.hub:
        print("  site: " + ", ".join(f"{v} {k}" for k, v in snap.hub.stats.items()), file=out)
    for unit, f in snap.faults.items():
        flag = "OK " if snap.ok(unit) else "SORUN"
        print(f"  [{flag}] arıza {unit}: {f.with_issue}/{f.reports} ({f.title})", file=out)
    for url, v in snap.vehicles.items():
        flag = "OK " if snap.ok(url) else "SORUN"
        print(f"  [{flag}] araç {v.name}: CarScore {v.carscore}, {v.opinions} görüş", file=out)
    for subject, problems in snap.problems.items():
        for p in problems:
            print(f"  [SORUN] {p}", file=out)
    for e in snap.errors:
        print(f"  [HATA] {e}", file=out)


def main(argv: Optional[list[str]] = None) -> int:
    from config import load_settings
    from fetcher import Fetcher
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    s = CarScoreSettings()
    snap = CarScoreSource(Fetcher(load_settings()), s).collect()
    day = snap.taken_at[:10]
    for subject, problems in drift(latest_snapshot(s.snapshot_dir, day), snap, s).items():
        snap.problems.setdefault(subject, []).extend(problems)
    print_summary(snap)
    print(f"  kaydedildi: {save_snapshot(snap, s.snapshot_dir, day)}")
    return 0 if snap.hub and not snap.errors else 1


if __name__ == "__main__":
    sys.exit(main())
