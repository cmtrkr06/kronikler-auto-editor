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
