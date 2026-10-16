import logging

import pytest
from pydantic import BaseModel

from ai_client import (AIClient, AIError, AIErrorKind, GenerateResult, classify, create_ai_client,
                       redact, retry_after_of)
from config import AISettings, load_ai_settings
from fakes import FakeBackend, HttpError


class Verdict(BaseModel):
    relevant: bool
    reason: str


OK = '{"relevant": true, "reason": "CarPlay ile ilgili"}'


def client(backend, **overrides):
    sleeps: list[float] = []
    c = AIClient(AISettings(**overrides), backend, sleep=sleeps.append, secret="SECRET-KEY-123")
    return c, sleeps


# ---- structured output
def test_valid_output_is_parsed_and_counted():
    b = FakeBackend(generate=[GenerateResult(OK, tokens=71)])
    c, _ = client(b)
    v = c.structured("fast", "prompt", Verdict, system="sys")
    assert v == Verdict(relevant=True, reason="CarPlay ile ilgili")
    assert c.stats.calls == 1 and c.stats.tokens == 71
    assert b.prompts == [("gemini-3.5-flash-lite", "prompt", "sys")]


def test_tier_selects_model():
    b = FakeBackend(generate=[OK])
    c, _ = client(b)
    c.structured("deep", "p", Verdict)
    assert b.prompts[0][0] == "gemini-3.8-flash"
    with pytest.raises(ValueError):
        c.structured("pro", "p", Verdict)


def test_turkish_text_survives_round_trip():
    b = FakeBackend(generate=['{"relevant": false, "reason": "Şanzıman değil, ığdır çöğüş"}'])
    c, _ = client(b)
    assert c.structured("fast", "p", Verdict).reason == "Şanzıman değil, ığdır çöğüş"


@pytest.mark.parametrize("bad", ["not json", '{"relevant": "maybe"}', '{"reason": "x"}', ""])
def test_invalid_output_retried_then_succeeds(bad):
    c, _ = client(FakeBackend(generate=[bad, OK]))
    assert c.structured("fast", "p", Verdict).relevant is True
    assert c.stats.invalid_outputs == 1 and c.stats.calls == 2


def test_invalid_output_gives_up_after_limit():
    c, _ = client(FakeBackend(generate=["x", "y", "z"]), max_invalid_output_retries=2)
    with pytest.raises(AIError) as e:
        c.structured("fast", "p", Verdict)
    assert e.value.kind == AIErrorKind.INVALID_OUTPUT
    assert c.stats.calls == 3


# ---- rate limits / errors
def test_429_waits_retry_after_then_succeeds():
    c, sleeps = client(FakeBackend(generate=[HttpError(429, retry_after="12"), OK]))
    assert c.structured("fast", "p", Verdict).relevant
    assert sleeps == [12.0] and c.stats.rate_limit_waits == 1


def test_429_retry_delay_from_body():
    err = HttpError(429, "RESOURCE_EXHAUSTED ... 'retryDelay': '45s'")
    c, sleeps = client(FakeBackend(generate=[err, OK]), rate_limit_wait_cap=90)
    c.structured("fast", "p", Verdict)
    assert sleeps == [45.0]


def test_429_daily_quota_fails_fast_without_waiting():
    # real message seen 2026-10-02 on the free tier
    err = HttpError(429, "Rate limit exceeded for model gemini-3.8-flash (limit: 20 requests per day "
                         "on Free Tier). Please retry in 3h44m48s or upgrade your tier")
    c, sleeps = client(FakeBackend(generate=[err]))
    with pytest.raises(AIError) as e:
        c.structured("deep", "p", Verdict)
    assert e.value.kind == AIErrorKind.RATE_LIMITED and sleeps == []
    assert "3.7h" in str(e.value) and c.stats.calls == 1


def test_429_without_hint_uses_default_wait():
    c, sleeps = client(FakeBackend(generate=[HttpError(429), OK]))
    c.structured("fast", "p", Verdict)
    assert sleeps == [30.0]


def test_429_gives_up_after_max_waits():
    b = FakeBackend(generate=[HttpError(429), HttpError(429), HttpError(429)])
    c, sleeps = client(b, max_rate_limit_waits=2)
    with pytest.raises(AIError) as e:
        c.structured("fast", "p", Verdict)
    assert e.value.kind == AIErrorKind.RATE_LIMITED and len(sleeps) == 2


@pytest.mark.parametrize("exc,kind", [
    (HttpError(404), AIErrorKind.CONFIG),
    (HttpError(401, attr="code"), AIErrorKind.CONFIG),
    (RuntimeError("weird"), AIErrorKind.UNKNOWN),
])
def test_client_errors_fail_fast(exc, kind):
    c, sleeps = client(FakeBackend(generate=[exc]))
    with pytest.raises(AIError) as e:
        c.structured("fast", "p", Verdict)
    assert e.value.kind == kind and c.stats.calls == 1 and sleeps == []


@pytest.mark.parametrize("exc,kind", [
    (HttpError(503), AIErrorKind.SERVER),
    (HttpError(500, attr="code"), AIErrorKind.SERVER),
    (TimeoutError("read timed out"), AIErrorKind.NETWORK),
])
def test_server_errors_are_retried_by_us_then_given_up(exc, kind):
    # the SDK does not retry (sdk_attempts=1), so every attempt is visible and counted here
    c, sleeps = client(FakeBackend(generate=[exc, exc, exc]), max_server_retries=2)
    with pytest.raises(AIError) as e:
        c.structured("fast", "p", Verdict)
    assert e.value.kind == kind and c.stats.calls == 3 and sleeps == [20.0, 60.0]


def test_server_error_then_success():
    c, sleeps = client(FakeBackend(generate=[HttpError(503), OK]), max_server_retries=2)
    assert c.structured("fast", "p", Verdict).relevant and c.stats.calls == 2 and sleeps == [20.0]


def test_by_default_a_busy_model_is_not_retried():
    # 2026-10-07: retries on 503 spent the whole free-tier day; one attempt, then hand over / stop
    c, sleeps = client(FakeBackend(generate=[HttpError(503), OK]))
    with pytest.raises(AIError) as e:
        c.structured("fast", "p", Verdict)
    assert e.value.kind == AIErrorKind.SERVER and c.stats.calls == 1 and sleeps == []


def test_sdk_retries_are_disabled_by_default():
    assert AISettings().sdk_attempts == 1


def test_classify_and_retry_after_helpers():
    assert classify(HttpError(429, attr="code")) == AIErrorKind.RATE_LIMITED
    assert classify(HttpError(408)) == AIErrorKind.SERVER
    assert retry_after_of(HttpError(429, retry_after="abc")) is None
    assert retry_after_of(RuntimeError("no hint")) is None
    assert retry_after_of(RuntimeError("Please retry in 27.5s")) == 27.5
    assert retry_after_of(RuntimeError("retry in 2m5s")) == 125
    assert retry_after_of(RuntimeError("retry in 1h")) == 3600


# ---- budget
def test_budget_stops_calls():
    c, _ = client(FakeBackend(generate=[OK, OK, OK]), call_budget=2)
    c.structured("fast", "p", Verdict)
    c.structured("fast", "p", Verdict)
    with pytest.raises(AIError) as e:
        c.structured("fast", "p", Verdict)
    assert e.value.kind == AIErrorKind.BUDGET and c.stats.calls == 2


def test_retries_count_against_budget():
    c, _ = client(FakeBackend(generate=["bad", OK]), call_budget=1)
    with pytest.raises(AIError) as e:
        c.structured("fast", "p", Verdict)
    assert e.value.kind == AIErrorKind.BUDGET


# ---- secrets
def test_api_key_never_reaches_error_or_log(caplog):
    caplog.set_level(logging.DEBUG)
    c, _ = client(FakeBackend(generate=[RuntimeError("bad request key=SECRET-KEY-123")]))
    with pytest.raises(AIError) as e:
        c.structured("fast", "p", Verdict)
    assert "SECRET-KEY-123" not in str(e.value)
    assert "SECRET-KEY-123" not in caplog.text
    assert "SECRET-KEY-123" not in " ".join(c.stats.failures)


def test_redact_and_missing_key():
    assert redact("a SECRET b", "SECRET") == "a *** b"
    assert redact("a", None) == "a"
    with pytest.raises(AIError) as e:
        create_ai_client(AISettings(), None)
    assert e.value.kind == AIErrorKind.CONFIG


# ---- embeddings
def test_embed_one_vector_per_text_in_batches():
    b = FakeBackend(embed=[lambda texts: [[float(len(t))] for t in texts]] * 3)
    c, _ = client(b, embed_batch_size=2)
    vectors = c.embed(["a", "bb", "ccc", "dddd", "e"])
    assert vectors == [[1.0], [2.0], [3.0], [4.0], [1.0]]
    assert [len(x) for x in b.embed_calls] == [2, 2, 1] and c.stats.calls == 3


def test_embed_count_mismatch_is_invalid_output():
    # the aggregation trap: a plain list sent to gemini-embedding-2 returns ONE vector
    c, _ = client(FakeBackend(embed=[[[0.1, 0.2]]]))
    with pytest.raises(AIError) as e:
        c.embed(["first", "second"])
    assert e.value.kind == AIErrorKind.INVALID_OUTPUT


# ---- model verification
MODELS = {"gemini-3.5-flash-lite": {"generateContent"}, "gemini-3.8-flash": {"generateContent"},
          "gemini-3.7-flash": {"generateContent"}, "gemini-3.6-flash": {"generateContent"},
          "gemini-3.5-flash": {"generateContent"}, "gemini-3-flash-preview": {"generateContent"},
          "gemini-embedding-2": {"embedContent"}}


def test_verify_models_all_ok():
    c, _ = client(FakeBackend(models=MODELS))
    assert c.verify_models() == []


def test_verify_models_reports_missing_and_wrong_action():
    models = dict(MODELS, **{"gemini-embedding-2": {"generateContent"}})
    del models["gemini-3.8-flash"]
    problems = client(FakeBackend(models=models))[0].verify_models()
    assert any("gemini-3.8-flash" in p and "not available" in p for p in problems)
    assert any("gemini-embedding-2" in p and "embedContent" in p for p in problems)


def test_verify_models_listing_error_is_ai_error():
    c, _ = client(FakeBackend(models=HttpError(403)))
    with pytest.raises(AIError) as e:
        c.verify_models()
    assert e.value.kind == AIErrorKind.CONFIG


# ---- settings
def test_ai_settings_from_env():
    s = load_ai_settings({"KRONIKLER_FAST_MODEL": "m-fast", "KRONIKLER_AI_CALL_BUDGET": "5"})
    assert s.fast_model == "m-fast" and s.call_budget == 5 and s.deep_model == AISettings().deep_model


# ---- main.py --check-ai
def test_check_ai_without_key_fails_cleanly(monkeypatch, capsys):
    import main
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert main.check_ai() == 1
    assert "GEMINI_API_KEY" in capsys.readouterr().out


def test_check_ai_reports_problems_and_success(monkeypatch, capsys):
    import ai_client
    import main
    monkeypatch.setattr(ai_client, "create_ai_client",
                        lambda s, k: AIClient(s, FakeBackend(models=MODELS)))
    assert main.check_ai() == 0
    assert "[OK]" in capsys.readouterr().out
    monkeypatch.setattr(ai_client, "create_ai_client",
                        lambda s, k: AIClient(s, FakeBackend(models={})))
    assert main.check_ai() == 1
    assert "not available" in capsys.readouterr().out
