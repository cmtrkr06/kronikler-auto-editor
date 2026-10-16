"""The single gateway to the AI provider (Gemini via the official google-genai SDK).

Responsibilities split:
- the SDK does NOT retry (AISettings.sdk_attempts = 1): every retry is a request the provider
  counts, so this module owns all of them - 5xx/408/timeouts, rate limits (429), schema-invalid
  output - and each attempt passes the daily quota ledger;
- it also keeps the per-run call budget and the call/token counters shown in the report.

Every caller gets either a validated Pydantic object or an AIError; nothing else escapes.
The API key never appears in logs or error messages.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Callable, Optional, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from config import AISettings

if TYPE_CHECKING:
    from quota import QuotaLedger

log = logging.getLogger("kronikler.ai")
T = TypeVar("T", bound=BaseModel)


class AIErrorKind(str, Enum):
    CONFIG = "config"              # missing key, unknown model, 400/401/403/404
    RATE_LIMITED = "rate_limited"  # 429 that outlived our waits
    SERVER = "server"              # 5xx/408 after the SDK's own retries
    NETWORK = "network"            # timeout / connection
    INVALID_OUTPUT = "invalid_output"
    BUDGET = "budget"              # per-run call budget exhausted
    UNKNOWN = "unknown"


class AIError(Exception):
    def __init__(self, kind: AIErrorKind, message: str) -> None:
        super().__init__(f"{kind.value}: {message}")
        self.kind = kind


@dataclass
class GenerateResult:
    text: str
    tokens: int = 0


class AIBackend(Protocol):
    """Thin transport. GeminiBackend talks to the API; tests use FakeBackend."""
    def generate_json(self, model: str, prompt: str, schema: dict, system: Optional[str],
                      images: Optional[list[tuple[bytes, str]]] = None) -> GenerateResult: ...
    def embed(self, model: str, texts: list[str], dimensions: int) -> list[list[float]]: ...
    def list_models(self) -> dict[str, set[str]]: ...  # name -> supported actions


# ------------------------------------------------------------- error classification
_RETRY_DELAY_RE = re.compile(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s")
# e.g. "Please retry in 3h44m48s" (interactions API, daily quota) or "retry in 27.5s"
_RETRY_IN_RE = re.compile(r"retry in\s+(?:(\d+)h)?\s*(?:(\d+)m(?!s))?\s*(?:(\d+(?:\.\d+)?)s)?", re.IGNORECASE)


def _status_of(exc: BaseException) -> Optional[int]:
    # interactions errors expose .status_code, models.* errors (google.genai.errors) expose .code
    for attr in ("status_code", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    return None


def retry_after_of(exc: BaseException) -> Optional[float]:
    """Seconds the server asked us to wait: Retry-After header first, then RetryInfo in the body."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        value = headers.get("retry-after")
        if value:
            try:
                return max(0.0, float(value))
            except ValueError:
                pass
    text = str(exc)
    match = _RETRY_DELAY_RE.search(text)
    if match:
        return float(match.group(1))
    match = _RETRY_IN_RE.search(text)
    if match and any(match.groups()):
        h, m, s = match.groups()
        return int(h or 0) * 3600 + int(m or 0) * 60 + float(s or 0)
    return None


def classify(exc: BaseException) -> AIErrorKind:
    status = _status_of(exc)
    if status == 429:
        return AIErrorKind.RATE_LIMITED
    if status is not None and (status >= 500 or status == 408):
        return AIErrorKind.SERVER
    if status in (400, 401, 403, 404):
        return AIErrorKind.CONFIG
    name = type(exc).__name__.lower()
    if "timeout" in name or "connection" in name or isinstance(exc, (TimeoutError, ConnectionError)):
        return AIErrorKind.NETWORK
    return AIErrorKind.UNKNOWN


def redact(text: str, secret: Optional[str]) -> str:
    return text.replace(secret, "***") if secret else text


# ------------------------------------------------------------- client
# Failures another model may not share. Invalid output or a config error would repeat there.
_HAND_OVER = (AIErrorKind.SERVER, AIErrorKind.NETWORK, AIErrorKind.RATE_LIMITED, AIErrorKind.BUDGET)


@dataclass
class AIStats:
    calls: int = 0
    tokens: int = 0
    rate_limit_waits: int = 0
    invalid_outputs: int = 0
    failures: list[str] = field(default_factory=list)


class AIClient:
    def __init__(self, settings: AISettings, backend: AIBackend,
                 sleep: Callable[[float], None] = time.sleep, secret: Optional[str] = None,
                 ledger: Optional["QuotaLedger"] = None) -> None:
        self.settings = settings
        self.backend = backend
        self._sleep = sleep
        self._secret = secret
        self.ledger = ledger
        self.stats = AIStats()
        self.last_model = ""     # the model that produced the last structured() answer

    # ---- public API
    def structured(self, tier: str, prompt: str, schema: type[T], system: Optional[str] = None,
                   step: str = "general", images: Optional[list[tuple[bytes, str]]] = None,
                   fallback: bool = False, avoid_model: Optional[str] = None) -> T:
        """One prompt -> one validated `schema` instance. tier: "fast", "deep" or "check".
        `step` names the pipeline step for the daily quota ledger. `images`: (bytes, mime) pairs.
        `fallback`: a busy or exhausted model hands over to the next model of the tier's chain
        (writer and fact-check only, other steps must not eat that quota); only the last model
        retries server errors. `avoid_model` is never used (the fact-checker passes the writer's model)."""
        primary = self._model_for(tier)
        spares = dict(self.settings.fallback_models).get(tier, ()) if fallback else ()
        models = [m for m in dict.fromkeys((primary, *spares)) if m and m != avoid_model]
        if not models:
            raise self._fail(AIErrorKind.CONFIG, f"{tier}: no model left besides {avoid_model}")
        for i, model in enumerate(models):
            last = i + 1 == len(models)
            try:
                result = self._structured_on(model, prompt, schema, system, step, images,
                                             server_retries=self.settings.max_server_retries if last else 0)
            except AIError as exc:
                if i + 1 < len(models) and exc.kind in _HAND_OVER and not self._budget_spent():
                    log.warning("%s unusable (%s), falling back to %s", model, exc.kind.value, models[i + 1])
                    continue
                raise
            self.last_model = model
            return result
        raise AssertionError("unreachable")

    def _structured_on(self, model: str, prompt: str, schema: type[T], system: Optional[str],
                       step: str, images, server_retries: Optional[int] = None) -> T:
        json_schema = schema.model_json_schema()
        bad_outputs = 0
        while True:
            result = self._call(lambda: self.backend.generate_json(model, prompt, json_schema, system, images),
                                model, step, server_retries=server_retries)
            self.stats.tokens += result.tokens
            try:
                return schema.model_validate_json(result.text)
            except (ValidationError, json.JSONDecodeError, ValueError) as exc:
                self.stats.invalid_outputs += 1
                bad_outputs += 1
                log.warning("invalid %s output from %s (%d/%d): %s", schema.__name__, model,
                            bad_outputs, self.settings.max_invalid_output_retries + 1,
                            (str(exc).splitlines() or [""])[0])
                if bad_outputs > self.settings.max_invalid_output_retries:
                    raise self._fail(AIErrorKind.INVALID_OUTPUT,
                                     f"{schema.__name__} did not validate after {bad_outputs} tries")

    def embed(self, texts: list[str], step: str = "embed") -> list[list[float]]:
        """One vector per text (vectors < 3072 dims come back normalized from the API)."""
        out: list[list[float]] = []
        size = max(1, self.settings.embed_batch_size)
        for start in range(0, len(texts), size):
            chunk = texts[start:start + size]
            vectors = self._call(lambda: self.backend.embed(
                self.settings.embed_model, chunk, self.settings.embed_dimensions),
                self.settings.embed_model, step, cost=len(chunk))   # the provider counts each text
            if len(vectors) != len(chunk):
                raise self._fail(AIErrorKind.INVALID_OUTPUT,
                                 f"embedding count {len(vectors)} != input count {len(chunk)}")
            out.extend(vectors)
        return out

    def verify_models(self) -> list[str]:
        """Problems with the configured models; empty list means all usable."""
        available = self._call(self.backend.list_models)
        needed = [(self.settings.fast_model, "generateContent"),
                  (self.settings.deep_model, "generateContent"),
                  (self.settings.check_model, "generateContent"),
                  (self.settings.embed_model, "embedContent")]
        needed += [(m, "generateContent") for _, chain in self.settings.fallback_models for m in chain
                   if (m, "generateContent") not in needed]
        problems = []
        for name, action in needed:
            actions = available.get(name)
            if actions is None:
                problems.append(f"{name}: not available to this API key")
            elif action not in actions:
                problems.append(f"{name}: does not support {action}")
        return problems

    # ---- internals
    def _budget_spent(self) -> bool:
        """The per-run call budget is shared by every model: no fallback can help."""
        return self.stats.calls >= self.settings.call_budget

    def _model_for(self, tier: str) -> str:
        if tier == "fast":
            return self.settings.fast_model
        if tier == "deep":
            return self.settings.deep_model
        if tier == "check":
            return self.settings.check_model
        raise ValueError(f"unknown model tier {tier!r}")

    def _call(self, fn: Callable[[], T], model: Optional[str] = None, step: str = "general",
              cost: int = 1, server_retries: Optional[int] = None) -> T:
        """`model` None = a call that uses no generation quota (model listing). `cost`: requests the
        provider counts for one attempt (embeddings: one per input text). `server_retries`: limit
        for 5xx/network retries (default max_server_retries; 0 when a fallback model is waiting)."""
        max_server = self.settings.max_server_retries if server_retries is None else server_retries
        waits = 0
        server_retries = 0
        while True:
            if self.stats.calls >= self.settings.call_budget:
                raise self._fail(AIErrorKind.BUDGET, f"call budget {self.settings.call_budget} used up")
            if self.ledger is not None and model is not None:
                reason = self.ledger.check(model, step, cost)
                if reason:
                    raise self._fail(AIErrorKind.BUDGET, f"{model} / {step}: {reason}")
                self.ledger.record(model, step, cost)  # every attempt counts on the provider side
            self.stats.calls += 1
            try:
                return fn()
            except AIError:
                raise
            except Exception as exc:  # SDK raises several unrelated hierarchies
                kind = classify(exc)
                asked = retry_after_of(exc) if kind == AIErrorKind.RATE_LIMITED else None
                if asked is not None and asked > self.settings.rate_limit_wait_cap:
                    # a quota (e.g. requests per day) not a burst limit: waiting cannot help
                    if self.ledger is not None and model is not None:
                        self.ledger.mark_exhausted(model)
                    raise self._fail(kind, f"quota exhausted, server asks to wait {asked / 3600:.1f}h - "
                                     + redact(str(exc), self._secret)[:200])
                if kind == AIErrorKind.RATE_LIMITED and waits < self.settings.max_rate_limit_waits:
                    waits += 1
                    self.stats.rate_limit_waits += 1
                    delay = asked if asked is not None else self.settings.rate_limit_default_wait
                    log.warning("rate limited, waiting %.0fs (%d/%d)", delay, waits,
                                self.settings.max_rate_limit_waits)
                    self._sleep(delay)
                    continue
                if (kind in (AIErrorKind.SERVER, AIErrorKind.NETWORK)
                        and server_retries < max_server):
                    delays = self.settings.server_retry_delays or (5.0,)
                    delay = delays[min(server_retries, len(delays) - 1)]
                    server_retries += 1
                    log.warning("%s error, retrying in %.0fs (%d/%d)", kind.value, delay, server_retries,
                                max_server)
                    self._sleep(delay)
                    continue
                raise self._fail(kind, redact(f"{type(exc).__name__}: {exc}", self._secret)[:300])

    def _fail(self, kind: AIErrorKind, message: str) -> AIError:
        self.stats.failures.append(f"{kind.value}: {message}")
        log.error("AI call failed - %s: %s", kind.value, message)
        return AIError(kind, message)


# ------------------------------------------------------------- real backend
class GeminiBackend:
    """google-genai >= 2.28 (verified 2026-10-02). Imported lazily so stage-1 runs without it."""

    def __init__(self, api_key: str, settings: AISettings) -> None:
        from google import genai
        from google.genai import types
        self._types = types
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=int(settings.request_timeout * 1000),  # milliseconds
                retry_options=types.HttpRetryOptions(
                    attempts=settings.sdk_attempts,
                    http_status_codes=[408, 500, 502, 503, 504],  # 429 is handled by AIClient
                ),
            ),
        )

    def generate_json(self, model, prompt, schema, system, images=None):
        # image parts as in ai.google.dev/gemini-api/docs/image-understanding (inline base64)
        content = prompt if not images else [{"type": "text", "text": prompt}] + [
            {"type": "image", "data": base64.b64encode(data).decode("ascii"), "mime_type": mime}
            for data, mime in images]
        kwargs = {"model": model, "input": content, "store": False,
                  "response_format": {"type": "text", "mime_type": "application/json", "schema": schema}}
        if system:
            kwargs["system_instruction"] = system
        interaction = self._client.interactions.create(**kwargs)
        usage = getattr(interaction, "usage", None)
        return GenerateResult(interaction.output_text or "", int(getattr(usage, "total_tokens", 0) or 0))

    def embed(self, model, texts, dimensions):
        # gemini-embedding-2 aggregates a plain list into ONE vector; one Content per text
        # gives one vector per text (ai.google.dev/gemini-api/docs/embeddings).
        t = self._types
        result = self._client.models.embed_content(
            model=model,
            contents=[t.Content(parts=[t.Part.from_text(text=x)]) for x in texts],
            config={"output_dimensionality": dimensions},
        )
        return [list(e.values) for e in result.embeddings]

    def list_models(self):
        return {m.name.removeprefix("models/"): set(m.supported_actions or [])
                for m in self._client.models.list()}


def create_ai_client(settings: AISettings, api_key: Optional[str],
                     ledger: Optional["QuotaLedger"] = None) -> AIClient:
    if not api_key:
        raise AIError(AIErrorKind.CONFIG, "GEMINI_API_KEY is not set (.env)")
    return AIClient(settings, GeminiBackend(api_key, settings), secret=api_key, ledger=ledger)
