"""Daily per-model request ledger.

The free tier limits requests per model per day and resets at midnight Pacific time
(ai.google.dev/gemini-api/docs/rate-limits). Every API attempt is recorded here before the
next one is allowed, so one step can never spend another step's share and a run never tries
a model the provider has already closed for the day. Stored in the memory store ("quota").
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from config import QuotaSettings
from memory import MemoryStore
from models import utcnow

PACIFIC = ZoneInfo("America/Los_Angeles")  # needs the tzdata package on Windows
META_KEY = "quota"
_EMPTY = {"total": 0, "steps": {}, "exhausted": False}


def quota_day(now: datetime) -> str:
    return now.astimezone(PACIFIC).date().isoformat()


class QuotaLedger:
    def __init__(self, store: MemoryStore, settings: QuotaSettings = QuotaSettings(),
                 now: Callable[[], datetime] = utcnow) -> None:
        self.store = store
        self.settings = settings
        self._now = now

    # ---- decisions
    def check(self, model: str, step: str, cost: int = 1) -> Optional[str]:
        """None if a call costing `cost` requests is allowed, otherwise why not. Read-only."""
        entry = self._data().get(self.today(), {}).get(model, _EMPTY)
        if entry["exhausted"]:
            return "sağlayıcı kotası bugün için doldu"
        limit = self.settings.daily_limits.get(model)
        if limit is not None and entry["total"] + cost > limit:
            return f"günlük üst sınır {limit} doldu"
        step_limit = self.settings.step_limits.get(model, {}).get(step)
        if step_limit is not None and entry["steps"].get(step, 0) + cost > step_limit:
            return f"'{step}' adımının günlük payı {step_limit} doldu"
        return None

    def record(self, model: str, step: str, cost: int = 1) -> None:
        entry = self._entry(model)
        entry["total"] += cost
        entry["steps"][step] = entry["steps"].get(step, 0) + cost
        self._save()

    def mark_exhausted(self, model: str) -> None:
        self._entry(model)["exhausted"] = True
        self._save()

    def today(self) -> str:
        return quota_day(self._now())

    def usage(self, day: Optional[str] = None) -> dict[str, Any]:
        return dict(self._data().get(day or self.today(), {}))

    # ---- storage
    def _data(self) -> dict[str, Any]:
        data = self.store.get_meta(META_KEY)
        if not isinstance(data, dict):
            data = {}
            self.store.set_meta(META_KEY, data)
        return data

    def _entry(self, model: str) -> dict[str, Any]:
        day = self._data().setdefault(self.today(), {})
        return day.setdefault(model, {"total": 0, "steps": {}, "exhausted": False})  # fresh dicts, never _EMPTY

    def _save(self) -> None:
        data = self._data()
        for old in sorted(data)[:-self.settings.history_days]:
            del data[old]
        self.store.set_meta(META_KEY, data)
