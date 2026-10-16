"""MEMORY - topics, small meta values (quota ledger, daily counts) and a capped log.

Copied from zapyus-auto-editor (e193136) without articles, clusters and the duplicate checker:
Kronikler crawls no news, its subjects come from CarScore data. Backends: in-memory (tests) and a
local JSON file, which GitHub Actions keeps on the `state` branch.
"""
from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

from config import Settings
from models import ChronicleTopic, utcnow

log = logging.getLogger("kronikler.memory")
MAX_LOGS = 500


class MemoryStore(ABC):
    @abstractmethod
    def get_topic(self, key: str) -> Optional[ChronicleTopic]: ...
    @abstractmethod
    def save_topic(self, topic: ChronicleTopic) -> None: ...
    @abstractmethod
    def all_topics(self) -> list[ChronicleTopic]: ...
    @abstractmethod
    def get_meta(self, key: str) -> Any: ...
    @abstractmethod
    def set_meta(self, key: str, value: Any) -> None: ...
    @abstractmethod
    def add_log(self, module: str, level: str, message: str,
                topic_key: Optional[str] = None, details: Optional[dict] = None) -> None: ...

    def flush(self) -> None:
        """Persist pending changes (no-op for volatile stores)."""


class InMemoryStore(MemoryStore):
    def __init__(self) -> None:
        self._topics: dict[str, ChronicleTopic] = {}
        self._meta: dict[str, Any] = {}       # small JSON-safe values (e.g. the quota ledger)
        self.logs: list[dict[str, Any]] = []

    def get_topic(self, key: str) -> Optional[ChronicleTopic]:
        return self._topics.get(key)

    def save_topic(self, topic: ChronicleTopic) -> None:
        self._topics[topic.key] = topic

    def all_topics(self) -> list[ChronicleTopic]:
        return list(self._topics.values())

    def get_meta(self, key: str) -> Any:
        return self._meta.get(key)

    def set_meta(self, key: str, value: Any) -> None:
        self._meta[key] = value

    def add_log(self, module, level, message, topic_key=None, details=None) -> None:
        self.logs.append({"timestamp": utcnow().isoformat(), "module": module, "level": level,
                          "message": message, "topic_key": topic_key, "details": details or {}})
        del self.logs[:-MAX_LOGS]


class JsonFileStore(InMemoryStore):
    """Atomic write; a corrupt file is set aside, not lost."""

    def __init__(self, path: str) -> None:
        super().__init__()
        self.path = Path(path)
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for raw in data.get("topics", {}).values():
                self.save_topic(ChronicleTopic.from_dict(raw))
            self._meta = dict(data.get("meta", {}))
            self.logs = list(data.get("logs", []))[-MAX_LOGS:]
        except (json.JSONDecodeError, ValueError, TypeError, KeyError) as exc:
            backup = self.path.with_suffix(self.path.suffix + ".corrupt")
            os.replace(self.path, backup)
            log.warning("memory file unreadable (%s); moved to %s, starting empty", exc, backup)
            InMemoryStore.__init__(self)

    def flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1,
                   "topics": {t.key: t.to_dict() for t in self._topics.values()},
                   "meta": self._meta,
                   "logs": self.logs}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)


def create_store(settings: Settings) -> MemoryStore:
    if settings.memory_backend == "memory":
        return InMemoryStore()
    if settings.memory_backend == "json":
        return JsonFileStore(settings.memory_path)
    raise ValueError(f"unknown memory backend {settings.memory_backend!r}")
