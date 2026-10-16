"""[P] PLANNER - which articles the CarScore data can carry today. Pure code, no AI.

First release (user decision 2026-10-16): fault chronicles and comparisons.
  FAULT    one unit from the hub's chronic-issue list, at least `min_fault_reports` reports
  COMPARE  two class leaders of the same body type, both with at least `min_compare_opinions` opinions

Gates: a subject with a parse/consistency/drift problem is never a candidate; a REJECTED topic is
never proposed again; a DRAFTED topic comes back only as NEEDS_UPDATE when its sample grew by
`update_growth` (one article per subject, updated instead of repeated).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from itertools import combinations
from typing import Optional

from carscore_source import Snapshot
from config import CarScoreSettings
from data_pack import ResearchPack, compare_pack, fault_pack
from memory import MemoryStore
from models import ChronicleTopic, TopicStatus, TopicType
from normalizer import topic_key

UPDATE_GROWTH = 1.3          # plan section 4: e.g. +30% reports -> update draft of the same article


@dataclass
class Candidate:
    topic: ChronicleTopic
    pack: Optional[ResearchPack]
    ok: bool
    reason: str               # why it is (not) a candidate
    action: str = "new"       # "new" | "update" | "skip"


def pack_hash(pack: ResearchPack) -> str:
    return hashlib.sha256(pack.render().encode("utf-8")).hexdigest()[:16]


def fault_key(unit: str) -> str:
    return topic_key(f"arıza {unit}")


def compare_key(url_a: str, url_b: str, label: str) -> str:
    """Order-independent; readable prefix + hash so two long names never collide after truncation."""
    digest = hashlib.sha1("|".join(sorted([url_a, url_b])).encode("utf-8")).hexdigest()[:8]
    return f"{topic_key('karşılaştırma ' + label, max_len=60)}-{digest}"


def _decide(topic: ChronicleTopic, store: Optional[MemoryStore]) -> tuple[str, str]:
    old = store.get_topic(topic.key) if store else None
    if old is None:
        return "new", ""
    if old.status == TopicStatus.REJECTED:
        return "skip", "editör reddetti"
    if old.status in (TopicStatus.DRAFTED, TopicStatus.NEEDS_UPDATE):
        if old.data_hash == topic.data_hash:
            return "skip", "veri değişmedi, zaten yazıldı"
        if topic.sample >= old.sample * UPDATE_GROWTH:
            return "update", f"örneklem {old.sample} -> {topic.sample}: güncelleme taslağı"
        return "skip", f"zaten yazıldı; örneklem {old.sample} -> {topic.sample}, güncelleme eşiği %{round((UPDATE_GROWTH - 1) * 100)}"
    return "new", ""


def candidates(snap: Snapshot, store: Optional[MemoryStore] = None,
               s: CarScoreSettings = CarScoreSettings()) -> list[Candidate]:
    out: list[Candidate] = []
    if snap.hub is None:
        return out

    for spot in snap.hub.spotlights:
        key = fault_key(spot.unit)
        title = f"{spot.unit} {spot.label}: sahiplerin {spot.reports} bildiriminin kroniği"
        topic = ChronicleTopic(key, title, TopicType.FAULT, TopicStatus.PROPOSED, [spot.url], spot.reports)
        fault = snap.faults.get(spot.unit)
        if fault is None or not snap.ok(spot.unit):
            out.append(Candidate(topic, None, False, "; ".join(snap.problems.get(spot.unit, ["veri yok"]))))
            continue
        if fault.reports < s.min_fault_reports:
            out.append(Candidate(topic, None, False, f"{fault.reports} bildirim < eşik {s.min_fault_reports}"))
            continue
        pack = fault_pack(key, title, spot, fault, snap.units.get(spot.url))
        topic.data_hash, topic.sample = pack_hash(pack), fault.reports
        topic.subject_urls = list(dict.fromkeys([spot.url, fault.source_url]))
        action, why = _decide(topic, store)
        out.append(Candidate(topic, pack, action != "skip", why or f"{fault.reports} bildirim", action))

    by_body: dict[str, list] = {}
    for lead in snap.hub.leads:
        by_body.setdefault(lead.body, []).append(lead)
    for body, leads in by_body.items():
        for la, lb in combinations(leads, 2):
            label = f"{la.name} {lb.name}"
            key = compare_key(la.url, lb.url, label)
            title = f"{la.name} {la.config} mi, {lb.name} {lb.config} mi? Sahip verisiyle ({body})"
            topic = ChronicleTopic(key, title, TopicType.COMPARE, TopicStatus.PROPOSED, [la.url, lb.url],
                                   min(la.opinions, lb.opinions))
            va, vb = snap.vehicles.get(la.url), snap.vehicles.get(lb.url)
            if va is None or vb is None or not (snap.ok(la.url) and snap.ok(lb.url)):
                missing = [lead.name for lead, v in ((la, va), (lb, vb)) if v is None or not snap.ok(lead.url)]
                out.append(Candidate(topic, None, False, "veri yok/sorunlu: " + ", ".join(missing)))
                continue
            low = min(va.opinions, vb.opinions)
            if low < s.min_compare_opinions:
                out.append(Candidate(topic, None, False, f"en az görüş {low} < eşik {s.min_compare_opinions}"))
                continue
            pack = compare_pack(key, title, va, vb)
            topic.data_hash, topic.sample = pack_hash(pack), low
            action, why = _decide(topic, store)
            out.append(Candidate(topic, pack, action != "skip", why or f"{va.opinions} ve {vb.opinions} görüş", action))
    return out


def print_plan(cands: list[Candidate], out=None) -> None:
    import sys
    out = out or sys.stdout
    ready = [c for c in cands if c.ok]
    print(f"Aday konular: {len(ready)} yazılabilir / {len(cands)} incelendi", file=out)
    for c in sorted(cands, key=lambda c: (not c.ok, c.topic.type.value, -c.topic.sample)):
        mark = {"new": "YENİ", "update": "GÜNCELLE"}.get(c.action, "-") if c.ok else "ELENDİ"
        size = f", paket {c.pack.total_chars} karakter/{len(c.pack.passages)} parça" if c.pack else ""
        print(f"  [{mark}] {c.topic.type.value}: {c.topic.title}  ({c.reason}{size})", file=out)
