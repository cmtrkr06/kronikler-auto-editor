from datetime import datetime, timedelta, timezone

import pytest
import requests
from requests.structures import CaseInsensitiveDict

from config import Settings
from fetcher import DomainRateLimiter, Fetcher, looks_like_challenge, parse_retry_after
from models import FetchOutcome

PAGE = b"<!doctype html><html><body><h1>DQ200</h1></body></html>"


# ---- fakes
class FakeResponse:
    def __init__(self, status=200, content=b"", headers=None):
        self.status_code, self.content = status, content
        self.headers = CaseInsensitiveDict(headers or {})


class FakeSession:
    """url -> list of FakeResponse/Exception, consumed in order (last one repeats)."""

    def __init__(self, script):
        self.script, self.calls = script, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        queue = self.script.get(url)
        if queue is None:
            return FakeResponse(404)  # e.g. robots.txt not scripted -> 404 -> allowed
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    def n_calls(self, url):
        return sum(1 for u, _ in self.calls if u == url)


URL = "https://example.com/feed.xml"


def make(script, **over):
    settings = Settings(per_domain_delay=0, **over)
    sleeps = []
    fetcher = Fetcher(settings, FakeSession(script), sleep_fn=sleeps.append, random_fn=lambda: 0.0)
    return fetcher, sleeps


# ---- fetch status handling
def test_200_ok_sends_honest_user_agent():
    f, sleeps = make({URL: [FakeResponse(200, PAGE)]})
    r = f.fetch(URL)
    assert r.ok and r.content == PAGE and r.attempts == 1 and sleeps == []
    ua = f.session.calls[-1][1]["headers"]["User-Agent"]
    assert ua == "KroniklerAutoEditor/1.0 (+https://otomobilkronikleri.blogspot.com/)"


@pytest.mark.parametrize("code,outcome", [
    (401, FetchOutcome.UNAUTHORIZED), (403, FetchOutcome.FORBIDDEN),
    (404, FetchOutcome.NOT_FOUND), (410, FetchOutcome.NOT_FOUND), (418, FetchOutcome.CLIENT_ERROR)])
def test_no_retry_statuses(code, outcome):
    f, sleeps = make({URL: [FakeResponse(code)]})
    r = f.fetch(URL)
    assert r.outcome == outcome and r.attempts == 1 and sleeps == []
    assert f.session.n_calls(URL) == 1


def test_403_with_cloudflare_challenge_is_skipped_not_bypassed():
    f, sleeps = make({URL: [FakeResponse(403, b"<title>Just a moment...</title>", {"cf-mitigated": "challenge"})]})
    r = f.fetch(URL)
    assert r.outcome == FetchOutcome.CHALLENGE and r.attempts == 1 and sleeps == []


def test_503_challenge_not_retried_but_plain_503_is():
    f, _ = make({URL: [FakeResponse(503, b"cf-chl-bypass stuff")]})
    assert f.fetch(URL).outcome == FetchOutcome.CHALLENGE
    f2, sleeps = make({URL: [FakeResponse(503), FakeResponse(200, PAGE)]})
    r = f2.fetch(URL)
    assert r.ok and r.attempts == 2 and sleeps == [2.0]


def test_500_exponential_backoff_then_success():
    f, sleeps = make({URL: [FakeResponse(500), FakeResponse(502), FakeResponse(504), FakeResponse(200, PAGE)]})
    r = f.fetch(URL)
    assert r.ok and r.attempts == 4
    assert sleeps == [2.0, 4.0, 8.0]


def test_500_forever_gives_up_after_max_retries():
    f, sleeps = make({URL: [FakeResponse(500)]}, max_retries=3)
    r = f.fetch(URL)
    assert r.outcome == FetchOutcome.SERVER_ERROR and r.attempts == 4
    assert len(sleeps) == 3 and f.session.n_calls(URL) == 4


def test_backoff_is_capped():
    f, sleeps = make({URL: [FakeResponse(500)]}, max_retries=6, backoff_cap=10.0)
    f.fetch(URL)
    assert max(sleeps) == 10.0


def test_429_honours_retry_after_seconds():
    f, sleeps = make({URL: [FakeResponse(429, headers={"Retry-After": "7"}), FakeResponse(200, PAGE)]})
    r = f.fetch(URL)
    assert r.ok and sleeps == [7.0]


def test_429_retry_after_http_date():
    when = (datetime.now(timezone.utc) + timedelta(seconds=30)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    wait = parse_retry_after(when)
    assert 25 <= wait <= 31


def test_429_retry_after_too_long_gives_up_without_sleeping():
    f, sleeps = make({URL: [FakeResponse(429, headers={"Retry-After": "3600"})]})
    r = f.fetch(URL)
    assert r.outcome == FetchOutcome.RATE_LIMITED and r.attempts == 1 and sleeps == []


def test_429_without_header_uses_backoff_and_is_bounded():
    f, sleeps = make({URL: [FakeResponse(429)]}, max_retries=2)
    r = f.fetch(URL)
    assert r.outcome == FetchOutcome.RATE_LIMITED and r.attempts == 3 and sleeps == [2.0, 4.0]


def test_408_limited_retry():
    f, sleeps = make({URL: [FakeResponse(408)]}, max_retries=10, max_408_retries=2)
    r = f.fetch(URL)
    assert r.outcome == FetchOutcome.TIMEOUT and r.attempts == 3 and len(sleeps) == 2


def test_timeout_retries_then_fails_gracefully():
    f, sleeps = make({URL: [requests.Timeout("slow")]}, max_retries=2)
    r = f.fetch(URL)
    assert r.outcome == FetchOutcome.TIMEOUT and r.attempts == 3 and len(sleeps) == 2


def test_timeout_then_recovery():
    f, _ = make({URL: [requests.Timeout("slow"), FakeResponse(200, PAGE)]})
    assert f.fetch(URL).ok


def test_connection_error_is_graceful():
    f, _ = make({URL: [requests.ConnectionError("dns")]}, max_retries=1)
    r = f.fetch(URL)
    assert r.outcome == FetchOutcome.NETWORK_ERROR and r.attempts == 2


def test_redirect_loop_not_retried():
    f, sleeps = make({URL: [requests.TooManyRedirects("loop")]})
    r = f.fetch(URL)
    assert r.outcome == FetchOutcome.TOO_MANY_REDIRECTS and sleeps == []


def test_oversized_response_rejected():
    f, _ = make({URL: [FakeResponse(200, b"x" * 100)]}, max_response_bytes=50)
    assert f.fetch(URL).outcome == FetchOutcome.TOO_LARGE


# ---- robots.txt
def test_robots_disallow_blocks_fetch():
    script = {"https://example.com/robots.txt": [FakeResponse(200, b"User-agent: *\nDisallow: /feed")],
              URL: [FakeResponse(200, PAGE)]}
    f, _ = make(script)
    r = f.fetch(URL)
    assert r.outcome == FetchOutcome.ROBOTS_DISALLOWED
    assert f.session.n_calls(URL) == 0


def test_robots_specific_agent_rule_applies():
    script = {"https://example.com/robots.txt": [FakeResponse(200, b"User-agent: KroniklerAutoEditor\nDisallow: /")],
              URL: [FakeResponse(200, PAGE)]}
    f, _ = make(script)
    assert f.fetch(URL).outcome == FetchOutcome.ROBOTS_DISALLOWED


def test_robots_missing_or_4xx_means_allowed_and_cached():
    f, _ = make({URL: [FakeResponse(200, PAGE)]})
    assert f.fetch(URL).ok and f.fetch(URL).ok
    assert f.session.n_calls("https://example.com/robots.txt") == 1


def test_robots_5xx_or_unreachable_means_skip():
    f, _ = make({"https://example.com/robots.txt": [FakeResponse(500)], URL: [FakeResponse(200, PAGE)]})
    assert f.fetch(URL).outcome == FetchOutcome.ROBOTS_UNREACHABLE
    f2, _ = make({"https://example.com/robots.txt": [requests.ConnectionError("x")], URL: [FakeResponse(200, PAGE)]})
    assert f2.fetch(URL).outcome == FetchOutcome.ROBOTS_UNREACHABLE
    assert f2.session.n_calls(URL) == 0


def test_robots_crawl_delay_raises_domain_delay():
    script = {"https://example.com/robots.txt": [FakeResponse(200, b"User-agent: *\nCrawl-delay: 9")],
              URL: [FakeResponse(200, PAGE)]}
    f, _ = make(script)
    f.fetch(URL)
    assert f.limiter._overrides["example.com"] == 9.0


# ---- rate limiter
def test_domain_rate_limiter_waits_only_for_same_domain():
    t = [100.0]
    sleeps = []
    lim = DomainRateLimiter(2.0, time_fn=lambda: t[0], sleep_fn=sleeps.append)
    lim.wait("a.com"); lim.wait("b.com")
    assert sleeps == []
    t[0] += 0.5
    lim.wait("a.com")
    assert sleeps == [1.5]


def test_looks_like_challenge():
    assert looks_like_challenge({}, b"<title>Just a moment...</title>")
    assert looks_like_challenge({"cf-mitigated": "challenge"}, b"")
    assert not looks_like_challenge({}, b"<rss></rss>")


def test_api_exception_skips_robots_only_for_listed_prefix():
    from fetcher import RobotsPolicy
    calls = []

    class Session:
        def get(self, url, **kw):
            calls.append(url)
            return type("R", (), {"status_code": 200, "content": b"User-agent: *\nDisallow: /w/\n"})()

    class Limiter:
        def wait(self, d): pass
        def set_delay(self, d, v): pass

    from config import Settings
    rp = RobotsPolicy(Session(), Settings(), Limiter(),
                      exceptions=(("https://commons.wikimedia.org/w/api.php", "policy"),))
    assert rp.check("https://commons.wikimedia.org/w/api.php?action=query") is None
    assert calls == []                                    # robots.txt not even fetched for the API
    assert rp.check("https://commons.wikimedia.org/w/index.php?title=x") is not None
    assert calls == ["https://commons.wikimedia.org/robots.txt"]


def test_default_exception_list_is_only_commons_api():
    from config import API_POLICY_EXCEPTIONS
    assert [p for p, _ in API_POLICY_EXCEPTIONS] == ["https://commons.wikimedia.org/w/api.php"]
