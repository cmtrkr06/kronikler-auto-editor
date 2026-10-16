"""Data models shared by every module (no I/O here)."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar, Optional


class FetchOutcome(str, Enum):
    OK = "ok"
    ROBOTS_DISALLOWED = "robots_disallowed"
    ROBOTS_UNREACHABLE = "robots_unreachable"
    UNAUTHORIZED = "unauthorized_401"
    FORBIDDEN = "forbidden_403"
    CHALLENGE = "bot_challenge"
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited_429"
    SERVER_ERROR = "server_error"
    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    TOO_MANY_REDIRECTS = "too_many_redirects"
    CLIENT_ERROR = "client_error"
    TOO_LARGE = "response_too_large"


@dataclass
class FetchResult:
    url: str
    outcome: FetchOutcome
    status_code: Optional[int] = None
    content: bytes = b""
    attempts: int = 0
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome == FetchOutcome.OK


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TopicType(str, Enum):
    FAULT = "ariza"                 # one gearbox/engine fault, owner reports (first release)
    COMPARE = "karsilastirma"       # two vehicles, owner data side by side (first release)


class TopicStatus(str, Enum):
    PROPOSED = "PROPOSED"           # planner found it and it passed the data gates
    DRAFTED = "DRAFTED"             # sent to Blogger as a draft (never auto-published)
    REJECTED = "REJECTED"           # editor rejected: never proposed again
    NEEDS_UPDATE = "NEEDS_UPDATE"   # drafted before, the CarScore data changed materially since


@dataclass
class ChronicleTopic:
    """One article subject. The planner (code, no AI) creates it from CarScore data; one article per
    key, a later material data change marks it NEEDS_UPDATE instead of producing a second article."""
    kind: ClassVar[str] = "chronicle"   # writer switch (Zapyus used guide/news/feature)
    key: str                            # normalizer.topic_key of the subject, e.g. "ariza-dq200"
    title: str                          # working title handed to the writer
    type: TopicType
    status: TopicStatus
    subject_urls: list[str] = field(default_factory=list)   # CarScore pages the data came from
    sample: int = 0                     # smallest owner sample behind the article (gate input)
    data_hash: str = ""                 # hash of the data the last draft was written from
    reason: str = ""                    # planner's gate message
    editor_note: str = ""
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        d = {f.name: getattr(self, f.name) for f in fields(self)}
        d["type"] = self.type.value
        d["status"] = self.status.value
        d["created_at"] = self.created_at.isoformat()
        d["updated_at"] = self.updated_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChronicleTopic":
        d = dict(data)
        d["type"] = TopicType(d["type"])
        d["status"] = TopicStatus(d["status"])
        for name in ("created_at", "updated_at"):
            d[name] = datetime.fromisoformat(d[name])
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})
