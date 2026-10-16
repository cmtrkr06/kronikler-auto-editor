"""FETCHER: polite, ethical HTTP fetching (copied from zapyus-auto-editor e193136 crawler.py,
without the feed parsers).

No CAPTCHA/Cloudflare/paywall/login bypass, no proxies, no fake User-Agent. robots.txt is obeyed
(RFC 9309), one domain is never hit faster than per_domain_delay. On 401/403/challenge the page is
skipped, never forced.
"""
from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Optional
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests

from config import API_POLICY_EXCEPTIONS, Settings
from models import FetchOutcome, FetchResult

log = logging.getLogger("kronikler.fetcher")

_ACCEPT = "text/html, application/xhtml+xml, application/xml;q=0.9, */*;q=0.5"
_CHALLENGE_MARKERS = (b"just a moment...", b"cf-chl", b"attention required! | cloudflare",
                      b"challenge-platform")


# ----------------------------------------------------------------- helpers
def parse_retry_after(value: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    """Retry-After: delta-seconds or HTTP-date -> seconds to wait (None if invalid)."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return max(0.0, (when - now).total_seconds())


def looks_like_challenge(headers, body: bytes) -> bool:
    if str(headers.get("cf-mitigated", "")).lower() == "challenge":
        return True
    head = body[:4096].lower()
    return any(m in head for m in _CHALLENGE_MARKERS)


class DomainRateLimiter:
    """Minimum delay between two requests to the same domain."""

    def __init__(self, min_delay: float, time_fn: Callable[[], float] = time.monotonic,
                 sleep_fn: Callable[[float], None] = time.sleep) -> None:
        self.min_delay = min_delay
        self._overrides: dict[str, float] = {}
        self._last: dict[str, float] = {}
        self._time, self._sleep = time_fn, sleep_fn

    def set_delay(self, domain: str, delay: float) -> None:
        self._overrides[domain] = max(delay, self.min_delay)

    def wait(self, domain: str) -> None:
        delay = self._overrides.get(domain, self.min_delay)
        now = self._time()
        last = self._last.get(domain)
        if last is not None:
            remaining = delay - (now - last)
            if remaining > 0:
                self._sleep(remaining)
                now = self._time()
        self._last[domain] = now


class RobotsPolicy:
    """robots.txt per origin (RFC 9309): 200 -> parse, 4xx -> allow, 5xx/error -> disallow."""

    def __init__(self, session, settings: Settings, limiter: DomainRateLimiter,
                 exceptions: tuple = API_POLICY_EXCEPTIONS) -> None:
        self.session, self.settings, self.limiter = session, settings, limiter
        self.exceptions = exceptions
        self._cache: dict[str, Optional[RobotFileParser]] = {}  # None == disallow all

    def check(self, url: str) -> Optional[FetchOutcome]:
        """None if allowed; otherwise ROBOTS_UNREACHABLE or ROBOTS_DISALLOWED.
        URLs under config.API_POLICY_EXCEPTIONS follow that API's own published policy."""
        if any(url.startswith(prefix) for prefix, _ in self.exceptions):
            return None
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._cache:
            self._cache[origin] = self._load(origin, parts.netloc)
        rp = self._cache[origin]
        if rp is None:
            return FetchOutcome.ROBOTS_UNREACHABLE
        if not rp.can_fetch(self.settings.user_agent, url):
            return FetchOutcome.ROBOTS_DISALLOWED
        return None

    def allowed(self, url: str) -> bool:
        return self.check(url) is None

    def _load(self, origin: str, domain: str) -> Optional[RobotFileParser]:
        robots_url = f"{origin}/robots.txt"
        self.limiter.wait(domain)
        try:
            resp = self.session.get(robots_url, headers={"User-Agent": self.settings.user_agent},
                                    timeout=self.settings.request_timeout, allow_redirects=True)
        except requests.RequestException as exc:
            log.warning("robots.txt unreachable for %s (%s) -> treating as disallowed", domain, exc)
            return None
        rp = RobotFileParser()
        if resp.status_code == 200:
            rp.parse(resp.content.decode("utf-8", errors="replace").splitlines())
            delay = rp.crawl_delay(self.settings.user_agent)
            if delay:
                self.limiter.set_delay(domain, float(delay))
            return rp
        if 400 <= resp.status_code < 500:
            rp.parse([])  # no usable robots.txt -> everything allowed
            return rp
        log.warning("robots.txt for %s returned %s -> treating as disallowed", domain, resp.status_code)
        return None


# ----------------------------------------------------------------- fetcher
class Fetcher:
    def __init__(self, settings: Settings, session: Optional[requests.Session] = None,
                 sleep_fn: Callable[[float], None] = time.sleep,
                 time_fn: Callable[[], float] = time.monotonic,
                 random_fn: Callable[[], float] = random.random) -> None:
        self.settings = settings
        if session is None:
            session = requests.Session()
            session.max_redirects = settings.max_redirects
        self.session = session
        self._sleep, self._random = sleep_fn, random_fn
        self.limiter = DomainRateLimiter(settings.per_domain_delay, time_fn, sleep_fn)
        self.robots = RobotsPolicy(session, settings, self.limiter)

    def _backoff(self, attempt: int) -> float:
        base = min(self.settings.backoff_cap, self.settings.backoff_base * (2 ** attempt))
        return base + base * 0.25 * self._random()

    def fetch(self, url: str) -> FetchResult:
        s = self.settings
        domain = urlsplit(url).netloc
        blocked = self.robots.check(url)
        if blocked == FetchOutcome.ROBOTS_DISALLOWED:
            return FetchResult(url, blocked, detail="blocked by robots.txt")
        if blocked == FetchOutcome.ROBOTS_UNREACHABLE:
            return FetchResult(url, blocked,
                               detail="robots.txt could not be read (network/5xx); skipped to be safe")

        attempts = 0
        retries_408 = 0
        last = FetchResult(url, FetchOutcome.NETWORK_ERROR, detail="no attempt made")
        while True:
            attempts += 1
            self.limiter.wait(domain)
            try:
                resp = self.session.get(
                    url, headers={"User-Agent": s.user_agent, "Accept": _ACCEPT},
                    timeout=s.request_timeout, allow_redirects=True)
            except requests.TooManyRedirects:
                return FetchResult(url, FetchOutcome.TOO_MANY_REDIRECTS, attempts=attempts)
            except requests.Timeout as exc:
                last = FetchResult(url, FetchOutcome.TIMEOUT, attempts=attempts, detail=str(exc))
                if not self._can_retry(attempts):
                    return last
                self._sleep(self._backoff(attempts - 1))
                continue
            except requests.RequestException as exc:
                last = FetchResult(url, FetchOutcome.NETWORK_ERROR, attempts=attempts, detail=str(exc))
                if not self._can_retry(attempts):
                    return last
                self._sleep(self._backoff(attempts - 1))
                continue

            code, body = resp.status_code, resp.content or b""
            if 200 <= code < 300:
                if len(body) > s.max_response_bytes:
                    return FetchResult(url, FetchOutcome.TOO_LARGE, code, attempts=attempts)
                return FetchResult(url, FetchOutcome.OK, code, body, attempts)
            if code == 401:
                return FetchResult(url, FetchOutcome.UNAUTHORIZED, code, attempts=attempts)
            if code in (403, 503) and looks_like_challenge(resp.headers, body):
                return FetchResult(url, FetchOutcome.CHALLENGE, code, attempts=attempts,
                                   detail="bot challenge detected; skipped, not bypassed")
            if code == 403:
                return FetchResult(url, FetchOutcome.FORBIDDEN, code, attempts=attempts)
            if code in (404, 410):
                return FetchResult(url, FetchOutcome.NOT_FOUND, code, attempts=attempts)
            if code == 408:
                retries_408 += 1
                last = FetchResult(url, FetchOutcome.TIMEOUT, code, attempts=attempts, detail="HTTP 408")
                if retries_408 > s.max_408_retries or not self._can_retry(attempts):
                    return last
                self._sleep(self._backoff(attempts - 1))
                continue
            if code == 429:
                wait = parse_retry_after(resp.headers.get("Retry-After"))
                last = FetchResult(url, FetchOutcome.RATE_LIMITED, code, attempts=attempts)
                if wait is not None and wait > s.retry_after_cap:
                    last.detail = f"Retry-After {wait:.0f}s exceeds cap; giving up"
                    return last
                if not self._can_retry(attempts):
                    return last
                self._sleep(wait if wait is not None else self._backoff(attempts - 1))
                continue
            if code in (500, 502, 503, 504):
                last = FetchResult(url, FetchOutcome.SERVER_ERROR, code, attempts=attempts)
                if not self._can_retry(attempts):
                    return last
                self._sleep(self._backoff(attempts - 1))
                continue
            if 500 <= code < 600:
                return FetchResult(url, FetchOutcome.SERVER_ERROR, code, attempts=attempts)
            return FetchResult(url, FetchOutcome.CLIENT_ERROR, code, attempts=attempts,
                               detail=f"unhandled HTTP {code}")

    def _can_retry(self, attempts: int) -> bool:
        return attempts <= self.settings.max_retries
