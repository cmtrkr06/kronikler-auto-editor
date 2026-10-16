from datetime import datetime, timedelta, timezone

import pytest
from pydantic import BaseModel

from ai_client import AIClient, AIError, AIErrorKind
from config import AISettings, QuotaSettings
from fakes import FakeBackend, HttpError
from memory import InMemoryStore, JsonFileStore
from quota import QuotaLedger, quota_day

UTC = timezone.utc


class V(BaseModel):
    ok: bool


OK = '{"ok": true}'


def ledger(store=None, now=datetime(2026, 10, 3, 12, tzinfo=UTC), **settings):
    clock = {"now": now}
    return QuotaLedger(store or InMemoryStore(), QuotaSettings(**settings), now=lambda: clock["now"]), clock


# ---- day boundary
def test_quota_day_is_pacific():
    # 06:59 UTC on Oct 3 is still Oct 2 in Los Angeles (UTC-7 in October)
    assert quota_day(datetime(2026, 10, 3, 6, 59, tzinfo=UTC)) == "2026-10-02"
    assert quota_day(datetime(2026, 10, 3, 7, 0, tzinfo=UTC)) == "2026-10-03"


def test_counts_reset_on_new_pacific_day():
    q, clock = ledger(daily_limits={"m": 2}, step_limits={})
    q.record("m", "s"); q.record("m", "s")
    assert q.check("m", "s") is not None
    clock["now"] += timedelta(days=1)
    assert q.check("m", "s") is None and q.usage() == {}


# ---- limits
def test_daily_and_step_limits():
    q, _ = ledger(daily_limits={"deep": 3}, step_limits={"deep": {"cluster_confirm": 1}})
    assert q.check("deep", "cluster_confirm") is None
    q.record("deep", "cluster_confirm")
    assert "cluster_confirm" in q.check("deep", "cluster_confirm")
    assert q.check("deep", "writer") is None          # other steps keep their share
    q.record("deep", "writer"); q.record("deep", "writer")
    assert "3" in q.check("deep", "writer")
    assert q.check("other-model", "writer") is None   # no local cap configured


def test_exhausted_blocks_until_next_day():
    q, clock = ledger(daily_limits={})
    q.mark_exhausted("m")
    assert "kota" in q.check("m", "x")
    clock["now"] += timedelta(days=1)
    assert q.check("m", "x") is None


def test_history_is_pruned_and_persisted(tmp_path):
    path = tmp_path / "m.json"
    store = JsonFileStore(str(path))
    q, clock = ledger(store, history_days=2)
    for _ in range(4):
        q.record("m", "s")
        clock["now"] += timedelta(days=1)
    store.flush()
    data = JsonFileStore(str(path)).get_meta("quota")
    assert len(data) == 2 and all(v["m"]["total"] == 1 for v in data.values())


# ---- through the AI client
def client(q, generate):
    return AIClient(AISettings(), FakeBackend(generate=generate), sleep=lambda s: None, ledger=q)


def test_client_records_each_attempt_with_step():
    q, _ = ledger(daily_limits={})
    c = client(q, ["bad json", OK])
    c.structured("deep", "p", V, step="writer")
    e = q.usage()["gemini-3.8-flash"]
    assert e["total"] == 2 and e["steps"] == {"writer": 2}


def test_client_refuses_when_share_used_without_calling_backend():
    q, _ = ledger(daily_limits={}, step_limits={"gemini-3.8-flash": {"topic_propose": 1}})
    c = client(q, [OK])
    c.structured("deep", "p", V, step="topic_propose")
    with pytest.raises(AIError) as e:
        c.structured("deep", "p", V, step="topic_propose")  # no response scripted: must not call
    assert e.value.kind == AIErrorKind.BUDGET and "topic_propose" in str(e.value)


def test_daily_quota_429_marks_model_exhausted():
    q, _ = ledger(daily_limits={})
    err = HttpError(429, "limit: 20 requests per day on Free Tier. Please retry in 3h44m48s")
    c = client(q, [err])
    with pytest.raises(AIError):
        c.structured("deep", "p", V, step="writer")
    assert q.usage()["gemini-3.8-flash"]["exhausted"] is True
    with pytest.raises(AIError) as e:
        c.structured("deep", "p", V, step="writer")
    assert e.value.kind == AIErrorKind.BUDGET and c.stats.calls == 1


def test_burst_429_does_not_mark_exhausted():
    q, _ = ledger(daily_limits={})
    c = client(q, [HttpError(429, retry_after="5"), OK])
    c.structured("fast", "p", V, step="scoring")
    e = q.usage()["gemini-3.5-flash-lite"]
    assert e["exhausted"] is False and e["total"] == 2


def test_check_tier_uses_check_model():
    q, _ = ledger(daily_limits={})
    backend = FakeBackend(generate=[OK])
    AIClient(AISettings(), backend, sleep=lambda s: None, ledger=q).structured("check", "p", V, step="fact_check")
    assert backend.prompts[0][0] == "gemini-3.7-flash" and "gemini-3.7-flash" in q.usage()


def test_model_listing_does_not_touch_ledger():
    q, _ = ledger(daily_limits={})
    c = AIClient(AISettings(), FakeBackend(models={}), sleep=lambda s: None, ledger=q)
    c.verify_models()
    assert q.usage() == {}


def test_default_settings_leave_the_writer_whole_day():
    s = QuotaSettings()
    assert s.daily_limits["gemini-3.8-flash"] < 20         # below the provider's free-tier limit
    assert s.step_limits == {}                             # no AI step besides writer and fact-check


def test_print_quota(capsys):
    import main
    store = InMemoryStore()
    q = QuotaLedger(store)
    q.record("gemini-3.8-flash", "writer")
    main.print_quota(store)
    out = capsys.readouterr().out
    assert "gemini-3.8-flash: 1 / 18  (writer 1)" in out


def test_every_server_retry_is_recorded_in_the_ledger():
    q, _ = ledger(daily_limits={})
    c = AIClient(AISettings(max_server_retries=2), FakeBackend(generate=[HttpError(503), HttpError(503), OK]),
                 sleep=lambda s: None, ledger=q)
    c.structured("deep", "p", V, step="writer")
    assert q.usage()["gemini-3.8-flash"]["total"] == 3


def test_embeddings_are_counted_per_input_text():
    q, _ = ledger(daily_limits={"gemini-embedding-2": 5})
    backend = FakeBackend(embed=[lambda texts: [[1.0] for _ in texts]] * 3)
    c = AIClient(AISettings(embed_batch_size=3), backend, sleep=lambda s: None, ledger=q)
    c.embed(["a", "b", "c"], step="cluster_embed")
    assert q.usage()["gemini-embedding-2"]["total"] == 3
    with pytest.raises(AIError) as e:          # 3 more would exceed the cap of 5
        c.embed(["d", "e", "f"], step="cluster_embed")
    assert e.value.kind == AIErrorKind.BUDGET and len(backend.embed_calls) == 1


def test_default_caps_stay_below_provider_free_tier_limits():
    provider = {"gemini-3.8-flash": 20, "gemini-3.7-flash": 20, "gemini-3.5-flash-lite": 500,
                "gemini-embedding-2": 1000}
    caps = QuotaSettings().daily_limits
    assert set(caps) == set(provider) and all(caps[m] < provider[m] for m in provider)
